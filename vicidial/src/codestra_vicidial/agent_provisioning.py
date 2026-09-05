"""Exact, file-backed authorization for one disabled VICIdial user."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .models import ProvisionAgentCommand
from .reconciliation import ReconciliationDenied, ReconciliationPolicy, canonical_sha256


class AgentProvisioningPolicy:
    def __init__(self, document: dict, *, expected_release_sha: str,
                 now: datetime | None = None):
        required = {"protected_sha", "authorization_reference", "expires_at",
                    "backup", "user_level", "agent"}
        if set(document) != required:
            raise ReconciliationDenied("agent provisioning policy schema mismatch")
        if document["protected_sha"] != expected_release_sha:
            raise ReconciliationDenied("agent provisioning policy/release SHA mismatch")
        current = now or datetime.now(UTC)
        expiry = datetime.fromisoformat(document["expires_at"])
        if expiry.tzinfo is None or expiry <= current:
            raise ReconciliationDenied("agent provisioning grant expired")
        backup = document["backup"]
        if set(backup) != {"reference", "created_at", "artifact", "sha256", "validation"}:
            raise ReconciliationDenied("agent provisioning backup schema mismatch")
        artifact = Path(backup["artifact"])
        created = datetime.fromisoformat(backup["created_at"])
        if (not artifact.is_absolute() or len(backup["sha256"]) != 64
                or backup["validation"] != "PASS" or created.tzinfo is None
                or not 0 <= (current - created).total_seconds() <= 86400):
            raise ReconciliationDenied("fresh verified backup evidence required")
        self.backup_fingerprint = ReconciliationPolicy._verify_backup(
            artifact, backup["sha256"]
        )
        self.backup_path = artifact
        self.backup_checksum = backup["sha256"]
        self.backup_created_at = created
        self.document = document

    @classmethod
    def from_file(cls, path: Path, *, expected_release_sha: str):
        if path.is_symlink() or not path.is_file():
            raise ReconciliationDenied("agent provisioning policy unavailable")
        metadata = path.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or metadata.st_mode & 0o007:
            raise ReconciliationDenied("agent provisioning policy ownership or mode unsafe")
        return cls(json.loads(path.read_text(encoding="utf-8")),
                   expected_release_sha=expected_release_sha)

    def authorize(self, command: ProvisionAgentCommand) -> None:
        policy = self.document
        expiry = datetime.fromisoformat(policy["expires_at"])
        if expiry <= datetime.now(UTC):
            raise ReconciliationDenied("agent provisioning grant expired")
        age = (datetime.now(UTC) - self.backup_created_at).total_seconds()
        if not 0 <= age <= 86400:
            raise ReconciliationDenied("backup evidence expired before request")
        ReconciliationPolicy._verify_backup(
            self.backup_path, self.backup_checksum, self.backup_fingerprint
        )
        if command.authorization_reference != policy["authorization_reference"]:
            raise ReconciliationDenied("agent provisioning authorization mismatch")
        if command.backup_reference != policy["backup"]["reference"]:
            raise ReconciliationDenied("agent provisioning backup mismatch")
        if command.user_level != policy["user_level"]:
            raise ReconciliationDenied("agent provisioning role mismatch")
        expected_hash = canonical_sha256(policy["agent"])
        if command.plan_sha256 != expected_hash:
            raise ReconciliationDenied("agent provisioning plan digest mismatch")
        if command.agent.model_dump(mode="json") != policy["agent"]:
            raise ReconciliationDenied("agent is absent from exact approved grant")
