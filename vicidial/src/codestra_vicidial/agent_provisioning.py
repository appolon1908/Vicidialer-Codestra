"""Exact, file-backed authorization for one disabled VICIdial user."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .models import ProvisionAgentCommand
from .reconciliation import ReconciliationDenied, canonical_sha256


class AgentProvisioningPolicy:
    def __init__(self, document: dict, *, expected_release_sha: str,
                 now: datetime | None = None):
        required = {"protected_sha", "authorization_reference", "expires_at",
                    "backup_reference", "user_level", "agent"}
        if set(document) != required:
            raise ReconciliationDenied("agent provisioning policy schema mismatch")
        if document["protected_sha"] != expected_release_sha:
            raise ReconciliationDenied("agent provisioning policy/release SHA mismatch")
        current = now or datetime.now(UTC)
        expiry = datetime.fromisoformat(document["expires_at"])
        if expiry.tzinfo is None or expiry <= current:
            raise ReconciliationDenied("agent provisioning grant expired")
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
        if command.authorization_reference != policy["authorization_reference"]:
            raise ReconciliationDenied("agent provisioning authorization mismatch")
        if command.backup_reference != policy["backup_reference"]:
            raise ReconciliationDenied("agent provisioning backup mismatch")
        if command.user_level != policy["user_level"]:
            raise ReconciliationDenied("agent provisioning role mismatch")
        expected_hash = canonical_sha256(policy["agent"])
        if command.plan_sha256 != expected_hash:
            raise ReconciliationDenied("agent provisioning plan digest mismatch")
        if command.agent.model_dump(mode="json") != policy["agent"]:
            raise ReconciliationDenied("agent is absent from exact approved grant")
