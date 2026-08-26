from __future__ import annotations

from datetime import datetime, timezone
import inspect

import pytest
from pydantic import ValidationError

from codestra_vicidial.models import (
    AgentCommand,
    CallbackCommand,
    CallbackSpec,
    CampaignCommand,
    TransferPolicyCommand,
)
from codestra_vicidial.repository import (
    CollisionError,
    MemoryRepository,
    MySQLRepository,
)
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore

from test_adapter import campaign_payload, context


def enabled_service(tmp_path):
    repo = MemoryRepository()
    flags = FeatureFlags(
        writes=True, provisioning=True, agent_sync=True,
        callback_writes=True, transfer_policy_sync=True,
    )
    return AdapterService(repo, StateStore(tmp_path / "state.sqlite3"), flags), repo


@pytest.mark.parametrize("external_id", ["unsafe%id", "unsafe_id", "slash/id", "space id"])
def test_callback_external_id_rejects_sql_pattern_and_unsafe_characters(external_id):
    with pytest.raises(ValidationError):
        CallbackSpec.model_validate({
            "external_id": external_id, "lead_id": 1, "campaign_id": "CODT0001",
            "user_id": "COD1001", "callback_time": "2026-08-25T10:00:00Z",
            "recipient": "USERONLY", "comments": "safe",
        })


def test_callback_rejects_naive_time_and_normalizes_offset_to_utc():
    base = {
        "external_id": "callback-safe", "lead_id": 1,
        "campaign_id": "CODT0001", "user_id": "COD1001",
        "recipient": "USERONLY", "comments": "safe",
    }
    with pytest.raises(ValidationError, match="timezone offset"):
        CallbackSpec.model_validate(base | {"callback_time": "2026-08-25T10:00:00"})
    value = CallbackSpec.model_validate(
        base | {"callback_time": "2026-08-25T10:00:00-04:00"})
    assert value.callback_time == datetime(2026, 8, 25, 14, tzinfo=timezone.utc)


def test_campaign_validation_uses_complete_non_mutating_preflight(tmp_path):
    service, repo = enabled_service(tmp_path)
    command = CampaignCommand.model_validate(campaign_payload())
    repo.scripts[command.campaign.scripts[0].code] = {
        "script_name": "collision", "script_text": "different",
        "version": 1, "active": "N",
    }
    before = dict(repo.campaigns)
    with pytest.raises(CollisionError, match="collision"):
        service.validate_campaign(command)
    assert repo.campaigns == before


def test_agent_desired_ownership_and_complete_readback_are_durable(tmp_path):
    service, repo = enabled_service(tmp_path)
    command = AgentCommand.model_validate({
        "context": context(),
        "agent": {"user_id": "COD1001", "full_name": "Synthetic Agent",
                  "user_group": "COD_TEST_SDR", "campaigns": ["CODT0001"],
                  "inbound_groups": ["COD_TEST_CLOSERS"], "active": False},
    })
    service.sync_agent("agent-sync", command)
    assert service.state.mapping("agent", "COD1001")["actual_id"] == "COD1001"
    assert service.state.agent_desired("COD1001")["desired"]["campaigns"] == ["CODT0001"]

    class IncompleteReadback(MemoryRepository):
        def sync_agent_disabled(self, spec):
            row = super().sync_agent_disabled(spec)
            row["inbound_groups"] = []
            return row

    broken = AdapterService(IncompleteReadback(), StateStore(tmp_path / "broken.sqlite3"),
                            service.flags)
    with pytest.raises(RuntimeError, match="inbound_groups"):
        broken.sync_agent("broken-agent", command)
    assert broken.state.mapping("agent", "COD1001") is None


def test_reconciliation_run_is_persisted_and_not_recomputed(tmp_path):
    service, repo = enabled_service(tmp_path)
    run = service.start_reconciliation("reconciliation-run-1")
    assert run["status"] == "blocked"
    repo.campaigns["LATER"] = {"campaign_id": "LATER"}
    stored = service.reconciliation_run(run["run_id"])
    assert stored is not None
    assert stored["inventory"]["campaigns"] == 0
    assert service.reconciliation_run("missing") is None


def test_reconciliation_run_retry_returns_original_persisted_run(tmp_path):
    service, _ = enabled_service(tmp_path)
    first = service.start_reconciliation("reconciliation-run-retry")
    replay = service.start_reconciliation("reconciliation-run-retry")
    assert replay["run_id"] == first["run_id"]
    assert first["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    run_count = service.state.db.execute(
        "SELECT COUNT(*) FROM reconciliation_runs"
    ).fetchone()[0]
    ledger_count = service.state.db.execute(
        "SELECT COUNT(*) FROM requests WHERE operation='reconciliation.run'"
    ).fetchone()[0]
    assert (run_count, ledger_count) == (1, 1)


