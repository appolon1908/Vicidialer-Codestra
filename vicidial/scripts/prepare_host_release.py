#!/usr/bin/env python3
"""Prepare an offline installed layout; never activate or authorize a release."""
from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True

from generate_installed_manifest import build

SCRIPT_ROOT = Path(__file__).resolve().parent
SOURCE_DIRS = ("src", "scripts", "config", "docs", "ops")
PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)")
HASHED_PIN = re.compile(PIN.pattern + r" --hash=sha256:([0-9a-f]{64})")
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_WHEEL_BYTES = 64 * 1024 * 1024


class PreparationError(ValueError):
    """A bounded preparation failure; never an activation success."""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pins(raw: str, *, hashed: bool) -> dict[str, tuple[str, str | None]]:
    result = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = (HASHED_PIN if hashed else PIN).fullmatch(line.strip())
        if not match:
            raise PreparationError("dependency lock requires exact single-line pins")
        name = package_name(match[1])
        if name in result:
            raise PreparationError("duplicate dependency pin")
        result[name] = (match[2], match[3] if hashed else None)
    if not result:
        raise PreparationError("empty dependency lock")
    return result


def plain_file(path: Path, *, limit: int) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise PreparationError("input must be a bounded ordinary file")


def verify_wheels(source_lock: Path, hashed_lock: Path, wheelhouse: Path) -> dict:
    plain_file(hashed_lock, limit=65536)
    expected = pins(source_lock.read_text(), hashed=False)
    locked = pins(hashed_lock.read_text(), hashed=True)
    if {k: v[0] for k, v in locked.items()} != {k: v[0] for k, v in expected.items()}:
        raise PreparationError("offline lock does not match selected source dependencies")
    if wheelhouse.is_symlink() or not wheelhouse.is_dir():
        raise PreparationError("wheelhouse must be an ordinary directory")
    found = {}
    for wheel in sorted(wheelhouse.iterdir()):
        plain_file(wheel, limit=MAX_WHEEL_BYTES)
        if wheel.suffix != ".whl":
            raise PreparationError("wheelhouse must contain only wheel files")
        actual_digest = digest(wheel)
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            metadata = [n for n in names if re.fullmatch(r"[^/]+\.dist-info/METADATA", n)]
            if len(metadata) != 1 or len(set(names)) != len(names):
                raise PreparationError("wheel metadata is ambiguous")
            item = archive.getinfo(metadata[0])
            if item.file_size > 65536:
                raise PreparationError("wheel metadata exceeds limit")
            document = email.parser.BytesParser().parsebytes(archive.read(item))
            if len(document.get_all("Name", [])) != 1 or len(document.get_all("Version", [])) != 1:
                raise PreparationError("wheel identity is ambiguous")
            name, version = package_name(document["Name"]), document["Version"]
        if name in found or locked.get(name) != (version, actual_digest):
            raise PreparationError("wheel identity or digest is not locked")
        found[name] = {"filename": wheel.name, "version": version, "sha256": actual_digest}
    if set(found) != set(locked):
        raise PreparationError("offline dependency wheel is missing")
    return found


