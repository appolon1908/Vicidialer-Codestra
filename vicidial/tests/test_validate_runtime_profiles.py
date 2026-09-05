import os
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def environment(tmp_path, deployment):
    secret = tmp_path / "hmac"
    secret.write_bytes(b"x" * 32)
    secret.chmod(0o600)
    campaigns = {}
    backup = tmp_path / "backup.tar"
    backup.write_bytes(b"synthetic backup evidence")
    source_sha = "a" * 40
    build_info = tmp_path / "BUILD_INFO.json"
    build_info.write_text(json.dumps({
        "application": "codestra-vicidial-restricted-adapter",
        "version": "0.2.0rc1", "release": "R3",
        "git_sha": source_sha, "build_id": "vicidial-r3-" + source_sha[:12],
        "built_at": "2026-08-25T20:00:00Z",
        "environment": "production-candidate", "python": "3.11",
    }))
    (tmp_path / "SOURCE.COMMIT").write_text(source_sha + "\n")
    policy = tmp_path / "reconciliation-policy.json"
    policy.write_text(json.dumps({
        "protected_sha": source_sha,
        "plan_sha256": hashlib.sha256(b"{}").hexdigest(),
        "authorization_reference": "CHG-20260821-CAMPAIGN-RECONCILIATION",
        "backup": {
            "reference": "backup/20260822T120000Z", "artifact": str(backup),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
            "validation": "PASS",
        },
        "campaigns": campaigns,
    }))
    policy.chmod(0o600)
    db_secret = tmp_path / "vicidial-db.json"
    db_secret.write_text(json.dumps({
        "host": "127.0.0.1",
        "user": "codestra_adapter",
        "password": "synthetic-test-only",
        "database": "asterisk",
    }))
    db_secret.chmod(0o600)
    call_policy = tmp_path / "internal-call-policy.json"
    call_policy.write_text(json.dumps({
        "schema_version": "1.0", "release_sha": source_sha,
        "authorization_reference": "CHG-APPOLON-INTERNAL-0001",
        "expires_at": "2099-09-05T20:00:00Z", "tenant_id": "tenant-test",
        "subject": "subject-appolon", "employee_id": "employee-appolon",
        "username": "appolon", "extension": "6901", "campaign": "TEST_SYN",
        "destination_alias": "internal:TEST_ECHO", "destination_extension": "6000",
        "dialplan_context": "codestra-internal-test", "internal_only": True,
        "external_dialing": False, "recording": False, "max_calls": 1,
        "route_sha256": "a" * 64,
    }))
    call_policy.chmod(0o600)
    lifecycle = tmp_path / "ami-events.sqlite3"
    from codestra_vicidial.ami_gateway import LifecycleStore
    LifecycleStore(lifecycle)
    ami_credential = tmp_path / "ami.json"
    ami_credential.write_text(json.dumps({
        "host": "127.0.0.1", "port": 5038, "username": "synthetic",
        "secret": "synthetic-private-value",
    }))
    ami_credential.chmod(0o600)
    mysql_profile = deployment in {
        "production-candidate", "production-reconciliation", "production-internal-test",
    }
    env = {
        **os.environ,
        "CODESTRA_ADAPTER_DEPLOYMENT": deployment,
        "CODESTRA_ADAPTER_BACKEND": "mysql" if mysql_profile else "memory",
        "ENABLE_VICIDIAL_WRITES": "true" if deployment == "production-reconciliation" else "false",
        "ENABLE_PROVISIONING": "true" if deployment == "production-reconciliation" else "false",
        "OWNER_FULL_AUTONOMY": "YES" if deployment == "production-reconciliation" else "NO",
        "ENABLE_AGENT_SYNC": "false",
        "ENABLE_LEAD_PUBLICATION": "false",
        "ENABLE_LIVE_CALL_CONTROL": "false",
        "ENABLE_WARM_TRANSFER": "false",
        "ENABLE_IVR_ROUTING": "false",
        "ENABLE_IVR_CALLBACKS": "false",
        "ENABLE_PRODUCTION_DIALING": "false",
        "ENABLE_SMS_DELIVERY": "false",
        "ENABLE_EMAIL_DELIVERY": "false",
        "ENABLE_EXTERNAL_DELIVERY": "false",
        "ENABLE_AI_ACTIONS": "false",
        "AUTHORIZED_APPLICATION_SERVER_IP": "65.109.65.169",
        "AUTHORIZED_PRIVATE_SOURCE_IP": "10.40.0.1",
        "MIDDLEWARE_BASE_URL": "https://api.codestra.co",
        "KEYCLOAK_ISSUER_URL": "https://auth.codestra.co/realms/codestra",
        "CODESTRA_ADAPTER_HMAC_FILE": str(secret),
        "CODESTRA_VICIDIAL_DB_FILE": str(db_secret),
        "CODESTRA_RECONCILIATION_POLICY_FILE": str(policy),
        "CODESTRA_INTERNAL_CALL_POLICY_FILE": str(call_policy),
        "CODESTRA_AMI_LIFECYCLE_STATE": str(lifecycle),
        "CODESTRA_AMI_CREDENTIAL_FILE": str(ami_credential),
        "ENABLE_INTERNAL_TEST_CALLING": (
            "true" if deployment == "production-internal-test" else "false"
        ),
        "CODESTRA_RELEASE_METADATA_REQUIRED": (
            "true" if deployment in {
                "production-reconciliation", "production-internal-test",
            } else "false"
        ),
        "CODESTRA_BUILD_INFO_FILE": str(build_info),
    }
    for legacy in (
        "ENABLE_CALLBACK_WRITES",
        "ENABLE_TRANSFER_POLICY_SYNC",
        "ENABLE_PSTN_DIALING",
    ):
        env.pop(legacy, None)
    return env


