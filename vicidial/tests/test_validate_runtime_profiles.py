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
    policy = tmp_path / "reconciliation-policy.json"
    policy.write_text(json.dumps({
        "protected_sha": "a" * 40,
        "plan_sha256": hashlib.sha256(b"{}").hexdigest(),
        "authorization_reference": "CHG-20260821-CAMPAIGN-RECONCILIATION",
        "backup": {
            "reference": "backup/20260822T120000Z",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "checksums": "PASS", "validation": "PASS",
        },
        "campaigns": campaigns,
    }))
    policy.chmod(0o600)
    return {
        **os.environ,
        "CODESTRA_ADAPTER_DEPLOYMENT": deployment,
        "CODESTRA_ADAPTER_BACKEND": "mysql" if deployment == "production-reconciliation" else "memory",
        "ENABLE_VICIDIAL_WRITES": "true" if deployment == "production-reconciliation" else "false",
        "ENABLE_PROVISIONING": "true" if deployment == "production-reconciliation" else "false",
        "OWNER_FULL_AUTONOMY": "YES" if deployment == "production-reconciliation" else "NO",
        "ENABLE_AGENT_SYNC": "false", "ENABLE_LEAD_PUBLICATION": "false",
        "ENABLE_IVR_CALLBACKS": "false", "ENABLE_WARM_TRANSFER": "false",
        "ENABLE_PRODUCTION_DIALING": "false",
        "AUTHORIZED_APPLICATION_SERVER_IP": "65.109.65.169",
        "AUTHORIZED_PRIVATE_SOURCE_IP": "10.40.0.1",
        "MIDDLEWARE_BASE_URL": "https://api.codestra.co",
        "KEYCLOAK_ISSUER_URL": "https://auth.codestra.co/realms/codestra",
        "CODESTRA_ADAPTER_HMAC_FILE": str(secret),
        "CODESTRA_RECONCILIATION_POLICY_FILE": str(policy),
    }


def run(env):
    return subprocess.run([sys.executable, "vicidial/scripts/validate_runtime.py"], env=env, capture_output=True)


def test_production_reconciliation_profile_is_bounded(tmp_path):
    env = environment(tmp_path, "production-reconciliation")
    # The production validator intentionally requires a root-owned policy.
    # CI runs unprivileged; construction/semantic success is covered by the
    # reconciliation-policy unit suite, while this subprocess must fail closed
    # on ownership when it cannot create the production ownership boundary.
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
    document["backup"]["checksums"] = "FAIL"
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
