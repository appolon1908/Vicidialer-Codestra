#!/usr/bin/env python3.11
"""Generate a deterministic, complete VICIdial R3 release manifest."""
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ROOTS = (Path("vicidial"), Path("deploy/vicidial-restricted-adapter"))
WORKFLOW = Path(".github/workflows/vicidial-adapter-container.yml")
EXCLUDED_PARTS = {".git", ".pytest_cache", ".mypy_cache", ".venv", "__pycache__", "coverage", "htmlcov", "evidence", "test-results"}
EXCLUDED_NAMES = {"RELEASE_MANIFEST.json"}

def release_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for relative_root in ROOTS:
        for path in (root / relative_root).rglob("*"):
            relative = path.relative_to(root)
            if not path.is_file() or any(part in EXCLUDED_PARTS for part in relative.parts):
                continue
            if path.name in EXCLUDED_NAMES or path.suffix in {".pyc", ".sqlite3", ".log"}:
                continue
            files.append(relative)
    if (root / WORKFLOW).is_file():
        files.append(WORKFLOW)
    return sorted(set(files), key=lambda item: item.as_posix())

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def classification(path: Path) -> str:
    return "generated" if path.parts[:2] == ("deploy", "vicidial-restricted-adapter") else "canonical"

def build(root: Path, source_sha: str, generated_at: str) -> dict[str, object]:
    if not SHA_PATTERN.fullmatch(source_sha):
        raise ValueError("source SHA must be 40 lowercase hexadecimal characters")
    if not generated_at.endswith("Z"):
        raise ValueError("generated timestamp must be explicit UTC")
    release_id = f"vicidial-r3-{source_sha[:12]}"
    return {
        "schema": "codestra.vicidial.r3.release-manifest.v2",
        "application": "codestra-vicidial-restricted-adapter",
        "version": "0.2.0rc1", "source_sha": source_sha,
        "release_id": release_id, "build_id": release_id,
        "generated_at": generated_at,
        "files": [{"path": relative.as_posix(), "size": (root / relative).stat().st_size,
                   "sha256": digest(root / relative),
                   "executable": bool((root / relative).stat().st_mode & 0o111),
                   "classification": classification(relative)}
                  for relative in release_files(root)],
    }

def verify(root: Path, document: dict[str, object]) -> None:
    expected = build(root, str(document.get("source_sha", "")), str(document.get("generated_at", "")))
    if document != expected:
        raise ValueError("release manifest does not match the complete R3 source set")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPOSITORY)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = build(args.root.resolve(), args.source_sha, args.generated_at)
    args.output.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"RELEASE_MANIFEST={args.output} files={len(document['files'])}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
