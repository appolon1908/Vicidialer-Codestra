"""Middleware-facing HTTP contracts exercised against an isolated adapter."""
from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from codestra_vicidial.app import _COMMAND_MODEL_BY_ROUTE, create_app
from codestra_vicidial.repository import MemoryRepository
from codestra_vicidial.security import RequestAuthenticator
from test_adapter import campaign_payload, service_for


@pytest.fixture
def api(tmp_path):
    service = service_for(MemoryRepository(), tmp_path)
    auth = RequestAuthenticator("middleware-test", b"synthetic-test-key", frozenset({"127.0.0.1"}))
    with TestClient(create_app(service, auth), client=("127.0.0.1", 50000)) as client:
        yield client, auth, service
    service.state.db.close()


def post(api, path, payload, key="integration-command", scope="telephony:provision"):
    client, auth, _service = api
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    timestamp, nonce = int(time.time()), uuid.uuid4().hex
    signature = auth.sign(timestamp, nonce, body, path=path, scopes={scope}, idempotency_key=key)
    return client.post(path, content=body, headers={
        "Content-Type": "application/json", "Idempotency-Key": key,
        "X-Service-Identity": auth.identity, "X-Service-Scopes": scope,
        "X-Request-Timestamp": str(timestamp), "X-Request-Nonce": nonce,
        "X-Request-Signature": signature, "X-Signature-Version": "v2",
    })


def test_openapi_publishes_complete_commands_and_resolvable_references(api):
    schema = api[0].get("/openapi.json").json()
    for path, model in _COMMAND_MODEL_BY_ROUTE.items():
        operation = schema["paths"][path]["post"]
        body = operation["requestBody"]
        assert body["required"] is True
        assert body["content"]["application/json"]["schema"]["$ref"].endswith("/" + model.__name__)
        assert {"403", "409", "422"} <= operation["responses"].keys()
    context = schema["components"]["schemas"]["RequestContext"]
    assert {"tenant_id", "business_unit", "campaign_id", "supervisor_subject", "expected_version"} <= set(context["required"])

    def check_refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                target = schema
                for part in value["$ref"].removeprefix("#/").split("/"):
                    target = target[part]
            for item in value.values():
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)
    check_refs(schema)


@pytest.mark.parametrize("path,scope", [
    ("/v1/campaigns/validate", "telephony:read"),
    ("/v1/campaigns/provision-disabled", "telephony:provision"),
    ("/v1/agents/sync", "telephony:agent-sync"),
    ("/v1/callbacks/create", "telephony:callback-write"),
])
@pytest.mark.parametrize("payload", [b'{"private-value":', {"context": {"tenant_id": "private-value"}}])
def test_invalid_command_returns_redacted_422(api, path, scope, payload):
    response = post(api, path, payload, scope=scope)
    assert response.status_code == 422
    assert "private-value" not in response.text
    assert all(set(item) == {"loc", "type"} for item in response.json()["detail"])
    assert api[2].repository.campaigns == {}


def test_invalid_unsigned_body_is_denied_before_validation(api):
    response = api[0].post("/v1/campaigns/validate", content=b'{"private-value":')
    assert response.status_code == 422  # missing authentication headers
    assert "private-value" not in response.text
    response = post(api, "/v1/campaigns/validate", b'{"private-value":', scope="wrong:scope")
    assert response.status_code == 403


def test_versioned_http_lifecycle_and_exact_retry(api):
    payload = campaign_payload()
    first = post(api, "/v1/campaigns/provision-disabled", payload)
    assert first.status_code == 200
    assert first.json()["resource_version"] == 1
    replay = post(api, "/v1/campaigns/provision-disabled", payload)
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["resource_version"] == 1
    for path, scope in [("/v1/campaigns/disable", "telephony:provision"),
                        ("/v1/campaigns/validate", "telephony:read")]:
        stale = post(api, path, payload, key="stale-command", scope=scope)
        assert stale.status_code == 409
        assert stale.json()["code"] == "stale_resource_version"
    payload["context"]["expected_version"] = 1
    updated = post(api, "/v1/campaigns/disable", payload, key="disable-command")
    assert updated.status_code == 200
    assert updated.json()["resource_version"] == 2
    assert api[2].repository.campaigns["CODT0001"]["active"] == "N"


