"""Mission 6: extension reservation/adoption and WebRTC provisioning.

Covers the phone/webrtc channel adapter added to unblock the Middleware
agent-provisioning saga's CHANNEL_PROVISIONING step (previously
CHANNEL_ADAPTER_NOT_IMPLEMENTED for every channel). Mirrors the harness
pattern used by test_adapter.py::test_agent_sync_disable.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from codestra_vicidial.credential_authority import IssuedCredential
from codestra_vicidial.models import (
    AgentCommand,
    ExtensionAdoptCommand,
    ExtensionReserveCommand,
    WebrtcProvisionCommand,
    WebrtcRevokeCommand,
    WebrtcRotateCommand,
)
from codestra_vicidial.repository import CollisionError, MemoryRepository
from codestra_vicidial.service import AdapterService, FeatureFlags, WebrtcSessionActive
from codestra_vicidial.state import StateStore


class SequencedAuthority:
    """Issues a distinct, deterministic credential per call so tests can
    tell a fresh issuance apart from a stale/reused one."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._count = 0

    def issue(self, **values) -> IssuedCredential:
        self.calls.append(values)
        self._count += 1
        return IssuedCredential(f"synthetic-credential-{self._count}", f"ref-{self._count}")


def context(*, expected_version: int = 0, campaign_id: str = "CODT0001") -> dict:
    return {
        "correlation_id": "corr-12345678", "actor": "middleware-test",
        "tenant_id": "tenant-cod", "business_unit": "COD",
        "campaign_id": campaign_id, "supervisor_subject": "supervisor-cod",
        "expected_version": expected_version,
        "reason": "synthetic channel provisioning test",
        "requested_at": "2026-09-11T20:00:00Z",
    }


@pytest.fixture
def harness(tmp_path: Path):
    repo = MemoryRepository()
    state = StateStore(tmp_path / "state.sqlite3")
    state.put_campaign_authority("CODT0001", "tenant-cod", "COD", "supervisor-cod")
    authority = SequencedAuthority()
    service = AdapterService(
        repo, state,
        FeatureFlags(
            writes=True, provisioning=True, agent_sync=True,
            extension_provisioning=True, webrtc_provisioning=True,
        ),
        credential_authority=authority,
    )
    return service, repo, state, authority


def sync_agent(service, user_id="COD1001", campaign="CODT0001"):
    command = AgentCommand.model_validate({
        "context": context(),
        "agent": {
            "user_id": user_id, "full_name": "Synthetic Agent",
            "user_group": "COD_TEST_SDR", "campaigns": [campaign],
            "inbound_groups": [], "active": False,
        },
    })
    service.sync_agent(f"agent-sync-{user_id}", command)


def test_reserve_extension_picks_lowest_free_and_excludes_adopted(harness):
    service, repo, _state, _authority = harness
    sync_agent(service)
    command = ExtensionReserveCommand.model_validate({
        "context": context(),
        "reservation": {"user_id": "COD1001", "pool": "transportation"},
    })
    result = service.reserve_extension("reserve-1", command)
    assert result["actual"]["extension"] == "6100"
    assert "pass" not in result["actual"]
    assert repo.phones["6100"]["user_id"] == "COD1001"


def test_reserve_extension_never_returns_6101(harness):
    service, repo, _state, _authority = harness
    for n in range(6100, 6101):
        repo.phones[str(n)] = {"extension": str(n), "user_id": f"HOLD{n}", "active": True}
    sync_agent(service, user_id="COD1002")
    command = ExtensionReserveCommand.model_validate({
        "context": context(expected_version=0),
        "reservation": {"user_id": "COD1002", "pool": "transportation"},
    })
    result = service.reserve_extension("reserve-2", command)
    # 6100 is taken by a fixture hold, 6101 is permanently excluded even
    # though it is free and inside the transportation range.
    assert result["actual"]["extension"] == "6102"


