from __future__ import annotations

import json
import logging
import os
import stat
import uuid
from collections.abc import Mapping
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from . import __version__
from .agent_provisioning import AgentProvisioningPolicy
from .internal_calling import (
    InternalCallError, InternalCallExecutor, InternalCallPolicy, OriginateCommand,
)
from .external_calling import (
    ExternalCallError, ExternalCallExecutor, ExternalCallPolicy, ExternalOriginateCommand,
)
from .internal_call_transport import SocketAmiTransport
from .credential_authority import UnixCredentialAuthority
from .models import (
    AgentCommand,
    CallbackCommand,
    CallbackLookup,
    CampaignCommand,
    CommandLookup,
    CommandLookupResult,
    DisableAgentCommand,
    ExtensionAdoptCommand,
    ExtensionReserveCommand,
    LeadCommand,
    ProvisionAgentCommand,
    ReconciliationCampaignCommand,
    TransferPolicyCommand,
    WebrtcProvisionCommand,
    WebrtcRevokeCommand,
    WebrtcPjsipConfigAckCommand,
    WebrtcRotateCommand,
)
from .reconciliation import ReconciliationPolicy
from .release import load_release_info
from .repository import CollisionError, MemoryRepository, MySQLRepository
from .security import RequestAuthenticator
from .service import AdapterService, FeatureFlags, ReadbackMismatch, WebrtcSessionActive
from .state import (
    CommandAuditUnavailable, CommandRecordUnavailable, IdempotencyConflict, MutationCompletionUncertain, ResourceMutationInProgress,
    ResourceSnapshotChanged, StaleResourceVersion, StateStore,
)

_AUTHENTICATION_HEADERS = (
    "X-Service-Identity",
    "X-Service-Scopes",
    "X-Request-Timestamp",
    "X-Request-Nonce",
    "X-Request-Signature",
    "X-Signature-Version",
)

_PROTECTED_SCOPE_BY_ROUTE = {
    ("post", "/v1/idempotency/lookup"): "telephony:read",
    ("post", "/v1/campaigns/validate"): "telephony:read",
    ("post", "/v1/campaigns/provision-disabled"): "telephony:provision",
    ("post", "/v1/campaigns/disable"): "telephony:provision",
    ("get", "/v1/campaigns/{campaign_id}/actual-state"): "telephony:read",
    ("post", "/v1/agents/sync"): "telephony:agent-sync",
    ("post", "/v1/agents/provision-disabled"): "telephony:agent-provision",
    ("post", "/v1/agents/disable"): "telephony:agent-sync",
    ("get", "/v1/agents/{user_id}/actual-state"): "telephony:read",
    ("get", "/v1/agents/availability/{user_id}/{extension}"): "telephony:read",
    ("post", "/v1/extensions/reserve"): "telephony:extension-provision",
    ("post", "/v1/extensions/adopt"): "telephony:extension-provision",
    ("get", "/v1/extensions/{user_id}/actual-state"): "telephony:read",
    ("post", "/v1/webrtc/provision"): "telephony:webrtc-provision",
    ("post", "/v1/webrtc/rotate"): "telephony:webrtc-provision",
    ("post", "/v1/webrtc/revoke"): "telephony:webrtc-provision",
    ("get", "/v1/webrtc/pjsip-config/pending"): "telephony:read",
    ("post", "/v1/webrtc/pjsip-config/ack"): "telephony:webrtc-provision",
    ("post", "/v1/calls/originate"): "telephony:external-call",
    ("post", "/v1/calls/internal/originate"): "telephony:internal-call",
    ("get", "/v1/calls/internal/{operation_id}"): "telephony:internal-call-read",
    ("post", "/v1/calls/internal/{operation_id}/hangup"): "telephony:internal-call-hangup",
    ("post", "/v1/leads/publish"): "telephony:lead-publish",
    ("get", "/v1/leads/by-external-id/{external_id}"): "telephony:read",
    ("post", "/v1/callbacks/create"): "telephony:callback-write",
    ("get", "/v1/callbacks/{callback_id}"): "telephony:read",
    ("get", "/v1/callbacks/by-external-id/{external_id}"): "telephony:read",
    (
        "post",
        "/v1/reconciliation/campaigns/provision-disabled",
    ): "telephony:reconcile",
    ("post", "/v1/transfer-policies/sync"): "telephony:transfer-policy",
    ("post", "/v1/reconciliation/run"): "telephony:read",
    ("get", "/v1/reconciliation/{run_id}"): "telephony:read",
}


_COMMAND_MODEL_BY_ROUTE = {
    "/v1/idempotency/lookup": CommandLookup,
    "/v1/calls/originate": ExternalOriginateCommand,
    "/v1/campaigns/validate": CampaignCommand,
    "/v1/campaigns/provision-disabled": CampaignCommand,
    "/v1/campaigns/disable": CampaignCommand,
    "/v1/agents/sync": AgentCommand,
    "/v1/agents/provision-disabled": ProvisionAgentCommand,
    "/v1/agents/disable": DisableAgentCommand,
    "/v1/extensions/reserve": ExtensionReserveCommand,
    "/v1/extensions/adopt": ExtensionAdoptCommand,
    "/v1/webrtc/provision": WebrtcProvisionCommand,
    "/v1/webrtc/rotate": WebrtcRotateCommand,
    "/v1/webrtc/revoke": WebrtcRevokeCommand,
    "/v1/webrtc/pjsip-config/ack": WebrtcPjsipConfigAckCommand,
    "/v1/leads/publish": LeadCommand,
    "/v1/callbacks/create": CallbackCommand,
    "/v1/transfer-policies/sync": TransferPolicyCommand,
    "/v1/reconciliation/campaigns/provision-disabled": ReconciliationCampaignCommand,
    "/v1/calls/internal/originate": OriginateCommand,
}


