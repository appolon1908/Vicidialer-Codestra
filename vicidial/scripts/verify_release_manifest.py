#!/usr/bin/env python3.11
"""Verify canonical bytes and complete coverage of an R3 manifest."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from generate_release_manifest import build

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    raw = args.manifest.read_bytes()
    document = json.loads(raw)
    expected = build(args.root.resolve(), document.get("source_sha", ""), document.get("generated_at", ""))
    canonical = (json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if raw != canonical or document != expected:
        raise ValueError("release manifest bytes or complete source set mismatch")
    print(f"RELEASE_MANIFEST=VERIFIED files={len(document['files'])}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