def test_reserve_extension_idempotent_replay_does_not_reallocate(harness):
    service, repo, _state, authority = harness
    sync_agent(service)
    command = ExtensionReserveCommand.model_validate({
        "context": context(),
        "reservation": {"user_id": "COD1001", "pool": "moneybee"},
    })
    first = service.reserve_extension("reserve-key-1", command)
    second = service.reserve_extension("reserve-key-1", command)
    assert first["actual"]["extension"] == second["actual"]["extension"] == "6200"
    assert len(repo.phones) == 1
    assert len(authority.calls) == 1


def test_adopt_extension_creates_new_phone_when_absent(harness):
    service, repo, state, _authority = harness
    sync_agent(service, user_id="COD0016")
    command = ExtensionAdoptCommand.model_validate({
        "context": context(),
        "adoption": {"user_id": "COD0016", "extension": "6101"},
    })
    result = service.adopt_extension("adopt-1", command)
    assert result["already_present"] is False
    assert repo.phones["6101"]["user_id"] == "COD0016"
    assert state.mapping("extension", "6101")["actual_id"] == "COD0016"


def test_adopt_extension_never_rewrites_an_existing_credential(harness):
    """The safety-critical case: 6101 already has a live phones row (as it
    would in production, per the permanent staging bootstrap) - adoption
    must link ownership without touching its existing SIP secret."""
    service, repo, _state, authority = harness
    sync_agent(service, user_id="COD0016")
    repo.phones["6101"] = {
        "extension": "6101", "user_id": None, "active": True,
        "pass": "already-live-production-secret",
    }
    command = ExtensionAdoptCommand.model_validate({
        "context": context(),
        "adoption": {"user_id": "COD0016", "extension": "6101"},
    })
    result = service.adopt_extension("adopt-2", command)
    assert result["already_present"] is True
    assert repo.phones["6101"]["pass"] == "already-live-production-secret"
    assert repo.phones["6101"]["user_id"] == "COD0016"
    assert authority.calls == []  # no credential was issued


def test_adopt_extension_rejects_a_second_owner(harness):
    service, repo, _state, _authority = harness
    sync_agent(service, user_id="COD0016")
    sync_agent(service, user_id="COD0017")
    first = ExtensionAdoptCommand.model_validate({
        "context": context(),
        "adoption": {"user_id": "COD0016", "extension": "6101"},
    })
    service.adopt_extension("adopt-3", first)
    second = ExtensionAdoptCommand.model_validate({
        "context": context(),
        "adoption": {"user_id": "COD0017", "extension": "6101"},
    })
    with pytest.raises(CollisionError):
        service.adopt_extension("adopt-4", second)


def test_reserve_extension_and_adopt_extension_are_separate_code_paths(harness):
    """adopt must never touch pool allocation: adopting 6101 (inside the
    transportation range) must not make 6100 skip to 6102 for a later
    reservation, the way a shared pool-consuming implementation could."""
    service, repo, _state, _authority = harness
    sync_agent(service, user_id="COD0016")
    sync_agent(service, user_id="COD0020")
    adopt = ExtensionAdoptCommand.model_validate({
        "context": context(),
        "adoption": {"user_id": "COD0016", "extension": "6101"},
    })
    service.adopt_extension("adopt-5", adopt)
    reserve = ExtensionReserveCommand.model_validate({
        "context": context(),
        "reservation": {"user_id": "COD0020", "pool": "transportation"},
    })
    result = service.reserve_extension("reserve-3", reserve)
    assert result["actual"]["extension"] == "6100"


def test_provision_webrtc_requires_a_phone_assignment(harness):
    service, _repo, _state, _authority = harness
    sync_agent(service, user_id="COD1001")
    command = WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": "COD1001"},
    })
    with pytest.raises(ValueError, match="no phone assignment"):
        service.provision_webrtc("webrtc-1", command)


