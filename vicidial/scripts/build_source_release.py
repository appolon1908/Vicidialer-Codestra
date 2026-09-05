#!/usr/bin/env python3
"""Build the deterministic source archive consumed by the Server B installer."""
from __future__ import annotations
import argparse, gzip, hashlib, json, os, subprocess, tarfile, tempfile
from pathlib import Path

EXCLUDED = {".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", "__pycache__", "evidence", "reports", ".venv"}
SUFFIXES = {".pyc", ".pyo", ".pem", ".key", ".crt", ".cer", ".der", ".p12", ".pfx", ".jks", ".keystore", ".sqlite", ".sqlite3", ".db", ".sql", ".dump"}
SENSITIVE_NAMES = {"credentials.json", "secrets.json", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc"}
REQUIRED = {"pyproject.toml", "src/codestra_vicidial/app.py", "src/codestra_vicidial/internal_calling.py", "scripts/validate_runtime.py"}

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def source_files(root: Path) -> list[Path]:
    result = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in EXCLUDED or part.endswith(".egg-info") for part in relative.parts) or path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() in SUFFIXES or path.name.startswith(".env") or path.name.lower() in SENSITIVE_NAMES:
            raise ValueError("sensitive or runtime-generated file is prohibited: " + relative.as_posix())
        result.append(relative)
    missing = REQUIRED - {item.as_posix() for item in result}
    if missing:
        raise ValueError("required runtime files missing: " + ",".join(sorted(missing)))
    return sorted(result, key=lambda item: item.as_posix())

def source_modes(root: Path) -> dict[str, int]:
    """Use the checked-out source commit's executable bit, never host chmod."""
    records = subprocess.check_output(["git", "-C", str(root), "ls-tree", "-rz", "HEAD", "--", "."])
    modes = {}
    for record in records.split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, _ = metadata.split()
        if kind == b"blob" and mode in {b"100644", b"100755"}:
            modes[os.fsdecode(name)] = 0o755 if mode == b"100755" else 0o644
    return modes


def build(root: Path, source_sha: str, builder_sha: str, output: Path) -> dict:
    if len(source_sha) != 40 or any(char not in "0123456789abcdef" for char in source_sha):
        raise ValueError("source SHA must be 40 lowercase hexadecimal characters")
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary)
        files = source_files(root)
        modes = source_modes(root)
        for relative in files:
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((root / relative).read_bytes())
            os.chmod(destination, modes[relative.as_posix()])
        (stage / "SOURCE_SHA").write_text(source_sha + "\n", encoding="ascii")
        sums = [f"{sha256(path)}  {path.relative_to(stage).as_posix()}" for path in sorted(stage.rglob("*")) if path.is_file()]
        (stage / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="ascii")
        manifest = {"schema_version": 1, "artifact_type": "codestra-vicidial-server-b-source-release", "source_repository": "appolon1908-hue/Vicidialer-Codestra", "source_sha": source_sha, "builder_workflow_sha": builder_sha, "archive_root": ".", "installer_contract": "systemd-python-source-v1", "file_count": len(sums), "checksums_sha256": sha256(stage / "SHA256SUMS")}
        manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
        (stage / "SOURCE_RELEASE_MANIFEST.json").write_bytes(manifest_bytes)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(stage.rglob("*"), key=lambda item: item.relative_to(stage).as_posix()):
                info = archive.gettarinfo(str(path), arcname="./" + path.relative_to(stage).as_posix())
                info.uid = info.gid = 0; info.uname = info.gname = "root"; info.mtime = 0
                info.mode = 0o755 if path.is_dir() else modes.get(path.relative_to(stage).as_posix(), 0o644)
                if path.is_dir(): archive.addfile(info)
                else:
                    with path.open("rb") as stream: archive.addfile(info, stream)
    os.chmod(output, 0o644)
    return {**manifest, "archive_sha256": sha256(output), "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest()}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True); parser.add_argument("--source-sha", required=True)
    parser.add_argument("--builder-sha", required=True); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args(); result = build(args.source_root.resolve(), args.source_sha, args.builder_sha, args.output)
    args.metadata.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n"); os.chmod(args.metadata, 0o644); print("SOURCE_RELEASE_SHA256=" + result["archive_sha256"]); return 0
if __name__ == "__main__": raise SystemExit(main())
