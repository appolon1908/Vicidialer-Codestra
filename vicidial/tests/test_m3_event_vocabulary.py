from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from codestra_vicidial.ami_gateway import (
    AMIMessage,
    AgentBinding,
    AgentDirectory,
    LifecycleStore,
)


BASE = datetime(2026, 9, 12, 3, 0, tzinfo=timezone.utc)


def binding(**changes: object) -> AgentBinding:
    values: dict[str, object] = {
        "extension": "6101",
        "tenant_id": "COD",
        "business_unit_id": "COD",
        "campaign_id": "TEST_SYN",
        "agent_id": "SYN6101",
        "keycloak_subject": "00000000-0000-0000-0000-000000006101",
        "default_direction": "inbound",
        "queue_aliases": ("TEST_SYN",),
    }
    values.update(changes)
    return AgentBinding(**values)


def directory() -> AgentDirectory:
    return AgentDirectory([binding()])


def message(
    event: str,
    linkedid: str,
    uniqueid: str,
    *,
    variables: tuple[str, ...] = (),
    **fields: str,
) -> AMIMessage:
    values: dict[str, tuple[str, ...]] = {
        "Event": (event,),
        "Linkedid": (linkedid,),
        "Uniqueid": (uniqueid,),
        "Channel": (f"PJSIP/6101-00000001",),
    }
    values.update({key: (str(value),) for key, value in fields.items()})
    if variables:
        values["ChanVariable"] = variables
    return AMIMessage(values)


def process(
    store: LifecycleStore,
    agents: AgentDirectory,
    offset: int,
    event: str,
    linkedid: str,
    uniqueid: str,
    *,
    variables: tuple[str, ...] = (),
    **fields: str,
) -> list[dict[str, object]]:
    return store.process(
        message(
            event,
            linkedid,
            uniqueid,
            variables=variables,
            **fields,
        ),
        agents,
        observed_at=BASE + timedelta(seconds=offset),
    )


def suffix(envelope: dict[str, object]) -> str:
    return str(envelope["event_type"]).rsplit(".", 1)[-1]


def test_real_ami_signals_cover_queue_to_terminal_and_preserve_identity(
    tmp_path: Path,
) -> None:
    store = LifecycleStore(tmp_path / "events.sqlite3")
    agents = directory()
    identity = (
        "CODESTRA_CALL_ID=odoo-call-001",
        "CODESTRA_CORRELATION_ID=odoo-correlation-001",
    )
    emitted: list[dict[str, object]] = []

    emitted += process(
        store,
        agents,
        0,
        "QueueCallerJoin",
        "linked-normal",
        "queue-1",
        variables=identity,
        Channel="Local/queue@default-00000001;1",
        Queue="TEST_SYN",
        Position="1",
    )
    emitted += process(
        store,
        agents,
        1,
        "AgentCalled",
        "linked-normal",
        "agent-1",
        Channel="PJSIP/6101-00000001",
    )
    emitted += process(
        store,
        agents,
        2,
        "DialBegin",
        "linked-normal",
        "agent-1",
        Channel="PJSIP/6101-00000001",
    )
    emitted += process(
        store,
        agents,
        3,
        "Newstate",
        "linked-normal",
        "agent-1",
        ChannelStateDesc="Ringing",
    )
    emitted += process(
        store,
        agents,
        4,
        "AgentConnect",
        "linked-normal",
        "agent-1",
        Channel="PJSIP/6101-00000001",
    )
    emitted += process(store, agents, 5, "Hold", "linked-normal", "agent-1")
    emitted += process(store, agents, 6, "Unhold", "linked-normal", "agent-1")
    emitted += process(
        store,
        agents,
        7,
        "AgentComplete",
        "linked-normal",
        "agent-1",
        Channel="PJSIP/6101-00000001",
    )

    assert [suffix(event) for event in emitted] == [
        "created",
        "queued",
        "offered",
        "dialing",
        "ringing",
        "answered",
        "connected",
        "held",
        "resumed",
        "hangup",
        "completed",
    ]
    assert all(
        event["correlation_id"] == "odoo-correlation-001"
        and event["payload"]["call_id"] == "odoo-call-001"
        for event in emitted
    )
    assert [event["payload"]["sequence"] for event in emitted] == list(
        range(1, len(emitted) + 1)
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("BUSY", "busy"),
        ("NOANSWER", "no_answer"),
        ("CHANUNAVAIL", "failed"),
        ("CONGESTION", "failed"),
    ],
)
def test_dial_end_dispositions_are_not_collapsed(
    tmp_path: Path, status: str, expected: str
) -> None:
    store = LifecycleStore(tmp_path / f"{status}.sqlite3")
    emitted = process(
        store,
        directory(),
        0,
        "DialBegin",
        f"linked-{status}",
        f"uid-{status}",
    )
    emitted += process(
        store,
        directory(),
        1,
        "DialEnd",
        f"linked-{status}",
        f"uid-{status}",
        DialStatus=status,
    )
    terminal = emitted[-1]
    assert suffix(terminal) == expected
    assert terminal["payload"]["dial_status"] == status


