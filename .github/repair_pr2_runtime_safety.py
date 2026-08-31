#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one replacement marker, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")

def insert_once(path: Path, marker: str, addition: str) -> None:
    text = path.read_text(encoding="utf-8")
    if addition.strip() in text:
        return
    count = text.count(marker)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one insertion marker, found {count}")
    path.write_text(text.replace(marker, addition + marker), encoding="utf-8")

event_client = ROOT / "scripts" / "canonical_event_client.py"
insert_once(event_client, 'class EventClient(object):\n', '\n\ndef validate_persistence_acknowledgement(body, prepared):\n    """Require a bounded acknowledgement of durable middleware persistence."""\n    if not body:\n        raise DeliveryError("durable persistence acknowledgement is missing", False)\n    try:\n        acknowledgement = json.loads(body.decode("utf-8"))\n    except (UnicodeDecodeError, ValueError, TypeError):\n        raise DeliveryError("durable persistence acknowledgement is invalid", False)\n\n    expected_fields = {\n        "persisted",\n        "status",\n        "event_id",\n        "idempotency_key",\n    }\n    if not isinstance(acknowledgement, dict) or set(acknowledgement) != expected_fields:\n        raise DeliveryError("durable persistence acknowledgement fields are invalid", False)\n    if acknowledgement["persisted"] is not True or acknowledgement["status"] != "persisted":\n        raise DeliveryError("middleware did not acknowledge durable persistence", False)\n    if acknowledgement["event_id"] != prepared.event_id:\n        raise DeliveryError("persistence acknowledgement event identity mismatch", False)\n    if acknowledgement["idempotency_key"] != prepared.idempotency_key:\n        raise DeliveryError("persistence acknowledgement idempotency mismatch", False)\n    return acknowledgement\n\n\n')
replace_once(event_client, '                size = 0\n                for chunk in response.iter_content(8192):\n                    size += len(chunk)\n                    if size > MAX_RESPONSE_BYTES:\n                        raise DeliveryError("response exceeds limit", False)\n                if 200 <= response.status_code < 300:\n                    return {"sent": True, "status_code": response.status_code}\n', '                size = 0\n                response_chunks = []\n                for chunk in response.iter_content(8192):\n                    size += len(chunk)\n                    if size > MAX_RESPONSE_BYTES:\n                        raise DeliveryError("response exceeds limit", False)\n                    if chunk:\n                        response_chunks.append(chunk)\n                if 200 <= response.status_code < 300:\n                    acknowledgement = validate_persistence_acknowledgement(\n                        b"".join(response_chunks), prepared\n                    )\n                    return {\n                        "sent": True,\n                        "status_code": response.status_code,\n                        "persistence_status": acknowledgement["status"],\n                    }\n')
(ROOT / "scripts" / "canonical_event_client.py.reference").write_text(
    event_client.read_text(encoding="utf-8"), encoding="utf-8"
)

worker = ROOT / "scripts" / "vicidial_sync.py"
text = worker.read_text(encoding="utf-8")
function_start = text.index("def process_pending_events(sync_conn, config):\n")
cursor_start = text.index("    with sync_conn.cursor() as cur:\n", function_start)
if 'def production_backlog_count(sync_conn):\n    """Count the complete eligible production queue before selecting a batch."""\n    with sync_conn.cursor() as cur:\n        cur.execute(\n            """\n            SELECT COUNT(*) AS backlog_count\n            FROM event_queue\n            WHERE status IN (\'pending\',\'retrying\')\n              AND retry_count < max_retries\n              AND event_type=%s\n              AND source_table IN (%s,%s)\n            """,\n            (PRODUCTION_EVENT_TYPE,) + PRODUCTION_SOURCE_TABLES,\n        )\n        row = cur.fetchone()\n    if not row or "backlog_count" not in row:\n        raise RuntimeError("production backlog count is unavailable")\n    try:\n        count = int(row["backlog_count"])\n    except (TypeError, ValueError):\n        raise RuntimeError("production backlog count is invalid")\n    if count < 0:\n        raise RuntimeError("production backlog count is invalid")\n    return count' not in text:
    text = text[:function_start] + '\n\ndef production_backlog_count(sync_conn):\n    """Count the complete eligible production queue before selecting a batch."""\n    with sync_conn.cursor() as cur:\n        cur.execute(\n            """\n            SELECT COUNT(*) AS backlog_count\n            FROM event_queue\n            WHERE status IN (\'pending\',\'retrying\')\n              AND retry_count < max_retries\n              AND event_type=%s\n              AND source_table IN (%s,%s)\n            """,\n            (PRODUCTION_EVENT_TYPE,) + PRODUCTION_SOURCE_TABLES,\n        )\n        row = cur.fetchone()\n    if not row or "backlog_count" not in row:\n        raise RuntimeError("production backlog count is unavailable")\n    try:\n        count = int(row["backlog_count"])\n    except (TypeError, ValueError):\n        raise RuntimeError("production backlog count is invalid")\n    if count < 0:\n        raise RuntimeError("production backlog count is invalid")\n    return count\n\n\n' + text[function_start:]
    function_start = text.index("def process_pending_events(sync_conn, config):\n")
    cursor_start = text.index("    with sync_conn.cursor() as cur:\n", function_start)
