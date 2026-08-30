import importlib
import json
import sys


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
    ):
        monkeypatch.delenv(name, raising=False)
    for name in ("codestra_vicidial.production_app", "codestra_vicidial.app"):
        sys.modules.pop(name, None)
    return importlib.import_module("codestra_vicidial.production_app")


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
