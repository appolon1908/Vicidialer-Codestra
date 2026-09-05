import hashlib
from datetime import UTC, datetime

import pytest

from codestra_vicidial.agent_provisioning import AgentProvisioningPolicy
from codestra_vicidial.models import ProvisionAgentCommand
from codestra_vicidial.reconciliation import ReconciliationDenied, canonical_sha256
from codestra_vicidial.repository import CollisionError, MemoryRepository, MySQLRepository
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore
from codestra_vicidial.credential_authority import IssuedCredential

AGENT = {
    "user_id": "appolon", "full_name": "Appolon Internal Supervisor",
    "user_group": "WEBTEST_SYN", "campaigns": ["TEST_SYN"],
    "inbound_groups": [], "active": False, "adopt_existing_sha256": None,
    "extension": "6901", "role": "supervisor", "campaign": "TEST_SYN",
    "internal_only": True, "external_dialing": False,
}


class SecretAuthority:
    def issue(self, **_values):
        return IssuedCredential("synthetic-value-never-returned-0001", "secret-ref-1")


def command(**changes):
    payload = {
        "context": {"correlation_id": "correlation-synthetic-0001",
                    "actor": "codestra-provisioning",
                    "reason": "Approved disabled synthetic provisioning",
                    "requested_at": "2026-09-05T12:00:00Z"},
        "agent": AGENT, "user_level": 8,
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
        "user_level": 8,
        "agent": agent,
    }, expected_release_sha="a" * 40)


def test_exact_grant_accepts_only_approved_agent(tmp_path):
    policy(tmp_path).authorize(command())
    other = AGENT | {"user_id": "COD1002"}
    with pytest.raises(ValueError):
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
        command(user_level=9)


def test_provision_is_separate_idempotent_and_collision_safe(tmp_path):
    repo = MemoryRepository()
    service = AdapterService(repo, StateStore(tmp_path / "state.sqlite3"),
        FeatureFlags(writes=True, agent_sync=False, agent_provisioning=True), SecretAuthority())
    first = service.provision_agent("idempotency-synthetic-0001", command())
    replay = service.provision_agent("idempotency-synthetic-0001", command())
    assert first["actual"]["active"] is False
    assert first["actual"]["user_level"] == 8
    assert replay["idempotent_replay"] is True
    assert len(repo.agents) == 1
    with pytest.raises(CollisionError, match="already exists"):
        service.provision_agent("idempotency-synthetic-0002", command())


def test_global_and_independent_kill_switches_remain_closed(tmp_path):
    for flags in (FeatureFlags(writes=False, agent_provisioning=True),
                  FeatureFlags(writes=True, agent_provisioning=False)):
        service = AdapterService(MemoryRepository(),
            StateStore(tmp_path / f"{flags.writes}.sqlite3"), flags, SecretAuthority())
        with pytest.raises(PermissionError):
            service.provision_agent("idempotency-synthetic-0001", command())
    assert FeatureFlags().production_dialing is False
    assert FeatureFlags().lead_publication is False
    assert FeatureFlags().callback_writes is False


def test_retry_recovers_commit_before_state_finalization(tmp_path, monkeypatch):
    repo = MemoryRepository()
    state = StateStore(tmp_path / "state.sqlite3")
    service = AdapterService(repo, state,
        FeatureFlags(writes=True, agent_provisioning=True), SecretAuthority())
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


@pytest.mark.parametrize("agent_change", [
    {"campaign": "PRODUCTION"}, {"campaigns": ["PRODUCTION"]},
    {"role": "administrator"}, {"internal_only": False},
    {"external_dialing": True}, {"active": True},
])
def test_schema_rejects_broadened_access(agent_change):
    with pytest.raises(ValueError):
        command(agent=AGENT | agent_change)


def test_username_and_extension_collisions_preserve_existing_agent(tmp_path):
    repo = MemoryRepository()
    repo.agents["cod00016"] = {"user_id": "cod00016", "extension": "6101", "active": False}
    repo.phones["6101"] = {"extension": "6101", "user_id": "cod00016", "active": False}
    repo.agents["appolon"] = {"user_id": "appolon", "active": False}
    service = AdapterService(repo, StateStore(tmp_path / "state.sqlite3"),
        FeatureFlags(writes=True, agent_provisioning=True), SecretAuthority())
    with pytest.raises(CollisionError, match="already exists"):
        service.provision_agent("idempotency-collision-0001", command())
    assert repo.agents["cod00016"]["extension"] == "6101"
    assert repo.phones["6101"]["user_id"] == "cod00016"

    del repo.agents["appolon"]
    repo.phones["6901"] = {"extension": "6901", "user_id": "other", "active": False}
    with pytest.raises(CollisionError, match="extension"):
        service.provision_agent("idempotency-collision-0002", command())


def test_credential_is_absent_from_result_and_audit(tmp_path):
    state = StateStore(tmp_path / "state.sqlite3")
    service = AdapterService(MemoryRepository(), state,
        FeatureFlags(writes=True, agent_provisioning=True), SecretAuthority())
    result = service.provision_agent("idempotency-redaction-0001", command())
    serialized = __import__("json").dumps(result)
    assert "synthetic-value-never-returned" not in serialized
    audit = state.db.execute("SELECT * FROM audit").fetchall()
    assert "synthetic-value-never-returned" not in repr([dict(row) for row in audit])


def test_secret_authority_unavailable_fails_closed(tmp_path):
    service = AdapterService(MemoryRepository(), StateStore(tmp_path / "state.sqlite3"),
        FeatureFlags(writes=True, agent_provisioning=True))
    with pytest.raises(RuntimeError, match="secret authority unavailable"):
        service.provision_agent("idempotency-no-authority-0001", command())


def test_partial_phone_failure_rolls_back_user(tmp_path):
    class FailingPhones(dict):
        def __setitem__(self, key, value):
            if key == "6901":
                raise RuntimeError("synthetic phone failure")
            return super().__setitem__(key, value)

    repo = MemoryRepository()
    repo.phones = FailingPhones()
    service = AdapterService(repo, StateStore(tmp_path / "state.sqlite3"),
        FeatureFlags(writes=True, agent_provisioning=True), SecretAuthority())
    with pytest.raises(RuntimeError, match="phone failure"):
        service.provision_agent("idempotency-rollback-0001", command())
    assert "appolon" not in repo.agents
    assert "6901" not in repo.phones


def test_mysql_agent_provisioning_rejects_nontransactional_tables(monkeypatch):
    repository = MySQLRepository(object())
    monkeypatch.setattr(repository, "_all", lambda *_args: [
        {"table_name": "vicidial_users", "engine": "MyISAM"},
        {"table_name": "phones", "engine": "MyISAM"},
    ])
    with pytest.raises(RuntimeError, match="requires transactional InnoDB"):
        repository._require_transactional_agent_tables()


def test_agent_provisioning_compose_requires_secret_authority_socket():
    compose = (
        __import__("pathlib").Path(__file__).parents[1]
        / "docker/compose.agent-provisioning.yml"
    ).read_text(encoding="utf-8")
    assert "CODESTRA_SECRET_AUTHORITY_SOCKET:?" in compose
    assert ":/run/secret-authority/authority.sock" in compose
    assert "CODESTRA_SECRET_AUTHORITY_SOCKET: /run/secret-authority/authority.sock" in compose