guard = (
    "    backlog_count = production_backlog_count(sync_conn)\n"
    "    if backlog_count > PRODUCTION_BACKLOG_LIMIT:\n"
    "        raise RuntimeError(\"production backlog exceeds hard limit\")\n"
)
text = text[:cursor_start] + guard + text[cursor_start:]
old_len_guard = (
    "    if len(events) > PRODUCTION_BACKLOG_LIMIT:\n"
    "        raise RuntimeError(\"production backlog exceeds hard limit\")\n\n"
)
if text.count(old_len_guard) != 1:
    raise SystemExit("vicidial_sync.py: expected old bounded-batch backlog guard once")
text = text.replace(old_len_guard, "", 1)
worker.write_text(text, encoding="utf-8")
(ROOT / "scripts" / "vicidial_sync.py.candidate").write_text(
    text, encoding="utf-8"
)

repository = ROOT / "vicidial" / "src" / "codestra_vicidial" / "repository.py"
replace_once(repository, '    def __init__(self, connection):\n        self.db = connection\n\n    def _one(self, sql: str, args: tuple = ()) -> dict | None:\n', '    def __init__(self, connection):\n        self.db = connection\n\n    def readiness(self) -> tuple[bool, str | None]:\n        """Probe and reconnect the exact connection used for requests."""\n        try:\n            ping = getattr(self.db, "ping", None)\n            if not callable(ping):\n                return False, None\n            try:\n                ping(reconnect=True)\n            except TypeError:\n                ping()\n            with self.db.cursor() as cursor:\n                cursor.execute("SELECT DATABASE() AS db_name, 1 AS ok")\n                row = cursor.fetchone()\n                if (\n                    not row\n                    or row.get("db_name") != "asterisk"\n                    or int(row.get("ok", 0)) != 1\n                ):\n                    return False, str(row.get("db_name")) if row else None\n                cursor.execute(\n                    "SELECT COUNT(*) AS table_count FROM information_schema.tables "\n                    "WHERE table_schema=%s AND table_name IN "\n                    "(\'vicidial_campaigns\',\'vicidial_users\',\'vicidial_list\',\'vicidial_callbacks\')",\n                    ("asterisk",),\n                )\n                row = cursor.fetchone()\n                if not row or int(row.get("table_count", 0)) != 4:\n                    return False, "asterisk"\n            return True, "asterisk"\n        except Exception:\n            return False, None\n\n    def _one(self, sql: str, args: tuple = ()) -> dict | None:\n')

app_path = ROOT / "vicidial" / "src" / "codestra_vicidial" / "app.py"
replace_once(app_path, '    app = FastAPI(\n        title="Codestra restricted VICIdial adapter",\n        version=__version__,\n    )\n\n', '    app = FastAPI(\n        title="Codestra restricted VICIdial adapter",\n        version=__version__,\n    )\n    app.state.adapter_service = service\n\n')