def test_pending_mutation_returns_conflict_without_writes(api):
    service = api[2]
    service.state.claim_resource_version("campaign:COD-TEST-OUT", 0, "pending-command")
    response = post(api, "/v1/campaigns/provision-disabled", campaign_payload())
    assert response.status_code == 409
    assert response.json()["code"] == "resource_mutation_in_progress"
    assert service.repository.campaigns == {}
    assert service.state.resource_version("campaign:COD-TEST-OUT") == 0


def test_validate_authority_denial_is_http_403(api):
    payload = campaign_payload()
    payload["context"]["tenant_id"] = "other-tenant"
    response = post(api, "/v1/campaigns/validate", payload, scope="telephony:read")
    assert response.status_code == 403


def get(api, path):
    client, auth, _service = api
    timestamp, nonce = int(time.time()), uuid.uuid4().hex
    signature = auth.sign(timestamp, nonce, b"", method="GET", path=path, scopes={"telephony:read"})
    return client.get(path, headers={
        "X-Service-Identity": auth.identity, "X-Service-Scopes": "telephony:read",
        "X-Request-Timestamp": str(timestamp), "X-Request-Nonce": nonce,
        "X-Request-Signature": signature, "X-Signature-Version": "v2",
    })


def test_campaign_readback_exposes_version_for_next_command(api):
    payload = campaign_payload()
    assert post(api, "/v1/campaigns/provision-disabled", payload).status_code == 200
    actual = get(api, "/v1/campaigns/CODT0001/actual-state")
    assert actual.status_code == 200
    assert actual.json()["resource_version"] == 1
    payload["context"]["expected_version"] = actual.json()["resource_version"]
    assert post(api, "/v1/campaigns/disable", payload, key="readback-disable").status_code == 200
    assert get(api, "/v1/campaigns/CODT0001/actual-state").json()["resource_version"] == 2


def test_unmanaged_campaign_has_no_invented_version(api):
    api[2].repository.campaigns["CODT0001"] = {"campaign_id": "CODT0001", "active": "N"}
    response = get(api, "/v1/campaigns/CODT0001/actual-state")
    assert response.status_code == 200
    assert response.json()["resource_version"] is None
    assert get(api, "/v1/campaigns/MISSING/actual-state").status_code == 404


def test_agent_and_lead_readback_include_committed_versions(api):
    from test_adapter import provision_callback_dependencies

    provision_callback_dependencies(api[2])
    agent = get(api, "/v1/agents/COD1001/actual-state")
    lead = get(api, "/v1/leads/by-external-id/callback-lead")
    assert agent.status_code == lead.status_code == 200
    assert agent.json()["resource_version"] == 1
    assert lead.json()["resource_version"] == 1
    assert lead.json()["external_id"] == "callback-lead"


def test_reused_command_key_with_changed_payload_is_409(api):
    payload = campaign_payload()
    assert post(api, "/v1/campaigns/provision-disabled", payload).status_code == 200
    payload["context"]["reason"] = "revised synthetic request"
    response = post(api, "/v1/campaigns/provision-disabled", payload)
    assert response.status_code == 409
    assert response.json()["code"] == "idempotency_conflict"
    assert api[2].state.resource_version("campaign:COD-TEST-OUT") == 1


def test_completion_failure_reports_reconciliation_and_blocks_retry(api):
    state = api[2].state
    state.db.execute("CREATE TRIGGER fail_completion BEFORE INSERT ON requests "
                     "BEGIN SELECT RAISE(ABORT, 'private-storage-error'); END")
    state.db.commit()
    response = post(api, "/v1/campaigns/provision-disabled", campaign_payload())
    assert response.status_code == 503
    assert response.json()["code"] == "mutation_completion_uncertain"
    assert "private-storage-error" not in response.text
    retry = post(api, "/v1/campaigns/provision-disabled", campaign_payload())
    assert retry.status_code == 409
    assert retry.json()["code"] == "resource_mutation_in_progress"


def create_callback(api):
    from test_adapter import callback_command, provision_callback_dependencies

    provision_callback_dependencies(api[2])
    response = post(api, "/v1/callbacks/create", callback_command().model_dump(mode="json"),
                    scope="telephony:callback-write")
    assert response.status_code == 200
    return response.json()["actual"]["callback_id"]


