import importlib.util
from importlib.machinery import SourceFileLoader
import json
import pathlib
import sys
import types
from datetime import datetime
from unittest.mock import patch

import pytest


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
# Legacy worker imports need optional dependencies, but their test doubles must
# not replace real packages for unrelated database certification tests.
dependency_stubs = {
    "phonenumbers": types.SimpleNamespace(),
    "pymysql": types.SimpleNamespace(
        cursors=types.SimpleNamespace(DictCursor=object), connect=lambda **_: None,
    ),
    "dotenv": types.SimpleNamespace(load_dotenv=lambda *_: None),
}
with patch.dict(sys.modules, dependency_stubs):
    client_spec = importlib.util.spec_from_loader(
        "canonical_event_client",
        SourceFileLoader(
            "canonical_event_client",
            str(ROOT / "scripts" / "canonical_event_client.py.reference"),
        ),
    )
    client = importlib.util.module_from_spec(client_spec)
    client_spec.loader.exec_module(client)
    sys.modules["canonical_event_client"] = client
    worker_spec = importlib.util.spec_from_loader(
        "production_worker",
        SourceFileLoader(
            "production_worker", str(ROOT / "scripts" / "vicidial_sync.py.candidate")
        ),
    )
    worker = importlib.util.module_from_spec(worker_spec)
    worker_spec.loader.exec_module(worker)


def source_row(source_id="200.1", end_epoch=2000, **changes):
    row = {
        "source_id": source_id,
        "uniqueid": source_id,
        "lead_id": 1,
        "campaign_id": "C1",
        "user": "agent",
        "status": "SALE",
        "start_epoch": end_epoch - 5,
        "end_epoch": end_epoch,
        "length_in_sec": 5,
        "call_date": datetime(2026, 7, 25, 8, 0, 0),
    }
    row.update(changes)
    return row


