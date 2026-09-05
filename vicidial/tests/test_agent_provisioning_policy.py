import hashlib
from datetime import UTC, datetime

import pytest

from codestra_vicidial.agent_provisioning import AgentProvisioningPolicy
from codestra_vicidial.models import ProvisionAgentCommand
from codestra_vicidial.reconciliation import ReconciliationDenied, canonical_sha256
from codestra_vicidial.repository import CollisionError, MemoryRepository
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore

AGENT = {
    "user_id": "COD1001", "full_name": "Synthetic Provisionee",
    "user_group": "COD_ADMIN", "campaigns": ["COD0001"],
    "inbound_groups": [], "active": False, "adopt_existing_sha256": None,
}


def command(**changes):
    payload = {
        "context": {"correlation_id": "correlation-synthetic-0001",
                    "actor": "codestra-provisioning",
                    "reason": "Approved disabled synthetic provisioning",
                    "requested_at": "2026-09-05T12:00:00Z"},
        "agent": AGENT, "user_level": 9,
        "authorization_reference": "CHG-20260905-SYNTHETIC-USER",
        "plan_sha256": canonical_sha256(AGENT),
        "backup_reference": "codestra-vicidial-20260905T092327Z.tar.enc",
    }
    payload.update(changes)
    return ProvisionAgentCommand.model_validate(payload)


def policy(tmp_path, agent=AGENT):
    backup = tmp_path / "backup.tar.enc"
    backup.write_bytes(b"synthetic encrypted backup")
    created_at = datetime.now(UTC).isoformat()
    return AgentProvisioningPolicy({
        "protected_sha": "a" * 40,
        "authorization_reference": "CHG-20260905-SYNTHETIC-USER",
        "expires_at": "2099-09-06T00:00:00Z",
        "backup": {"reference": "codestra-vicidial-20260905T092327Z.tar.enc",
                   "created_at": created_at, "artifact": str(backup),
                   "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
                   "validation": "PASS"},
        "user_level": 9,
        "agent": agent,
    }, expected_release_sha="a" * 40)


def test_exact_grant_accepts_only_approved_agent(tmp_path):
    policy(tmp_path).authorize(command())
    other = AGENT | {"user_id": "COD1002"}
    with pytest.raises(ReconciliationDenied, match="absent from exact approved grant"):
        policy(tmp_path).authorize(command(agent=other))
    with pytest.raises(ReconciliationDenied, match="authorization"):
        policy(tmp_path).authorize(command(authorization_reference="CHG-OTHER-USER-00001"))


def test_backup_replacement_after_policy_load_is_rejected(tmp_path):
    approved = policy(tmp_path)
    approved.backup_path.write_bytes(b"replacement")
    with pytest.raises(ReconciliationDenied, match="replaced or resized"):
        approved.authorize(command())


def test_missing_expired_or_malformed_grant_is_rejected(tmp_path):
    with pytest.raises(ReconciliationDenied, match="schema"):
        AgentProvisioningPolicy({}, expected_release_sha="a" * 40)
    expired = policy(tmp_path).__class__
    with pytest.raises(ReconciliationDenied, match="expired"):
        expired({"protected_sha": "a" * 40,
                 "authorization_reference": "CHG-20260905-SYNTHETIC-USER",
                 "expires_at": "2020-01-01T00:00:00Z",
                 "backup": {}, "user_level": 9,
                 "agent": AGENT},
                expected_release_sha="a" * 40,
                now=datetime(2026, 9, 5, tzinfo=UTC))

    with pytest.raises(ValueError):
        command(user_level=8)


def test_provision_is_separate_idempotent_and_collision_safe(tmp_path):
    repo = MemoryRepository()
    service = AdapterService(repo, StateStore(tmp_path / "state.sqlite3"),
        FeatureFlags(writes=True, agent_sync=False, agent_provisioning=True))
    first = service.provision_agent("idempotency-synthetic-0001", command())
    replay = service.provision_agent("idempotency-synthetic-0001", command())
    assert first["actual"]["active"] is False
    assert first["actual"]["user_level"] == 9
    assert replay["idempotent_replay"] is True
    assert len(repo.agents) == 1
    with pytest.raises(CollisionError, match="already exists"):
        service.provision_agent("idempotency-synthetic-0002", command())


def test_global_and_independent_kill_switches_remain_closed(tmp_path):
    for flags in (FeatureFlags(writes=False, agent_provisioning=True),
                  FeatureFlags(writes=True, agent_provisioning=False)):
        service = AdapterService(MemoryRepository(),
            StateStore(tmp_path / f"{flags.writes}.sqlite3"), flags)
        with pytest.raises(PermissionError):
            service.provision_agent("idempotency-synthetic-0001", command())
    assert FeatureFlags().production_dialing is False
    assert FeatureFlags().lead_publication is False
    assert FeatureFlags().callback_writes is False


def test_retry_recovers_commit_before_state_finalization(tmp_path, monkeypatch):
    repo = MemoryRepository()
    state = StateStore(tmp_path / "state.sqlite3")
    service = AdapterService(repo, state,
        FeatureFlags(writes=True, agent_provisioning=True))
    finalize = state.finalize_agent_adoption
    monkeypatch.setattr(state, "finalize_agent_adoption",
                        lambda *args: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError, match="crash"):
        service.provision_agent("idempotency-crash-0001", command())
    assert len(repo.agents) == 1
    monkeypatch.setattr(state, "finalize_agent_adoption", finalize)
    recovered = service.provision_agent("idempotency-crash-0001", command())
    assert recovered["recovered_after_commit"] is True
    assert len(repo.agents) == 1
