#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from codestra_vicidial import __version__


APPLICATION = "codestra-vicidial-restricted-adapter"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def fetch_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "User-Agent": "codestra-vicidial-r3-contract-check"
        },
    )
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(
                f"{url} returned HTTP {response.status}"
            )
        document = json.loads(response.read().decode("utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError(f"{url} did not return a JSON object")
    return document


def validate_documents(
    *,
    health: dict[str, Any],
    ready: dict[str, Any],
    version: dict[str, Any],
    expected_sha: str,
    deployment_profile: str = "staging-read-only",
) -> None:
    if not SHA_PATTERN.fullmatch(expected_sha):
        raise ValueError(
            "SOURCE.COMMIT is not a lowercase 40-character SHA"
        )
    if health.get("status") != "live":
        raise ValueError("/health contract failed")
    if ready.get("status") != "ready":
        raise ValueError("/ready status contract failed")
    if ready.get("deployment_profile") != deployment_profile:
        raise ValueError("/ready deployment profile mismatch")
    expected_writes = deployment_profile == "production-reconciliation"
    if deployment_profile not in {
        "staging-read-only", "production-candidate",
        "production-reconciliation", "production-internal-test",
        "production-external-one-call",
    }:
        raise ValueError("unsupported deployment profile")
    if ready.get("writes_enabled") is not expected_writes:
        raise ValueError("/ready writes contract failed")
    if deployment_profile == "production-candidate":
        if ready.get("backend") != "mysql":
            raise ValueError("/ready candidate requires mysql backend")
        if ready.get("release_sha") != expected_sha:
            raise ValueError("/ready SHA mismatch")
        for field in (
            "internal_test_calling", "production_dialing",
            "customer_traffic_authorized", "reconciliation_policy_verified",
        ):
            if ready.get(field) is not False:
                raise ValueError(f"/ready candidate {field} must be false")
        checks = ready.get("checks")
        if not isinstance(checks, dict) or not all(
            checks.get(name) is True for name in (
                "release", "hmac", "source_policy", "state_store",
                "repository", "kill_switches",
            )
        ) or any(value is not True for value in checks.values()):
            raise ValueError("/ready candidate checks are incomplete or failing")
    if expected_writes and ready.get("reconciliation_policy_verified") is not True:
        raise ValueError("/ready reconciliation policy is not verified")
    if deployment_profile == "production-internal-test":
        if ready.get("internal_test_calling") is not True:
            raise ValueError("/ready internal calling contract failed")
        if ready.get("checks", {}).get("internal_call_policy") is not True:
            raise ValueError("/ready internal call policy is not verified")
    if deployment_profile == "production-external-one-call":
        if ready.get("production_dialing") is not True:
            raise ValueError("/ready external dialing contract failed")
        if ready.get("checks", {}).get("external_call_policy") is not True:
            raise ValueError("/ready external call policy is not verified")
    if ready.get("release_verified") is not True:
        raise ValueError(
            "/ready reports unverified release metadata"
        )
    if version.get("application") != APPLICATION:
        raise ValueError("/version application mismatch")
    if version.get("version") != __version__:
        raise ValueError("/version package version mismatch")
    if version.get("release") != "R3":
        raise ValueError("/version release mismatch")
    if version.get("verified") is not True:
        raise ValueError("/version metadata is unverified")
    if version.get("git_sha") != expected_sha:
        raise ValueError("/version SHA mismatch")
    if version.get("build_id") != f"vicidial-r3-{expected_sha[:12]}":
        raise ValueError("/version build_id mismatch")
    if version.get("python") != "3.11":
        raise ValueError("/version Python contract mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the canonical Codestra VICIdial R3 HTTP contract"
        ),
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8097",
    )
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--deployment-profile", required=True)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    if args.attempts < 1:
        raise SystemExit("attempts must be at least one")

    expected_sha = (
        Path(args.source_commit)
        .read_text(encoding="utf-8")
        .strip()
    )
    last_error: Exception | None = None

    for attempt in range(1, args.attempts + 1):
        try:
            health = fetch_json(f"{args.base_url}/health")
            ready = fetch_json(f"{args.base_url}/ready")
            version = fetch_json(f"{args.base_url}/version")
            validate_documents(
                health=health,
                ready=ready,
                version=version,
                expected_sha=expected_sha,
                deployment_profile=args.deployment_profile,
            )
            print("OPERATIONAL_CONTRACT=PASS")
            print(f"GIT_SHA={expected_sha}")
            print(f"BUILD_ID={version['build_id']}")
            return 0
        except (
            HTTPError,
            URLError,
            TimeoutError,
            json.JSONDecodeError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            last_error = exc
            if attempt < args.attempts:
                time.sleep(args.delay)

    raise SystemExit(
        "operational contract failed after "
        f"{args.attempts} attempts: {last_error}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
