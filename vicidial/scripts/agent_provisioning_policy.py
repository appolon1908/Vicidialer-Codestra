#!/usr/bin/env python3
"""Generate or validate the exact runtime disabled-agent policy offline."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))

from codestra_vicidial.agent_provisioning import AgentProvisioningPolicy  # noqa: E402
from codestra_vicidial.models import AgentSpec  # noqa: E402


def build(args: argparse.Namespace) -> dict:
    artifact = args.backup_artifact.resolve()
    runtime_artifact = Path(args.runtime_backup_artifact)
    if not runtime_artifact.is_absolute():
        raise ValueError("runtime backup artifact must be absolute")
    agent = AgentSpec.model_validate(json.loads(args.agent_json.read_text(encoding="utf-8")))
    return {
        "protected_sha": args.release_sha,
        "authorization_reference": args.authorization_reference,
        "expires_at": args.expires_at,
        "backup": {
            "reference": args.backup_reference,
            "created_at": args.backup_created_at,
            "artifact": str(runtime_artifact),
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "validation": "PASS",
        },
        "user_level": 9,
        "agent": agent.model_dump(mode="json"),
    }


def validate(document: dict, release_sha: str, now: datetime | None = None,
             backup_artifact_source: Path | None = None) -> None:
    checked = copy.deepcopy(document)
    if backup_artifact_source is not None:
        checked["backup"]["artifact"] = str(backup_artifact_source.resolve())
    AgentProvisioningPolicy(checked, expected_release_sha=release_sha, now=now)
    AgentSpec.model_validate(document.get("agent"))


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="operation", required=True)
    validator = subparsers.add_parser("validate")
    validator.add_argument("--policy", type=Path, required=True)
    validator.add_argument("--release-sha", required=True)
    validator.add_argument("--backup-artifact-source", type=Path)
    generator = subparsers.add_parser("generate")
    generator.add_argument("--agent-json", type=Path, required=True)
    generator.add_argument("--release-sha", required=True)
    generator.add_argument("--authorization-reference", required=True)
    generator.add_argument("--expires-at", required=True)
    generator.add_argument("--backup-reference", required=True)
    generator.add_argument("--backup-created-at", required=True)
    generator.add_argument("--backup-artifact", type=Path, required=True)
    generator.add_argument("--runtime-backup-artifact", required=True)
    generator.add_argument("--runtime-gid", type=int, required=True)
    generator.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "generate":
        document = build(args)
        validate(document, args.release_sha, backup_artifact_source=args.backup_artifact)
        content = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
        try:
            os.fchown(descriptor, -1, args.runtime_gid)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            args.output.unlink(missing_ok=True)
            raise
        print(f"POLICY_GENERATED={args.output}")
    else:
        document = json.loads(args.policy.read_text(encoding="utf-8"))
        validate(
            document, args.release_sha, now=datetime.now(UTC),
            backup_artifact_source=args.backup_artifact_source,
        )
        print("POLICY_VALID=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