def run(env):
    script = Path(__file__).resolve().parents[1] / "scripts" / "validate_runtime.py"
    return subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=True,
    )


def test_production_reconciliation_profile_is_bounded(tmp_path):
    env = environment(tmp_path, "production-reconciliation")
    result = run(env)
    if os.getuid() == 0:
        assert result.returncode == 0
    else:
        assert result.returncode != 0
        assert b"ownership or mode unsafe" in result.stderr
    env["ENABLE_AGENT_SYNC"] = "true"
    assert run(env).returncode != 0


def test_production_reconciliation_requires_verified_backup_and_exact_plan(tmp_path):
    env = environment(tmp_path, "production-reconciliation")
    policy = env["CODESTRA_RECONCILIATION_POLICY_FILE"]
    document = json.loads(open(policy, encoding="utf-8").read())
    document["backup"]["sha256"] = "0" * 64
    open(policy, "w", encoding="utf-8").write(json.dumps(document))
    assert run(env).returncode != 0

    env = environment(tmp_path, "production-reconciliation")
    policy = env["CODESTRA_RECONCILIATION_POLICY_FILE"]
    document = json.loads(open(policy, encoding="utf-8").read())
    document["plan_sha256"] = "0" * 64
    open(policy, "w", encoding="utf-8").write(json.dumps(document))
    assert run(env).returncode != 0


def test_staging_profile_stays_read_only(tmp_path):
    env = environment(tmp_path, "staging-read-only")
    assert run(env).returncode == 0
    env["ENABLE_VICIDIAL_WRITES"] = "true"
    assert run(env).returncode != 0


def test_production_candidate_requires_mysql_but_keeps_writes_off(tmp_path):
    env = environment(tmp_path, "production-candidate")
    assert run(env).returncode == 0
    env["CODESTRA_ADAPTER_BACKEND"] = "memory"
    assert run(env).returncode != 0


def test_production_internal_test_requires_exact_policy_and_lifecycle(tmp_path):
    env = environment(tmp_path, "production-internal-test")
    result = run(env)
    if os.getuid() != 0:
        assert result.returncode != 0
        assert b"root-owned and protected" in result.stderr
        return
    assert result.returncode == 0
    env["ENABLE_INTERNAL_TEST_CALLING"] = "false"
    assert run(env).returncode != 0


