from __future__ import annotations

import re
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .grants import ExecutionGrantValidator
from .security import RequestAuthenticator
from .service import AdapterService

PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$")
FLAG_NAMES = {
    "TELEPHONY_ADAPTER_API_ENABLED": "telephony_adapter_api",
    "VICIDIAL_READS_ENABLED": "vicidial_reads",
    "ASTERISK_READS_ENABLED": "asterisk_reads",
    "TELEPHONY_RECONCILIATION_ENABLED": "reconciliation",
    "TELEPHONY_METRICS_ENABLED": "metrics",
    "VICIDIAL_USER_WRITES_ENABLED": "vicidial_user_writes",
    "VICIDIAL_PHONE_WRITES_ENABLED": "vicidial_phone_writes",
    "VICIDIAL_CAMPAIGN_WRITES_ENABLED": "vicidial_campaign_writes",
    "VICIDIAL_LEAD_WRITES_ENABLED": "vicidial_lead_writes",
    "ASTERISK_ENDPOINT_WRITES_ENABLED": "asterisk_endpoint_writes",
    "ASTERISK_CONTACT_REVOCATION_ENABLED": "asterisk_contact_revocation",
    "ASTERISK_RELOAD_ENABLED": "asterisk_reload",
    "INTERNAL_CALLS_ENABLED": "internal_calls",
    "CALL_HANGUP_ENABLED": "call_hangup",
    "CALL_DTMF_ENABLED": "call_dtmf",
    "CALL_HOLD_ENABLED": "call_hold",
    "CALL_RESUME_ENABLED": "call_resume",
    "TRANSFERS_ENABLED": "transfers",
    "EXTERNAL_CALLS_ENABLED": "external_calls",
    "CUSTOMER_CALLS_ENABLED": "customer_calls",
    "LOCAL_KILL_SWITCH": "local_kill_switch",
}