def test_queue_abandon_is_the_only_canceled_queue_signal(tmp_path: Path) -> None:
    store = LifecycleStore(tmp_path / "canceled.sqlite3")
    emitted = process(
        store,
        directory(),
        0,
        "QueueCallerJoin",
        "linked-canceled",
        "queue-canceled",
        Channel="Local/queue@default-00000002;1",
        Queue="TEST_SYN",
    )
    emitted += process(
        store,
        directory(),
        1,
        "QueueCallerAbandon",
        "linked-canceled",
        "queue-canceled",
        Channel="Local/queue@default-00000002;1",
        Queue="TEST_SYN",
    )
    assert suffix(emitted[-1]) == "canceled"


def test_hangup_cause_rejection_is_preserved_as_rejected(tmp_path: Path) -> None:
    store = LifecycleStore(tmp_path / "rejected.sqlite3")
    emitted = process(
        store,
        directory(),
        0,
        "AgentCalled",
        "linked-rejected",
        "uid-rejected",
    )
    emitted += process(
        store,
        directory(),
        1,
        "Hangup",
        "linked-rejected",
        "uid-rejected",
        Cause="21",
        **{"Cause-txt": "Call Rejected"},
    )
    assert suffix(emitted[-1]) == "rejected"
    assert emitted[-1]["payload"]["hangup_cause_code"] == 21


def test_late_duplicate_and_out_of_order_events_cannot_regress_state(
    tmp_path: Path,
) -> None:
    store = LifecycleStore(tmp_path / "ordering.sqlite3")
    agents = directory()
    emitted = process(
        store, agents, 0, "AgentCalled", "linked-order", "uid-order"
    )
    emitted += process(
        store, agents, 2, "AgentConnect", "linked-order", "uid-order"
    )
    before = len(emitted)

    # The signal is delayed in both transport order and event time. It is
    # real evidence, but it must not move a connected call backward.
    assert process(
        store,
        agents,
        -1,
        "Newstate",
        "linked-order",
        "uid-order",
        ChannelStateDesc="Ringing",
    ) == []

    # Exact AMI redelivery is idempotent, including its event fingerprint.
    assert process(
        store, agents, 2, "AgentConnect", "linked-order", "uid-order"
    ) == []

    # Dialing arriving after connection is also a no-op for the durable state.
    assert process(
        store, agents, 3, "DialBegin", "linked-order", "uid-order"
    ) == []
    assert len(emitted) == before


def test_hangup_evidence_identifies_agent_or_peer_channel_only(
    tmp_path: Path,
) -> None:
    for role, uniqueid in (("agent_leg", "agent-leg"), ("peer_leg", "peer-leg")):
        store = LifecycleStore(tmp_path / f"{role}.sqlite3")
        agents = directory()
        emitted = process(
            store,
            agents,
            0,
            "AgentCalled",
            f"linked-{role}",
            "agent-leg",
            Channel="PJSIP/6101-00000001",
        )
        emitted += process(
            store,
            agents,
            1,
            "AgentConnect",
            f"linked-{role}",
            "agent-leg",
            Channel="PJSIP/6101-00000001",
            DestChannel="PJSIP/6000-00000002",
            DestUniqueid="peer-leg",
        )
        emitted += process(
            store,
            agents,
            2,
            "AgentComplete",
            f"linked-{role}",
            uniqueid,
            Channel=(
                "PJSIP/6101-00000001"
                if role == "agent_leg"
                else "PJSIP/6000-00000002"
            ),
        )
        assert emitted[-2]["payload"]["hangup_leg"] == role


def test_ambiguous_queue_never_invents_an_agent_binding(tmp_path: Path) -> None:
    agents = AgentDirectory(
        [
            binding(extension="6101", agent_id="SYN6101"),
            binding(
                extension="6102",
                agent_id="SYN6102",
                keycloak_subject="00000000-0000-0000-0000-000000006102",
            ),
        ]
    )
    store = LifecycleStore(tmp_path / "ambiguous.sqlite3")
    assert process(
        store,
        agents,
        0,
        "QueueCallerJoin",
        "linked-ambiguous",
        "queue-ambiguous",
        Channel="Local/queue@default-00000003;1",
        Queue="TEST_SYN",
    ) == []


def test_identity_change_is_rejected_after_first_event(tmp_path: Path) -> None:
    store = LifecycleStore(tmp_path / "identity.sqlite3")
    agents = directory()
    first = process(
        store,
        agents,
        0,
        "AgentCalled",
        "linked-identity",
        "uid-identity",
        variables=(
            "CODESTRA_CALL_ID=call-stable",
            "CODESTRA_CORRELATION_ID=correlation-stable",
        ),
    )
    changed = process(
        store,
        agents,
        1,
        "DialBegin",
        "linked-identity",
        "uid-identity",
        variables=(
            "CODESTRA_CALL_ID=call-other",
            "CODESTRA_CORRELATION_ID=correlation-other",
        ),
    )
    assert first
    assert changed == []
