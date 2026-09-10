from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from codestra_vicidial.external_calling import (
    ExternalCallError,
    ExternalCallExecutor,
    ExternalCallPolicy,
    ExternalOriginateCommand,
)


RELEASE = "a" * 40


def policy(**overrides) -> ExternalCallPolicy:
    values = {
        "schema_version": "1.0",
        "release_sha": RELEASE,
        "authorization_reference": "CHG-PSTN-ONE-CALL-20260910",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "campaign": "TEST_SYN",
        "destination_e164": "+18496582053",
        "dialplan_context": "outbound-didww",
        "dial_prefix": "8",
        "trunk": "didww-out",
        "caller_id": "16173419794",
        "max_calls": 1,
        "max_connected_seconds": 30,
        "recording_requested": False,
    }
    values.update(overrides)
    return ExternalCallPolicy.model_validate(values)


def command(**overrides) -> ExternalOriginateCommand:
    values = {
        "operation_id": "pstn-test-20260910-001",
        "correlation_id": "corr-pstn-20260910-001",
        "campaign": "TEST_SYN",
        "destination": "+18496582053",
        "authorization_reference": "CHG-PSTN-ONE-CALL-20260910",
        "purpose": "controlled_test",
        "recording_requested": False,
    }
    values.update(overrides)
    return ExternalOriginateCommand.model_validate(values)


class FakeTransport:
    def __init__(self):
        self.verify_count = 0
        self.originate_count = 0
        self.last = None

    def verify_external_route(self, pol):
        self.verify_count += 1
        assert pol.trunk == "didww-out"
        assert pol.dialplan_context == "outbound-didww"

    def originate_external(self, pol, operation_id, uniqueid):
        self.originate_count += 1
        self.last = (pol.destination_e164, operation_id, uniqueid)


def executor(tmp_path: Path, transport: FakeTransport, enabled=lambda: True):
    return ExternalCallExecutor(
        policy(),
        tmp_path / "external.sqlite3",
        transport,
        enabled=enabled,
        audit_path=tmp_path / "audit.sqlite3",
    )


def test_exact_destination_campaign_and_one_call_are_enforced(tmp_path):
    transport = FakeTransport()
    service = executor(tmp_path, transport)
    result = service.originate(command())
    assert result["status"] == "accepted"
    assert result["duplicate"] is False
    assert transport.verify_count == 1
    assert transport.originate_count == 1

    duplicate = service.originate(command())
    assert duplicate["duplicate"] is True
    assert transport.originate_count == 1

    with pytest.raises(PermissionError, match="one-call authorization already consumed"):
        service.originate(command(operation_id="pstn-test-20260910-002",
                                  correlation_id="corr-pstn-20260910-002"))


def test_destination_and_campaign_are_policy_bound(tmp_path):
    for altered, message in [
        ({"destination": "+18495550123"}, "destination"),
        ({"campaign": "OTHER"}, "campaign"),
        ({"authorization_reference": "CHG-OTHER-ONE-CALL-20260910"}, "authorization"),
    ]:
        transport = FakeTransport()
        service = ExternalCallExecutor(
            policy(), tmp_path / (message + ".sqlite3"), transport,
            enabled=lambda: True,
            audit_path=tmp_path / (message + "-audit.sqlite3"),
        )
        with pytest.raises(PermissionError, match=message):
            service.originate(command(**altered))
        assert transport.originate_count == 0


def test_gate_is_fail_closed_and_audited(tmp_path):
    transport = FakeTransport()
    service = executor(tmp_path, transport, enabled=lambda: False)
    with pytest.raises(PermissionError, match="disabled"):
        service.originate(command())
    assert transport.originate_count == 0


def test_operation_id_cannot_be_reused_with_different_content(tmp_path):
    transport = FakeTransport()
    service = executor(tmp_path, transport)
    service.originate(command())
    with pytest.raises(ExternalCallError, match="reused"):
        service.originate(command(correlation_id="corr-pstn-20260910-999"))
    assert transport.originate_count == 1


def test_policy_rejects_recording_and_unbounded_call_duration():
    with pytest.raises(Exception):
        policy(recording_requested=True)
    with pytest.raises(Exception):
        policy(max_connected_seconds=61)