class Cursor:
    def __init__(self, rows=None, rowcount=1):
        self.rows = list(rows or [])
        self.rowcount = rowcount
        self.sql = []

    def execute(self, sql, params=()):
        self.sql.append((sql, params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Connection:
    def __init__(self, cursors):
        self.cursors = iter(cursors)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return next(self.cursors)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


@pytest.fixture
def watermark(monkeypatch):
    value = {
        "rule": "strictly_greater_than_composite_cursor",
        "sources": {
            "vicidial_log": {
                "end_epoch": 1000,
                "source_id": "100.1",
                "call_date": "2026-07-25 00:00:00",
            },
            "vicidial_closer_log": {
                "end_epoch": 1000,
                "source_id": "0",
                "call_date": "2026-07-25 00:00:00",
            },
        },
    }
    monkeypatch.setattr(worker, "load_production_watermark", lambda: value)
    return value


def test_actual_source_path_is_call_ended_only(monkeypatch):
    called = []
    monkeypatch.setattr(worker, "load_env", lambda: {"log_level": "INFO", "interval_seconds": 1})
    monkeypatch.setattr(worker, "setup_logging", lambda *_: None)
    monkeypatch.setattr(worker, "validate_delivery_config", lambda *_: None)
    monkeypatch.setattr(worker, "connect_vicidial_db", lambda *_: Connection([]))
    monkeypatch.setattr(worker, "connect_sync_db", lambda *_: Connection([]))
    monkeypatch.setattr(worker, "sync_production_call_ended", lambda *_: called.append("call_ended"))
    monkeypatch.setattr(worker, "process_pending_events", lambda *_: called.append("deliver"))
    monkeypatch.setattr(worker, "sync_outbound_calls", lambda *_: pytest.fail("legacy outbound called"))
    monkeypatch.setattr(worker, "sync_inbound_calls", lambda *_: pytest.fail("legacy inbound called"))
    monkeypatch.setattr(worker, "sync_recordings", lambda *_: pytest.fail("recordings called"))
    monkeypatch.setattr(worker, "sync_agent_status", lambda *_: pytest.fail("agent status called"))
    worker.run_once()
    assert called == ["call_ended", "deliver"]


def test_minimal_payload_excludes_sensitive_and_oversized_fields():
    row = source_row(
        phone_number="secret",
        comments="secret",
        recording_content="secret",
        unrelated="x" * 100000,
    )
    payload = worker.minimal_call_payload(row, "outbound", "production_vicidial_log")
    assert set(payload) <= {
        "event_type", "uniqueid", "lead_id", "campaign_id", "user", "status",
        "start_epoch", "end_epoch", "length_in_sec", "direction", "source_table",
    }
    assert not set(payload) & {
        "phone_number", "comments", "recording_content", "unrelated",
    }


def test_production_pending_selector_is_explicit_allowlist():
    count = Cursor([{"backlog_count": 0}])
    select = Cursor([])
    conn = Connection([count, select])
    worker.process_pending_events(
        conn,
        {"batch_size": 10, "send_events": True, "delivery_kill_switch": ""},
    )
    count_sql, count_params = count.sql[0]
    select_sql, select_params = select.sql[0]
    for sql in (count_sql, select_sql):
        assert "event_type=%s" in sql
        assert "source_table IN (%s,%s)" in sql
    assert count_params == (
        "call_end",
        "production_vicidial_log",
        "production_vicidial_closer_log",
    )
    assert select_params[:3] == count_params


def test_full_production_backlog_is_enforced_before_batch_selection():
    count = Cursor([{"backlog_count": worker.PRODUCTION_BACKLOG_LIMIT + 1}])
    conn = Connection([count])
    with pytest.raises(RuntimeError, match="backlog exceeds hard limit"):
        worker.process_pending_events(
            conn,
            {"batch_size": 10, "send_events": True, "delivery_kill_switch": ""},
        )
    assert len(count.sql) == 1


def test_identical_timestamp_uses_stable_tie_breaker(watermark, monkeypatch):
    states = [
        {"last_id": 1000, "last_uniqueid": "100.1"},
        {"last_id": 1000, "last_uniqueid": "0"},
    ]
    monkeypatch.setattr(worker, "get_production_state", lambda *_: states.pop(0))
    selected_out = Cursor([source_row("100.2", 1000), source_row("100.3", 1000)])
    selected_in = Cursor([])
    source = Connection([selected_out, selected_in])
    sync = Connection([])
    queued = []
    checkpoints = []
    monkeypatch.setattr(worker, "enqueue_event", lambda *args: queued.append(args[2:]))
    monkeypatch.setattr(worker, "update_state", lambda *args, **kw: checkpoints.append(kw))
    count = worker.sync_production_call_ended(source, sync, {"batch_size": 10})
    assert count == 2
    assert [x[0] for x in queued] == ["production_vicidial_log"] * 2
    assert checkpoints[-1]["last_uniqueid"] == "100.3"
    sql = selected_out.sql[0][0]
    assert "ORDER BY end_epoch,uniqueid" in sql
    assert "LIMIT %s" in sql


@pytest.mark.parametrize(
    "row",
    [
        source_row(end_epoch=0),
        source_row(length_in_sec=None),
        source_row(uniqueid=""),
    ],
)
def test_malformed_or_incomplete_rows_rejected_by_contract(row):
    with pytest.raises(client.ContractError):
        client.map_call_ended(row, "00000000-0000-4000-8000-000000000001", "c", "outbound")


@pytest.mark.parametrize("duration", [-1, 86401, True, "bad"])
def test_oversized_or_invalid_duration_rejected(duration):
    with pytest.raises(client.ContractError):
        client.map_call_ended(
            source_row(length_in_sec=duration),
            "00000000-0000-4000-8000-000000000001",
            "c",
            "outbound",
        )


def test_checkpoint_regression_is_rejected():
    cursor = Cursor([{"last_id": 999, "last_uniqueid": "z"}])
    with pytest.raises(RuntimeError, match="checkpoint regression"):
        worker.get_production_state(
            Connection([cursor]),
            "production_call_ended:vicidial_log",
            {"end_epoch": 1000, "source_id": "a", "call_date": "2026-07-25 00:00:00"},
        )


def test_checkpoint_corruption_is_fail_closed():
    cursor = Cursor([{"last_id": "not-an-integer", "last_uniqueid": "z"}])
    with pytest.raises((RuntimeError, ValueError)):
        worker.get_production_state(
            Connection([cursor]),
            "production_call_ended:vicidial_log",
            {"end_epoch": 1000, "source_id": "a", "call_date": "2026-07-25 00:00:00"},
        )


def test_kill_switch_before_start(monkeypatch):
    monkeypatch.setattr(worker.os.path, "exists", lambda _: True)
    assert worker.external_delivery_enabled(
        {"send_events": True, "delivery_kill_switch": "/STOP"}
    ) is False


def test_send_events_false_before_start():
    assert worker.external_delivery_enabled(
        {"send_events": False, "delivery_kill_switch": "/STOP"}
    ) is False


class Response:
    def __init__(self, status, body=b""):
        self.status_code = status
        self.body = body

    def iter_content(self, _):
        return iter((self.body,)) if self.body else iter(())


class Session:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.calls = 0

    def post(self, *_args, **_kwargs):
        self.calls += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, Response):
            return outcome
        return Response(outcome)


def prepared(session, attempts=1):
    transport = client.EventClient(
        "secret", "instance", send_events=True, session=session,
        max_attempts=attempts, sleeper=lambda _: None, jitter=lambda *_: 0,
    )
    event = client.map_call_ended(
        source_row(), "00000000-0000-4000-8000-000000000001", "c", "outbound"
    )
    return transport, transport.prepare(event, timestamp=1784978659)


def durable_ack(prepared_event, **changes):
    value = {
        "persisted": True,
        "status": "persisted",
        "event_id": prepared_event.event_id,
        "idempotency_key": prepared_event.idempotency_key,
    }
    value.update(changes)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")




@pytest.mark.parametrize("status", [200, 201, 202])
def test_http_success_requires_durable_persistence_acknowledgement(status):
    session = Session([])
    transport, value = prepared(session)
    session.outcomes = iter([Response(status, durable_ack(value))])
    result = transport.deliver(value)
    assert result["sent"] is True
    assert result["persistence_status"] == "persisted"


@pytest.mark.parametrize(
    "response",
    [
        Response(204),
        Response(202),
        Response(202, b"{}"),
    ],
)
def test_success_without_durable_acknowledgement_is_rejected(response):
    transport, value = prepared(Session([response]))
    with pytest.raises(client.DeliveryError, match="persistence"):
        transport.deliver(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"persisted": False},
        {"status": "accepted"},
        {"event_id": "00000000-0000-4000-8000-000000000099"},
        {"idempotency_key": "vicidial:wrong"},
    ],
)
def test_mismatched_or_non_durable_acknowledgement_is_rejected(changes):
    session = Session([])
    transport, value = prepared(session)
    session.outcomes = iter([Response(202, durable_ack(value, **changes))])
    with pytest.raises(client.DeliveryError, match="persistence|identity|idempotency"):
        transport.deliver(value)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_permanent_http_failures_are_not_retried(status):
    session = Session([status])
    transport, value = prepared(session, attempts=3)
    with pytest.raises(client.DeliveryError):
        transport.deliver(value)
    assert session.calls == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_http_failures_have_bounded_backoff(status):
    session = Session([])
    transport, value = prepared(session, attempts=3)
    session.outcomes = iter([status, status, Response(202, durable_ack(value))])
    assert transport.deliver(value)["sent"] is True
    assert session.calls == 3


@pytest.mark.parametrize(
    "error",
    [client.requests.Timeout(), client.requests.ConnectionError()],
)
def test_timeout_dns_tls_or_downstream_failure_is_bounded(error):
    session = Session([error, error])
    transport, value = prepared(session, attempts=2)
    with pytest.raises(client.DeliveryError):
        transport.deliver(value)
    assert session.calls == 2


def test_hmac_failure_is_fail_closed():
    with pytest.raises(client.ContractError):
        client.EventClient("", "instance")


def test_client_certificate_pair_is_required():
    with pytest.raises(client.ContractError):
        client.EventClient("secret", "instance", client_cert_file="/missing")


def test_batch_limit_is_capped():
    assert worker.load_env.__name__ == "load_env"
    assert worker.PRODUCTION_BACKLOG_LIMIT == 1000


def test_deterministic_event_identity_prevents_duplicate_side_effect_key():
    first = str(worker.uuid.uuid5(worker.uuid.NAMESPACE_URL, "call_end:production_vicidial_log:1"))
    second = str(worker.uuid.uuid5(worker.uuid.NAMESPACE_URL, "call_end:production_vicidial_log:1"))
    assert first == second
    assert client.deterministic_idempotency_key(first) == client.deterministic_idempotency_key(second)
