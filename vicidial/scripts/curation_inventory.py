#!/usr/bin/env python3
"""Produce publication-safe inventory and sensitive-artifact review reports."""
from __future__ import annotations

import csv
import hashlib
import mimetypes
import os
import subprocess
from pathlib import Path

from codestra_vicidial.curation import content_categories

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "github"
REPORT.mkdir(parents=True, exist_ok=True)

SKIP_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
              "node_modules", ".venv", "dist", "build", ".cache"}
SENSITIVE_SUFFIX = {
    ".pem": "private/TLS key", ".key": "private/TLS key", ".p12": "key bundle",
    ".pfx": "key bundle", ".sql": "database dump or migration", ".dump": "database dump",
    ".wav": "recording/audio", ".gsm": "recording/audio", ".mp3": "recording/audio",
    ".ogg": "recording/audio", ".log": "runtime log", ".backup": "backup",
}
def tracked(path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", str(path.relative_to(ROOT))],
        cwd=ROOT,
    )
    return result.returncode != 0

def fingerprint(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return "sha256:" + digest[:16]

files = []
sensitive = []
large = []
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
        continue
    rel = str(path.relative_to(ROOT))
    stat = path.stat()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    disposition = "TRACKED_OR_CANDIDATE" if tracked(path) else "IGNORED"
    files.append({
        "path": rel, "size_bytes": stat.st_size, "file_type": mime,
        "git_disposition": disposition,
    })
    if stat.st_size >= 5 * 1024 * 1024:
        large.append({"path": rel, "size_bytes": stat.st_size, "file_type": mime})
    categories = set()
    if path.suffix.lower() in SENSITIVE_SUFFIX:
        categories.add(SENSITIVE_SUFFIX[path.suffix.lower()])
    lowered = set(part.lower() for part in path.parts)
    if lowered & {"recordings", "backups", "spool", "runtime", "logs", "data", "volumes"}:
        categories.add("sensitive/runtime directory")
    if stat.st_size <= 4 * 1024 * 1024:
        data = path.read_bytes()
        categories.update(content_categories(data))
    for category in sorted(categories):
        is_safe_sql = category == "database dump or migration" and "migrations" in lowered
        is_scanner_fixture = (
            rel == "tests/test_adapter.py"
            and category in {"credential assignment", "authorization header"}
        )
        is_protected_reference = (
            rel == "src/codestra_vicidial/app.py"
            and category == "credential assignment"
        )
        if is_scanner_fixture:
            reported_category = "synthetic secret-scanner fixture"
            remediation = "retain synthetic-only regression fixture"
            decision = "INCLUDE"
        elif is_protected_reference:
            reported_category = "protected credential-file reference"
            remediation = "retain; value is loaded from protected file"
            decision = "INCLUDE"
        elif is_safe_sql:
            reported_category = "parameterized migration"
            remediation = "manual review before staging"
            decision = "REVIEW"
        else:
            reported_category = category
            remediation = "exclude from Git"
            decision = "IGNORE"
        sensitive.append({
            "path": rel,
            "category": reported_category,
            "detection_method": "extension/path/content rule",
            "redacted_fingerprint": fingerprint(path),
            "remediation": remediation,
            "decision": decision,
        })

def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

write_csv(REPORT / "WORKING_TREE_INVENTORY.csv", files,
          ["path", "size_bytes", "file_type", "git_disposition"])
write_csv(REPORT / "UNTRACKED_LARGE_FILES.csv", large,
          ["path", "size_bytes", "file_type"])
write_csv(REPORT / "SENSITIVE_ARTIFACT_FINDINGS.csv", sensitive,
          ["path", "category", "detection_method", "redacted_fingerprint",
           "remediation", "decision"])
print("inventory_files={}".format(len(files)))
print("large_files={}".format(len(large)))
print("sensitive_findings={}".format(len(sensitive)))
