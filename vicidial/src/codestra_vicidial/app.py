from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from . import __version__
from .models import (
    AgentCommand, CallbackCommand, CampaignCommand, DisableAgentCommand,
    LeadCommand, ReconciliationCampaignCommand, TransferPolicyCommand,
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
    ("post", "/v1/agents/disable"): "telephony:agent-sync",
    ("get", "/v1/agents/{user_id}/actual-state"): "telephony:read",
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

    service = AdapterService(
        repository,
        state,
        flags,
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
    return create_app(
        service,
        auth,
        policy,
        release_info=release_info,
    )


app = app_from_environment()
