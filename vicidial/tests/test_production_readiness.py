import importlib
import json
import sys

import pytest


def load_production_app(monkeypatch, tmp_path):
    state = tmp_path / "state.sqlite3"
    monkeypatch.setenv("CODESTRA_ADAPTER_DEPLOYMENT", "development-loopback")
    monkeypatch.setenv("CODESTRA_ADAPTER_BACKEND", "memory")
    monkeypatch.setenv("CODESTRA_ADAPTER_STATE", str(state))
    monkeypatch.setenv("CODESTRA_RELEASE_METADATA_REQUIRED", "false")
    for name in (
        "ENABLE_VICIDIAL_WRITES",
        "ENABLE_PROVISIONING",
        "ENABLE_AGENT_SYNC",
        "ENABLE_LEAD_PUBLICATION",
        "ENABLE_LIVE_CALL_CONTROL",
        "ENABLE_WARM_TRANSFER",
        "ENABLE_IVR_ROUTING",
        "ENABLE_IVR_CALLBACKS",
        "ENABLE_PRODUCTION_DIALING",
        "ENABLE_SMS_DELIVERY",
        "ENABLE_EMAIL_DELIVERY",
        "ENABLE_EXTERNAL_DELIVERY",
        "ENABLE_AI_ACTIONS",
        "ENABLE_CALLBACK_WRITES",
        "ENABLE_TRANSFER_POLICY_SYNC",
        "ENABLE_PSTN_DIALING",
        "ENABLE_INTERNAL_TEST_CALLING",
    ):
        monkeypatch.delenv(name, raising=False)
    for name in ("codestra_vicidial.production_app", "codestra_vicidial.app"):
        sys.modules.pop(name, None)
    return importlib.import_module("codestra_vicidial.production_app")


def configure_release_profile(
    monkeypatch, tmp_path, profile, environment="production-candidate"
):
    from codestra_vicidial.release import build_release_document

    sha = "a" * 40
    metadata = tmp_path / "BUILD_INFO.json"
    metadata.write_text(json.dumps(build_release_document(
        git_sha=sha, built_at="2026-09-10T00:00:00Z",
        environment=environment,
    )))
    metadata.with_name("SOURCE.COMMIT").write_text(sha + "\n")
    secret = tmp_path / "adapter-hmac"
    secret.write_bytes(b"test-readiness-hmac-secret-32-bytes")
    monkeypatch.setenv("CODESTRA_BUILD_INFO_FILE", str(metadata))
    monkeypatch.setenv("CODESTRA_ADAPTER_HMAC_FILE", str(secret))
    monkeypatch.setenv("CODESTRA_ADAPTER_DEPLOYMENT", profile)
    monkeypatch.setenv("AUTHORIZED_PRIVATE_SOURCE_IP", "10.40.0.1")


@pytest.mark.parametrize("profile,expected_status", [
    ("staging-read-only", 200),
    ("production-candidate", 503),
])
def test_memory_readiness_cannot_certify_production_candidate(
    monkeypatch, tmp_path, profile, expected_status
):
    module = load_production_app(monkeypatch, tmp_path)
    configure_release_profile(monkeypatch, tmp_path, profile)

    status, body = module.build_readiness()

    assert status == expected_status
    assert body["backend"] == "memory"
    assert body["release_verified"] is True
    assert body["checks"]["repository"] is (expected_status == 200)
    assert all(ok for name, ok in body["checks"].items() if name != "repository")
    assert body["writes_enabled"] is False
    assert body["production_dialing"] is False


def test_development_readiness_is_real_and_healthy(monkeypatch, tmp_path):
    module = load_production_app(monkeypatch, tmp_path)
    status, body = module.build_readiness()
    assert status == 200
    assert body["status"] == "ready"
    assert body["checks"]["state_store"] is True
    assert body["checks"]["repository"] is True
    assert body["production_dialing"] is False
    assert body["customer_traffic_authorized"] is False