def test_internal_profile_rejects_each_consumed_lifecycle_column_gap(tmp_path):
    for column in (
        "extension", "binding_json", "call_id", "answered", "updated_at",
    ):
        case = tmp_path / column
        case.mkdir()
        env = environment(case, "production-internal-test")
        lifecycle = Path(env["CODESTRA_AMI_LIFECYCLE_STATE"])
        with __import__("sqlite3").connect(lifecycle) as db:
            definitions = {
                "call_key": "TEXT PRIMARY KEY", "linkedid": "TEXT",
                "extension": "TEXT", "call_id": "TEXT", "binding_json": "TEXT",
                "asterisk_uniqueid": "TEXT", "state": "TEXT", "answered": "INTEGER",
                "terminal": "INTEGER", "created_at": "TEXT", "answered_at": "TEXT",
                "ended_at": "TEXT", "updated_at": "TEXT",
            }
            db.execute("DROP TABLE calls")
            schema = ",".join(
                f"{name} {kind}" for name, kind in definitions.items()
                if name != column
            )
            db.execute(f"CREATE TABLE calls ({schema})")
        result = run(env)
        assert result.returncode != 0
        assert b"lifecycle database schema invalid" in result.stderr
    env = environment(tmp_path, "production-internal-test")
    Path(env["CODESTRA_AMI_LIFECYCLE_STATE"]).unlink()
    assert run(env).returncode != 0
    env = environment(tmp_path, "production-internal-test")
    lifecycle = Path(env["CODESTRA_AMI_LIFECYCLE_STATE"])
    lifecycle.unlink()
    lifecycle.touch(mode=0o600)
    assert run(env).returncode != 0
    env = environment(tmp_path, "production-internal-test")
    credential = Path(env["CODESTRA_AMI_CREDENTIAL_FILE"])
    credential.write_text(json.dumps({"host": "127.0.0.1", "port": 5038}))
    assert run(env).returncode != 0
    env = environment(tmp_path, "production-candidate")
    env["ENABLE_PROVISIONING"] = "true"
    assert run(env).returncode != 0


def test_mysql_profiles_reject_remote_database_host(tmp_path):
    env = environment(tmp_path, "production-candidate")
    path = Path(env["CODESTRA_VICIDIAL_DB_FILE"])
    document = json.loads(path.read_text())
    document["host"] = "10.40.0.2"
    path.write_text(json.dumps(document))
    result = run(env)
    assert result.returncode != 0
    assert b"loopback-only" in result.stderr


def test_deprecated_feature_flag_aliases_are_denied(tmp_path):
    for legacy in (
        "ENABLE_CALLBACK_WRITES",
        "ENABLE_TRANSFER_POLICY_SYNC",
        "ENABLE_PSTN_DIALING",
    ):
        env = environment(tmp_path, "staging-read-only")
        env[legacy] = "false"
        result = run(env)
        assert result.returncode != 0
        assert b"deprecated feature flag" in result.stderr


def test_deprecated_or_incomplete_keycloak_issuer_is_denied(tmp_path):
    for issuer in ("https://auth.codestra.agency", "https://auth.codestra.co"):
        env = environment(tmp_path, "staging-read-only")
        env["KEYCLOAK_ISSUER_URL"] = issuer
        result = run(env)
        assert result.returncode != 0
        assert b"Keycloak issuer mismatch" in result.stderr


def test_all_deployable_adapter_templates_use_canonical_realm_issuer():
    expected = "KEYCLOAK_ISSUER_URL=https://auth.codestra.co/realms/codestra"
    root = Path(__file__).parents[2]
    templates = (
        root / "vicidial/config/adapter.env.example",
        root / "vicidial/config/vicidial-server.env.example",
        root / "deploy/vicidial-restricted-adapter/adapter.env.example",
    )
    for template in templates:
        lines = template.read_text(encoding="utf-8").splitlines()
        assert [line for line in lines if line.startswith("KEYCLOAK_ISSUER_URL=")] == [expected]