def test_owned_agent_accepts_desired_state_update_after_readback(tmp_path):
    service, _ = enabled_service(tmp_path)
    payload = {
        "context": context(),
        "agent": {
            "user_id": "COD1001", "full_name": "Synthetic Agent",
            "user_group": "COD_TEST_SDR", "campaigns": ["CODT0001"],
            "inbound_groups": ["COD_TEST_CLOSERS"], "active": False,
        },
    }
    service.sync_agent("agent-version-1", AgentCommand.model_validate(payload))
    payload["agent"]["full_name"] = "Updated Synthetic Agent"
    payload["agent"]["campaigns"] = ["CODT0001", "CODT0002"]
    result = service.sync_agent(
        "agent-version-2", AgentCommand.model_validate(payload)
    )
    assert result["actual"]["full_name"] == "Updated Synthetic Agent"
    assert service.state.agent_desired("COD1001")["desired"]["campaigns"] == [
        "CODT0001", "CODT0002"
    ]


def test_unowned_existing_agent_is_rejected_before_mutation(tmp_path):
    service, repository = enabled_service(tmp_path)
    repository.agents["COD1001"] = {
        "user_id": "COD1001", "full_name": "Legacy Agent",
        "user_group": "LEGACY", "campaigns": ["LEGACY01"],
        "inbound_groups": [], "active": True,
    }
    command = AgentCommand.model_validate({
        "context": context(),
        "agent": {
            "user_id": "COD1001", "full_name": "Codestra Agent",
            "user_group": "COD_TEST_SDR", "campaigns": ["CODT0001"],
            "inbound_groups": ["COD_TEST_CLOSERS"], "active": False,
        },
    })
    with pytest.raises(CollisionError, match="active existing agent cannot be adopted"):
        service.sync_agent("unowned-agent", command)
    assert repository.agents["COD1001"]["full_name"] == "Legacy Agent"
    assert repository.agents["COD1001"]["active"] is True
    assert service.state.mapping("agent", "COD1001") is None


def test_disabled_existing_agent_requires_and_accepts_exact_adoption_proof(tmp_path):
    service, repository = enabled_service(tmp_path)
    existing = {
        "user_id": "COD1001", "full_name": "Legacy Agent",
        "user_group": "LEGACY", "campaigns": ["LEGACY01"],
        "inbound_groups": [], "active": False,
    }
    repository.agents["COD1001"] = dict(existing)
    payload = {
        "context": context(),
        "agent": {
            "user_id": "COD1001", "full_name": "Codestra Agent",
            "user_group": "COD_TEST_SDR", "campaigns": ["CODT0001"],
            "inbound_groups": ["COD_TEST_CLOSERS"], "active": False,
        },
    }
    with pytest.raises(CollisionError, match="exact adoption proof"):
        service.sync_agent("adopt-agent-missing-proof", AgentCommand.model_validate(payload))
    payload["agent"]["adopt_existing_sha256"] = service.state.digest(existing)
    result = service.sync_agent(
        "adopt-agent-exact-proof", AgentCommand.model_validate(payload)
    )
    assert result["actual"]["full_name"] == "Codestra Agent"
    assert service.state.mapping("agent", "COD1001")["actual_id"] == "COD1001"


def test_mysql_agent_adoption_uses_myisam_table_lock_through_ownership():
    source = inspect.getsource(MySQLRepository.sync_agent_disabled)
    service_source = inspect.getsource(AdapterService.sync_agent)
    assert "LOCK TABLES" in source
    assert "vicidial_users AS u WRITE" in source
    assert "vicidial_user_groups AS g READ" in source
    assert "adoption proof became stale" in source
    assert "AND active='N'" in source
    assert "release_agent_adoption_lock" in service_source
    assert "finally:" in service_source


def test_mysql_agent_disable_does_not_parse_legacy_campaign_membership():
    source = inspect.getsource(MySQLRepository.disable_agent)
    view_source = inspect.getsource(MySQLRepository._agent_disable_view)
    assert "self.agent(" not in source
    assert "_agent_disable_view" in source
    assert "allowed_campaigns" not in view_source
    assert "closer_campaigns" not in view_source


def test_test_syn_transfer_uses_test_business_unit(tmp_path):
    service, repo = enabled_service(tmp_path)
    payload = campaign_payload()
    payload["campaign"].update({
        "canonical_id": "TEST_SYN", "actual_id": "TSTSYN01",
        "business_unit": "TEST", "name": "Synthetic TEST campaign",
    })
    payload["campaign"]["groups"] = [
        {"code": "WEBTEST_SYN", "name": "TEST synthetic users",
         "kind": "user", "active": False},
        {"code": "TEST_SYN_CLOSE", "name": "TEST synthetic closer",
         "kind": "closer", "active": False},
    ]
    payload["campaign"]["transfer_targets"] = ["TEST_SYN_CLOSE"]
    payload["campaign"]["default_list_id"] = 91001
    payload["campaign"]["lists"] = [
        {"list_id": 91001, "code": "TEST_SYN_PRIMARY", "active": False}
    ]
    command = CampaignCommand.model_validate(payload)
    service.provision_campaign("test-syn", command)
    target = command.campaign.transfer_targets[0]
    policy = TransferPolicyCommand.model_validate({
        "context": context(),
        "policy": {"campaign_id": command.campaign.actual_id,
                   "allowed_targets": [target]},
    })
    assert service.sync_transfer_policy("test-syn-policy", policy)[
        "allowed_targets"] == [target]
