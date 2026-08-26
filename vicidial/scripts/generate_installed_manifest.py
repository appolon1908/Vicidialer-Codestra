#!/usr/bin/env python3.11
"""Generate a deterministic manifest for the flattened host release layout."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

INCLUDED = ("src", "scripts", "config", "docs", "ops", ".venv")
INCLUDED_FILES = ("SOURCE.COMMIT", "BUILD_INFO.json", "pyproject.toml", "requirements.lock")
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def is_manifest_file(path: Path, root: Path) -> bool:
    """Return true only for stable installed-release bytes.

    The verifier imports this module from the copied ``scripts`` directory.
    Python may therefore create ``scripts/__pycache__`` between manifest
    generation and verification. Bytecode and cache files are runtime-derived
    state, not release inputs, and must never make the immutable manifest
    self-invalidating.
    """
    relative = path.relative_to(root)
    return (
        path.is_file()
        and not any(part in EXCLUDED_PARTS for part in relative.parts)
        and path.suffix not in EXCLUDED_SUFFIXES
    )


def build(root: Path, source_sha: str, generated_at: str) -> dict:
    files = []
    candidates = []
    for directory in INCLUDED:
        base = root / directory
        if not base.is_dir():
            raise ValueError(f"installed release directory missing: {directory}")
        candidates.extend(path for path in base.rglob("*") if is_manifest_file(path, root))
    for name in INCLUDED_FILES:
        path = root / name
        if not path.is_file():
            raise ValueError(f"installed release file missing: {name}")
        candidates.append(path)
    for path in sorted(set(candidates)):
        raw = path.read_bytes()
        item = {
            "path": path.relative_to(root).as_posix(),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "executable": bool(path.stat().st_mode & 0o111),
        }
        if path.is_symlink():
            item["symlink_target"] = os.readlink(path)
        files.append(item)
    if not files:
        raise ValueError("installed release manifest is empty")
    return {
        "schema": "codestra-installed-release-manifest/v1",
        "source_sha": source_sha,
        "generated_at": generated_at,
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = build(args.root.resolve(), args.source_sha, args.generated_at)
    args.output.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
