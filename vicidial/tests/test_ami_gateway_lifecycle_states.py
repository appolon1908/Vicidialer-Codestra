from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from codestra_vicidial.ami_gateway import (
    AgentBinding,
    AgentDirectory,
    LifecycleStore,
    parse_ami_block,
)

EXTENSION = "6101"
REMOTE_CHANNEL = "PJSIP/trunk-carrier-00000002"


def _directory() -> AgentDirectory:
    return AgentDirectory(
        [
            AgentBinding(
                extension=EXTENSION,
                tenant_id="COD",
                business_unit_id="COD",
                campaign_id="TEST_SYN",
                agent_id="SYN6101",
                keycloak_subject="00000000-0000-0000-0000-000000006101",
                default_direction="inbound",
            )
        ]
    )


def _block(event: str, linkedid: str, uniqueid: str, **fields: str) -> bytes:
    values = {
        "Event": event,
        "Linkedid": linkedid,
        "Uniqueid": uniqueid,
        "Channel": f"PJSIP/{EXTENSION}-00000001",
        **fields,
    }
    body = "\r\n".join(f"{key}: {value}" for key, value in values.items())
    return body.encode()


def _process(store: LifecycleStore, directory: AgentDirectory, event: str, linkedid: str, uniqueid: str, *, observed_at: datetime, **fields: str):
    return store.process(
        parse_ami_block(_block(event, linkedid, uniqueid, **fields)),
        directory,
        observed_at=observed_at,
    )


def test_dial_end_busy_and_no_answer_are_distinct_states(tmp_path: Path) -> None:
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    _process(store, directory, "DialBegin", "linked-busy", "uid-busy", observed_at=now)
    busy_events = _process(
        store, directory, "DialEnd", "linked-busy", "uid-busy",
        observed_at=now + timedelta(seconds=1), DialStatus="BUSY",
    )
    assert [e["event_type"].rsplit(".", 1)[-1] for e in busy_events] == ["busy"]

    _process(store, directory, "DialBegin", "linked-na", "uid-na", observed_at=now)
    no_answer_events = _process(
        store, directory, "DialEnd", "linked-na", "uid-na",
        observed_at=now + timedelta(seconds=1), DialStatus="NOANSWER",
    )
    assert [e["event_type"].rsplit(".", 1)[-1] for e in no_answer_events] == ["no_answer"]


def test_hangup_cause_codes_map_to_distinct_pre_answer_outcomes(tmp_path: Path) -> None:
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    scenarios = {
        "16": "canceled",
        "17": "busy",
        "18": "no_answer",
        "19": "no_answer",
        "21": "rejected",
    }
    for cause, expected in scenarios.items():
        linkedid = f"linked-cause-{cause}"
        uniqueid = f"uid-cause-{cause}"
        _process(store, directory, "DialBegin", linkedid, uniqueid, observed_at=now)
        events = _process(
            store, directory, "Hangup", linkedid, uniqueid,
            observed_at=now + timedelta(seconds=1), Cause=cause,
        )
        assert [e["event_type"].rsplit(".", 1)[-1] for e in events] == [expected], cause


def test_agent_ring_no_answer_is_a_timeout_not_a_far_end_outcome(tmp_path: Path) -> None:
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    _process(store, directory, "AgentCalled", "linked-timeout", "uid-timeout", observed_at=now)
    events = _process(
        store, directory, "AgentRingNoAnswer", "linked-timeout", "uid-timeout",
        observed_at=now + timedelta(seconds=1),
    )
    assert [e["event_type"].rsplit(".", 1)[-1] for e in events] == ["timeout"]


def test_dial_begin_is_reported_as_dialing_not_offered(tmp_path: Path) -> None:
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    events = _process(store, directory, "DialBegin", "linked-dialing", "uid-dialing", observed_at=now)
    # A brand-new call_key also emits "created" first; "dialing" is what
    # DialBegin/AgentCalled now reports itself as (previously "offered").
    assert [e["event_type"].rsplit(".", 1)[-1] for e in events] == ["created", "dialing"]


def test_hangup_after_answer_distinguishes_local_from_remote_initiator(tmp_path: Path) -> None:
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    # Local (agent) hangup: the terminating Channel is the agent's own extension.
    _process(store, directory, "AgentCalled", "linked-local", "uid-local", observed_at=now)
    _process(store, directory, "AgentConnect", "linked-local", "uid-local", observed_at=now + timedelta(seconds=1))
    events = _process(
        store, directory, "Hangup", "linked-local", "uid-local",
        observed_at=now + timedelta(seconds=2),
    )
    hangup = next(e for e in events if e["event_type"].endswith(".hangup"))
    assert hangup["payload"]["hangup_initiator"] == "local"

    # Remote (far-end/trunk) hangup: a different channel terminates the call.
    directory2 = _directory()
    _process(store, directory2, "AgentCalled", "linked-remote", "uid-remote", observed_at=now)
    _process(store, directory2, "AgentConnect", "linked-remote", "uid-remote", observed_at=now + timedelta(seconds=1))
    remote_block = (
        f"Event: Hangup\r\nLinkedid: linked-remote\r\nUniqueid: uid-remote\r\n"
        f"Channel: {REMOTE_CHANNEL}"
    ).encode()
    remote_events = store.process(
        parse_ami_block(remote_block), directory2, observed_at=now + timedelta(seconds=2)
    )
    remote_hangup = next(e for e in remote_events if e["event_type"].endswith(".hangup"))
    assert remote_hangup["payload"]["hangup_initiator"] == "remote"


def test_duplicate_hangup_delivery_is_idempotent(tmp_path: Path) -> None:
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    _process(store, directory, "DialBegin", "linked-dup", "uid-dup", observed_at=now)
    block = _block("Hangup", "linked-dup", "uid-dup", Cause="21")
    message = parse_ami_block(block)

    first = store.process(message, directory, observed_at=now + timedelta(seconds=1))
    assert [e["event_type"].rsplit(".", 1)[-1] for e in first] == ["rejected"]

    # Exact byte-for-byte redelivery of the same AMI message: no new event.
    second = store.process(message, directory, observed_at=now + timedelta(seconds=1))
    assert second == []


def test_hangup_after_terminal_state_is_not_reprocessed(tmp_path: Path) -> None:
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    _process(store, directory, "DialBegin", "linked-late", "uid-late", observed_at=now)
    terminal_events = _process(
        store, directory, "Hangup", "linked-late", "uid-late",
        observed_at=now + timedelta(seconds=1), Cause="17",
    )
    assert [e["event_type"].rsplit(".", 1)[-1] for e in terminal_events] == ["busy"]

    # A second, distinct Hangup AMI message for the same already-terminal
    # uniqueid (e.g. a duplicate emitted by Asterisk itself with a different
    # cause) must not regress or re-emit a new outcome for it -- the store's
    # `if state.terminal: return []` guard applies once terminal is reached.
    late_events = _process(
        store, directory, "Hangup", "linked-late", "uid-late",
        observed_at=now + timedelta(seconds=5), Cause="16",
    )
    assert late_events == []
