#!/usr/bin/env python3.11
"""Build a complete, manifest-bound installed layout without activating it."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


COPIED_DIRECTORIES = ("src", "scripts", "config", "docs", "ops")


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--generated-at", required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    if output.exists():
        raise ValueError("installed layout output already exists")
    output.mkdir(parents=True)
    for directory in COPIED_DIRECTORIES:
        shutil.copytree(
            source / directory,
            output / directory,
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc"),
        )
    shutil.copy2(source / "pyproject.toml", output / "pyproject.toml")
    shutil.copy2(source / "docker/requirements.lock", output / "requirements.lock")
    (output / "SOURCE.COMMIT").write_text(args.source_sha + "\n", encoding="utf-8")
    run(
        sys.executable,
        str(output / "scripts/generate_build_info.py"),
        "--git-sha", args.source_sha,
        "--environment", "production-candidate",
        "--built-at", args.generated_at,
        "--output", str(output / "BUILD_INFO.json"),
    )

    # This builder proves the deterministic installed-layout contract. Its
    # runner-created virtual environment is validation evidence, not a host
    # production package. The immutable OCI image is the deployable artifact.
    run(sys.executable, "-m", "venv", "--copies", str(output / ".venv"))
    python = output / ".venv/bin/python"
    run(str(python), "-m", "pip", "install", "--requirement", str(output / "requirements.lock"))
    run(str(python), "-m", "pip", "uninstall", "--yes", "pip", "setuptools", "wheel")

    generator = output / "scripts/generate_installed_manifest.py"
    verifier = output / "scripts/verify_installed_manifest.py"
    manifest = output / "INSTALLED_MANIFEST.json"
    run(str(python), str(generator), "--root", str(output), "--source-sha", args.source_sha,
        "--generated-at", args.generated_at, "--output", str(manifest))
    run(str(python), str(verifier), "--root", str(output), "--manifest", str(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
