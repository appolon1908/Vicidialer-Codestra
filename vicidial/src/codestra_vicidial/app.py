from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from . import __version__
from .agent_provisioning import AgentProvisioningPolicy
from .internal_calling import (
    InternalCallError, InternalCallExecutor, InternalCallPolicy, OriginateCommand,
)
from .internal_call_transport import SocketAmiTransport
from .credential_authority import UnixCredentialAuthority
from .models import (
    AgentCommand,
    CallbackCommand,
    CampaignCommand,
    DisableAgentCommand,
    LeadCommand,
    ProvisionAgentCommand,
    ReconciliationCampaignCommand,
    TransferPolicyCommand,
)
from .reconciliation import ReconciliationPolicy
from .release import load_release_info
from .repository import CollisionError, MemoryRepository, MySQLRepository
from .security import RequestAuthenticator
from .service import AdapterService, FeatureFlags, ReadbackMismatch
from .state import StateStore

_AUTHENTICATION_HEADERS = (
    "X-Service-Identity",
    "X-Service-Scopes",
    "X-Request-Timestamp",
    "X-Request-Nonce",
    "X-Request-Signature",
    "X-Signature-Version",
)

_PROTECTED_SCOPE_BY_ROUTE = {
    ("post", "/v1/campaigns/validate"): "telephony:read",
    ("post", "/v1/campaigns/provision-disabled"): "telephony:provision",
    ("post", "/v1/campaigns/disable"): "telephony:provision",
    ("get", "/v1/campaigns/{campaign_id}/actual-state"): "telephony:read",
    ("post", "/v1/agents/sync"): "telephony:agent-sync",
    ("post", "/v1/agents/provision-disabled"): "telephony:agent-provision",
    ("post", "/v1/agents/disable"): "telephony:agent-sync",
    ("get", "/v1/agents/{user_id}/actual-state"): "telephony:read",
    ("get", "/v1/agents/availability/{user_id}/{extension}"): "telephony:read",
    ("post", "/v1/calls/originate"): "telephony:external-call",
    ("post", "/v1/calls/internal/originate"): "telephony:internal-call",
    ("get", "/v1/calls/internal/{operation_id}"): "telephony:internal-call-read",
    ("post", "/v1/calls/internal/{operation_id}/hangup"): "telephony:internal-call-hangup",
    ("post", "/v1/leads/publish"): "telephony:lead-publish",
    ("get", "/v1/leads/by-external-id/{external_id}"): "telephony:read",
    ("post", "/v1/callbacks/create"): "telephony:callback-write",
    ("get", "/v1/callbacks/{callback_id}"): "telephony:read",
    (
        "post",
        "/v1/reconciliation/campaigns/provision-disabled",
    ): "telephony:reconcile",
    ("post", "/v1/transfer-policies/sync"): "telephony:transfer-policy",
    ("post", "/v1/reconciliation/run"): "telephony:read",
    ("get", "/v1/reconciliation/{run_id}"): "telephony:read",
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

    @app.post("/v1/campaigns/validate")
    async def campaign_validate(request: Request, values=Depends(headers)):
        key, identity, scopes, timestamp, nonce, signature, signature_version = values
        body = await authorize(
            request, "telephony:read", identity, scopes, timestamp, nonce,
            signature, signature_version, key,
        )
        return service.validate_campaign(CampaignCommand.model_validate_json(body))

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
        actual = service.repository.campaign(campaign_id)
        if not actual:
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
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/agents/disable")
    async def agent_disable(request: Request, values=Depends(headers)):
        return await mutation(request, DisableAgentCommand, service.disable_agent,
                              "telephony:agent-sync", *values)

    @app.get("/v1/agents/{user_id}/actual-state")
    async def agent_actual(request: Request, user_id: str, values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        actual = service.repository.agent(user_id)
        if not actual:
            raise HTTPException(404, "agent missing")
        return actual

    @app.get("/v1/agents/availability/{user_id}/{extension}")
    async def agent_availability(request: Request, user_id: str, extension: str,
                                 values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        if user_id != "appolon" or extension != "6901":
            raise HTTPException(403, "availability query outside approved scope")
        return service.agent_availability(user_id, extension)


    @app.post("/v1/calls/originate")
    async def external_originate_disabled(request: Request):
        raise HTTPException(503, "production dialing executor is not enabled")

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
        return {"external_id": external_id, "lead_id": mapping["actual_id"]}

    @app.post("/v1/callbacks/create")
    async def callback_create(request: Request, values=Depends(headers)):
        return await mutation(request, CallbackCommand, service.create_callback,
                              "telephony:callback-write", *values)

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
        production_dialing=False,
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
    if flags.agent_provisioning:
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
    return create_app(
        service,
        auth,
        policy,
        agent_policy,
        internal_executor,
        release_info=release_info,
    )


app = app_from_environment()
