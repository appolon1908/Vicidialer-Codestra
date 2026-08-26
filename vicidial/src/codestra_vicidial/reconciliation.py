"""Request-bound authorization for the temporary production reconciliation profile."""
from __future__ import annotations

import hashlib
import json
import os
import stat
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
            or len(expected_checksum) != 64
            or backup["validation"] != "PASS"
        ):
            raise ReconciliationDenied("fresh verified backup evidence required")
        self.backup_fingerprint = self._verify_backup(artifact, expected_checksum)
        self.backup_path = artifact
        self.backup_checksum = expected_checksum
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
        self._verify_backup(
            self.backup_path, self.backup_checksum, self.backup_fingerprint
        )
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

    @staticmethod
    def _verify_backup(
        path: Path,
        expected_checksum: str,
        expected_fingerprint: tuple[int, int, int] | None = None,
    ) -> tuple[int, int, int]:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ReconciliationDenied("fresh verified backup evidence required") from exc
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise ReconciliationDenied("backup artifact is not a regular file")
            fingerprint = (before.st_dev, before.st_ino, before.st_size)
            if expected_fingerprint is not None and fingerprint != expected_fingerprint:
                raise ReconciliationDenied("backup artifact was replaced or resized")
            digest = hashlib.sha256()
            while chunk := os.read(fd, 1024 * 1024):
                digest.update(chunk)
            after = os.fstat(fd)
            if (after.st_dev, after.st_ino, after.st_size) != fingerprint:
                raise ReconciliationDenied("backup artifact mutated during verification")
            if digest.hexdigest() != expected_checksum:
                raise ReconciliationDenied("backup artifact checksum mismatch")
            return fingerprint
        finally:
            os.close(fd)
