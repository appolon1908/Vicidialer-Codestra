from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def document(cmd: ReconciliationCampaignCommand, *, age_hours: int = 0) -> dict:
    campaigns = {
        cmd.campaign.canonical_id: {
            "actual_id": cmd.campaign.actual_id,
            "campaign_sha256": canonical_sha256(cmd.campaign.model_dump(mode="json")),
        }
    }
    return {
        "protected_sha": "a" * 40,
        "plan_sha256": canonical_sha256(campaigns),
        "authorization_reference": OWNER_REFERENCE,
        "backup": {
            "reference": BACKUP,
            "created_at": (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat(),
            "checksums": "PASS", "validation": "PASS",
        },
        "campaigns": campaigns,
    }


def test_exact_request_is_authorized():
    cmd = command()
    ReconciliationPolicy(document(cmd)).authorize(cmd)


@pytest.mark.parametrize("field,value", [
    ("authorization_reference", "CHG-WRONG-AUTHORIZATION"),
    ("backup_reference", "backup/wrong-reference"),
    ("plan_sha256", "0" * 64),
])
def test_request_binding_mismatch_is_denied(field, value):
    cmd = command()
    policy = ReconciliationPolicy(document(cmd))
    changed = cmd.model_copy(update={field: value})
    with pytest.raises(ReconciliationDenied):
        policy.authorize(changed)


def test_campaign_not_in_exact_plan_is_denied():
    cmd = command()
    policy = ReconciliationPolicy(document(cmd))
    changed_campaign = cmd.campaign.model_copy(update={"name": "unexpected mutation"})
    with pytest.raises(ReconciliationDenied):
        policy.authorize(cmd.model_copy(update={"campaign": changed_campaign}))


def test_stale_or_unverified_backup_is_denied():
    cmd = command()
    with pytest.raises(ReconciliationDenied, match="stale"):
        ReconciliationPolicy(document(cmd, age_hours=25))
    unverified = document(cmd)
    unverified["backup"]["validation"] = "FAIL"
    with pytest.raises(ReconciliationDenied, match="verified backup"):
        ReconciliationPolicy(unverified)
