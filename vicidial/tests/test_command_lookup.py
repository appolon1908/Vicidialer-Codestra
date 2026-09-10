"""Recover committed adapter outcomes without dispatching another mutation."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from codestra_vicidial.app import create_app
from codestra_vicidial.models import CampaignCommand
from codestra_vicidial.repository import MemoryRepository
from codestra_vicidial.security import RequestAuthenticator
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore
from test_adapter import campaign_payload, service_for

LOOKUP_PATH = "/v1/idempotency/lookup"
PROVISION_PATH = "/v1/campaigns/provision-disabled"
OPERATION = "campaign.provision-disabled"
KEY = "lost-response-command"


@pytest.fixture
def api(tmp_path):
    service = service_for(MemoryRepository(), tmp_path)
    auth = RequestAuthenticator("middleware-test", b"synthetic-test-key", frozenset({"127.0.0.1"}))
    with TestClient(create_app(service, auth), client=("127.0.0.1", 50000)) as client:
        yield client, auth, service
    service.state.db.close()


def signed_post(api, path, payload, *, scope="telephony:read", key=""):
    client, auth, _service = api
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    timestamp, nonce = int(time.time()), uuid.uuid4().hex
    signature = auth.sign(timestamp, nonce, body, path=path, scopes={scope}, idempotency_key=key)
    headers = {
        "Content-Type": "application/json",
        "X-Service-Identity": auth.identity, "X-Service-Scopes": scope,
        "X-Request-Timestamp": str(timestamp), "X-Request-Nonce": nonce,
        "X-Request-Signature": signature, "X-Signature-Version": "v2",
    }
    if key:
        headers["Idempotency-Key"] = key
    return client.post(path, content=body, headers=headers)


def provision(api, payload=None):
    response = signed_post(api, PROVISION_PATH, payload or campaign_payload(),
                           scope="telephony:provision", key=KEY)
    assert response.status_code == 200, response.text
    result = response.json()
    result.pop("idempotent_replay")
    return result


def query(payload=None, *, operation=OPERATION, key=KEY):
    return {"operation": operation, "idempotency_key": key,
            "command": payload or campaign_payload()}


def snapshot(state):
    return {table: [tuple(row) for row in state.db.execute(f"SELECT * FROM {table}")]
            for table in ("requests", "audit", "resource_versions", "resource_mutations")}


def test_lost_response_recovers_durable_result_with_writes_disabled(api, monkeypatch):
    expected = provision(api)
    service = api[2]
    service.flags = FeatureFlags()
    before = snapshot(service.state)

    def must_not_dispatch(*_args, **_kwargs):
        pytest.fail("result lookup must not dispatch or read telephony state")

    monkeypatch.setattr(service, "_execute", must_not_dispatch)
    monkeypatch.setattr(service.repository, "campaign", must_not_dispatch)
    for _ in range(2):
        response = signed_post(api, LOOKUP_PATH, query())
        assert response.status_code == 200
        assert response.json() == {
            "status": "recorded", "operation": OPERATION, "idempotency_key": KEY,
            "recorded_at": response.json()["recorded_at"], "result": expected,
        }
        assert response.json()["recorded_at"].endswith("Z")
        assert response.json()["result"]["resource_version"] == 1
    assert snapshot(service.state) == before


def test_result_survives_state_and_service_restart(api):
    expected = provision(api)
    original = api[2]
    original.state.db.close()
    restarted_state = StateStore(original.state.path)
    try:
        restarted = AdapterService(MemoryRepository(), restarted_state, FeatureFlags())
        with TestClient(create_app(restarted, api[1]), client=("127.0.0.1", 50000)) as client:
            response = signed_post((client, api[1], restarted), LOOKUP_PATH, query())
        assert response.status_code == 200
        assert response.json()["result"] == expected
        assert restarted.repository.campaigns == {}
    finally:
        restarted_state.db.close()


def test_result_is_historical_after_a_later_resource_version(api):
    expected = provision(api)
    changed = campaign_payload()
    changed["context"]["expected_version"] = 1
    update = signed_post(api, "/v1/campaigns/disable", changed,
                         scope="telephony:provision", key="later-command")
    assert update.status_code == 200
    assert update.json()["resource_version"] == 2
    response = signed_post(api, LOOKUP_PATH, query())
    assert response.status_code == 200
    assert response.json()["result"] == expected
    assert api[2].state.resource_version("campaign:COD-TEST-OUT") == 2


@pytest.mark.parametrize("change", ["name", "correlation", "version"])
def test_lookup_requires_the_exact_normalized_original_command(api, change):
    provision(api)
    changed = campaign_payload()
    if change == "name":
        changed["campaign"]["name"] = "Different synthetic campaign"
    elif change == "correlation":
        changed["context"]["correlation_id"] = "different-correlation"
    else:
        changed["context"]["expected_version"] = 1
    before = snapshot(api[2].state)
    response = signed_post(api, LOOKUP_PATH, query(changed))
    assert response.status_code == 409
    assert snapshot(api[2].state) == before


def test_lookup_normalizes_defaults_like_the_original_operation(api):
    expected = provision(api)
    normalized = CampaignCommand.model_validate(campaign_payload()).model_dump(mode="json")
    response = signed_post(api, LOOKUP_PATH, query(normalized))
    assert response.status_code == 200
    assert response.json()["result"] == expected


@pytest.mark.parametrize(("field", "value"), [
    ("tenant_id", "other-tenant"),
    ("business_unit", "FTP"),
    ("supervisor_subject", "other-supervisor"),
    ("campaign_id", "OTHER001"),
])
def test_lookup_rechecks_registered_campaign_authority(api, field, value):
    provision(api)
    changed = campaign_payload()
    changed["context"][field] = value
    response = signed_post(api, LOOKUP_PATH, query(changed))
    assert response.status_code == 403
    assert response.json()["detail"] == "command campaign authority denied"


def test_lookup_denies_a_revoked_campaign_authority(api):
    provision(api)
    api[2].state.db.execute(
        "UPDATE campaign_authority SET tenant_id=?,supervisor_subject=? WHERE campaign_id=?",
        ("tenant-new", "supervisor-new", "CODT0001"),
    )
    api[2].state.db.commit()
    response = signed_post(api, LOOKUP_PATH, query())
    assert response.status_code == 403


@pytest.mark.parametrize(("operation", "key"), [
    (OPERATION, "unknown-command-key"),
    ("campaign.disable", KEY),
])
def test_unrecorded_operation_or_key_is_explicitly_inconclusive(api, operation, key):
    provision(api)
    before = snapshot(api[2].state)
    response = signed_post(api, LOOKUP_PATH, query(operation=operation, key=key))
    assert response.status_code == 404
    assert response.json()["status"] == "not_recorded"
    assert "reconciliation" in response.json()["detail"]
    assert snapshot(api[2].state) == before


def test_uncertain_completion_retains_the_claim_and_never_reports_nonexecution(api, monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic completion failure")

    monkeypatch.setattr(api[2].state, "complete_command", unavailable)
    original = signed_post(api, PROVISION_PATH, campaign_payload(),
                           scope="telephony:provision", key=KEY)
    assert original.status_code == 503
    assert api[2].repository.campaigns["CODT0001"]["active"] == "N"
    before = snapshot(api[2].state)
    assert len(before["resource_mutations"]) == 1
    response = signed_post(api, LOOKUP_PATH, query())
    assert response.status_code == 404
    assert response.json()["status"] == "not_recorded"
    assert snapshot(api[2].state) == before


def test_lookup_cannot_see_or_commit_another_connection_transaction(api):
    state = api[2].state
    command_hash = state.digest(CampaignCommand.model_validate(campaign_payload()).model_dump(mode="json"))
    state.db.execute(
        "INSERT INTO requests VALUES(?,?,?,?,?)",
        (OPERATION, KEY, command_hash, '{"status":"synthetic-uncommitted"}', "2026-09-10T00:00:00+00:00"),
    )
    try:
        assert state.db.in_transaction
        response = signed_post(api, LOOKUP_PATH, query())
        assert response.status_code == 404
        assert state.db.in_transaction
    finally:
        state.db.rollback()
    assert state.lookup_command(OPERATION, KEY, command_hash) is None


@pytest.mark.parametrize(("stored_result", "recorded_at"), [
    ('{"private-fixture-value":', "2026-09-10T00:00:00+00:00"),
    ('{}', "2026-09-10T00:00:00+00:00"),
    ('{"resource_version":true}', "2026-09-10T00:00:00+00:00"),
    ('{"resource_version":0}', "2026-09-10T00:00:00+00:00"),
    ('["private-fixture-value"]', "2026-09-10T00:00:00+00:00"),
    ('{"private-fixture-value": NaN}', "2026-09-10T00:00:00+00:00"),
    ('{"resource_version":1}', "private-fixture-value"),
    ('{"resource_version":1}', "2026-09-10T00:00:00"),
])
def test_unverifiable_record_returns_redacted_reconciliation_error(api, stored_result, recorded_at):
    provision(api)
    state = api[2].state
    state.db.execute("UPDATE requests SET result=?,created_at=?", (stored_result, recorded_at))
    state.db.commit()
    before = snapshot(state)
    response = signed_post(api, LOOKUP_PATH, query())
    assert response.status_code == 503
    assert response.json()["code"] == "command_result_unavailable"
    assert response.json()["status"] == "reconciliation_required"
    assert "private-fixture-value" not in response.text
    assert snapshot(state) == before


def test_missing_database_is_not_recreated_by_lookup(api, tmp_path, monkeypatch):
    missing = tmp_path / "missing.sqlite3"
    monkeypatch.setattr(api[2].state, "path", missing)
    response = signed_post(api, LOOKUP_PATH, query())
    assert response.status_code == 503
    assert response.json()["code"] == "command_result_unavailable"
    assert not missing.exists()


def test_scope_is_checked_before_body_validation(api):
    response = signed_post(api, LOOKUP_PATH, b'{"private-fixture-value":',
                           scope="telephony:provision")
    assert response.status_code == 403
    assert "private-fixture-value" not in response.text


@pytest.mark.parametrize("invalid", [
    {"operation": "unsupported", "idempotency_key": KEY, "command": {"unexpected": "private-fixture-value"}},
    {"operation": OPERATION, "idempotency_key": KEY, "command": {"unexpected": "private-fixture-value"}},
])
def test_lookup_validation_is_typed_and_redacted(api, invalid):
    response = signed_post(api, LOOKUP_PATH, invalid)
    assert response.status_code == 422
    assert "private-fixture-value" not in response.text


def test_openapi_exposes_typed_recovery_and_external_call_contracts(api):
    schema = api[0].get("/openapi.json").json()
    route = schema["paths"][LOOKUP_PATH]["post"]
    assert route["requestBody"]["required"] is True
    assert {"200", "403", "404", "409", "422", "503"} <= route["responses"].keys()
    assert not any(p["name"].lower() == "idempotency-key" for p in route["parameters"])
    lookup = schema["components"]["schemas"]["CommandLookup"]
    assert set(lookup["discriminator"]["mapping"]) == {
        "campaign.provision-disabled", "campaign.disable", "agent.sync",
        "agent.provision-disabled", "agent.disable", "lead.publish",
        "callback.create", "transfer-policy.sync",
    }
    external = schema["paths"]["/v1/calls/originate"]["post"]["requestBody"]
    assert external["required"] is True
    assert external["content"]["application/json"]["schema"]["$ref"].endswith("/ExternalOriginateCommand")


@pytest.mark.parametrize(("operation", "method"), [
    ("campaign.disable", "disable_campaign"),
    ("agent.sync", "sync_agent"),
    ("agent.disable", "disable_agent"),
    ("lead.publish", "publish_lead"),
    ("callback.create", "create_callback"),
    ("transfer-policy.sync", "sync_transfer_policy"),
])
def test_lookup_recovers_each_command_family(api, operation, method):
    from test_adapter import context, provision_callback_dependencies
    from codestra_vicidial.models import CommandLookup

    service = api[2]
    provision_callback_dependencies(service)
    campaign = campaign_payload()
    campaign["context"]["expected_version"] = 1
    payloads = {
        "campaign.disable": campaign,
        "agent.sync": {"context": context(), "agent": {
            "user_id": "COD1002", "full_name": "Synthetic Second Agent",
            "user_group": "COD_TEST_SDR", "campaigns": ["CODT0001"],
        }},
        "agent.disable": {"context": context(expected_version=1), "user_id": "COD1001"},
        "lead.publish": {"context": context(), "lead": {
            "external_id": "lookup-synthetic-lead", "list_id": 21001,
            "phone_code": "1", "phone_number": "5550100002",
        }},
        "callback.create": {"context": context(), "callback": {
            "external_id": "lookup-synthetic-callback",
            "lead_id": int(service.state.mapping("lead", "callback-lead")["actual_id"]),
            "campaign_id": "CODT0001", "user_id": "COD1001",
            "callback_time": "2026-09-15T12:00:00Z",
        }},
        "transfer-policy.sync": {"context": context(), "policy": {
            "campaign_id": "CODT0001", "allowed_targets": ["COD_TEST_CLOSERS"],
        }},
    }
    request = query(payloads[operation], operation=operation)
    typed = CommandLookup.model_validate(request)
    expected = getattr(service, method)(KEY, typed.root.command)
    expected.pop("idempotent_replay")
    service.flags = FeatureFlags()
    before = snapshot(service.state)
    response = signed_post(api, LOOKUP_PATH, request)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == expected
    assert snapshot(service.state) == before


def test_disabled_agent_result_lookup_does_not_issue_credentials(api, monkeypatch):
    from test_agent_provisioning_policy import SecretAuthority, command

    service = api[2]
    original = command()
    service.state.put_campaign_authority("TEST_SYN", "tenant-test", "TEST", "subject-appolon")
    service.flags = FeatureFlags(writes=True, agent_provisioning=True)
    service.credential_authority = SecretAuthority()
    expected = service.provision_agent(KEY, original)
    expected.pop("idempotent_replay")
    service.flags = FeatureFlags()

    def must_not_issue(**_values):
        pytest.fail("result lookup must never issue credentials")

    monkeypatch.setattr(service.credential_authority, "issue", must_not_issue)
    response = signed_post(api, LOOKUP_PATH, query(
        original.model_dump(mode="json"), operation="agent.provision-disabled",
    ))
    assert response.status_code == 200
    assert response.json()["result"] == expected
    assert "synthetic-value-never-returned" not in response.text


def test_lookup_preserves_the_original_header_key_bytes(api):
    original_key = " original-command-key "
    response = signed_post(api, PROVISION_PATH, campaign_payload(),
                           scope="telephony:provision", key=original_key)
    assert response.status_code == 200
    recovered = signed_post(api, LOOKUP_PATH, query(key=original_key))
    assert recovered.status_code == 200
    assert recovered.json()["idempotency_key"] == original_key
    trimmed = signed_post(api, LOOKUP_PATH, query(key=original_key.strip()))
    assert trimmed.status_code == 404
