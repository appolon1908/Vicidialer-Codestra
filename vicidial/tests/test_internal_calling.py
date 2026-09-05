from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from codestra_vicidial.internal_calling import (
    InternalCallError,
    InternalCallExecutor,
    InternalCallPolicy,
    OriginateCommand,
)


class FakeAmi:
    def __init__(self):
        self.actions = []

    def verify_route(self, policy):
        return None

    def action(self, fields):
        self.actions.append(fields)
        return {"Response": "Success"}


def policy():
    return InternalCallPolicy(
        schema_version="1.0",
        release_sha="a" * 40,
        route_sha256="a" * 64,
        authorization_reference="CHG-APPOLON-INTERNAL-0001",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        tenant_id="tenant-test",
        subject="subject-appolon",
        employee_id="employee-appolon",
        username="appolon",
        extension="6901",
        campaign="TEST_SYN",
        destination_alias="internal:TEST_ECHO",
        destination_extension="6000",
        dialplan_context="codestra-internal-test",
        internal_only=True,
        external_dialing=False,
        recording=False,
        max_calls=1,
    )


def command(**changes):
    value = {
        "operation_id": "operation-internal-0001",
        "correlation_id": "correlation-internal-0001",
        "tenant_id": "tenant-test",
        "subject": "subject-appolon",
        "employee_id": "employee-appolon",
        "username": "appolon",
        "extension": "6901",
        "campaign": "TEST_SYN",
        "destination": "internal:TEST_ECHO",
        "internal_only": True,
        "external_dialing": False,
        "recording_requested": False,
        "authorization_reference": "CHG-APPOLON-INTERNAL-0001",
    }
    return OriginateCommand(**(value | changes))


def executor(tmp_path):
    lifecycle = tmp_path / "lifecycle.sqlite3"
    from codestra_vicidial.ami_gateway import LifecycleStore

    LifecycleStore(lifecycle)
    transport = FakeAmi()
    return (
        InternalCallExecutor(
            policy(),
            tmp_path / "calls.sqlite3",
            lifecycle,
            transport,
            identity_ready=lambda: True,
            enabled=lambda: True,
        ),
        transport,
        lifecycle,
    )


def test_originate_uses_only_policy_owned_channel_context_and_target(tmp_path):
    service, transport, _ = executor(tmp_path)
    result = service.originate(command())
    assert result["status"] == "accepted"
    assert transport.actions == [
        {
            "Action": "Originate",
            "ActionID": "operation-internal-0001",
            "Channel": "PJSIP/6901",
            "Context": "codestra-internal-test",
            "Exten": "6000",
            "Priority": "1",
            "Async": "true",
            "ChannelId": result["asterisk_uniqueid"],
            "Variable": "CODESTRA_OPERATION_ID=operation-internal-0001",
        }
    ]


def test_duplicate_does_not_originate_twice_and_changed_content_conflicts(tmp_path):
    service, transport, _ = executor(tmp_path)
    service.originate(command())
    assert service.originate(command())["duplicate"] is True
    assert len(transport.actions) == 1
    with pytest.raises(InternalCallError, match="different content"):
        service.originate(command(correlation_id="correlation-internal-0002"))


@pytest.mark.parametrize(
    "change",
    [
        {"tenant_id": "other-tenant"},
        {"subject": "other-subject"},
        {"employee_id": "other-employee"},
    ],
)
def test_identity_mismatch_is_rejected_before_ami(tmp_path, change):
    service, transport, _ = executor(tmp_path)
    with pytest.raises(PermissionError):
        service.originate(command(**change))
    assert transport.actions == []


def test_readback_and_hangup_use_authoritative_lifecycle_channel(tmp_path):
    service, transport, lifecycle = executor(tmp_path)
    accepted = service.originate(command())
    seed_connected(lifecycle, accepted["asterisk_uniqueid"])
    evidence = service.readback(command().operation_id)
    assert (
        evidence["linkedid"] == accepted["asterisk_uniqueid"]
        and evidence["call_state"] == "connected"
    )
    result = service.hangup(command().operation_id)
    assert result["hangup"] == "requested"
    assert transport.actions[-1] == {
        "Action": "Hangup",
        "ActionID": "operation-internal-0001:hangup",
        "Channel": "PJSIP/6901-00000001",
    }
    assert service.hangup(command().operation_id)["duplicate"] is True
    assert len(transport.actions) == 2


def test_unknown_channel_cannot_be_claimed_cancelled(tmp_path):
    service, _, _ = executor(tmp_path)
    service.originate(command())
    with pytest.raises(InternalCallError, match="reconcile"):
        service.hangup(command().operation_id)


def test_ambiguous_dispatch_is_persisted_and_never_retried(tmp_path):
    service, transport, _ = executor(tmp_path)

    def fail(_fields):
        raise TimeoutError("synthetic timeout")

    transport.action = fail
    with pytest.raises(InternalCallError, match="reconciliation"):
        service.originate(command())
    replay = service.originate(command())
    assert replay["status"] == "dispatch_unknown"
    assert replay["duplicate"] is True


def seed_connected(path, uniqueid):
    from codestra_vicidial.ami_gateway import (
        AgentBinding,
        AgentDirectory,
        LifecycleStore,
        parse_ami_block,
    )

    directory = AgentDirectory(
        [
            AgentBinding(
                extension="6901",
                tenant_id="tenant-test",
                business_unit_id="synthetic-unit",
                campaign_id="TEST_SYN",
                agent_id="appolon",
                keycloak_subject="subject-appolon",
            )
        ]
    )
    message = parse_ami_block(
        (
            f"Event: Newstate\r\nUniqueid: {uniqueid}\r\nLinkedid: {uniqueid}\r\n"
            "Channel: PJSIP/6901-00000001\r\nChannelStateDesc: Up"
        ).encode()
    )
    LifecycleStore(path).process(message, directory)


def test_one_call_grant_cannot_use_a_second_operation_id(tmp_path):
    service, transport, _ = executor(tmp_path)
    service.originate(command())
    with pytest.raises(PermissionError, match="already consumed"):
        service.originate(command(operation_id="operation-internal-0002"))
    assert len(transport.actions) == 1


def test_originate_and_hangup_write_append_only_audit_outcomes(tmp_path):
    service, _, lifecycle = executor(tmp_path)
    accepted = service.originate(command())
    seed_connected(lifecycle, accepted["asterisk_uniqueid"])
    service.hangup(command().operation_id)
    with sqlite3.connect(service.audit_path) as db:
        rows = db.execute(
            "SELECT operation,outcome FROM audit ORDER BY sequence"
        ).fetchall()
    assert rows == [
        ("internal-call.originate", "requested"),
        ("internal-call.originate", "accepted"),
        ("internal-call.hangup", "requested"),
        ("internal-call.hangup", "requested"),
    ]
