"""Mission 4 delivery gap: the adapter generates PJSIP artifacts but had no
way to learn whether Server B's sync agent actually applied them. These
tests validate the pending/ack tracking added to close that gap -- they
exercise the service/state layer directly (same standard as
test_pjsip_webrtc_enforcement.py: generated-artifact correctness, not live
Asterisk verification, since this sandbox has no Server B access either).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from codestra_vicidial.credential_authority import IssuedCredential
from codestra_vicidial.models import (
    AgentCommand,
    ExtensionReserveCommand,
    WebrtcPjsipConfigAckCommand,
    WebrtcProvisionCommand,
    WebrtcRevokeCommand,
)
from codestra_vicidial.repository import MemoryRepository
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore


class SequencedAuthority:
    def __init__(self) -> None:
        self._count = 0

    def issue(self, **values) -> IssuedCredential:
        self._count += 1
        return IssuedCredential(f"synthetic-credential-{self._count}", f"ref-{self._count}")


def context(*, expected_version: int = 0, campaign_id: str = "CODT0001") -> dict:
    return {
        "correlation_id": "corr-12345678", "actor": "middleware-test",
        "tenant_id": "tenant-cod", "business_unit": "COD",
        "campaign_id": campaign_id, "supervisor_subject": "supervisor-cod",
        "expected_version": expected_version,
        "reason": "synthetic pjsip delivery test",
        "requested_at": "2026-09-12T00:00:00Z",
    }


@pytest.fixture
def harness(tmp_path: Path):
    repo = MemoryRepository()
    state = StateStore(tmp_path / "state.sqlite3")
    state.put_campaign_authority("CODT0001", "tenant-cod", "COD", "supervisor-cod")
    service = AdapterService(
        repo, state,
        FeatureFlags(
            writes=True, provisioning=True, agent_sync=True,
            extension_provisioning=True, webrtc_provisioning=True,
        ),
        credential_authority=SequencedAuthority(),
    )
    return service, state


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


def provision(service, user_id="COD1001"):
    sync_agent(service, user_id=user_id)
    service.reserve_extension(f"reserve-{user_id}", ExtensionReserveCommand.model_validate({
        "context": context(), "reservation": {"user_id": user_id, "pool": "web_ai"},
    }))
    service.provision_webrtc(f"webrtc-{user_id}", WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": user_id},
    }))


def test_freshly_provisioned_artifact_is_pending(harness):
    service, _state = harness
    provision(service)
    pending = service.list_pending_pjsip_config()
    assert len(pending) == 1
    assert pending[0]["user_id"] == "COD1001"
    assert "max_contacts=1" in pending[0]["content"]


def test_ack_with_correct_hash_clears_pending(harness):
    service, _state = harness
    provision(service)
    [item] = service.list_pending_pjsip_config()
    service.ack_pjsip_config_applied(
        "ack-1", WebrtcPjsipConfigAckCommand.model_validate({
            "context": context(expected_version=1),
            "user_id": "COD1001", "content_hash": item["content_hash"],
        }),
    )
    assert service.list_pending_pjsip_config() == []


def test_ack_with_stale_hash_is_rejected_and_stays_pending(harness):
    service, _state = harness
    provision(service)
    with pytest.raises(ValueError):
        service.ack_pjsip_config_applied(
            "ack-2", WebrtcPjsipConfigAckCommand.model_validate({
                "context": context(expected_version=1),
                "user_id": "COD1001", "content_hash": "0" * 64,
            }),
        )
    assert len(service.list_pending_pjsip_config()) == 1


def test_revoke_after_ack_becomes_pending_again(harness):
    """A delivered+acked artifact must NOT be silently considered
    up-to-date forever -- a subsequent revoke changes the desired
    content_hash and must reappear as pending until that new content is
    also delivered and acked."""
    service, _state = harness
    provision(service)
    [item] = service.list_pending_pjsip_config()
    service.ack_pjsip_config_applied(
        "ack-3", WebrtcPjsipConfigAckCommand.model_validate({
            "context": context(expected_version=1),
            "user_id": "COD1001", "content_hash": item["content_hash"],
        }),
    )
    assert service.list_pending_pjsip_config() == []

    service.revoke_webrtc("revoke-1", WebrtcRevokeCommand.model_validate({
        "context": context(expected_version=2), "user_id": "COD1001",
    }))

    pending = service.list_pending_pjsip_config()
    assert len(pending) == 1
    assert "disallow=all" in pending[0]["content"]
    assert pending[0]["content_hash"] != item["content_hash"]


def test_pending_list_is_read_only_and_not_gated_by_webrtc_flag(tmp_path):
    """Observing drift must remain possible even while the mutation flag
    is closed -- only ack (a write) requires webrtc_provisioning."""
    state = StateStore(tmp_path / "state.sqlite3")
    state.put_campaign_authority("CODT0001", "tenant-cod", "COD", "supervisor-cod")
    service = AdapterService(
        MemoryRepository(), state,
        FeatureFlags(
            writes=True, provisioning=True, agent_sync=True,
            extension_provisioning=True, webrtc_provisioning=False,
        ),
        credential_authority=SequencedAuthority(),
    )
    # No artifacts exist yet (webrtc_provisioning is off so none could be
    # created), but the call itself must not raise/require the flag.
    assert service.list_pending_pjsip_config() == []
