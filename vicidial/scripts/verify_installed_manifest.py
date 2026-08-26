#!/usr/bin/env python3.11
"""Verify the complete deterministic flattened host release manifest."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from generate_installed_manifest import build


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    document = json.loads(raw)
    expected = build(args.root.resolve(), document.get("source_sha", ""),
                     document.get("generated_at", ""))
    canonical = (json.dumps(expected, sort_keys=True,
                            separators=(",", ":")) + "\n").encode()
    if raw != canonical:
        raise ValueError("installed release manifest mismatch")
    print(f"INSTALLED_RELEASE_MANIFEST=VERIFIED files={len(expected['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