def run(command: list[str], *, env: dict[str, str], cwd: Path) -> None:
    try:
        completed = subprocess.run(
            command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PreparationError("offline preparation subprocess failed") from None
    if completed.returncode:
        # Never echo dependency/transport output, arguments or local credentials.
        raise PreparationError("offline preparation subprocess failed")


def verify_archive(archive: Path, archive_sha: str, source_sha: str, builder_sha: str) -> None:
    if not SHA.fullmatch(source_sha) or not SHA.fullmatch(builder_sha) or not DIGEST.fullmatch(archive_sha):
        raise PreparationError("trusted source identity is invalid")
    plain_file(archive, limit=MAX_ARCHIVE_BYTES)
    if digest(archive) != archive_sha:
        raise PreparationError("source archive digest mismatch")
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > 10000 or sum(m.size for m in members) > MAX_EXPANDED_BYTES:
            raise PreparationError("expanded source archive exceeds limit")
    run([
        sys.executable, "-B", str(SCRIPT_ROOT / "verify_source_release.py"),
        "--archive", str(archive), "--source-sha", source_sha, "--builder-sha", builder_sha,
    ], env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"}, cwd=SCRIPT_ROOT)


def extract_source(archive: Path, target: Path) -> None:
    # Called only for the private snapshot accepted by the canonical verifier.
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            path = target / member.name.removeprefix("./")
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                stream = bundle.extractfile(member)
                if stream is None:
                    raise PreparationError("source member is not a file")
                with stream, path.open("xb") as output:
                    shutil.copyfileobj(stream, output)
            path.chmod(member.mode)


def verify_layout(root: Path, source_sha: str, generated_at: str) -> dict:
    venv = root / ".venv"
    for path in root.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(root):
            raise PreparationError("installed symlink escapes release")
        if path.suffix in {".pyc", ".pyo"} or path.name in {"__pycache__", ".pytest_cache", ".mypy_cache"}:
            raise PreparationError("installed cache or bytecode is prohibited")
    python = venv / "bin/python"
    if python.is_symlink() or not python.is_file():
        raise PreparationError("release interpreter must contain local bytes")
    for path in (venv / "bin").iterdir():
        if path.is_file():
            with path.open("rb") as stream:
                first = stream.read(4096).split(b"\n", 1)[0]
            if first.startswith(b"#!") and first != ("#!" + str(python)).encode():
                raise PreparationError("console script is not bound to this release")
    metadata = json.loads((root / "BUILD_INFO.json").read_text())
    if metadata.get("git_sha") != source_sha or (root / "SOURCE.COMMIT").read_text() != source_sha + "\n":
        raise PreparationError("installed source identity mismatch")
    manifest = build(root, source_sha, generated_at)
    (root / "INSTALLED_MANIFEST.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return manifest


def prepare(*, archive: Path, archive_sha: str, source_sha: str, builder_sha: str,
            wheelhouse: Path, hashed_lock: Path, output: Path, generated_at: str) -> dict:
    if sys.version_info[:2] != (3, 11):
        raise PreparationError("host preparation requires Python 3.11")
    if datetime.fromisoformat(generated_at.replace("Z", "+00:00")).tzinfo is None:
        raise PreparationError("build timestamp must include timezone")
    output = output.absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise PreparationError("output and its ancestors must not be symlinks")
    if output.exists() or not output.parent.is_dir():
        raise PreparationError("output must be absent under an existing parent")
    # Snapshot first. Validation and installation consume these same bytes,
    # even if the operator's supplied bundle is replaced concurrently.
    with tempfile.TemporaryDirectory(prefix="codestra-offline-") as scratch:
        private = Path(scratch)
        plain_file(archive, limit=MAX_ARCHIVE_BYTES)
        selected = private / "source.tar.gz"
        shutil.copyfile(archive, selected)
        verify_archive(selected, archive_sha, source_sha, builder_sha)
        source = private / "source"
        source.mkdir()
        extract_source(selected, source)
        plain_file(hashed_lock, limit=65536)
        lock = private / "requirements.offline.lock"
        shutil.copyfile(hashed_lock, lock)
        wheels = private / "wheels"
        wheels.mkdir()
        if wheelhouse.is_symlink() or not wheelhouse.is_dir():
            raise PreparationError("wheelhouse must be an ordinary directory")
        for wheel in wheelhouse.iterdir():
            plain_file(wheel, limit=MAX_WHEEL_BYTES)
            shutil.copyfile(wheel, wheels / wheel.name)
        wheel_evidence = verify_wheels(source / "docker/requirements.lock", lock, wheels)
        # All independent input validation precedes the only release mkdir.
        output.mkdir(mode=0o755)
        try:
            for directory in SOURCE_DIRS:
                shutil.copytree(source / directory, output / directory)
            for original, name in [
                (source / "pyproject.toml", "pyproject.toml"),
                (source / "docker/requirements.lock", "requirements.lock"),
            ]:
                shutil.copyfile(original, output / name)
            (output / "SOURCE.COMMIT").write_text(source_sha + "\n")
            env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PIP_CONFIG_FILE": os.devnull,
                   "PIP_NO_CACHE_DIR": "1", "PYTHONNOUSERSITE": "1"}
            run([sys.executable, "-B", str(output / "scripts/generate_build_info.py"),
                 "--git-sha", source_sha, "--environment", "production-candidate",
                 "--built-at", generated_at, "--output", str(output / "BUILD_INFO.json")], env=env, cwd=output)
            run([sys.executable, "-B", "-m", "venv", "--copies", str(output / ".venv")], env=env, cwd=output)
            python = output / ".venv/bin/python"
            run([str(python), "-B", "-m", "pip", "--isolated", "install", "--no-index",
                 "--no-deps", "--require-hashes", "--no-compile", "--no-cache-dir",
                 "--only-binary=:all:", "--find-links", str(wheels), "-r", str(lock)], env=env, cwd=output)
            run([str(python), "-B", "-m", "pip", "--isolated", "check"], env=env, cwd=output)
            run([str(python), "-B", "-m", "pip", "--isolated", "uninstall", "--yes",
                 "pip", "setuptools", "wheel"], env=env, cwd=output)
            # ensurepip may generate seed bytecode; no caches ship in the final layout.
            for path in sorted(output.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if path.name == "__pycache__" and path.is_dir():
                    shutil.rmtree(path)
                elif path.suffix in {".pyc", ".pyo"}:
                    path.unlink()
            # The service uses source via PYTHONPATH. Probe from the final path.
            env["PYTHONPATH"] = str(output / "src")
            probe = (
                "import pathlib,sys,codestra_vicidial,fastapi,pymysql,uvicorn;"
                "assert sys.version_info[:2]==(3,11);"
                "assert pathlib.Path(sys.prefix)==pathlib.Path('.venv').absolute();"
                "assert pathlib.Path(codestra_vicidial.__file__).resolve().is_relative_to(pathlib.Path('src').absolute())"
            )
            run([str(python), "-B", "-c", probe], env=env, cwd=output)
            manifest = verify_layout(output, source_sha, generated_at)
            for path in output.rglob("*"):
                if not path.is_symlink():
                    path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
            run([str(python), "-B", str(output / "scripts/verify_installed_manifest.py"),
                 "--root", str(output), "--manifest", str(output / "INSTALLED_MANIFEST.json")], env=env, cwd=output)
            return {
                "schema_version": 1, "status": "prepared_only", "source_sha": source_sha,
                "builder_sha": builder_sha, "artifact_sha256": archive_sha,
                "installed_manifest_sha256": digest(output / "INSTALLED_MANIFEST.json"),
                "offline_lock_sha256": digest(lock), "interpreter_sha256": digest(python),
                "generated_at": generated_at, "file_count": len(manifest["files"]),
                "wheels": wheel_evidence,
                "activation_authorized": False,
                "outstanding_verification": ["signature", "provenance", "protected_ancestry",
                                             "service_identity", "state_compatibility", "backup_restore"],
            }
        except Exception:
            # Preserve failed output for diagnosis. Never delete/overwrite an
            # existing release, predecessor, current pointer or shared state.
            raise PreparationError("preparation failed; incomplete output retained and cannot be reused") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("archive", "wheelhouse", "hashed-lock", "output"):
        parser.add_argument("--" + flag, type=Path, required=True)
    for flag in ("archive-sha", "source-sha", "builder-sha", "generated-at"):
        parser.add_argument("--" + flag, required=True)
    args = parser.parse_args()
    try:
        result = prepare(**vars(args))
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile):
        print("OFFLINE_PREPARATION=FAILED", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
