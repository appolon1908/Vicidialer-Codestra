from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlite3

from codestra_vicidial.ami_gateway import (
    AgentBinding,
    AgentDirectory,
    CorrelationResolver,
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


def test_delayed_ringing_event_does_not_regress_connected_state(tmp_path: Path) -> None:
    """A buffered/reordered Newstate(Ringing) delivered after the call has
    already advanced to connected must not revert the durable state -- only
    the AMI transport can reorder delivery like this, so this must be
    guarded at the gateway's own parsing layer, not just downstream."""
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    _process(store, directory, "AgentCalled", "linked-reorder", "uid-reorder", observed_at=now)
    connect_events = _process(
        store, directory, "AgentConnect", "linked-reorder", "uid-reorder",
        observed_at=now + timedelta(seconds=1),
    )
    assert [e["event_type"].rsplit(".", 1)[-1] for e in connect_events] == [
        "answered",
        "connected",
    ]

    # A stale Ringing Newstate for the same channel, delivered late.
    stale_events = _process(
        store, directory, "Newstate", "linked-reorder", "uid-reorder",
        observed_at=now + timedelta(seconds=2), ChannelStateDesc="Ringing",
    )
    assert stale_events == []

    # State must still read as connected, not regressed to ringing.
    row = sqlite3.connect(tmp_path / "lifecycle.sqlite3").execute(
        "SELECT state FROM calls WHERE asterisk_uniqueid=?", ("uid-reorder",)
    ).fetchone()
    assert row[0] == "connected"


def test_malformed_dial_status_does_not_crash_and_is_ignored(tmp_path: Path) -> None:
    """An unrecognized/malformed DialStatus value must be absorbed safely
    rather than raising or fabricating an outcome the adapter never actually
    observed."""
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    _process(store, directory, "DialBegin", "linked-malformed", "uid-malformed", observed_at=now)
    events = _process(
        store, directory, "DialEnd", "linked-malformed", "uid-malformed",
        observed_at=now + timedelta(seconds=1), DialStatus="THIS-IS-NOT-A-REAL-STATUS",
    )
    assert events == []


def test_unbound_agent_event_is_ignored_not_a_correlation_mismatch_crash(tmp_path: Path) -> None:
    """An AMI event referencing an extension with no directory binding at all
    (e.g. a channel this gateway has no agent record for) must be dropped
    quietly, not raise or attempt to force a binding."""
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    unbound_block = (
        b"Event: DialBegin\r\nLinkedid: linked-unbound\r\nUniqueid: uid-unbound\r\n"
        b"Channel: PJSIP/9999-00000001"
    )
    events = store.process(parse_ami_block(unbound_block), directory, observed_at=now)
    assert events == []


def test_correlation_id_resolves_real_middleware_id_when_available(tmp_path: Path) -> None:
    """The envelope's correlation_id must carry the real correlation_id
    Middleware established at origination (recorded in internal_calls,
    keyed by the Asterisk-assigned uniqueid via ChannelId=<uniqueid> on the
    Originate action) rather than a locally-synthesized call_id."""
    internal_calls_db = tmp_path / "internal-calls.sqlite3"
    with sqlite3.connect(internal_calls_db) as conn:
        conn.execute(
            "CREATE TABLE internal_calls(operation_id TEXT PRIMARY KEY,"
            "request_hash TEXT NOT NULL,correlation_id TEXT NOT NULL,"
            "uniqueid TEXT NOT NULL UNIQUE,state TEXT NOT NULL,"
            "hangup_state TEXT,created_at TEXT NOT NULL,policy_hash TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO internal_calls VALUES(?,?,?,?,?,?,?,?)",
            (
                "op-real-corr-1", "hash", "corr-real-established-by-middleware",
                "uid-real-corr", "accepted", None, "2026-09-12T00:00:00Z", "policy-hash",
            ),
        )
        conn.commit()

    resolver = CorrelationResolver(internal_calls_path=internal_calls_db)
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3", correlation_resolver=resolver)
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    events = _process(
        store, directory, "AgentCalled", "linked-real-corr", "uid-real-corr",
        observed_at=now,
    )
    created = next(e for e in events if e["event_type"].endswith(".created"))
    assert created["correlation_id"] == "corr-real-established-by-middleware"


def test_correlation_id_falls_back_to_local_call_id_without_a_match(tmp_path: Path) -> None:
    """Ordinary VICIdial-dialer traffic never originated through the
    controlled internal/external call paths has no real correlation_id to
    resolve -- the gateway must keep emitting its own locally-derived call_id
    rather than fail or emit an empty correlation_id."""
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = _directory()
    now = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc)

    events = _process(
        store, directory, "AgentCalled", "linked-no-resolver", "uid-no-resolver",
        observed_at=now,
    )
    created = next(e for e in events if e["event_type"].endswith(".created"))
    assert created["correlation_id"].startswith("vici-")
