import argparse
import hashlib
import importlib.util
import json
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
    module.validate(document, "a" * 40)
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
        module.validate(document, "a" * 40)
