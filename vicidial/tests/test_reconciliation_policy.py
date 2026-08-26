from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from codestra_vicidial.models import CampaignCommand, ReconciliationCampaignCommand
from codestra_vicidial.reconciliation import (
    ReconciliationDenied, ReconciliationPolicy, canonical_sha256,
)
from test_adapter import campaign_payload


OWNER_REFERENCE = "CHG-20260821-CAMPAIGN-RECONCILIATION"
BACKUP = "backup/20260822T120000Z"


def command() -> ReconciliationCampaignCommand:
    payload = campaign_payload()
    normalized = CampaignCommand.model_validate(payload)
    campaign_hash = canonical_sha256(normalized.campaign.model_dump(mode="json"))
    campaigns = {
        payload["campaign"]["canonical_id"]: {
            "actual_id": payload["campaign"]["actual_id"],
            "campaign_sha256": campaign_hash,
        }
    }
    payload.update({
        "authorization_reference": OWNER_REFERENCE,
        "backup_reference": BACKUP,
        "plan_sha256": canonical_sha256(campaigns),
    })
    return ReconciliationCampaignCommand.model_validate(payload)


def document(
    cmd: ReconciliationCampaignCommand, backup: Path, *,
    age_hours: int = 0, protected_sha: str = "a" * 40,
) -> dict:
    campaigns = {
        cmd.campaign.canonical_id: {
            "actual_id": cmd.campaign.actual_id,
            "campaign_sha256": canonical_sha256(cmd.campaign.model_dump(mode="json")),
        }
    }
    return {
        "protected_sha": protected_sha,
        "plan_sha256": canonical_sha256(campaigns),
        "authorization_reference": OWNER_REFERENCE,
        "backup": {
            "reference": BACKUP, "artifact": str(backup),
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat(),
            "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
            "validation": "PASS",
        },
        "campaigns": campaigns,
    }


def make_backup(tmp_path: Path) -> Path:
    backup = tmp_path / "vicidial-backup.tar"
    backup.write_bytes(b"synthetic backup evidence")
    return backup


def test_exact_request_is_authorized(tmp_path: Path):
    cmd = command()
    ReconciliationPolicy(
        document(cmd, make_backup(tmp_path)), expected_release_sha="a" * 40
    ).authorize(cmd)


@pytest.mark.parametrize("field,value", [
    ("authorization_reference", "CHG-WRONG-AUTHORIZATION"),
    ("backup_reference", "backup/wrong-reference"),
    ("plan_sha256", "0" * 64),
])
def test_request_binding_mismatch_is_denied(field, value, tmp_path: Path):
    cmd = command()
    policy = ReconciliationPolicy(
        document(cmd, make_backup(tmp_path)), expected_release_sha="a" * 40
    )
    changed = cmd.model_copy(update={field: value})
    with pytest.raises(ReconciliationDenied):
        policy.authorize(changed)


def test_campaign_not_in_exact_plan_is_denied(tmp_path: Path):
    cmd = command()
    policy = ReconciliationPolicy(
        document(cmd, make_backup(tmp_path)), expected_release_sha="a" * 40
    )
    changed_campaign = cmd.campaign.model_copy(update={"name": "unexpected mutation"})
    with pytest.raises(ReconciliationDenied):
        policy.authorize(cmd.model_copy(update={"campaign": changed_campaign}))


def test_stale_or_unverified_backup_is_denied(tmp_path: Path):
    cmd = command()
    backup = make_backup(tmp_path)
    with pytest.raises(ReconciliationDenied, match="stale"):
        ReconciliationPolicy(
            document(cmd, backup, age_hours=25), expected_release_sha="a" * 40
        )
    unverified = document(cmd, backup)
    unverified["backup"]["validation"] = "FAIL"
    with pytest.raises(ReconciliationDenied, match="backup"):
        ReconciliationPolicy(unverified, expected_release_sha="a" * 40)


def test_policy_release_sha_and_backup_checksum_are_bound(tmp_path: Path):
    cmd = command()
    backup = make_backup(tmp_path)
    with pytest.raises(ReconciliationDenied, match="policy/release SHA mismatch"):
        ReconciliationPolicy(document(cmd, backup), expected_release_sha="b" * 40)
    policy = document(cmd, backup)
    backup.write_bytes(b"changed after approval")
    with pytest.raises(ReconciliationDenied, match="backup"):
        ReconciliationPolicy(policy, expected_release_sha="a" * 40)


@pytest.mark.parametrize("change", ["delete", "replace", "tamper"])
def test_backup_is_revalidated_for_every_authorize(tmp_path: Path, change: str):
    cmd = command()
    backup = make_backup(tmp_path)
    policy = ReconciliationPolicy(
        document(cmd, backup), expected_release_sha="a" * 40
    )
    if change == "delete":
        backup.unlink()
    elif change == "replace":
        replacement = tmp_path / "replacement.tar"
        replacement.write_bytes(b"synthetic backup evidence")
        replacement.replace(backup)
    else:
        backup.write_bytes(b"mutated backup evidence")
    with pytest.raises(ReconciliationDenied, match="backup"):
        policy.authorize(cmd)


def test_backup_symlink_is_never_followed(tmp_path: Path):
    cmd = command()
    target = make_backup(tmp_path)
    link = tmp_path / "backup-link.tar"
    link.symlink_to(target)
    with pytest.raises(ReconciliationDenied, match="backup"):
        ReconciliationPolicy(document(cmd, link), expected_release_sha="a" * 40)