def test_any_live_capability_fails_readiness(monkeypatch, tmp_path):
    module = load_production_app(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_PRODUCTION_DIALING", "true")
    status, body = module.build_readiness()
    assert status == 503
    assert body["checks"]["kill_switches"] is False


def test_internal_calling_requires_exact_profile_and_loaded_policy(monkeypatch, tmp_path):
    module = load_production_app(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_INTERNAL_TEST_CALLING", "true")
    module.app.state.internal_call_executor = object()
    assert module._kill_switch_ready("development-loopback", False) is False
    assert module._kill_switch_ready("production-internal-test", False) is True
    module.app.state.internal_call_executor = None
    assert module._kill_switch_ready("production-internal-test", False) is False


def test_deprecated_activation_alias_fails_even_when_false(monkeypatch, tmp_path):
    module = load_production_app(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_CALLBACK_WRITES", "false")
    status, body = module.build_readiness()
    assert status == 503
    assert body["checks"]["kill_switches"] is False


def test_non_loopback_database_secret_is_rejected_before_connection(monkeypatch, tmp_path):
    module = load_production_app(monkeypatch, tmp_path)
    secret = tmp_path / "vicidial-db.json"
    secret.write_text(json.dumps({
        "host": "10.40.0.2",
        "user": "adapter",
        "password": "not-used",
        "database": "asterisk",
    }))
    monkeypatch.setenv("CODESTRA_VICIDIAL_DB_FILE", str(secret))
    ok, database = module._mysql_ready()
    assert ok is False
    assert database is None


def test_private_source_policy_is_required_outside_development(monkeypatch, tmp_path):
    module = load_production_app(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTHORIZED_PRIVATE_SOURCE_IP", "8.8.8.8")
    assert module._source_policy_ready("production-candidate") is False
    monkeypatch.setenv("AUTHORIZED_PRIVATE_SOURCE_IP", "10.40.0.1")
    assert module._source_policy_ready("production-candidate") is True

@pytest.mark.parametrize("profile,environment,expected_status", [
    ("development-loopback", "production-candidate", 200),
    ("production-candidate", "production-candidate", 200),
    ("production-candidate", "staging-read-only", 503),
    ("production-candidate", "production", 503),
])
def test_mysql_readiness_reconnects_and_probes_operational_repository(
    monkeypatch, tmp_path, profile, environment, expected_status
):
    module = load_production_app(monkeypatch, tmp_path)
    from codestra_vicidial.repository import MySQLRepository

    class ReadyCursor:
        def __init__(self, rows):
            self.rows = iter(rows)
            self.executed = []

        def execute(self, sql, params=()):
            self.executed.append((sql, params))

        def fetchone(self):
            return next(self.rows)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class ReadyConnection:
        def __init__(self):
            self.ping_calls = []
            self.cursor_value = ReadyCursor(
                [
                    {"db_name": "asterisk", "ok": 1},
                    {"table_count": 4},
                ]
            )

        def ping(self, reconnect=False):
            self.ping_calls.append(reconnect)

        def cursor(self):
            return self.cursor_value

    configure_release_profile(monkeypatch, tmp_path, profile, environment)
    connection = ReadyConnection()
    repository = MySQLRepository(connection)
    module.app.state.adapter_service.repository = repository
    monkeypatch.setenv("CODESTRA_ADAPTER_BACKEND", "mysql")

    status, body = module.build_readiness()

    assert status == expected_status
    assert body["checks"]["release"] is (expected_status == 200)
    assert body["checks"]["repository"] is True
    assert body["backend"] == "mysql"
    assert connection.ping_calls == [True]


@pytest.mark.parametrize("profile", ["development-loopback", "production-candidate"])
def test_mysql_readiness_fails_when_operational_connection_cannot_reconnect(
    monkeypatch, tmp_path, profile
):
    module = load_production_app(monkeypatch, tmp_path)
    from codestra_vicidial.repository import MySQLRepository

    class DeadConnection:
        def ping(self, reconnect=False):
            raise RuntimeError("stale connection")

    configure_release_profile(monkeypatch, tmp_path, profile)
    module.app.state.adapter_service.repository = MySQLRepository(DeadConnection())
    monkeypatch.setenv("CODESTRA_ADAPTER_BACKEND", "mysql")

    status, body = module.build_readiness()

    assert status == 503
    assert body["checks"]["repository"] is False


def test_readiness_rejects_empty_sqlite_file(monkeypatch, tmp_path):
    module = load_production_app(monkeypatch, tmp_path)
    state = module.app.state.adapter_service.state
    state.db.close()
    for suffix in ("", "-wal", "-shm"):
        path = tmp_path / ("state.sqlite3" + suffix)
        path.unlink(missing_ok=True)
    (tmp_path / "state.sqlite3").touch()
    status, body = module.build_readiness()
    assert status == 503
    assert body["checks"]["state_store"] is False
    assert (tmp_path / "state.sqlite3").stat().st_size == 0


def test_readiness_rejects_missing_replay_schema(monkeypatch, tmp_path):
    module = load_production_app(monkeypatch, tmp_path)
    state = module.app.state.adapter_service.state
    state.db.execute("DROP TABLE requests")
    state.db.commit()
    status, body = module.build_readiness()
    assert status == 503
    assert body["checks"]["state_store"] is False


def test_readiness_rejects_incompatible_version_schema(monkeypatch, tmp_path):
    module = load_production_app(monkeypatch, tmp_path)
    state = module.app.state.adapter_service.state
    state.db.execute("ALTER TABLE resource_epochs RENAME COLUMN epoch TO obsolete_epoch")
    state.db.commit()
    assert module.build_readiness()[0] == 503


def test_readiness_rejects_different_configured_state_store(monkeypatch, tmp_path):
    from codestra_vicidial.state import StateStore

    module = load_production_app(monkeypatch, tmp_path)
    other_path = tmp_path / "other.sqlite3"
    StateStore(other_path).db.close()
    monkeypatch.setenv("CODESTRA_ADAPTER_STATE", str(other_path))
    assert module.build_readiness()[0] == 503


def test_readiness_missing_file_is_not_recreated(monkeypatch, tmp_path):
    module = load_production_app(monkeypatch, tmp_path)
    state = module.app.state.adapter_service.state
    state.db.close()
    state.path.unlink()
    assert module.build_readiness()[0] == 503
    assert not state.path.exists()
