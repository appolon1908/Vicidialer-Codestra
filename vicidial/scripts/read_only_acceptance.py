#!/usr/bin/env python3
"""Validate a captured staging readback without issuing a mutation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate(document: dict, expected_target: str) -> list[str]:
    failures: list[str] = []
    if document.get("status") != "ready":
        failures.append("adapter is not ready")
    attestation = document.get("target_attestation", {})
    if attestation.get("target_id") != expected_target:
        failures.append("target attestation mismatch")
    digest = attestation.get("inventory_sha256", "")
    if len(digest) != 64:
        failures.append("inventory attestation is missing")
    flags = document.get("feature_flags", {})
    enabled = sorted(name for name, value in flags.items() if value)
    if enabled:
        failures.append("enabled flags: " + ",".join(enabled))
    if document.get("kill_switch_active") is not True:
        failures.append("local kill switch is not active")
    inventory = document.get("inventory")
    if not isinstance(inventory, dict):
        failures.append("runtime inventory is missing")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("readback", type=Path)
    parser.add_argument(
        "--target", default="vicidial:65.21.67.207:staging")
    args = parser.parse_args()
    failures = validate(json.loads(args.readback.read_text()), args.target)
    print(json.dumps({
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
    }, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
