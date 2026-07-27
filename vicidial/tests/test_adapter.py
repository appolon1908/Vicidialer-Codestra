from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from codestra_vicidial.app import create_app
from codestra_vicidial.models import (
    AgentCommand, CallbackCommand, CampaignCommand, DisableAgentCommand,
    LeadCommand, TransferPolicyCommand,
)
from codestra_vicidial.repository import CollisionError, MemoryRepository
from codestra_vicidial.security import RequestAuthenticator
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore


def context() -> dict:
    return {
        "correlation_id": "corr-12345678", "actor": "middleware-test",
        "reason": "synthetic disabled provisioning test",
        "requested_at": "2026-07-27T20:00:00Z",
    }


def campaign_payload() -> dict:
    return {
        "context": context(),
        "campaign": {
            "canonical_id": "COD-TEST-OUT", "actual_id": "CODT0001",
            "name": "STG COD test outbound", "business_unit": "COD",
            "direction": "outbound", "active": False, "dial_method": "MANUAL",
            "call_time": "9am-9pm", "recording_policy": "ONDEMAND",
            "callback_enabled": True, "default_list_id": 21001,
            "lists": [{"list_id": 21001, "code": "COD_TEST_PRIMARY_001", "active": False}],
            "groups": [
                {"code": "COD_TEST_SDR", "name": "COD test SDR", "kind": "user", "active": False},
                {"code": "COD_TEST_CLOSERS", "name": "COD test closers", "kind": "closer", "active": False},
            ],
            "scripts": [{
                "code": "COD_TEST_SDR_V1", "name": "COD test script", "text": "Synthetic only",
                "version": 1, "active": False,
            }],
            "dispositions": [{
                "code": "TNA", "name": "Test No Answer", "selectable": True,
                "human_answered": False, "category": "UNDEFINED",
            }],
            "transfer_targets": ["COD_TEST_CLOSERS"],
            "reporting_mapping": "STG-COD-TEST-V1",
            "recording_policy_reference": "policy:test-recording-v1",
        },
    }


@pytest.fixture
def harness(tmp_path: Path):
    repo = MemoryRepository()
    service = AdapterService(
        repo, StateStore(tmp_path / "state.sqlite3"),
        FeatureFlags(writes=True, provisioning=True, agent_sync=True,
                     lead_publication=True, callback_writes=True,
                     transfer_policy_sync=True, production_dialing=False),
    )
    return service, repo


def test_route_contract_is_present(harness):
    service, _ = harness
    auth = RequestAuthenticator("codestra-middleware", b"x" * 32, frozenset({"127.0.0.1"}))
    routes = {(method, route.path) for route in create_app(service, auth).routes
              for method in getattr(route, "methods", set())}
    required = {
        ("GET", "/health"), ("GET", "/ready"), ("GET", "/version"),
        ("POST", "/v1/campaigns/validate"),
        ("POST", "/v1/campaigns/provision-disabled"),
        ("POST", "/v1/campaigns/update-disabled"),
        ("POST", "/v1/campaigns/disable"),
        ("GET", "/v1/campaigns/{campaign_id}/actual-state"),
        ("POST", "/v1/agents/sync"), ("POST", "/v1/agents/disable"),
        ("GET", "/v1/agents/{user_id}/actual-state"),
        ("POST", "/v1/leads/publish"), ("GET", "/v1/leads/by-idempotency/{key}"),
        ("POST", "/v1/callbacks/create"), ("GET", "/v1/callbacks/{callback_id}"),
        ("POST", "/v1/scripts/sync"), ("POST", "/v1/dispositions/sync"),
        ("POST", "/v1/transfer-policies/sync"),
        ("POST", "/v1/reconciliation/run"), ("GET", "/v1/reconciliation/{run_id}"),
    }
    assert required <= routes
    prohibited_fragments = {"/sql", "/shell", "/command", "/files", "/exec"}
    assert not any(any(fragment in path for fragment in prohibited_fragments)
                   for _method, path in routes)


