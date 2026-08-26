#!/usr/bin/env python3.11
"""Generate a deterministic manifest for the flattened host release layout."""
from __future__ import annotations
import argparse, hashlib, json, os
from pathlib import Path

INCLUDED = ("src", "scripts", "config", "docs", "ops")


def build(root: Path, source_sha: str, generated_at: str) -> dict:
    files = []
    for directory in INCLUDED:
        base = root / directory
        if not base.is_dir():
            raise ValueError(f"installed release directory missing: {directory}")
        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            if any(part in {"__pycache__", ".pytest_cache"} for part in path.parts):
                continue
            raw = path.read_bytes()
            files.append({
                "path": path.relative_to(root).as_posix(), "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "executable": bool(path.stat().st_mode & 0o111),
            })
    if not files:
        raise ValueError("installed release manifest is empty")
    return {"schema": "codestra-installed-release-manifest/v1",
            "source_sha": source_sha, "generated_at": generated_at,
            "files": files}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = build(args.root.resolve(), args.source_sha, args.generated_at)
    args.output.write_text(json.dumps(document, sort_keys=True,
                                      separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