def test_callback_lookup_recovers_id_and_live_state(api):
    callback_id = create_callback(api)
    path = "/v1/callbacks/by-external-id/callback-isolated"
    api[2].repository.callbacks[callback_id]["status"] = "INACTIVE"
    response = get(api, path)
    assert response.status_code == 200
    assert response.json() == {
        "external_id": "callback-isolated", "callback_id": callback_id,
        "resource_version": 1, "campaign_id": "CODT0001", "lead_id": 1,
        "user_id": "COD1001", "recipient": "USERONLY", "status": "INACTIVE",
        "callback_time": "2026-07-28T14:00:00Z",
    }
    assert api[0].get(path).status_code == 422
    assert get(api, "/v1/callbacks/by-external-id/unknown").status_code == 404


@pytest.mark.parametrize("drift", ["missing", "external_id", "callback_time"])
def test_callback_lookup_rejects_unverified_actual_state(api, drift):
    callback_id = create_callback(api)
    if drift == "missing":
        del api[2].repository.callbacks[callback_id]
    else:
        api[2].repository.callbacks[callback_id][drift] = "invalid-value"
    response = get(api, "/v1/callbacks/by-external-id/callback-isolated")
    assert response.status_code == 409
    assert response.json()["status"] == "reconciliation_required"
    assert "invalid-value" not in response.text


def test_callback_lookup_normalizes_mysql_shape(api):
    callback_id = create_callback(api)
    actual = api[2].repository.callbacks[callback_id]
    actual["user"] = actual.pop("user_id")
    actual["comments"] = "CODESTRA:callback-isolated:" + "a" * 64 + ":Synthetic"
    del actual["external_id"]
    actual["callback_time"] = "2026-07-28 14:00:00"
    response = get(api, "/v1/callbacks/by-external-id/callback-isolated")
    assert response.status_code == 200
    assert response.json()["callback_time"] == "2026-07-28T14:00:00Z"
    assert "comments" not in response.json()


@pytest.mark.parametrize("outcome", ["complete", "failed", "pending"])
def test_agent_readback_never_pairs_stale_state_with_a_new_version(api, monkeypatch, outcome):
    from test_adapter import provision_callback_dependencies

    provision_callback_dependencies(api[2])
    service = api[2]
    read = service.repository.agent

    def concurrent_read(user_id):
        old = read(user_id)
        service.state.claim_resource_version("agent:COD1001", 1, "overlapping-command")
        service.repository.agents[user_id]["full_name"] = "Concurrent update"
        if outcome == "complete":
            service.state.complete_resource_version("agent:COD1001", 1, "overlapping-command")
        elif outcome == "failed":
            service.state.release_resource_claim("agent:COD1001", 1, "overlapping-command")
        return old

    monkeypatch.setattr(service.repository, "agent", concurrent_read)
    response = get(api, "/v1/agents/COD1001/actual-state")
    assert response.status_code == 409
    assert response.json()["code"] == "resource_snapshot_changed"
    assert "actual" not in response.json()


def test_pending_agent_write_blocks_read_before_repository_access(api, monkeypatch):
    api[2].state.claim_resource_version("agent:COD1001", 0, "pending-agent")

    def must_not_read(_user_id):
        pytest.fail("pending mutation must block the repository read")

    monkeypatch.setattr(api[2].repository, "agent", must_not_read)
    response = get(api, "/v1/agents/COD1001/actual-state")
    assert response.status_code == 409
    assert response.json()["code"] == "resource_mutation_in_progress"


def test_campaign_readback_rejects_overlapping_mutation(api, monkeypatch):
    assert post(api, "/v1/campaigns/provision-disabled", campaign_payload()).status_code == 200
    read = api[2].repository.campaign

    def concurrent_read(campaign_id):
        old = read(campaign_id)
        api[2].state.claim_resource_version("campaign:COD-TEST-OUT", 1, "campaign-overlap")
        api[2].state.complete_resource_version("campaign:COD-TEST-OUT", 1, "campaign-overlap")
        return old

    monkeypatch.setattr(api[2].repository, "campaign", concurrent_read)
    response = get(api, "/v1/campaigns/CODT0001/actual-state")
    assert response.status_code == 409
    assert response.json()["code"] == "resource_snapshot_changed"