production_app = ROOT / "vicidial" / "src" / "codestra_vicidial" / "production_app.py"
text = production_app.read_text(encoding="utf-8")
start = text.index("def _mysql_ready() -> tuple[bool, str | None]:\n")
end = text.index("\n\ndef _repository_ready", start)
text = text[:start] + 'def _mysql_ready() -> tuple[bool, str | None]:\n    """Exercise the repository connection used by operational requests."""\n    try:\n        service = getattr(app.state, "adapter_service", None)\n        repository = getattr(service, "repository", None)\n        readiness = getattr(repository, "readiness", None)\n        if not callable(readiness):\n            return False, None\n        ok, database = readiness()\n        return bool(ok), database\n    except Exception:\n        return False, None\n' + text[end:]
if text.count("import json\n") != 1:
    raise SystemExit("production_app.py: expected json import once")
text = text.replace("import json\n", "", 1)
production_app.write_text(text, encoding="utf-8")

test_worker = ROOT / "tests" / "test_production_vicidial_sync.py"
text = test_worker.read_text(encoding="utf-8")
start = text.index("def test_production_pending_selector_is_explicit_allowlist():\n")
end = text.index("\n\ndef test_identical_timestamp_uses_stable_tie_breaker", start)
text = text[:start] + 'def test_production_pending_selector_is_explicit_allowlist():\n    count = Cursor([{"backlog_count": 0}])\n    select = Cursor([])\n    conn = Connection([count, select])\n    worker.process_pending_events(\n        conn,\n        {"batch_size": 10, "send_events": True, "delivery_kill_switch": ""},\n    )\n    count_sql, count_params = count.sql[0]\n    select_sql, select_params = select.sql[0]\n    for sql in (count_sql, select_sql):\n        assert "event_type=%s" in sql\n        assert "source_table IN (%s,%s)" in sql\n    assert count_params == (\n        "call_end",\n        "production_vicidial_log",\n        "production_vicidial_closer_log",\n    )\n    assert select_params[:3] == count_params\n\n\ndef test_full_production_backlog_is_enforced_before_batch_selection():\n    count = Cursor([{"backlog_count": worker.PRODUCTION_BACKLOG_LIMIT + 1}])\n    conn = Connection([count])\n    with pytest.raises(RuntimeError, match="backlog exceeds hard limit"):\n        worker.process_pending_events(\n            conn,\n            {"batch_size": 10, "send_events": True, "delivery_kill_switch": ""},\n        )\n    assert len(count.sql) == 1\n' + text[end:]
test_worker.write_text(text, encoding="utf-8")
replace_once(test_worker, 'class Response:\n    def __init__(self, status):\n        self.status_code = status\n\n    def iter_content(self, _):\n        return iter(())\n\n\nclass Session:\n', 'class Response:\n    def __init__(self, status, body=b""):\n        self.status_code = status\n        self.body = body\n\n    def iter_content(self, _):\n        return iter((self.body,)) if self.body else iter(())\n\n\nclass Session:\n')
replace_once(test_worker, '        if isinstance(outcome, Exception):\n            raise outcome\n        return Response(outcome)\n', '        if isinstance(outcome, Exception):\n            raise outcome\n        if isinstance(outcome, Response):\n            return outcome\n        return Response(outcome)\n')
insert_once(test_worker, '\n\n@pytest.mark.parametrize("status", [200, 201, 202, 204])\n', '\n\ndef durable_ack(prepared_event, **changes):\n    value = {\n        "persisted": True,\n        "status": "persisted",\n        "event_id": prepared_event.event_id,\n        "idempotency_key": prepared_event.idempotency_key,\n    }\n    value.update(changes)\n    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")\n\n\n')
replace_once(test_worker, '@pytest.mark.parametrize("status", [200, 201, 202, 204])\ndef test_http_success_codes(status):\n    transport, value = prepared(Session([status]))\n    assert transport.deliver(value)["sent"] is True\n', '@pytest.mark.parametrize("status", [200, 201, 202])\ndef test_http_success_requires_durable_persistence_acknowledgement(status):\n    session = Session([])\n    transport, value = prepared(session)\n    session.outcomes = iter([Response(status, durable_ack(value))])\n    result = transport.deliver(value)\n    assert result["sent"] is True\n    assert result["persistence_status"] == "persisted"\n\n\n@pytest.mark.parametrize(\n    "response",\n    [\n        Response(204),\n        Response(202),\n        Response(202, b"{}"),\n    ],\n)\ndef test_success_without_durable_acknowledgement_is_rejected(response):\n    transport, value = prepared(Session([response]))\n    with pytest.raises(client.DeliveryError, match="persistence"):\n        transport.deliver(value)\n\n\n@pytest.mark.parametrize(\n    "changes",\n    [\n        {"persisted": False},\n        {"status": "accepted"},\n        {"event_id": "00000000-0000-4000-8000-000000000099"},\n        {"idempotency_key": "vicidial:wrong"},\n    ],\n)\ndef test_mismatched_or_non_durable_acknowledgement_is_rejected(changes):\n    session = Session([])\n    transport, value = prepared(session)\n    session.outcomes = iter([Response(202, durable_ack(value, **changes))])\n    with pytest.raises(client.DeliveryError, match="persistence|identity|idempotency"):\n        transport.deliver(value)\n')
replace_once(test_worker, 'def test_retryable_http_failures_have_bounded_backoff(status):\n    session = Session([status, status, 202])\n    transport, value = prepared(session, attempts=3)\n    assert transport.deliver(value)["sent"] is True\n    assert session.calls == 3\n', 'def test_retryable_http_failures_have_bounded_backoff(status):\n    session = Session([])\n    transport, value = prepared(session, attempts=3)\n    session.outcomes = iter([status, status, Response(202, durable_ack(value))])\n    assert transport.deliver(value)["sent"] is True\n    assert session.calls == 3\n')

