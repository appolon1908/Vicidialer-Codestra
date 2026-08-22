#!/usr/bin/env python3
"""Build a fail-closed desired-versus-observed campaign reconciliation report.

The tool is deliberately read-only.  It consumes secret-free CSV snapshots from
Odoo, middleware, and VICIdial and emits a deterministic plan.  Applying that
plan remains the responsibility of the authenticated restricted adapter.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, asdict
from pathlib import Path


CANONICAL = re.compile(r"^(MOY|COD|SCP|MBL|RLP|FTP|TRX|CAL)-[A-Z0-9-]+$")
PHYSICAL = re.compile(r"^[A-Z0-9_]{2,8}$")


class ReconciliationError(ValueError):
    pass


@dataclass(frozen=True)
class Desired:
    canonical: str
    physical: str
    environment: str
    active: bool


def _bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "t", "yes", "y", "1"}:
        return True
    if normalized in {"false", "f", "no", "n", "0", ""}:
        return False
    raise ReconciliationError(f"invalid boolean: {value!r}")


def load_desired(path: Path) -> list[Desired]:
    rows: list[Desired] = []
    seen_canonical: set[tuple[str, str]] = set()
    seen_physical: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            canonical = raw["canonical_campaign_code"].strip().upper()
            physical = raw["vicidial_campaign_id"].strip().upper()
            environment = raw["environment"].strip().lower()
            active = _bool(raw.get("active", "false"))
            if not CANONICAL.fullmatch(canonical):
                raise ReconciliationError(f"invalid canonical campaign: {canonical}")
            if not PHYSICAL.fullmatch(physical):
                raise ReconciliationError(f"invalid physical campaign: {physical}")
            if environment not in {"development", "staging", "production"}:
                raise ReconciliationError(f"invalid environment: {environment}")
            if environment == "production" and active:
                raise ReconciliationError("active production desired state is prohibited")
            key = (environment, canonical)
            if key in seen_canonical or physical in seen_physical:
                raise ReconciliationError("duplicate desired campaign identity")
            seen_canonical.add(key)
            seen_physical.add(physical)
            rows.append(Desired(canonical, physical, environment, active))
    if not rows:
        raise ReconciliationError("Odoo authority snapshot is empty")
    return sorted(rows, key=lambda row: (row.environment, row.canonical))


def _index(path: Path, key: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if None in row or any(value is None for value in row.values()):
                raise ReconciliationError("malformed observed CSV row")
            value = row[key].strip().upper()
            if value in result:
                raise ReconciliationError(f"duplicate observed identity: {value}")
            result[value] = row
    return result


def reconcile(
    desired: Iterable[Desired], middleware_path: Path, vicidial_path: Path
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    middleware = _index(middleware_path, "canonical_campaign_code")
    vicidial = _index(vicidial_path, "campaign_id")
    rows: list[dict[str, object]] = []
    desired_physical: set[str] = set()
    desired_canonical: set[str] = set()
    for item in desired:
        desired_physical.add(item.physical)
        desired_canonical.add(item.canonical)
        mw = middleware.get(item.canonical)
        vd = vicidial.get(item.physical)
        reasons: list[str] = []
        if mw is None:
            reasons.append("MIDDLEWARE_MAPPING_MISSING")
        elif mw.get("vicidial_campaign_id", "").strip().upper() != item.physical:
            reasons.append("MIDDLEWARE_PHYSICAL_ID_MISMATCH")
        else:
            if mw.get("test_status", "").strip().upper() != "PASS":
                reasons.append("FUNCTIONAL_TEST_EVIDENCE_MISSING")
            if not mw.get("evidence_reference", "").strip():
                reasons.append("EVIDENCE_REFERENCE_MISSING")
        if vd is None:
            reasons.append("VICIDIAL_CAMPAIGN_MISSING")
        else:
            marker = vd.get("campaign_description", "")
            expected_marker = f"CODESTRA:{item.canonical}"
            if marker != expected_marker:
                reasons.append("VICIDIAL_OWNERSHIP_MARKER_MISMATCH")
            if vd.get("active", "").strip().upper() != "N":
                reasons.append("VICIDIAL_CAMPAIGN_NOT_DISABLED")
            if vd.get("auto_dial_level", "").strip() not in {"0", "0.0", "0.00"}:
                reasons.append("VICIDIAL_AUTO_DIAL_NOT_ZERO")
        rows.append({
            **asdict(item),
            "middleware_present": mw is not None,
            "vicidial_present": vd is not None,
            "status": "PASS" if not reasons else "BLOCKED",
            "reasons": reasons,
        })
    legacy = [
        row for key, row in sorted(vicidial.items()) if key not in desired_physical
    ]
    unexplained_middleware = sorted(set(middleware) - desired_canonical)
    if unexplained_middleware:
        raise ReconciliationError(
            "middleware contains mappings outside Odoo authority: "
            + ",".join(unexplained_middleware)
        )
    return rows, legacy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--odoo", type=Path, required=True)
    parser.add_argument("--middleware", type=Path, required=True)
    parser.add_argument("--vicidial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    desired = load_desired(args.odoo)
    rows, legacy = reconcile(desired, args.middleware, args.vicidial)
    payload = {
        "schema": "codestra.campaign-reconciliation.v1",
        "mode": "READ_ONLY",
        "desired_count": len(rows),
        "pass_count": sum(row["status"] == "PASS" for row in rows),
        "blocked_count": sum(row["status"] == "BLOCKED" for row in rows),
        "legacy_unmanaged_count": len(legacy),
        "production_dialing": "DISABLED",
        "rows": rows,
        "legacy_unmanaged": legacy,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