def parse_allowed_lead_list_ids(raw: str) -> frozenset[int]:
    if not raw.strip():
        return frozenset()

    values: set[int] = set()
    for item in raw.split(","):
        token = item.strip()
        if not token or not token.isdecimal():
            raise ValueError(
                "lead list allowlist contains a non-numeric value"
            )
        list_id = int(token)
        if not 11000 <= list_id <= 91999:
            raise ValueError(
                "lead list allowlist value is outside the valid range"
            )
        values.add(list_id)
    return frozenset(values)


def install_openapi_contract(app: FastAPI) -> None:
    def custom_openapi():
        if app.openapi_schema is not None:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        schemes = components.setdefault("securitySchemes", {})
        schemes.update({
            "ServiceIdentity": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Service-Identity",
                "description": (
                    "Codestra machine identity; valid only with the "
                    "signed-request scheme."
                ),
            },
            "RequestSignature": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Request-Signature",
                "description": (
                    "Version 2 HMAC-SHA256 signature bound to method, path, "
                    "identity, sorted scopes, timestamp, nonce, idempotency "
                    "key, and body digest."
                ),
            },
        })

        # Bodies are parsed only after signature verification. Publish their
        # actual Pydantic schemas explicitly so generated clients see the same
        # command contract without moving validation ahead of authentication.
        schemas = components.setdefault("schemas", {})
        for route_path, model in _COMMAND_MODEL_BY_ROUTE.items():
            command_schema = model.model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
            schemas.update(command_schema.pop("$defs", {}))
            schemas[model.__name__] = command_schema
            schema["paths"][route_path]["post"]["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": {
                    "$ref": f"#/components/schemas/{model.__name__}"
                }}},
            }

        observed: set[tuple[str, str]] = set()
        for route_path, path_item in schema.get("paths", {}).items():
            if not route_path.startswith("/v1/"):
                continue
            for method, operation in path_item.items():
                method_lower = method.lower()
                if method_lower not in {
                    "get", "post", "put", "patch", "delete"
                }:
                    continue
                key = (method_lower, route_path)
                observed.add(key)
                scope = _PROTECTED_SCOPE_BY_ROUTE.get(key)
                if scope is None:
                    raise RuntimeError(
                        "OpenAPI protected route has no scope contract: "
                        f"{key}"
                    )
                operation["responses"]["403"] = {
                    "description": "Authentication or operation authority denied"
                }
                operation["responses"]["409"] = {
                    "description": "Resource collision, stale version, mutation in progress, or read-back mismatch"
                }
                operation["responses"]["503"] = {
                    "description": "Command completion uncertain; reconciliation required before another write"
                }
                operation["responses"]["422"] = {
                    "description": "Invalid request; submitted values are omitted from command validation errors"
                }
                operation["security"] = [{
                    "ServiceIdentity": [],
                    "RequestSignature": [],
                }]
                operation["x-codestra-authentication"] = "hmac-sha256"
                operation["x-codestra-required-scope"] = scope
                operation["x-codestra-required-headers"] = list(
                    _AUTHENTICATION_HEADERS
                )

        expected = set(_PROTECTED_SCOPE_BY_ROUTE)
        if observed != expected:
            raise RuntimeError(
                "OpenAPI protected route contract mismatch: "
                f"missing={sorted(expected - observed)} "
                f"unexpected={sorted(observed - expected)}"
            )

        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = custom_openapi