def test_auth_source_identity_scope_signature_and_replay():
    auth = RequestAuthenticator(
        "codestra-middleware", b"x" * 32,
        frozenset({"65.109.65.169"}), max_clock_skew_seconds=60,
    )
    body = b'{"safe":true}'
    now = int(time.time())
    signature = auth.sign(now, "nonce-1234567890", body)
    auth.verify(source="65.109.65.169", identity="codestra-middleware",
                scopes={"telephony:provision"}, timestamp=now,
                nonce="nonce-1234567890", body=body, signature=signature,
                required_scope="telephony:provision", now=now)
    with pytest.raises(PermissionError, match="replay"):
        auth.verify(source="65.109.65.169", identity="codestra-middleware",
                    scopes={"telephony:provision"}, timestamp=now,
                    nonce="nonce-1234567890", body=body, signature=signature,
                    required_scope="telephony:provision", now=now)
    for override, message in [
        ({"source": "203.0.113.1"}, "source"),
        ({"identity": "intruder"}, "identity"),
        ({"scopes": {"telephony:read"}}, "scope"),
        ({"signature": "0" * 64}, "signature"),
    ]:
        values = {
            "source": "65.109.65.169", "identity": "codestra-middleware",
            "scopes": {"telephony:provision"}, "timestamp": now,
            "nonce": "unique-" + message + "-123456", "body": body,
            "signature": signature, "required_scope": "telephony:provision", "now": now,
        } | override
        if "nonce" not in override:
            values["signature"] = auth.sign(now, values["nonce"], body) if message != "signature" else "0" * 64
        with pytest.raises(PermissionError, match=message):
            auth.verify(**values)


def test_schema_rejects_extra_active_and_wrong_range():
    payload = campaign_payload()
    payload["campaign"]["active"] = True
    with pytest.raises(ValidationError):
        CampaignCommand.model_validate(payload)
    payload = campaign_payload()
    payload["campaign"]["lists"][0]["list_id"] = 31001
    payload["campaign"]["default_list_id"] = 31001
    with pytest.raises(ValidationError, match="business-unit range"):
        CampaignCommand.model_validate(payload)
    payload = campaign_payload()
    payload["shell"] = "id"
    with pytest.raises(ValidationError, match="Extra inputs"):
        CampaignCommand.model_validate(payload)


def test_provision_disabled_readback_and_retry(harness):
    service, repo = harness
    command = CampaignCommand.model_validate(campaign_payload())
    first = service.provision_campaign("idem-12345678", command)
    assert first["status"] == "complete_disabled"
    assert first["actual"]["active"] == "N"
    assert repo.lists[21001]["active"] == "N"
    snapshot = service.state.get_snapshot(first["snapshot_id"])
    assert snapshot is not None
    assert snapshot["state"]["campaign"] is None
    second = service.provision_campaign("idem-12345678", command)
    assert second["idempotent_replay"] is True
    assert len(repo.campaigns) == len(repo.lists) == 1


def test_idempotency_conflict(harness):
    service, _ = harness
    service.provision_campaign("same-key", CampaignCommand.model_validate(campaign_payload()))
    changed = campaign_payload()
    changed["campaign"]["name"] = "Different"
    with pytest.raises(ValueError, match="idempotency conflict"):
        service.provision_campaign("same-key", CampaignCommand.model_validate(changed))


def test_compatible_adoption(harness):
    service, _ = harness
    command = CampaignCommand.model_validate(campaign_payload())
    service.provision_campaign("first-key", command)
    result = service.provision_campaign("second-key", command)
    assert {row["result"] for row in result["resources"]} == {"adopted"}


def test_incompatible_collision_blocks(harness):
    service, repo = harness
    repo.campaigns["CODT0001"] = {
        "campaign_id": "CODT0001", "canonical_id": "SCP-TEST-OUT",
        "campaign_name": "Other owner", "active": "N", "dial_method": "MANUAL",
        "local_call_time": "9am-9pm",
    }
    with pytest.raises(CollisionError, match="incompatible collision"):
        service.provision_campaign(
            "collision-key", CampaignCommand.model_validate(campaign_payload()))
    assert repo.lists == {}


