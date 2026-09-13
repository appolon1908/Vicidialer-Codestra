"""Mission 4: Asterisk-level WebRTC single-device enforcement.

These tests validate that this adapter *generates correct PJSIP
configuration content* (max_contacts=1/remove_existing=yes on
provision, max_contacts=0/disallow=all on revoke) as an artifact
independent of the app-level StateStore session check. They do NOT
validate live Asterisk registration/SIP behavior -- this codebase has no
verified way to reach a live Asterisk instance's real PJSIP backend
(static conf vs. realtime ODBC/pgsql), per repository.py's bind_phone
docstring and Vicidialer-Codestra#53. Two-browser rejection at the real
telephony layer, and SIP/WebRTC trace confirmation, remain live-
verification work for once that access exists.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from codestra_vicidial import pjsip_config
from codestra_vicidial.credential_authority import IssuedCredential
from codestra_vicidial.models import (
    AgentCommand,
    DisableAgentCommand,
    ExtensionReserveCommand,
    WebrtcProvisionCommand,
    WebrtcRevokeCommand,
    WebrtcRotateCommand,
)
from codestra_vicidial.repository import MemoryRepository
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore


class SequencedAuthority:
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
        "reason": "synthetic pjsip enforcement test",
        "requested_at": "2026-09-12T00:00:00Z",
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


# --- pure config-generation correctness -------------------------------

def test_active_config_enforces_single_contact():
    cfg = pjsip_config.PjsipEndpointConfig(
        user_id="COD1001", extension="6300", credential_reference="ref-1", active=True,
    )
    rendered = cfg.render()
    assert "max_contacts=1" in rendered
    assert "remove_existing=yes" in rendered
    assert "webrtc=yes" in rendered
    assert "disallow=all" not in rendered


def test_revoked_config_rejects_all_registration():
    cfg = pjsip_config.revoked("COD1001", "6300")
    rendered = cfg.render()
    assert "max_contacts=0" in rendered
    assert "disallow=all" in rendered


def test_content_hash_is_deterministic_and_identity_sensitive():
    a = pjsip_config.PjsipEndpointConfig("COD1001", "6300", "ref-1", True)
    b = pjsip_config.PjsipEndpointConfig("COD1001", "6300", "ref-1", True)
    c = pjsip_config.PjsipEndpointConfig("COD1001", "6300", "ref-2", True)
    assert a.content_hash() == b.content_hash()
    assert a.content_hash() != c.content_hash()


# --- wiring into the service -------------------------------------------

def test_provision_webrtc_generates_active_pjsip_artifact(harness):
    service, _repo, state, _authority = harness
    sync_agent(service, user_id="COD1001")
    service.reserve_extension("reserve-1", ExtensionReserveCommand.model_validate({
        "context": context(), "reservation": {"user_id": "COD1001", "pool": "web_ai"},
    }))
    service.provision_webrtc("webrtc-1", WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": "COD1001"},
    }))
    artifact = state.webrtc_pjsip_config("COD1001")
    assert artifact is not None
    assert artifact["active"] == 1
    assert "max_contacts=1" in artifact["content"]
    assert artifact["extension"] == "6300"


def test_revoke_webrtc_generates_teardown_pjsip_artifact(harness):
    service, _repo, state, _authority = harness
    sync_agent(service, user_id="COD1001")
    service.reserve_extension("reserve-2", ExtensionReserveCommand.model_validate({
        "context": context(), "reservation": {"user_id": "COD1001", "pool": "web_ai"},
    }))
    service.provision_webrtc("webrtc-2", WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": "COD1001"},
    }))
    service.revoke_webrtc("webrtc-revoke-1", WebrtcRevokeCommand.model_validate({
        "context": context(expected_version=1), "user_id": "COD1001",
    }))
    artifact = state.webrtc_pjsip_config("COD1001")
    assert artifact is not None
    assert artifact["active"] == 0
    assert "max_contacts=0" in artifact["content"]
    assert "disallow=all" in artifact["content"]


def test_reprovision_after_revoke_restores_active_artifact(harness):
    """Idempotency across the revoke/re-provision cycle: no drift, no
    accumulated rows, the artifact simply reflects current desired state."""
    service, _repo, state, _authority = harness
    sync_agent(service, user_id="COD1001")
    service.reserve_extension("reserve-3", ExtensionReserveCommand.model_validate({
        "context": context(), "reservation": {"user_id": "COD1001", "pool": "web_ai"},
    }))
    service.provision_webrtc("webrtc-3", WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": "COD1001"},
    }))
    service.revoke_webrtc("webrtc-revoke-2", WebrtcRevokeCommand.model_validate({
        "context": context(expected_version=1), "user_id": "COD1001",
    }))
    service.provision_webrtc("webrtc-4", WebrtcProvisionCommand.model_validate({
        "context": context(expected_version=2), "webrtc": {"user_id": "COD1001"},
    }))
    artifact = state.webrtc_pjsip_config("COD1001")
    assert artifact["active"] == 1
    assert "max_contacts=1" in artifact["content"]


def test_rotate_webrtc_secret_regenerates_artifact_with_new_credential(harness):
    service, _repo, state, _authority = harness
    sync_agent(service, user_id="COD1001")
    service.reserve_extension("reserve-4", ExtensionReserveCommand.model_validate({
        "context": context(), "reservation": {"user_id": "COD1001", "pool": "web_ai"},
    }))
    service.provision_webrtc("webrtc-5", WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": "COD1001"},
    }))
    first_hash = state.webrtc_pjsip_config("COD1001")["content_hash"]
    service.rotate_webrtc_secret("webrtc-rotate-1", WebrtcRotateCommand.model_validate({
        "context": context(expected_version=1), "user_id": "COD1001",
    }))
    second = state.webrtc_pjsip_config("COD1001")
    assert second["active"] == 1
    assert second["content_hash"] != first_hash


def test_disable_agent_tears_down_pjsip_artifact(harness):
    service, _repo, state, _authority = harness
    sync_agent(service, user_id="COD1001")
    service.reserve_extension("reserve-5", ExtensionReserveCommand.model_validate({
        "context": context(), "reservation": {"user_id": "COD1001", "pool": "web_ai"},
    }))
    service.provision_webrtc("webrtc-6", WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": "COD1001"},
    }))
    service.disable_agent("agent-disable-1", DisableAgentCommand.model_validate({
        "context": context(expected_version=1), "user_id": "COD1001",
    }))
    artifact = state.webrtc_pjsip_config("COD1001")
    assert artifact["active"] == 0
    assert "disallow=all" in artifact["content"]
