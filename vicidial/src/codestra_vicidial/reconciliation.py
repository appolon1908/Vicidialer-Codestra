"""Request-bound authorization for the temporary production reconciliation profile."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .models import ReconciliationCampaignCommand


class ReconciliationDenied(PermissionError):
    pass


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


class ReconciliationPolicy:
    """An exact, file-backed plan and fresh-backup authorization boundary."""

    def __init__(
        self, document: dict, *, expected_release_sha: str,
        now: datetime | None = None,
    ):
        required = {
            "protected_sha", "plan_sha256", "authorization_reference",
            "backup", "campaigns",
        }
        if set(document) != required:
            raise ReconciliationDenied("reconciliation policy schema mismatch")
        if document["protected_sha"] != expected_release_sha:
            raise ReconciliationDenied("reconciliation policy/release SHA mismatch")
        if canonical_sha256(document["campaigns"]) != document["plan_sha256"]:
            raise ReconciliationDenied("reconciliation plan digest mismatch")
        backup = document["backup"]
        if set(backup) != {"reference", "created_at", "artifact", "sha256", "validation"}:
            raise ReconciliationDenied("backup evidence schema mismatch")
        artifact = Path(backup["artifact"])
        expected_checksum = backup["sha256"]
        if (
            not artifact.is_absolute()
            or artifact.is_symlink()
            or not artifact.is_file()
            or len(expected_checksum) != 64
            or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected_checksum
            or backup["validation"] != "PASS"
        ):
            raise ReconciliationDenied("fresh verified backup evidence required")
        created = datetime.fromisoformat(backup["created_at"].replace("Z", "+00:00"))
        current = now or datetime.now(timezone.utc)
        if created.tzinfo is None or not 0 <= (current - created).total_seconds() <= 86400:
            raise ReconciliationDenied("backup evidence is stale or future-dated")
        self.document = document
        self.backup_created_at = created

    @classmethod
    def from_file(cls, path: Path, *, expected_release_sha: str) -> "ReconciliationPolicy":
        if path.is_symlink() or not path.is_file():
            raise ReconciliationDenied("reconciliation policy file unavailable")
        stat = path.stat()
        if stat.st_uid != 0 or stat.st_mode & 0o022 or stat.st_mode & 0o007:
            raise ReconciliationDenied("reconciliation policy ownership or mode unsafe")
        return cls(
            json.loads(path.read_text(encoding="utf-8")),
            expected_release_sha=expected_release_sha,
        )

    def authorize(self, command: ReconciliationCampaignCommand) -> None:
        policy = self.document
        age = (datetime.now(timezone.utc) - self.backup_created_at).total_seconds()
        if not 0 <= age <= 86400:
            raise ReconciliationDenied("backup evidence expired before request")
        if command.plan_sha256 != policy["plan_sha256"]:
            raise ReconciliationDenied("request plan digest mismatch")
        if command.authorization_reference != policy["authorization_reference"]:
            raise ReconciliationDenied("request owner authorization mismatch")
        if command.backup_reference != policy["backup"]["reference"]:
            raise ReconciliationDenied("request backup reference mismatch")
        expected = policy["campaigns"].get(command.campaign.canonical_id)
        actual = {
            "actual_id": command.campaign.actual_id,
            "campaign_sha256": canonical_sha256(command.campaign.model_dump(mode="json")),
        }
        if expected != actual:
            raise ReconciliationDenied("campaign is absent from exact approved plan")