def test_collision_is_compensating_rollback_safe(harness):
    service, repo = harness
    repo.lists[21001] = {
        "list_id": 21001, "list_name": "OTHER_OWNER",
        "campaign_id": "OTHER001", "active": "N",
    }
    before = json.dumps({
        "campaigns": repo.campaigns, "lists": repo.lists,
        "groups": repo.groups, "scripts": repo.scripts,
        "dispositions": repo.dispositions,
    }, sort_keys=True)
    with pytest.raises(CollisionError):
        service.provision_campaign(
            "rollback-collision", CampaignCommand.model_validate(campaign_payload()))
    after = json.dumps({
        "campaigns": repo.campaigns, "lists": repo.lists,
        "groups": repo.groups, "scripts": repo.scripts,
        "dispositions": repo.dispositions,
    }, sort_keys=True)
    # Memory repository exposes a real weakness if creation happens before all
    # compatibility checks: this assertion requires all-or-nothing behavior.
    assert after == before


def test_kill_switch_blocks_mutation(tmp_path):
    repo = MemoryRepository()
    service = AdapterService(repo, StateStore(tmp_path / "state.db"),
                             FeatureFlags(writes=False, provisioning=True))
    with pytest.raises(PermissionError, match="kill switch"):
        service.provision_campaign(
            "kill-key", CampaignCommand.model_validate(campaign_payload()))
    assert repo.campaigns == {}


def test_agent_sync_disable(harness):
    service, repo = harness
    command = AgentCommand.model_validate({
        "context": context(),
        "agent": {
            "user_id": "COD1001", "full_name": "Synthetic Agent",
            "user_group": "COD_TEST_SDR", "campaigns": ["CODT0001"],
            "inbound_groups": ["COD_TEST_CLOSERS"], "active": False,
        },
    })
    assert service.sync_agent("agent-sync", command)["actual"]["active"] is False
    disabled = service.disable_agent(
        "agent-disable", DisableAgentCommand.model_validate(
            {"context": context(), "user_id": "COD1001"}))
    assert disabled["actual"]["active"] is False
    assert repo.agents["COD1001"]["full_name"] == "Synthetic Agent"


def test_lead_publication_duplicate_retry_and_collision(harness):
    service, repo = harness
    payload = {
        "context": context(),
        "lead": {
            "external_id": "synthetic-lead-1", "list_id": 21001,
            "phone_code": "1", "phone_number": "2025550199",
            "first_name": "Synthetic", "last_name": "Lead", "status": "NEW",
        },
    }
    command = LeadCommand.model_validate(payload)
    first = service.publish_lead("lead-one", command)
    second = service.publish_lead("lead-two", command)
    assert first["actual"]["lead_id"] == second["actual"]["lead_id"]
    assert len(repo.leads) == 1
    payload["lead"]["phone_number"] = "2025550100"
    with pytest.raises(CollisionError):
        service.publish_lead("lead-three", LeadCommand.model_validate(payload))


def test_callback_and_transfer_policy(harness):
    service, repo = harness
    service.provision_campaign(
        "campaign-for-xfer", CampaignCommand.model_validate(campaign_payload()))
    callback = CallbackCommand.model_validate({
        "context": context(),
        "callback": {
            "external_id": "callback-1", "lead_id": 1, "campaign_id": "CODT0001",
            "user_id": "COD1001", "callback_time": "2026-07-28T14:00:00Z",
            "recipient": "USERONLY", "comments": "Synthetic",
        },
    })
    created = service.create_callback("callback-one", callback)
    assert repo.callback(created["actual"]["callback_id"]) is not None
    policy = TransferPolicyCommand.model_validate({
        "context": context(),
        "policy": {"campaign_id": "CODT0001", "allowed_targets": ["COD_TEST_CLOSERS"]},
    })
    result = service.sync_transfer_policy("policy-one", policy)
    assert result["allowed_targets"] == ["COD_TEST_CLOSERS"]
    bad = TransferPolicyCommand.model_validate({
        "context": context(),
        "policy": {"campaign_id": "CODT0001", "allowed_targets": ["SCP_OTHER_CLOSER"]},
    })
    with pytest.raises(ValueError, match="target missing"):
        service.sync_transfer_policy("policy-two", bad)


def test_disable_and_reconciliation(harness):
    service, _ = harness
    command = CampaignCommand.model_validate(campaign_payload())
    service.provision_campaign("provision-first", command)
    disabled = service.disable_campaign("disable-next", command)
    assert disabled["actual"]["active"] == "N"
    assert service.reconciliation()["inventory"]["campaigns"] == 1