def create_app(
    service: AdapterService,
    authenticator: RequestAuthenticator,
    reconciliation_policy: ReconciliationPolicy | None = None,
    agent_provisioning_policy: AgentProvisioningPolicy | None = None,
    internal_call_executor: InternalCallExecutor | None = None,
    release_info: Mapping[str, object] | None = None,
    external_call_executor: ExternalCallExecutor | None = None,
) -> FastAPI:
    release_document = dict(
        release_info
        if release_info is not None
        else load_release_info(required=False)
    )
    app = FastAPI(
        title="Codestra restricted VICIdial adapter",
        version=__version__,
    )
    app.state.adapter_service = service
    app.state.internal_call_executor = internal_call_executor
    app.state.external_call_executor = external_call_executor


    @app.middleware("http")
    async def audit_command_lookup(request: Request, call_next):
        if request.url.path != "/v1/idempotency/lookup" or request.method != "POST":
            return await call_next(request)
        metadata = {
            "correlation_id": "lookup-" + uuid.uuid4().hex,
            "actor": "unauthenticated",
            "target": {"route": "/v1/idempotency/lookup"},
        }
        request.state.lookup_audit = metadata
        try:
            response = await call_next(request)
        except Exception:
            # Never expose storage/validation exception text or a partial result.
            response = JSONResponse(status_code=503, content={
                "code": "command_result_unavailable", "status": "reconciliation_required",
                "detail": "durable command result could not be verified",
            })
        outcome = {
            200: "recorded", 403: "denied", 404: "not_recorded",
            409: "conflict", 422: "invalid", 503: "unavailable",
        }.get(response.status_code, "failed")
        try:
            service.state.audit_lookup(
                metadata["correlation_id"], metadata["actor"],
                metadata["target"], outcome, response.status_code,
            )
        except CommandAuditUnavailable:
            # The service log retains a redacted failure event if the durable
            # sink is unavailable. A successful lookup must never hide that loss.
            logging.getLogger("codestra_vicidial.audit").error(
                "lookup_audit_unavailable %s", json.dumps({
                    "correlation_id": metadata["correlation_id"],
                    "actor": metadata["actor"],
                    "target_hash": service.state.digest(metadata["target"]),
                    "outcome": outcome, "http_status": response.status_code,
                    "durable": False,
                }, sort_keys=True),
            )
            if response.status_code != 503:
                return JSONResponse(status_code=503, content={
                    "code": "lookup_audit_unavailable", "status": "reconciliation_required",
                    "detail": "lookup audit could not be persisted",
                })
        return response

    @app.exception_handler(ValidationError)
    async def command_validation_handler(_request: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": list(error["loc"]), "type": error["type"]}
            for error in exc.errors(include_input=False, include_context=False)
        ]})

    @app.exception_handler(ResourceSnapshotChanged)
    async def snapshot_handler(_request: Request, _exc: ResourceSnapshotChanged):
        return JSONResponse(status_code=409, content={
            "code": "resource_snapshot_changed",
            "detail": "resource changed during read-back; retry the read",
        })

    @app.exception_handler(MutationCompletionUncertain)
    async def completion_handler(_request: Request, _exc: MutationCompletionUncertain):
        return JSONResponse(status_code=503, content={
            "code": "mutation_completion_uncertain",
            "status": "reconciliation_required",
            "detail": "command completion requires reconciliation",
        })

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_handler(_request: Request, _exc: IdempotencyConflict):
        return JSONResponse(status_code=409, content={
            "code": "idempotency_conflict",
            "detail": "idempotency key already used with a different command",
        })

    @app.exception_handler(StaleResourceVersion)
    @app.exception_handler(ResourceMutationInProgress)
    async def version_conflict_handler(_request: Request, exc: Exception):
        code = (
            "stale_resource_version" if isinstance(exc, StaleResourceVersion)
            else "resource_mutation_in_progress"
        )
        return JSONResponse(status_code=409, content={
            "code": code, "detail": str(exc),
        })

    @app.exception_handler(CommandRecordUnavailable)
    async def command_record_handler(_request: Request, _exc: CommandRecordUnavailable):
        return JSONResponse(status_code=503, content={
            "code": "command_result_unavailable", "status": "reconciliation_required",
            "detail": "durable command result could not be verified",
        })

    @app.exception_handler(CollisionError)
    async def collision_handler(_request: Request, exc: CollisionError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ReadbackMismatch)
    async def readback_handler(_request: Request, exc: ReadbackMismatch):
        return JSONResponse(
            status_code=409,
            content={
                "status": "reconciliation_required",
                "detail": str(exc),
                "readback": exc.results,
            },
        )

    @app.exception_handler(WebrtcSessionActive)
    async def webrtc_session_active_handler(_request: Request, exc: WebrtcSessionActive):
        return JSONResponse(status_code=409, content={
            "code": "WEBRTC_SESSION_ALREADY_ACTIVE", "detail": str(exc),
        })

    @app.get("/health")
    def health():
        return {"status": "live"}

    @app.get("/ready")
    def ready():
        return {
            "status": "ready",
            "writes_enabled": service.flags.writes,
            "release_verified": bool(release_document.get("verified")),
            "deployment_profile": os.environ.get(
                "CODESTRA_ADAPTER_DEPLOYMENT", "staging-read-only"
            ),
            "reconciliation_policy_verified": reconciliation_policy is not None,
        }

    @app.get("/version")
    def version():
        return dict(release_document)

    @app.get("/capabilities")
    def capabilities():
        """Publish the bounded adapter surface without revealing credentials.

        This endpoint is intentionally read-only. It lets Middleware and
        deployment probes discover the exact route scopes and current safety
        gates before submitting a command.
        """
        routes = [
            {
                "method": method.upper(),
                "path": path,
                "scope": scope,
                "mutation": method.lower() == "post",
            }
            for (method, path), scope in sorted(_PROTECTED_SCOPE_BY_ROUTE.items())
        ]
        return {
            "schema_version": "vicidial-capabilities.v1",
            "service": "codestra-vicidial-adapter",
            "adapter_version": __version__,
            "contract_version": 1,
            "external_effects": {
                "production_dialing": bool(service.flags.production_dialing),
                "telephony_admin_write": False,
                "provider_credentials_exposed": False,
            },
            "feature_flags": {
                "writes": bool(service.flags.writes),
                "provisioning": bool(service.flags.provisioning),
                "agent_sync": bool(service.flags.agent_sync),
                "agent_provisioning": bool(service.flags.agent_provisioning),
                "lead_publication": bool(service.flags.lead_publication),
                "callback_writes": bool(service.flags.callback_writes),
                "transfer_policy_sync": bool(service.flags.transfer_policy_sync),
                "extension_provisioning": bool(service.flags.extension_provisioning),
                "webrtc_provisioning": bool(service.flags.webrtc_provisioning),
            },
            "routes": routes,
        }


    async def authorize(
        request: Request, required_scope: str, identity: str, scopes: str,
        timestamp: int, nonce: str, signature: str, signature_version: str,
        idempotency_key: str = "",
    ) -> bytes:
        body = await request.body()
        source = request.client.host if request.client else ""
        try:
            authenticator.verify(
                source=source, identity=identity,
                scopes={scope for scope in scopes.split(" ") if scope},
                timestamp=timestamp, nonce=nonce, body=body, signature=signature,
                required_scope=required_scope,
                method=request.method, path=request.url.path,
                idempotency_key=idempotency_key,
                signature_version=signature_version,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return body

    async def mutation(request: Request, model, handler, scope: str,
                       idempotency_key: str, identity: str, scopes: str,
                       timestamp: int, nonce: str, signature: str,
                       signature_version: str):
        body = await authorize(
            request, scope, identity, scopes, timestamp, nonce, signature,
            signature_version, idempotency_key,
        )
        try:
            command = model.model_validate_json(body)
            return handler(idempotency_key, command)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValidationError, StaleResourceVersion, IdempotencyConflict, CollisionError):
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def deny_generic_reconciliation_mutation() -> None:
        if reconciliation_policy is not None:
            raise HTTPException(403, "generic mutation disabled in reconciliation profile")

    def headers(
        idempotency_key: str = Header(..., min_length=8, max_length=200),
        identity: str = Header(..., alias="X-Service-Identity"),
        scopes: str = Header(..., alias="X-Service-Scopes"),
        timestamp: int = Header(..., alias="X-Request-Timestamp"),
        nonce: str = Header(..., alias="X-Request-Nonce", min_length=16, max_length=128),
        signature: str = Header(..., alias="X-Request-Signature", min_length=64, max_length=64),
        signature_version: str = Header(..., alias="X-Signature-Version"),
    ):
        return idempotency_key, identity, scopes, timestamp, nonce, signature, signature_version

    def read_headers(
        identity: str = Header(..., alias="X-Service-Identity"),
        scopes: str = Header(..., alias="X-Service-Scopes"),
        timestamp: int = Header(..., alias="X-Request-Timestamp"),
        nonce: str = Header(..., alias="X-Request-Nonce", min_length=16, max_length=128),
        signature: str = Header(..., alias="X-Request-Signature", min_length=64, max_length=64),
        signature_version: str = Header(..., alias="X-Signature-Version"),
    ):
        return identity, scopes, timestamp, nonce, signature, signature_version

    @app.post(
        "/v1/idempotency/lookup", response_model=CommandLookupResult,
        responses={404: {"description": "No committed result; execution may still require reconciliation"}},
    )
    async def command_lookup(request: Request, values=Depends(read_headers)):
        body = await authorize(request, "telephony:read", *values)
        request.state.lookup_audit["actor"] = authenticator.identity
        query = CommandLookup.model_validate_json(body)
        request.state.lookup_audit.update({
            "correlation_id": query.root.command.context.correlation_id,
            "target": {"operation": query.root.operation, "idempotency_key": query.root.idempotency_key},
        })
        try:
            result = service.lookup_command(query)
        except PermissionError as exc:
            raise HTTPException(403, "command campaign authority denied") from exc
        if result is None:
            return JSONResponse(status_code=404, content={
                "code": "command_result_not_found", "status": "not_recorded",
                "detail": "no committed result; execution may still require reconciliation",
            })
        return result

    @app.post("/v1/campaigns/validate")
    async def campaign_validate(request: Request, values=Depends(headers)):
        return await mutation(
            request, CampaignCommand, lambda _key, command: service.validate_campaign(command),
            "telephony:read", *values,
        )

    @app.post("/v1/campaigns/provision-disabled")
    async def campaign_provision(request: Request, values=Depends(headers)):
        deny_generic_reconciliation_mutation()
        return await mutation(request, CampaignCommand, service.provision_campaign,
                              "telephony:provision", *values)

    @app.post("/v1/campaigns/disable")
    async def campaign_disable(request: Request, values=Depends(headers)):
        deny_generic_reconciliation_mutation()
        return await mutation(request, CampaignCommand, service.disable_campaign,
                              "telephony:provision", *values)

    @app.get("/v1/campaigns/{campaign_id}/actual-state")
    async def campaign_actual(request: Request, campaign_id: str,
                              values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        mapping = service.state.mapping_for_actual("campaign", campaign_id)
        if mapping:
            actual = service.versioned_readback(
                f"campaign:{mapping['external_id']}",
                lambda: service.repository.campaign(campaign_id),
            )
        else:
            row = service.repository.campaign(campaign_id)
            actual = row | {"resource_version": None} if row else None
        if actual is None:
            raise HTTPException(404, "campaign missing")
        return actual

    @app.post("/v1/agents/sync")
    async def agent_sync(request: Request, values=Depends(headers)):
        return await mutation(request, AgentCommand, service.sync_agent,
                              "telephony:agent-sync", *values)

    @app.post("/v1/agents/provision-disabled")
    async def agent_provision(request: Request, values=Depends(headers)):
        if agent_provisioning_policy is None:
            raise HTTPException(403, "agent provisioning profile is disabled")
        key, identity, scopes, timestamp, nonce, signature, signature_version = values
        body = await authorize(
            request, "telephony:agent-provision", identity, scopes, timestamp,
            nonce, signature, signature_version, key,
        )
        try:
            command = ProvisionAgentCommand.model_validate_json(body)
            agent_provisioning_policy.authorize(command)
            return service.provision_agent(key, command)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValidationError, StaleResourceVersion, IdempotencyConflict, CollisionError):
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/agents/disable")
    async def agent_disable(request: Request, values=Depends(headers)):
        return await mutation(request, DisableAgentCommand, service.disable_agent,
                              "telephony:agent-sync", *values)

    @app.get("/v1/agents/{user_id}/actual-state")
    async def agent_actual(request: Request, user_id: str, values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        actual = service.versioned_readback(
            f"agent:{user_id}", lambda: service.repository.agent(user_id),
        )
        if actual is None:
            raise HTTPException(404, "agent missing")
        return actual

    @app.get("/v1/agents/availability/{user_id}/{extension}")
    async def agent_availability(request: Request, user_id: str, extension: str,
                                 values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        if user_id != "appolon" or extension != "6901":
            raise HTTPException(403, "availability query outside approved scope")
        return service.agent_availability(user_id, extension)

    @app.post("/v1/extensions/reserve")
    async def extension_reserve(request: Request, values=Depends(headers)):
        return await mutation(request, ExtensionReserveCommand, service.reserve_extension,
                              "telephony:extension-provision", *values)

    @app.post("/v1/extensions/adopt")
    async def extension_adopt(request: Request, values=Depends(headers)):
        return await mutation(request, ExtensionAdoptCommand, service.adopt_extension,
                              "telephony:extension-provision", *values)

    @app.get("/v1/extensions/{user_id}/actual-state")
    async def extension_actual(request: Request, user_id: str, values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        actual = service.versioned_readback(
            f"extension:{user_id}", lambda: service.repository.phone(user_id),
        )
        if actual is None:
            raise HTTPException(404, "phone assignment missing")
        return actual

    @app.post("/v1/webrtc/provision")
    async def webrtc_provision(request: Request, values=Depends(headers)):
        return await mutation(request, WebrtcProvisionCommand, service.provision_webrtc,
                              "telephony:webrtc-provision", *values)

    @app.post("/v1/webrtc/rotate")
    async def webrtc_rotate(request: Request, values=Depends(headers)):
        return await mutation(request, WebrtcRotateCommand, service.rotate_webrtc_secret,
                              "telephony:webrtc-provision", *values)

    @app.post("/v1/webrtc/revoke")
    async def webrtc_revoke(request: Request, values=Depends(headers)):
        return await mutation(request, WebrtcRevokeCommand, service.revoke_webrtc,
                              "telephony:webrtc-provision", *values)

    @app.get("/v1/webrtc/pjsip-config/pending")
    async def webrtc_pjsip_config_pending(request: Request, values=Depends(read_headers)):
        """Mission 4 delivery: Server B's sync agent polls this to learn
        which generated PJSIP artifacts have not yet been confirmed applied.
        Deliberately available even while webrtc_provisioning is disabled --
        observing drift is not a mutation."""
        await authorize(request, "telephony:read", *values)
        return {"pending": service.list_pending_pjsip_config()}

    @app.post("/v1/webrtc/pjsip-config/ack")
    async def webrtc_pjsip_config_ack(request: Request, values=Depends(headers)):
        return await mutation(request, WebrtcPjsipConfigAckCommand,
                              service.ack_pjsip_config_applied,
                              "telephony:webrtc-provision", *values)


    @app.post("/v1/calls/originate")
    async def external_originate(request: Request, values=Depends(headers)):
        key, identity, scopes, timestamp, nonce, signature, signature_version = values
        body = await authorize(
            request, "telephony:external-call", identity, scopes, timestamp, nonce,
            signature, signature_version, key,
        )
        if external_call_executor is None:
            raise HTTPException(503, "production dialing executor is not enabled")
        try:
            command = ExternalOriginateCommand.model_validate_json(body)
            if key != command.operation_id:
                raise ValueError("idempotency key must equal operation_id")
            return external_call_executor.originate(command, actor=identity)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, "invalid external call command") from exc
        except ExternalCallError as exc:
            raise HTTPException(503, str(exc)) from exc

    @app.exception_handler(InternalCallError)
    async def internal_call_error(_request: Request, _exc: InternalCallError):
        return JSONResponse(status_code=503, content={"detail": "internal call requires reconciliation"})

    @app.post("/v1/calls/internal/originate")
    async def internal_originate(request: Request, values=Depends(headers)):
        if internal_call_executor is None:
            raise HTTPException(403, "internal calling profile is disabled")
        key, identity, scopes, timestamp, nonce, signature, signature_version = values
        body = await authorize(request, "telephony:internal-call", identity, scopes,
                               timestamp, nonce, signature, signature_version, key)
        try:
            command = OriginateCommand.model_validate_json(body)
            if key != command.operation_id:
                raise ValueError("idempotency key must equal operation_id")
            return internal_call_executor.originate(command, actor=identity)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            internal_call_executor._audit("originate", key, key, identity, "invalid_request")
            raise HTTPException(422, "invalid internal call command") from exc

    @app.get("/v1/calls/internal/{operation_id}")
    async def internal_readback(request: Request, operation_id: str,
                                values=Depends(read_headers)):
        if internal_call_executor is None:
            raise HTTPException(403, "internal calling profile is disabled")
        await authorize(request, "telephony:internal-call-read", *values)
        try:
            return internal_call_executor.readback(operation_id)
        except PermissionError as exc:
            raise HTTPException(403, "internal call policy denied") from exc
        except KeyError as exc:
            raise HTTPException(404, "internal call operation missing") from exc

    @app.post("/v1/calls/internal/{operation_id}/hangup")
    async def internal_hangup(request: Request, operation_id: str,
                              values=Depends(headers)):
        if internal_call_executor is None:
            raise HTTPException(403, "internal calling profile is disabled")
        key, identity, scopes, timestamp, nonce, signature, signature_version = values
        await authorize(request, "telephony:internal-call-hangup", identity, scopes,
                        timestamp, nonce, signature, signature_version, key)
        if key != operation_id + ":hangup":
            internal_call_executor._audit(
                "hangup", operation_id, operation_id, identity, "invalid_request"
            )
            raise HTTPException(422, "hangup idempotency key is not operation-bound")
        try:
            return internal_call_executor.hangup(operation_id, actor=identity)
        except PermissionError as exc:
            raise HTTPException(403, "internal call policy denied") from exc
        except KeyError as exc:
            raise HTTPException(404, "internal call operation missing") from exc

    @app.post("/v1/leads/publish")
    async def lead_publish(request: Request, values=Depends(headers)):
        return await mutation(request, LeadCommand, service.publish_lead,
                              "telephony:lead-publish", *values)

    @app.get("/v1/leads/by-external-id/{external_id}")
    async def lead_by_external_id(request: Request, external_id: str,
                                  values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        mapping = service.state.mapping("lead", external_id)
        if not mapping:
            raise HTTPException(404, "lead missing")
        return {
            "external_id": external_id, "lead_id": mapping["actual_id"],
            "resource_version": service.state.resource_version(f"lead:{external_id}"),
        }

    @app.post("/v1/callbacks/create")
    async def callback_create(request: Request, values=Depends(headers)):
        return await mutation(request, CallbackCommand, service.create_callback,
                              "telephony:callback-write", *values)

    @app.get("/v1/callbacks/by-external-id/{external_id}", response_model=CallbackLookup)
    async def callback_lookup(request: Request, external_id: str,
                              values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        result = service.callback_by_external_id(external_id)
        if result is None:
            raise HTTPException(404, "callback mapping missing")
        return result

    @app.get("/v1/callbacks/{callback_id}")
    async def callback_actual(request: Request, callback_id: int,
                              values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        actual = service.repository.callback(callback_id)
        if not actual:
            raise HTTPException(404, "callback missing")
        return actual

    @app.post("/v1/reconciliation/campaigns/provision-disabled")
    async def reconciliation_campaign(request: Request, values=Depends(headers)):
        if reconciliation_policy is None:
            raise HTTPException(403, "production reconciliation profile is disabled")
        key, identity, scopes, timestamp, nonce, signature, signature_version = values
        body = await authorize(
            request, "telephony:reconcile", identity, scopes, timestamp, nonce,
            signature, signature_version, key,
        )
        try:
            command = ReconciliationCampaignCommand.model_validate_json(body)
            reconciliation_policy.authorize(command)
            return service.provision_campaign(key, command)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ValidationError, StaleResourceVersion, IdempotencyConflict, CollisionError):
            raise
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/transfer-policies/sync")
    async def transfer_sync(request: Request, values=Depends(headers)):
        return await mutation(request, TransferPolicyCommand, service.sync_transfer_policy,
                              "telephony:transfer-policy", *values)

    @app.post("/v1/reconciliation/run")
    async def reconciliation_run(request: Request, values=Depends(headers)):
        key, identity, scopes, timestamp, nonce, signature, signature_version = values
        await authorize(
            request, "telephony:read", identity, scopes, timestamp, nonce,
            signature, signature_version, key,
        )
        return service.start_reconciliation(key)

    @app.get("/v1/reconciliation/{run_id}")
    async def reconciliation_get(request: Request, run_id: str,
                                 values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        result = service.reconciliation_run(run_id)
        if result is None:
            raise HTTPException(404, "reconciliation run missing")
        return {"run_id": run_id, **result}

    install_openapi_contract(app)
    return app


def app_from_environment() -> FastAPI:
    # No environment variable contains a credential value. The MySQL backend
    # accepts only a loopback host and a protected JSON secret file.
    deployment = os.environ.get("CODESTRA_ADAPTER_DEPLOYMENT", "development-loopback")
    secret_file = Path(os.environ.get("CODESTRA_ADAPTER_HMAC_FILE", "/run/secrets/adapter-hmac"))
    if secret_file.exists():
        key = secret_file.read_bytes().strip()
    elif deployment == "development-loopback":
        key = b"development-disabled"
    else:
        raise RuntimeError("adapter HMAC credential is required")
    flags = FeatureFlags(
        writes=os.environ.get("ENABLE_VICIDIAL_WRITES") == "true",
        provisioning=os.environ.get("ENABLE_PROVISIONING") == "true",
        agent_sync=os.environ.get("ENABLE_AGENT_SYNC") == "true",
        agent_provisioning=os.environ.get("ENABLE_AGENT_PROVISIONING") == "true",
        lead_publication=os.environ.get("ENABLE_LEAD_PUBLICATION") == "true",
        allowed_lead_list_ids=parse_allowed_lead_list_ids(
            os.environ.get("CODESTRA_ALLOWED_LEAD_LIST_IDS", "")
        ),
        callback_writes=os.environ.get("ENABLE_IVR_CALLBACKS") == "true",
        transfer_policy_sync=os.environ.get("ENABLE_WARM_TRANSFER") == "true",
        production_dialing=os.environ.get("ENABLE_PRODUCTION_DIALING") == "true",
        extension_provisioning=os.environ.get("ENABLE_EXTENSION_PROVISIONING") == "true",
        webrtc_provisioning=os.environ.get("ENABLE_WEBRTC_PROVISIONING") == "true",
    )
    backend_name = os.environ.get("CODESTRA_ADAPTER_BACKEND", "memory")
    if backend_name == "mysql":
        import pymysql
        credential_file = Path(os.environ.get(
            "CODESTRA_VICIDIAL_DB_FILE", "/run/secrets/vicidial-db.json"))
        credentials = __import__("json").loads(credential_file.read_text())
        host = credentials.get("host", "127.0.0.1")
        if host not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("VICIdial database must be loopback-only")
        connection = pymysql.connect(
            host=host, unix_socket=credentials.get("unix_socket"),
            user=credentials["user"], password=credentials["password"],
            database=credentials.get("database", "asterisk"),
            port=int(credentials.get("port", 3306)), autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
        repository = MySQLRepository(connection)
    elif backend_name == "memory":
        repository = MemoryRepository()
    else:
        raise RuntimeError("unsupported adapter backend")
    state = StateStore(
        Path(
            os.environ.get(
                "CODESTRA_ADAPTER_STATE",
                "/var/lib/codestra-vicidial/state.sqlite3",
            )
        )
    )

    credential_authority = None
    if flags.agent_provisioning or flags.extension_provisioning or flags.webrtc_provisioning:
        authority_path = Path(os.environ.get(
            "CODESTRA_SECRET_AUTHORITY_SOCKET", "/run/secrets/authority.sock"
        ))
        try:
            authority_stat = authority_path.stat()
        except OSError as exc:
            raise RuntimeError("approved secret authority unavailable") from exc
        if not stat.S_ISSOCK(authority_stat.st_mode) or not os.access(
            authority_path, os.R_OK | os.W_OK
        ):
            raise RuntimeError("approved secret authority unavailable or unauthorized")
        credential_authority = UnixCredentialAuthority(authority_path)
    service = AdapterService(
        repository,
        state,
        flags,
        credential_authority,
    )

    configured_sources = {
        os.environ.get("AUTHORIZED_APPLICATION_SERVER_IP", "65.109.65.169"),
        os.environ.get("AUTHORIZED_PRIVATE_SOURCE_IP", "10.40.0.1"),
        "127.0.0.1",
    }
    container_proxy = os.environ.get("AUTHORIZED_CONTAINER_PROXY_IP")
    if container_proxy:
        import ipaddress
        address = ipaddress.ip_address(container_proxy)
        if not address.is_private:
            raise RuntimeError("container proxy source must be a private address")
        configured_sources.add(str(address))
    if deployment == "development-loopback":
        configured_sources = {"127.0.0.1"}
    auth = RequestAuthenticator(
        identity=os.environ.get(
            "CODESTRA_ADAPTER_IDENTITY",
            "codestra-middleware",
        ),
        key=key,
        authorized_sources=frozenset(configured_sources),
        nonce_store=state.consume_nonce,
        rate_limit_store=state.consume_rate_limit,
        rate_limit_requests=int(
            os.environ.get(
                "CODESTRA_ADAPTER_RATE_LIMIT_REQUESTS",
                "120",
            )
        ),
        rate_limit_window_seconds=int(
            os.environ.get(
                "CODESTRA_ADAPTER_RATE_LIMIT_WINDOW_SECONDS",
                "60",
            )
        ),
    )
    release_info = load_release_info(
        required=(
            os.environ.get("CODESTRA_RELEASE_METADATA_REQUIRED", "false").lower()
            == "true"
            or deployment in {
                "production-reconciliation",
                "production-internal-test",
                "production-external-one-call",
            }
        )
    )
    policy = None
    if deployment == "production-reconciliation":
        release_sha = release_info.get("git_sha")
        if release_info.get("verified") is not True or not isinstance(release_sha, str):
            raise RuntimeError("verified release metadata required for reconciliation")
        policy = ReconciliationPolicy.from_file(
            Path(os.environ["CODESTRA_RECONCILIATION_POLICY_FILE"]),
            expected_release_sha=release_sha,
        )
    agent_policy = None
    if os.environ.get("ENABLE_AGENT_PROVISIONING") == "true":
        release_sha = release_info.get("git_sha")
        if release_info.get("verified") is not True or not isinstance(release_sha, str):
            raise RuntimeError("verified release metadata required for agent provisioning")
        agent_policy = AgentProvisioningPolicy.from_file(
            Path(os.environ["CODESTRA_AGENT_PROVISIONING_POLICY_FILE"]),
            expected_release_sha=release_sha,
        )
    internal_executor = None
    internal_gate = os.environ.get("ENABLE_INTERNAL_TEST_CALLING", "false")
    if internal_gate not in {"true", "false"}:
        raise RuntimeError("invalid internal call gate")
    if deployment != "production-internal-test" and internal_gate != "false":
        raise RuntimeError("internal calls require their dedicated production-internal-test profile")
    if deployment == "production-internal-test":
        if backend_name != "mysql":
            raise RuntimeError("internal calls require the live mysql repository")
        if any(os.environ.get(name, "false") != "false" for name in (
            "ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING", "ENABLE_AGENT_PROVISIONING",
            "ENABLE_AGENT_SYNC", "ENABLE_LEAD_PUBLICATION", "ENABLE_LIVE_CALL_CONTROL",
            "ENABLE_WARM_TRANSFER", "ENABLE_IVR_ROUTING", "ENABLE_IVR_CALLBACKS",
            "ENABLE_PRODUCTION_DIALING", "ENABLE_SMS_DELIVERY", "ENABLE_EMAIL_DELIVERY",
            "ENABLE_EXTERNAL_DELIVERY", "ENABLE_AI_ACTIONS")):
            raise RuntimeError("internal calling requires unrelated writes disabled")
        release_sha = release_info.get("git_sha")
        if release_info.get("verified") is not True or not isinstance(release_sha, str):
            raise RuntimeError("verified release metadata required for internal calling")
        call_policy = InternalCallPolicy.from_file(
            Path(os.environ["CODESTRA_INTERNAL_CALL_POLICY_FILE"]), release_sha
        )
        internal_executor = InternalCallExecutor(
            call_policy,
            Path(os.environ.get("CODESTRA_INTERNAL_CALL_STATE", "/var/lib/codestra-vicidial/internal-calls.sqlite3")),
            Path(os.environ.get(
                "CODESTRA_AMI_LIFECYCLE_STATE",
                os.environ.get(
                    "AMI_EVENT_STATE_DB",
                    "/var/lib/codestra-vicidial/ami-events.sqlite3",
                ),
            )),
            SocketAmiTransport(Path(os.environ["CODESTRA_AMI_CREDENTIAL_FILE"])),
            policy_provider=lambda: InternalCallPolicy.from_file(
                Path(os.environ["CODESTRA_INTERNAL_CALL_POLICY_FILE"]), release_sha),
            identity_ready=repository.internal_call_identity_ready,
            enabled=lambda: os.environ.get("ENABLE_INTERNAL_TEST_CALLING", "false") == "true",
            audit_path=Path(os.environ.get("CODESTRA_ADAPTER_STATE", "/var/lib/codestra-vicidial/state.sqlite3")),
        )
    external_executor = None
    external_gate = os.environ.get("ENABLE_PRODUCTION_DIALING", "false")
    if external_gate not in {"true", "false"}:
        raise RuntimeError("invalid production dialing gate")
    external_profile = deployment == "production-external-one-call"
    if not external_profile and external_gate != "false":
        raise RuntimeError("external dialing requires production-external-one-call profile")
    if external_profile:
        if external_gate != "true":
            raise RuntimeError("external one-call profile requires explicit dialing gate")
        if backend_name != "memory":
            raise RuntimeError("external one-call profile requires isolated memory backend")
        if any(os.environ.get(name, "false") != "false" for name in (
            "ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING", "ENABLE_AGENT_PROVISIONING",
            "ENABLE_AGENT_SYNC", "ENABLE_LEAD_PUBLICATION", "ENABLE_LIVE_CALL_CONTROL",
            "ENABLE_WARM_TRANSFER", "ENABLE_IVR_ROUTING", "ENABLE_IVR_CALLBACKS",
            "ENABLE_SMS_DELIVERY", "ENABLE_EMAIL_DELIVERY", "ENABLE_EXTERNAL_DELIVERY",
            "ENABLE_AI_ACTIONS", "ENABLE_INTERNAL_TEST_CALLING")):
            raise RuntimeError("external one-call profile requires unrelated writes disabled")
        release_sha = release_info.get("git_sha")
        if release_info.get("verified") is not True or not isinstance(release_sha, str):
            raise RuntimeError("verified release metadata required for external calling")
        external_policy_path = Path(os.environ["CODESTRA_EXTERNAL_CALL_POLICY_FILE"])
        external_policy = ExternalCallPolicy.from_file(external_policy_path, release_sha)
        ami_credential_path = Path(os.environ["CODESTRA_AMI_CREDENTIAL_FILE"])
        external_executor = ExternalCallExecutor(
            external_policy,
            Path(os.environ.get(
                "CODESTRA_EXTERNAL_CALL_STATE",
                "/var/lib/codestra-vicidial/external-calls.sqlite3",
            )),
            SocketAmiTransport(ami_credential_path),
            policy_provider=lambda: ExternalCallPolicy.from_file(
                external_policy_path, release_sha
            ),
            enabled=lambda: (
                os.environ.get("CODESTRA_ADAPTER_DEPLOYMENT")
                == "production-external-one-call"
                and os.environ.get("ENABLE_PRODUCTION_DIALING") == "true"
            ),
            audit_path=Path(os.environ.get(
                "CODESTRA_ADAPTER_STATE",
                "/var/lib/codestra-vicidial/state.sqlite3",
            )),
        )

    return create_app(
        service,
        auth,
        policy,
        agent_policy,
        internal_executor,
        release_info=release_info,
        external_call_executor=external_executor,
    )


app = app_from_environment()
