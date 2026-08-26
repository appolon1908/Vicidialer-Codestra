#!/usr/bin/env python3.11
"""Generate or verify the deterministic VICIdial R3 release manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ROOTS = (Path("vicidial"), Path("deploy/vicidial-restricted-adapter"))
EXCLUDED_PARTS = {".pytest_cache", "__pycache__", ".venv"}
EXCLUDED_NAMES = {"RELEASE_MANIFEST.json"}


def release_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for relative_root in ROOTS:
        for path in (root / relative_root).rglob("*"):
            relative = path.relative_to(root)
            if not path.is_file() or any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.name in EXCLUDED_NAMES or path.suffix == ".pyc":
                continue
            files.append(relative)
    return sorted(files, key=lambda item: item.as_posix())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(root: Path, source_sha: str) -> dict[str, object]:
    if not SHA_PATTERN.fullmatch(source_sha):
        raise ValueError("source SHA must be 40 lowercase hexadecimal characters")
    return {
        "schema": "codestra.vicidial.r3.release-manifest.v1",
        "source_sha": source_sha,
        "files": [
            {"path": relative.as_posix(), "sha256": digest(root / relative)}
            for relative in release_files(root)
        ],
    }


def verify(root: Path, document: dict[str, object]) -> None:
    expected = build(root, str(document.get("source_sha", "")))
    if document != expected:
        raise ValueError("release manifest does not match the complete R3 source set")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPOSITORY)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.verify:
        document = json.loads(args.output.read_text(encoding="utf-8"))
        verify(root, document)
        print(f"RELEASE_MANIFEST=VERIFIED files={len(document['files'])}")
        return 0
    document = build(root, args.source_sha)
    args.output.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"RELEASE_MANIFEST={args.output} files={len(document['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
