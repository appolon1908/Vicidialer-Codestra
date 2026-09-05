import argparse
import hashlib
import importlib.util
import json
import sys
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from codestra_vicidial.reconciliation import ReconciliationDenied

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "vicidial/scripts/agent_provisioning_policy.py"
SCHEMA = ROOT / "vicidial/config/agent-provisioning-policy.schema.json"
spec = importlib.util.spec_from_file_location("agent_policy_tool", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

AGENT = {
    "user_id": "SYN1001", "full_name": "Synthetic Agent", "user_group": "SYNTHETIC",
    "campaigns": ["SYN0001"], "inbound_groups": [], "active": False,
    "adopt_existing_sha256": None,
}


def generated(tmp_path):
    backup = tmp_path / "synthetic-backup.enc"
    backup.write_bytes(b"synthetic backup only")
    agent = tmp_path / "agent.json"
    agent.write_text(json.dumps(AGENT), encoding="utf-8")
    now = datetime.now(UTC)
    args = argparse.Namespace(
        backup_artifact=backup, agent_json=agent, release_sha="a" * 40,
        runtime_backup_artifact="/run/provisioning-backup/synthetic-backup.enc",
        authorization_reference="CHG-SYNTHETIC-00001",
        expires_at=(now + timedelta(hours=1)).isoformat(),
        backup_reference="synthetic/backup-0001",
        backup_created_at=now.isoformat(),
    )
    return module.build(args), backup


def test_schema_is_canonical_and_strict():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert schema["properties"]["backup"]["additionalProperties"] is False
    assert schema["properties"]["agent"]["additionalProperties"] is False


def test_generator_constructs_runtime_valid_policy(tmp_path):
    document, backup = generated(tmp_path)
    module.validate(document, "a" * 40, backup_artifact_source=backup)
    assert document["backup"]["sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
    assert document["agent"] == AGENT


@pytest.mark.parametrize("mutation", [
    lambda value: value.pop("agent"),
    lambda value: value.update(extra=True),
    lambda value: value.update(protected_sha="b" * 40),
    lambda value: value.update(expires_at="2020-01-01T00:00:00Z"),
    lambda value: value["backup"].update(validation="FAIL"),
    lambda value: value["backup"].update(sha256="0" * 64),
])
def test_validator_rejects_invalid_policy(tmp_path, mutation):
    document, _ = generated(tmp_path)
    mutation(document)
    with pytest.raises((ReconciliationDenied, ValueError, TypeError)):
        module.validate(document, "a" * 40, backup_artifact_source=tmp_path / "synthetic-backup.enc")


@pytest.mark.parametrize("creation_umask", [0o022, 0o077])
def test_cli_writes_runtime_path_with_private_mode(tmp_path, creation_umask):
    document, backup = generated(tmp_path)
    output = tmp_path / "policy.json"
    agent = tmp_path / "agent.json"
    agent.write_text(json.dumps(AGENT), encoding="utf-8")
    import subprocess
    command = [
        sys.executable, str(SCRIPT), "generate", "--agent-json", str(agent),
        "--release-sha", document["protected_sha"], "--authorization-reference",
        document["authorization_reference"], "--expires-at", document["expires_at"],
        "--backup-reference", document["backup"]["reference"], "--backup-created-at",
        document["backup"]["created_at"], "--backup-artifact", str(backup),
        "--runtime-backup-artifact", "/run/provisioning-backup/synthetic-backup.enc",
        "--runtime-gid", str(os.getgid()),
        "--output", str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, umask=creation_umask)
    assert completed.returncode == 0, completed.stderr
    assert output.stat().st_mode & 0o777 == 0o640
    assert output.stat().st_gid == os.getgid()
    assert json.loads(output.read_text())["backup"]["artifact"].startswith("/run/provisioning-backup/")