def test_provision_webrtc_issues_single_use_short_lived_credential(harness):
    service, repo, state, authority = harness
    sync_agent(service, user_id="COD1001")
    reserve = ExtensionReserveCommand.model_validate({
        "context": context(),
        "reservation": {"user_id": "COD1001", "pool": "web_ai"},
    })
    service.reserve_extension("reserve-4", reserve)
    command = WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": "COD1001"},
    })
    result = service.provision_webrtc("webrtc-2", command)
    assert result["extension"] == "6300"
    assert result["max_active_sessions"] == 1
    assert authority.calls[-1]["operation"] == "webrtc-registration"
    session = state.webrtc_session("COD1001")
    assert session["extension"] == "6300"
    assert session["single_use_consumed"] == 0


def test_second_concurrent_webrtc_provision_is_rejected(harness):
    service, repo, state, _authority = harness
    sync_agent(service, user_id="COD1001")
    reserve = ExtensionReserveCommand.model_validate({
        "context": context(),
        "reservation": {"user_id": "COD1001", "pool": "web_ai"},
    })
    service.reserve_extension("reserve-5", reserve)
    first = WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": "COD1001"},
    })
    service.provision_webrtc("webrtc-3", first)
    second = WebrtcProvisionCommand.model_validate({
        "context": context(expected_version=1), "webrtc": {"user_id": "COD1001"},
    })
    with pytest.raises(WebrtcSessionActive):
        service.provision_webrtc("webrtc-4", second)


def test_revoke_webrtc_clears_session_and_phone_credential(harness):
    service, repo, state, _authority = harness
    sync_agent(service, user_id="COD1001")
    reserve = ExtensionReserveCommand.model_validate({
        "context": context(),
        "reservation": {"user_id": "COD1001", "pool": "web_ai"},
    })
    service.reserve_extension("reserve-6", reserve)
    provision = WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": "COD1001"},
    })
    service.provision_webrtc("webrtc-5", provision)
    revoke = WebrtcRevokeCommand.model_validate({
        "context": context(expected_version=1), "user_id": "COD1001",
    })
    service.revoke_webrtc("webrtc-revoke-1", revoke)
    assert state.webrtc_session("COD1001") is None
    assert "pass" not in repo.phones["6300"]


def test_rotate_webrtc_secret_replaces_the_session_credential(harness):
    service, repo, state, authority = harness
    sync_agent(service, user_id="COD1001")
    reserve = ExtensionReserveCommand.model_validate({
        "context": context(),
        "reservation": {"user_id": "COD1001", "pool": "web_ai"},
    })
    service.reserve_extension("reserve-7", reserve)
    provision = WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": "COD1001"},
    })
    service.provision_webrtc("webrtc-6", provision)
    first_ref = state.webrtc_session("COD1001")["credential_reference"]
    rotate = WebrtcRotateCommand.model_validate({
        "context": context(expected_version=1), "user_id": "COD1001",
    })
    service.rotate_webrtc_secret("webrtc-rotate-1", rotate)
    second_ref = state.webrtc_session("COD1001")["credential_reference"]
    assert first_ref != second_ref
    assert authority.calls[-1]["operation"] == "webrtc-registration"


def test_disable_agent_atomically_revokes_webrtc(harness):
    """Mission 6: disable_agent must be atomic and immediate - a disabled
    agent can never be left with a live WebRTC session/credential."""
    service, repo, state, _authority = harness
    sync_agent(service, user_id="COD1001")
    reserve = ExtensionReserveCommand.model_validate({
        "context": context(),
        "reservation": {"user_id": "COD1001", "pool": "web_ai"},
    })
    service.reserve_extension("reserve-8", reserve)
    provision = WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": "COD1001"},
    })
    service.provision_webrtc("webrtc-7", provision)
    assert state.webrtc_session("COD1001") is not None

    from codestra_vicidial.models import DisableAgentCommand
    disable = DisableAgentCommand.model_validate({
        "context": context(expected_version=1), "user_id": "COD1001",
    })
    service.disable_agent("agent-disable-1", disable)
    assert state.webrtc_session("COD1001") is None
    assert repo.phones["6300"]["active"] is False
