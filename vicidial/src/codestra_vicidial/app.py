from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from . import __version__
from .models import (
    AgentCommand, CallbackCommand, CampaignCommand, DisableAgentCommand,
    LeadCommand, ReconciliationCampaignCommand, TransferPolicyCommand,
)
from .reconciliation import ReconciliationPolicy
from .repository import CollisionError, MemoryRepository, MySQLRepository
from .security import RequestAuthenticator
from .service import AdapterService, FeatureFlags, ReadbackMismatch
from .state import StateStore


def create_app(service: AdapterService, authenticator: RequestAuthenticator,
               reconciliation_policy: ReconciliationPolicy | None = None) -> FastAPI:
    app = FastAPI(title="Codestra restricted VICIdial adapter", version=__version__)

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
        return {"status": "ready", "writes_enabled": service.flags.writes}

    @app.get("/version")
    def version():
        return {"version": __version__}

    async def authorize(
        request: Request, required_scope: str, identity: str, scopes: str,
        timestamp: int, nonce: str, signature: str,
    ) -> bytes:
        body = await request.body()
        source = request.client.host if request.client else ""
        try:
            authenticator.verify(
                source=source, identity=identity,
                scopes={scope for scope in scopes.split(" ") if scope},
                timestamp=timestamp, nonce=nonce, body=body, signature=signature,
                required_scope=required_scope,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return body

    async def mutation(request: Request, model, handler, scope: str,
                       idempotency_key: str, identity: str, scopes: str,
                       timestamp: int, nonce: str, signature: str):
        body = await authorize(request, scope, identity, scopes, timestamp, nonce, signature)
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
    ):
        return idempotency_key, identity, scopes, timestamp, nonce, signature

    def read_headers(
        identity: str = Header(..., alias="X-Service-Identity"),
        scopes: str = Header(..., alias="X-Service-Scopes"),
        timestamp: int = Header(..., alias="X-Request-Timestamp"),
        nonce: str = Header(..., alias="X-Request-Nonce", min_length=16, max_length=128),
        signature: str = Header(..., alias="X-Request-Signature", min_length=64, max_length=64),
    ):
        return identity, scopes, timestamp, nonce, signature

    @app.post("/v1/campaigns/validate")
    async def campaign_validate(request: Request, values=Depends(headers)):
        key, identity, scopes, timestamp, nonce, signature = values
        body = await authorize(request, "telephony:read", identity, scopes, timestamp, nonce, signature)
        return service.validate_campaign(CampaignCommand.model_validate_json(body))

    @app.post("/v1/campaigns/provision-disabled")
    async def campaign_provision(request: Request, values=Depends(headers)):
        deny_generic_reconciliation_mutation()
        return await mutation(request, CampaignCommand, service.provision_campaign,
                              "telephony:provision", *values)

    @app.post("/v1/campaigns/update-disabled")
    async def campaign_update(request: Request, values=Depends(headers)):
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

    @app.get("/v1/leads/by-idempotency/{key}")
    async def lead_by_key(request: Request, key: str, values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        mapping = service.state.mapping("lead", key)
        if not mapping:
            raise HTTPException(404, "lead missing")
        return {"external_id": key, "lead_id": mapping["actual_id"]}

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

    @app.post("/v1/scripts/sync")
    async def scripts_sync(request: Request, values=Depends(headers)):
        deny_generic_reconciliation_mutation()
        return await mutation(request, CampaignCommand, service.provision_campaign,
                              "telephony:provision", *values)

    @app.post("/v1/dispositions/sync")
    async def dispositions_sync(request: Request, values=Depends(headers)):
        deny_generic_reconciliation_mutation()
        return await mutation(request, CampaignCommand, service.provision_campaign,
                              "telephony:provision", *values)

    @app.post("/v1/reconciliation/campaigns/provision-disabled")
    async def reconciliation_campaign(request: Request, values=Depends(headers)):
        if reconciliation_policy is None:
            raise HTTPException(403, "production reconciliation profile is disabled")
        key, identity, scopes, timestamp, nonce, signature = values
        body = await authorize(
            request, "telephony:reconcile", identity, scopes, timestamp, nonce, signature
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
    async def reconciliation_run(request: Request, values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        return service.reconciliation()

    @app.get("/v1/reconciliation/{run_id}")
    async def reconciliation_get(request: Request, run_id: str,
                                 values=Depends(read_headers)):
        await authorize(request, "telephony:read", *values)
        return {"run_id": run_id, **service.reconciliation()}

    return app


def app_from_environment() -> FastAPI:
    # No environment variable contains a credential value. The MySQL backend
    # accepts only a loopback host and a protected JSON secret file.
    secret_file = Path(os.environ.get("CODESTRA_ADAPTER_HMAC_FILE", "/run/secrets/adapter-hmac"))
    key = secret_file.read_bytes().strip() if secret_file.exists() else b"development-disabled"
    flags = FeatureFlags(
        writes=os.environ.get("ENABLE_VICIDIAL_WRITES") == "true",
        provisioning=os.environ.get("ENABLE_PROVISIONING") == "true",
        agent_sync=os.environ.get("ENABLE_AGENT_SYNC") == "true",
        lead_publication=os.environ.get("ENABLE_LEAD_PUBLICATION") == "true",
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
    service = AdapterService(
        repository,
        StateStore(Path(os.environ.get("CODESTRA_ADAPTER_STATE", "/var/lib/codestra-vicidial/state.sqlite3"))),
        flags,
    )
    auth = RequestAuthenticator(
        identity=os.environ.get("CODESTRA_ADAPTER_IDENTITY", "codestra-middleware"),
        key=key,
        authorized_sources=frozenset({
            os.environ.get("AUTHORIZED_APPLICATION_SERVER_IP", "65.109.65.169"),
            os.environ.get("AUTHORIZED_PRIVATE_SOURCE_IP", "10.40.0.1"),
            "127.0.0.1",
        }),
    )
    policy = None
    if os.environ.get("CODESTRA_ADAPTER_DEPLOYMENT") == "production-reconciliation":
        policy = ReconciliationPolicy.from_file(Path(os.environ["CODESTRA_RECONCILIATION_POLICY_FILE"]))
    return create_app(service, auth, policy)


app = app_from_environment()
