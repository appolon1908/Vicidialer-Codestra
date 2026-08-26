from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from . import __version__


APPLICATION = "codestra-vicidial-restricted-adapter"
RELEASE = "R3"
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_ENVIRONMENTS = frozenset({
    "staging-read-only",
    "production-candidate",
    "production",
})


def _python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _validate_built_at(value: object) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(
            "built_at must be a UTC ISO-8601 timestamp ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("built_at is not valid ISO-8601") from exc
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("built_at must be UTC")
    return value


def validate_release_document(
    document: Mapping[str, Any],
) -> dict[str, Any]:
    application = document.get("application")
    version = document.get("version")
    release = document.get("release")
    git_sha = document.get("git_sha")
    build_id = document.get("build_id")
    built_at = document.get("built_at")
    environment = document.get("environment")
    python_version = document.get("python")

    if application != APPLICATION:
        raise ValueError("release application mismatch")
    if version != __version__:
        raise ValueError("release version mismatch")
    if release != RELEASE:
        raise ValueError("release program mismatch")
    if not isinstance(git_sha, str) or not _SHA_PATTERN.fullmatch(git_sha):
        raise ValueError(
            "release git_sha must be a lowercase 40-character SHA"
        )

    expected_build_id = f"vicidial-r3-{git_sha[:12]}"
    if build_id != expected_build_id:
        raise ValueError("release build_id mismatch")

    built_at = _validate_built_at(built_at)
    if environment not in _ALLOWED_ENVIRONMENTS:
        raise ValueError("release environment is not allowed")
    if python_version != "3.11":
        raise ValueError("release Python version must be 3.11")

    return {
        "application": APPLICATION,
        "version": __version__,
        "release": RELEASE,
        "git_sha": git_sha,
        "build_id": expected_build_id,
        "built_at": built_at,
        "environment": environment,
        "python": "3.11",
    }


def build_release_document(
    *,
    git_sha: str,
    built_at: str,
    environment: str,
) -> dict[str, Any]:
    return validate_release_document({
        "application": APPLICATION,
        "version": __version__,
        "release": RELEASE,
        "git_sha": git_sha,
        "build_id": f"vicidial-r3-{git_sha[:12]}",
        "built_at": built_at,
        "environment": environment,
        "python": "3.11",
    })


def load_release_info(
    *,
    required: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    required = required or (
        os.environ.get(
            "CODESTRA_RELEASE_METADATA_REQUIRED",
            "false",
        ).lower()
        == "true"
    )
    release_root = (
        Path(__file__).resolve().parents[2]
        if root is None
        else Path(root)
    )
    metadata_file = Path(
        os.environ.get(
            "CODESTRA_BUILD_INFO_FILE",
            str(release_root / "BUILD_INFO.json"),
        )
    )

    if metadata_file.is_file():
        document = json.loads(
            metadata_file.read_text(encoding="utf-8")
        )
        result = validate_release_document(document)
        source_commit_file = metadata_file.with_name("SOURCE.COMMIT")
        if required:
            source_commit = source_commit_file.read_text(encoding="utf-8").strip()
            if source_commit != result["git_sha"]:
                raise ValueError("SOURCE.COMMIT and BUILD_INFO git_sha mismatch")
        result["verified"] = True
        result["metadata_source"] = "BUILD_INFO.json"
        return result

    if required:
        raise RuntimeError(f"release metadata missing: {metadata_file}")

    return {
        "application": APPLICATION,
        "version": __version__,
        "release": RELEASE,
        "git_sha": None,
        "build_id": None,
        "built_at": None,
        "environment": "development",
        "python": _python_version(),
        "verified": False,
        "metadata_source": "development",
    }
