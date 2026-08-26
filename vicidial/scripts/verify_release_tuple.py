#!/usr/bin/env python3.11
"""Fail closed unless every installed R3 release identity agrees."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def verify(release_root: Path, manifest: Path, version_document: Path | None = None) -> str:
    source_sha = (release_root / "SOURCE.COMMIT").read_text(encoding="utf-8").strip()
    if not SHA_PATTERN.fullmatch(source_sha):
        raise ValueError("SOURCE.COMMIT is invalid")
    if release_root.name != source_sha:
        raise ValueError("release directory does not equal SOURCE.COMMIT")
    build_info = json.loads((release_root / "BUILD_INFO.json").read_text(encoding="utf-8"))
    manifest_info = json.loads(manifest.read_text(encoding="utf-8"))
    values = {
        "SOURCE.COMMIT": source_sha,
        "release directory": release_root.name,
        "BUILD_INFO git_sha": build_info.get("git_sha"),
        "manifest source_sha": manifest_info.get("source_sha"),
    }
    if version_document is not None:
        values["/version git_sha"] = json.loads(
            version_document.read_text(encoding="utf-8")
        ).get("git_sha")
    if set(values.values()) != {source_sha}:
        raise ValueError(f"release tuple mismatch: {values}")
    return source_sha


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--version-document", type=Path)
    args = parser.parse_args()
    sha = verify(args.release_root, args.manifest, args.version_document)
    print(f"RELEASE_TUPLE=VERIFIED git_sha={sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