readiness_tests = ROOT / "vicidial" / "tests" / "test_production_readiness.py"
text = readiness_tests.read_text(encoding="utf-8")
if "test_mysql_readiness_reconnects_and_probes_operational_repository" not in text:
    readiness_tests.write_text(text.rstrip() + '\n\ndef test_mysql_readiness_reconnects_and_probes_operational_repository(\n    monkeypatch, tmp_path\n):\n    module = load_production_app(monkeypatch, tmp_path)\n    from types import SimpleNamespace\n\n    from codestra_vicidial.repository import MySQLRepository\n\n    class ReadyCursor:\n        def __init__(self, rows):\n            self.rows = iter(rows)\n            self.executed = []\n\n        def execute(self, sql, params=()):\n            self.executed.append((sql, params))\n\n        def fetchone(self):\n            return next(self.rows)\n\n        def __enter__(self):\n            return self\n\n        def __exit__(self, *_):\n            return False\n\n    class ReadyConnection:\n        def __init__(self):\n            self.ping_calls = []\n            self.cursor_value = ReadyCursor(\n                [\n                    {"db_name": "asterisk", "ok": 1},\n                    {"table_count": 4},\n                ]\n            )\n\n        def ping(self, reconnect=False):\n            self.ping_calls.append(reconnect)\n\n        def cursor(self):\n            return self.cursor_value\n\n    connection = ReadyConnection()\n    repository = MySQLRepository(connection)\n    module.app.state.adapter_service = SimpleNamespace(repository=repository)\n    monkeypatch.setenv("CODESTRA_ADAPTER_BACKEND", "mysql")\n\n    status, body = module.build_readiness()\n\n    assert status == 200\n    assert body["checks"]["repository"] is True\n    assert body["backend"] == "mysql"\n    assert connection.ping_calls == [True]\n\n\ndef test_mysql_readiness_fails_when_operational_connection_cannot_reconnect(\n    monkeypatch, tmp_path\n):\n    module = load_production_app(monkeypatch, tmp_path)\n    from types import SimpleNamespace\n\n    from codestra_vicidial.repository import MySQLRepository\n\n    class DeadConnection:\n        def ping(self, reconnect=False):\n            raise RuntimeError("stale connection")\n\n    module.app.state.adapter_service = SimpleNamespace(\n        repository=MySQLRepository(DeadConnection())\n    )\n    monkeypatch.setenv("CODESTRA_ADAPTER_BACKEND", "mysql")\n\n    status, body = module.build_readiness()\n\n    assert status == 503\n    assert body["checks"]["repository"] is False\n' + "\n", encoding="utf-8")

for path in (
    event_client,
    ROOT / "scripts" / "canonical_event_client.py.reference",
    worker,
    ROOT / "scripts" / "vicidial_sync.py.candidate",
    repository,
    app_path,
    production_app,
    test_worker,
    readiness_tests,
):
    data = path.read_text(encoding="utf-8")
    compile(data, str(path), "exec")

print("PR2_RUNTIME_SAFETY_PATCH=PASS")