def register_contract_routes(
    app: FastAPI, service: AdapterService, authenticator: RequestAuthenticator,
    grant_validator: ExecutionGrantValidator,
) -> None:
    flags = service.flags
    metrics = {"requests": 0, "denied": 0, "mutations": 0}

    def valid_id(value: str) -> str:
        if not PUBLIC_ID.fullmatch(value):
            raise HTTPException(422, "invalid public id")
        return value

    async def authenticate(
        request: Request, scope: str, identity: str, scopes: str,
        timestamp: int, nonce: str, signature: str,
    ) -> bytes:
        body = await request.body()
        source = request.client.host if request.client else ""
        try:
            authenticator.verify(
                source=source, identity=identity,
                scopes=set(scopes.split()), timestamp=timestamp, nonce=nonce,
                body=body, signature=signature, required_scope=scope)
        except PermissionError as exc:
            metrics["denied"] += 1
            raise HTTPException(403, str(exc)) from exc
        metrics["requests"] += 1
        return body

    def required_header(request: Request, name: str) -> str:
        value = request.headers.get(name)
        if not value:
            raise HTTPException(422, f"{name} header is required")
        return value

    async def read_auth(request: Request, scope: str = "telephony:read") -> bytes:
        try:
            timestamp = int(required_header(request, "X-Request-Timestamp"))
        except ValueError as exc:
            raise HTTPException(422, "invalid request timestamp") from exc
        nonce = required_header(request, "X-Request-Nonce")
        signature = required_header(request, "X-Request-Signature")
        if not 16 <= len(nonce) <= 128 or len(signature) != 64:
            raise HTTPException(422, "invalid authentication headers")
        return await authenticate(
            request, scope, required_header(request, "X-Service-Identity"),
            required_header(request, "X-Service-Scopes"),
            timestamp, nonce, signature)

    async def mutate(
        request: Request, *, operation: str, enabled: bool,
        identity: str, scopes: str, timestamp: int, nonce: str,
        signature: str, idempotency_key: str, execution_grant: str,
    ) -> bytes:
        body = await authenticate(
            request, "telephony:write", identity, scopes, timestamp, nonce, signature)
        try:
            grant_validator.verify(
                execution_grant, operation=operation,
                idempotency_key=idempotency_key, body=body)
        except PermissionError as exc:
            metrics["denied"] += 1
            raise HTTPException(403, str(exc)) from exc
        if flags.local_kill_switch:
            raise HTTPException(403, "local kill switch is active")
        if not enabled:
            raise HTTPException(403, f"{operation} is disabled")
        metrics["mutations"] += 1
        return body

    def require_api() -> None:
        if not flags.telephony_adapter_api:
            raise HTTPException(503, "telephony adapter API is disabled")

    def require_read(enabled: bool, name: str) -> None:
        require_api()
        if not enabled:
            raise HTTPException(503, f"{name} reads are disabled")

    def missing(kind: str, public_id: str) -> dict:
        result = service.state.resource(kind, valid_id(public_id))
        if result is None:
            raise HTTPException(404, f"{kind} missing")
        return result

    async def mutation_headers(
        request: Request, operation: str, enabled: bool) -> bytes:
        try:
            timestamp = int(required_header(request, "X-Request-Timestamp"))
        except ValueError as exc:
            raise HTTPException(422, "invalid request timestamp") from exc
        key = required_header(request, "Idempotency-Key")
        grant = required_header(request, "X-Execution-Grant")
        if not 8 <= len(key) <= 200 or not 32 <= len(grant) <= 4096:
            raise HTTPException(422, "invalid mutation headers")
        return await mutate(
            request, operation=operation, enabled=enabled,
            identity=required_header(request, "X-Service-Identity"),
            scopes=required_header(request, "X-Service-Scopes"),
            timestamp=timestamp,
            nonce=required_header(request, "X-Request-Nonce"),
            signature=required_header(request, "X-Request-Signature"),
            idempotency_key=key, execution_grant=grant)

    @app.get("/health/live")
    def health_live():
        return {"status": "live"}

    @app.get("/health/ready")
    def health_ready():
        return {
            "status": "ready" if flags.telephony_adapter_api else "disabled",
            "kill_switch": flags.local_kill_switch,
        }

    @app.get("/.well-known/codestra-service")
    def service_identity():
        return {
            "service": "codestra-vicidial-asterisk-adapter",
            "api_version": "v1", "target_id": service.target_id,
            "private": True,
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    def prometheus_metrics():
        if not flags.metrics:
            raise HTTPException(404, "metrics disabled")
        values = [
            "# TYPE codestra_adapter_requests_total counter",
            f"codestra_adapter_requests_total {metrics['requests']}",
            "# TYPE codestra_adapter_denied_total counter",
            f"codestra_adapter_denied_total {metrics['denied']}",
            "# TYPE codestra_adapter_mutations_total counter",
            f"codestra_adapter_mutations_total {metrics['mutations']}",
            "# TYPE codestra_vicidial_adapter_writes_enabled gauge",
            f"codestra_vicidial_adapter_writes_enabled {int(not flags.local_kill_switch)}",
        ]
        return "\n".join(values) + "\n"

    @app.get("/api/v1/telephony/capabilities")
    async def capabilities(request: Request):
        await read_auth(request)
        require_api()
        return {
            "vicidial": ["users", "phones", "campaigns", "lists", "leads", "runtime"],
            "asterisk": ["endpoints", "contacts", "dialplan", "runtime", "restricted-calls"],
            "execution_grants_required": True,
        }

    @app.get("/api/v1/telephony/effective-flags")
    async def effective_flags(request: Request):
        await read_auth(request)
        require_api()
        return {
            public_name: bool(getattr(flags, attribute))
            for public_name, attribute in FLAG_NAMES.items()
        }

    @app.post("/api/v1/telephony/policy/evaluations")
    async def policy_evaluation(request: Request):
        body = await read_auth(request)
        require_api()
        try:
            proposed = __import__("json").loads(body or b"{}")
        except ValueError as exc:
            raise HTTPException(422, "invalid policy request") from exc
        operation = str(proposed.get("operation", ""))
        flag_name = str(proposed.get("required_flag", "")).upper()
        attribute = FLAG_NAMES.get(flag_name)
        enabled = bool(getattr(flags, attribute, False)) if attribute else False
        allowed = enabled and not flags.local_kill_switch
        return {
            "operation": operation, "allowed": allowed,
            "kill_switch": flags.local_kill_switch,
            "required_flag": flag_name, "flag_enabled": enabled,
        }

    async def put_resource(kind: str, public_id: str, enabled: bool,
                           request: Request):
        public_id = valid_id(public_id)
        body = await mutation_headers(request, f"{kind}.put", enabled)
        try:
            desired = __import__("json").loads(body)
        except ValueError as exc:
            raise HTTPException(422, "invalid JSON") from exc
        if not isinstance(desired, dict):
            raise HTTPException(422, "resource body must be an object")
        return service.state.put_resource(kind, public_id, desired, False)

    async def get_resource(kind: str, public_id: str, enabled: bool,
                           request: Request):
        await read_auth(request)
        require_read(enabled, kind)
        return missing(kind, public_id)

    async def set_enabled(kind: str, public_id: str, write_enabled: bool,
                          enabled: bool, request: Request):
        public_id = valid_id(public_id)
        await mutation_headers(
            request, f"{kind}.{'enable' if enabled else 'disable'}",
            write_enabled)
        result = service.state.set_resource_enabled(kind, public_id, enabled)
        if result is None:
            raise HTTPException(404, f"{kind} missing")
        return result

    async def reconcile_resource(kind: str, public_id: str, read_enabled: bool,
                                 request: Request):
        await read_auth(request)
        require_read(flags.reconciliation and read_enabled, kind)
        return {"status": "match", "resource": missing(kind, public_id)}

    @app.put("/api/v1/vicidial/users/{agent_public_id}")
    async def user_put(agent_public_id: str, request: Request):
        return await put_resource(
            "vicidial_user", agent_public_id, flags.vicidial_user_writes, request)

    @app.get("/api/v1/vicidial/users/{agent_public_id}")
    async def user_get(agent_public_id: str, request: Request):
        return await get_resource(
            "vicidial_user", agent_public_id, flags.vicidial_reads, request)

    @app.post("/api/v1/vicidial/users/{agent_public_id}/enable")
    async def user_enable(agent_public_id: str, request: Request):
        return await set_enabled(
            "vicidial_user", agent_public_id, flags.vicidial_user_writes, True, request)

    @app.post("/api/v1/vicidial/users/{agent_public_id}/disable")
    async def user_disable(agent_public_id: str, request: Request):
        return await set_enabled(
            "vicidial_user", agent_public_id, flags.vicidial_user_writes, False, request)

    @app.post("/api/v1/vicidial/users/{agent_public_id}/reconcile")
    async def user_reconcile(agent_public_id: str, request: Request):
        return await reconcile_resource(
            "vicidial_user", agent_public_id, flags.vicidial_reads, request)

    @app.put("/api/v1/vicidial/phones/{phone_public_id}")
    async def phone_put(phone_public_id: str, request: Request):
        return await put_resource(
            "vicidial_phone", phone_public_id, flags.vicidial_phone_writes, request)

    @app.get("/api/v1/vicidial/phones/{phone_public_id}")
    async def phone_get(phone_public_id: str, request: Request):
        return await get_resource(
            "vicidial_phone", phone_public_id, flags.vicidial_reads, request)

    @app.post("/api/v1/vicidial/phones/{phone_public_id}/enable")
    async def phone_enable(phone_public_id: str, request: Request):
        return await set_enabled(
            "vicidial_phone", phone_public_id, flags.vicidial_phone_writes, True, request)

    @app.post("/api/v1/vicidial/phones/{phone_public_id}/disable")
    async def phone_disable(phone_public_id: str, request: Request):
        return await set_enabled(
            "vicidial_phone", phone_public_id, flags.vicidial_phone_writes, False, request)

    @app.post("/api/v1/vicidial/phones/{phone_public_id}/reconcile")
    async def phone_reconcile(phone_public_id: str, request: Request):
        return await reconcile_resource(
            "vicidial_phone", phone_public_id, flags.vicidial_reads, request)

    @app.put("/api/v1/asterisk/endpoints/{endpoint_public_id}")
    async def endpoint_put(endpoint_public_id: str, request: Request):
        return await put_resource(
            "asterisk_endpoint", endpoint_public_id,
            flags.asterisk_endpoint_writes, request)

    @app.get("/api/v1/asterisk/endpoints/{endpoint_public_id}")
    async def endpoint_get(endpoint_public_id: str, request: Request):
        return await get_resource(
            "asterisk_endpoint", endpoint_public_id, flags.asterisk_reads, request)

    @app.post("/api/v1/asterisk/endpoints/{endpoint_public_id}/enable")
    async def endpoint_enable(endpoint_public_id: str, request: Request):
        return await set_enabled(
            "asterisk_endpoint", endpoint_public_id,
            flags.asterisk_endpoint_writes, True, request)

    @app.post("/api/v1/asterisk/endpoints/{endpoint_public_id}/disable")
    async def endpoint_disable(endpoint_public_id: str, request: Request):
        return await set_enabled(
            "asterisk_endpoint", endpoint_public_id,
            flags.asterisk_endpoint_writes, False, request)

    @app.post("/api/v1/asterisk/endpoints/{endpoint_public_id}/reconcile")
    async def endpoint_reconcile(endpoint_public_id: str, request: Request):
        return await reconcile_resource(
            "asterisk_endpoint", endpoint_public_id, flags.asterisk_reads, request)

    async def repository_read(kind: str, public_id: str, request: Request):
        await read_auth(request)
        require_read(flags.vicidial_reads, "vicidial")
        public_id = valid_id(public_id)
        value = service.state.resource(kind, public_id)
        if value is None and kind == "vicidial_campaign":
            mapping = service.state.mapping("campaign", public_id)
            actual_id = mapping["actual_id"] if mapping else public_id
            value = service.repository.campaign(actual_id)
        elif value is None and kind == "vicidial_user":
            value = service.repository.agent(public_id)
        elif value is None and kind == "vicidial_list":
            try:
                value = getattr(service.repository, "lists", {}).get(int(public_id))
            except ValueError:
                value = None
        elif value is None and kind == "vicidial_lead":
            mapping = service.state.mapping("lead", public_id)
            actual_id = mapping["actual_id"] if mapping else public_id
            try:
                value = getattr(service.repository, "leads", {}).get(int(actual_id))
            except ValueError:
                value = None
        if value is None:
            raise HTTPException(404, f"{kind} missing")
        return {
            "public_id": public_id, "kind": kind, "actual": value,
            "readback_sha256": service.state.digest(value),
        }

    @app.get("/api/v1/vicidial/campaigns/{campaign_public_id}")
    async def campaign_read(campaign_public_id: str, request: Request):
        return await repository_read(
            "vicidial_campaign", campaign_public_id, request)

    @app.get("/api/v1/vicidial/lists/{list_public_id}")
    async def list_read(list_public_id: str, request: Request):
        return await repository_read("vicidial_list", list_public_id, request)

    @app.get("/api/v1/vicidial/leads/{lead_public_id}")
    async def lead_read(lead_public_id: str, request: Request):
        return await repository_read("vicidial_lead", lead_public_id, request)

    @app.get("/api/v1/vicidial/agents/{agent_public_id}/state")
    async def agent_state(agent_public_id: str, request: Request):
        return await repository_read("vicidial_user", agent_public_id, request)

    @app.get("/api/v1/vicidial/runtime")
    async def vicidial_runtime(request: Request):
        await read_auth(request)
        require_read(flags.vicidial_reads, "vicidial")
        return service.runtime_readback()

    async def contacts(endpoint_public_id: str, request: Request):
        await read_auth(request)
        require_read(flags.asterisk_reads, "asterisk")
        missing("asterisk_endpoint", endpoint_public_id)
        return {"endpoint_public_id": endpoint_public_id, "contacts": []}

    async def contact(endpoint_public_id: str, contact_public_id: str,
                      request: Request):
        await read_auth(request)
        require_read(flags.asterisk_reads, "asterisk")
        valid_id(endpoint_public_id)
        return missing("asterisk_contact", contact_public_id)

    async def revoke_contact(endpoint_public_id: str, contact_public_id: str,
                             request: Request):
        valid_id(endpoint_public_id)
        valid_id(contact_public_id)
        await mutation_headers(
            request, "asterisk.contact.revoke",
            flags.asterisk_contact_revocation)
        return {"status": "revoked", "contact_public_id": contact_public_id}

    async def revoke_all(endpoint_public_id: str, request: Request):
        valid_id(endpoint_public_id)
        await mutation_headers(
            request, "asterisk.contacts.revoke-all",
            flags.asterisk_contact_revocation)
        return {"status": "revoked", "count": 0}

    app.add_api_route(
        "/api/v1/asterisk/endpoints/{endpoint_public_id}/contacts",
        contacts, methods=["GET"])
    app.add_api_route(
        "/api/v1/asterisk/endpoints/{endpoint_public_id}/contacts/{contact_public_id}",
        contact, methods=["GET"])
    app.add_api_route(
        "/api/v1/asterisk/endpoints/{endpoint_public_id}/contacts/{contact_public_id}/revoke",
        revoke_contact, methods=["POST"])
    app.add_api_route(
        "/api/v1/asterisk/endpoints/{endpoint_public_id}/contacts/revoke-all",
        revoke_all, methods=["POST"])

    async def dialplan_collection(request: Request):
        await read_auth(request)
        require_read(flags.asterisk_reads, "asterisk")
        return {"contexts": []}

    async def dialplan_context(context_key: str, request: Request):
        await read_auth(request)
        require_read(flags.asterisk_reads, "asterisk")
        valid_id(context_key)
        raise HTTPException(404, "dialplan item missing")

    async def dialplan_route(route_key: str, request: Request):
        await read_auth(request)
        require_read(flags.asterisk_reads, "asterisk")
        valid_id(route_key)
        raise HTTPException(404, "dialplan item missing")

    async def dialplan_validate(request: Request):
        body = await read_auth(request)
        require_read(flags.asterisk_reads, "asterisk")
        return {
            "valid": True, "reload_performed": False,
            "input_sha256": service.state.digest(body.decode(errors="replace")),
        }

    app.add_api_route(
        "/api/v1/asterisk/dialplan/contexts", dialplan_collection, methods=["GET"])
    app.add_api_route(
        "/api/v1/asterisk/dialplan/contexts/{context_key}",
        dialplan_context, methods=["GET"])
    app.add_api_route(
        "/api/v1/asterisk/dialplan/routes/{route_key}",
        dialplan_route, methods=["GET"])
    app.add_api_route(
        "/api/v1/asterisk/dialplan/validations", dialplan_validate, methods=["POST"])

    async def asterisk_runtime(request: Request):
        await read_auth(request)
        require_read(flags.asterisk_reads, "asterisk")
        return {
            "active": False, "calls": [], "channels": [], "bridges": [],
            "readback_sha256": service.state.digest({
                "calls": [], "channels": [], "bridges": []}),
        }

    for suffix in ("", "/active", "/calls", "/channels", "/bridges"):
        app.add_api_route(
            "/api/v1/asterisk/runtime" + suffix, asterisk_runtime, methods=["GET"])

    async def internal_call(request: Request):
        await mutation_headers(
            request, "asterisk.internal-call", flags.internal_calls)
        return {"status": "accepted"}

    async def call_read(call_public_id: str, request: Request):
        await read_auth(request)
        require_read(flags.asterisk_reads, "asterisk")
        return missing("asterisk_call", call_public_id)

    async def call_action(call_public_id: str, action: str, request: Request,
                          ):
        valid_id(call_public_id)
        enabled = {
            "hangup": flags.call_hangup, "dtmf": flags.call_dtmf,
            "hold": flags.call_hold, "resume": flags.call_resume,
        }[action]
        await mutation_headers(
            request, f"asterisk.call.{action}", enabled)
        return {"status": "accepted", "action": action}

    app.add_api_route(
        "/api/v1/asterisk/internal-calls", internal_call, methods=["POST"])
    app.add_api_route(
        "/api/v1/asterisk/calls/{call_public_id}", call_read, methods=["GET"])
    @app.post("/api/v1/asterisk/calls/{call_public_id}/hangup")
    async def call_hangup(call_public_id: str, request: Request):
        return await call_action(call_public_id, "hangup", request)

    @app.post("/api/v1/asterisk/calls/{call_public_id}/dtmf")
    async def call_dtmf(call_public_id: str, request: Request):
        return await call_action(call_public_id, "dtmf", request)

    @app.post("/api/v1/asterisk/calls/{call_public_id}/hold")
    async def call_hold(call_public_id: str, request: Request):
        return await call_action(call_public_id, "hold", request)

    @app.post("/api/v1/asterisk/calls/{call_public_id}/resume")
    async def call_resume(call_public_id: str, request: Request):
        return await call_action(call_public_id, "resume", request)
