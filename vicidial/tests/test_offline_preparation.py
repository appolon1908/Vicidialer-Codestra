"""Offline preparation tests: real archive/wheel bytes; no live activation."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("prepare_host_release", SCRIPTS / "prepare_host_release.py")
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)


def wheel(root, name="example_pkg", version="1.2.3", content=b""):
    path = root / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}-{version}.dist-info/METADATA",
                         f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        archive.writestr(f"{name}/__init__.py", content)
    return path


@pytest.fixture
def dependencies(tmp_path):
    source = tmp_path / "requirements.lock"
    locked = tmp_path / "requirements.offline.lock"
    directory = tmp_path / "wheels"
    directory.mkdir()
    selected = wheel(directory)
    source.write_text("example-pkg==1.2.3\n")
    locked.write_text(f"example_pkg==1.2.3 --hash=sha256:{prep.digest(selected)}\n")
    return source, locked, directory, selected


def test_exact_wheel_identity_and_hash(dependencies):
    source, locked, directory, selected = dependencies
    result = prep.verify_wheels(source, locked, directory)
    assert result == {"example-pkg": {
        "filename": selected.name, "version": "1.2.3", "sha256": prep.digest(selected)
    }}


@pytest.mark.parametrize("change", ["digest", "version", "missing", "extra", "duplicate", "symlink", "nonwheel"])
def test_dependency_drift_rejected_before_install(dependencies, change):
    source, locked, directory, selected = dependencies
    if change == "digest":
        locked.write_text("example_pkg==1.2.3 --hash=sha256:" + "0" * 64 + "\n")
    elif change == "version":
        source.write_text("example-pkg==2.0.0\n")
    elif change == "missing":
        selected.unlink()
    elif change == "extra":
        wheel(directory, "unapproved")
    elif change == "duplicate":
        (directory / "duplicate.whl").write_bytes(selected.read_bytes())
    elif change == "symlink":
        outside = directory.parent / "outside.whl"
        selected.rename(outside)
        selected.symlink_to(outside)
    else:
        (directory / "extra.txt").write_text("unapproved")
    with pytest.raises(prep.PreparationError):
        prep.verify_wheels(source, locked, directory)


@pytest.mark.parametrize("line", [
    "example-pkg>=1.0", "--index-url https://example.invalid",
    "example-pkg @ https://example.invalid/pkg.whl",
    "-e ./local", "example-pkg==1.2.3; python_version>'3'",
    "example-pkg==1.2.3 --hash=sha256:" + "0" * 64 + " --extra-index-url https://example.invalid",
    "example-pkg==1.2.3\\\n --hash=sha256:" + "0" * 64,
])
def test_lock_cannot_add_dependency_install_options(line):
    with pytest.raises(prep.PreparationError):
        prep.pins(line, hashed=True)


def test_duplicate_normalized_pin_denied():
    with pytest.raises(prep.PreparationError):
        prep.pins("example_pkg==1\nexample-pkg==1\n", hashed=False)


def test_ambiguous_wheel_metadata_denied(dependencies):
    source, locked, directory, selected = dependencies
    with zipfile.ZipFile(selected, "a") as archive:
        archive.writestr("another.dist-info/METADATA", "Name: other\nVersion: 1\n")
    locked.write_text(f"example-pkg==1.2.3 --hash=sha256:{prep.digest(selected)}\n")
    with pytest.raises(prep.PreparationError):
        prep.verify_wheels(source, locked, directory)


def test_archive_digest_mismatch_never_executes_verifier(tmp_path, monkeypatch):
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"not-an-archive")
    calls = []
    monkeypatch.setattr(prep, "run", lambda *a, **k: calls.append(a))
    with pytest.raises(prep.PreparationError, match="digest mismatch"):
        prep.verify_archive(archive, "0" * 64, "a" * 40, "b" * 40)
    assert calls == []


def test_subprocess_errors_are_redacted(tmp_path, monkeypatch):
    marker = "CREDENTIAL_MUST_NOT_APPEAR"
    monkeypatch.setattr(prep.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 1, marker.encode(), marker.encode()))
    with pytest.raises(prep.PreparationError) as failure:
        prep.run(["opaque"], env={}, cwd=tmp_path)
    assert marker not in str(failure.value)
    assert failure.value.__cause__ is None


def fixture_source(root):
    for name in prep.SOURCE_DIRS:
        (root / name).mkdir(parents=True)
    (root / "docker").mkdir()
    (root / "docker/requirements.lock").write_text("example-pkg==1.2.3\n")
    (root / "pyproject.toml").write_text('[project]\nname="example"\nversion="1"\n')
    (root / "src/module.py").write_text("SYNTHETIC = True\n")


def setup_preparation(tmp_path, monkeypatch, dependencies):
    source_lock, locked, wheels, selected = dependencies
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"synthetic-reviewed-archive")
    def extract(_archive, target):
        fixture_source(target)
    monkeypatch.setattr(prep, "extract_source", extract)
    monkeypatch.setattr(prep, "verify_archive", lambda *a: None)
    monkeypatch.setattr(prep, "sys", SimpleNamespace(version_info=(3, 11), executable=sys.executable))
    return dict(archive=archive, archive_sha=hashlib.sha256(archive.read_bytes()).hexdigest(),
                source_sha="a" * 40, builder_sha="b" * 40, wheelhouse=wheels,
                hashed_lock=locked, output=tmp_path / "final-release",
                generated_at="2026-09-10T19:00:00Z")


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_never_overwrites_existing_release_or_current(tmp_path, monkeypatch, dependencies, kind):
    args = setup_preparation(tmp_path, monkeypatch, dependencies)
    predecessor = tmp_path / "predecessor"
    predecessor.mkdir()
    marker = predecessor / "marker"
    marker.write_text("unchanged")
    output = args["output"]
    if kind == "symlink":
        output.symlink_to(predecessor, target_is_directory=True)
    else:
        output.mkdir()
    with pytest.raises(prep.PreparationError):
        prep.prepare(**args)
    assert marker.read_text() == "unchanged"
    assert output.exists()


def test_bad_dependency_input_creates_no_release(tmp_path, monkeypatch, dependencies):
    args = setup_preparation(tmp_path, monkeypatch, dependencies)
    args["hashed_lock"].write_text("example-pkg==9 --hash=sha256:" + "0" * 64 + "\n")
    calls = []
    monkeypatch.setattr(prep, "run", lambda *a, **kw: calls.append(a))
    with pytest.raises(prep.PreparationError):
        prep.prepare(**args)
    assert not args["output"].exists()
    assert calls == []


def test_partial_failure_preserves_output_and_predecessor(tmp_path, monkeypatch, dependencies):
    args = setup_preparation(tmp_path, monkeypatch, dependencies)
    predecessor = tmp_path / "previous"
    predecessor.mkdir()
    (predecessor / "marker").write_bytes(b"unchanged")
    current = tmp_path / "current"
    current.symlink_to(predecessor, target_is_directory=True)
    def fail(*a, **kw):
        raise RuntimeError("SECRET")
    monkeypatch.setattr(prep, "run", fail)
    with pytest.raises(prep.PreparationError, match="incomplete output retained") as failure:
        prep.prepare(**args)
    assert "SECRET" not in str(failure.value)
    assert args["output"].is_dir()
    assert current.resolve() == predecessor
    assert (predecessor / "marker").read_bytes() == b"unchanged"


def test_offline_commands_and_prepared_only_receipt(tmp_path, monkeypatch, dependencies):
    args = setup_preparation(tmp_path, monkeypatch, dependencies)
    commands = []
    def fake_run(command, *, env, cwd):
        commands.append(command)
        assert not any(name in env for name in ("HTTP_PROXY", "HTTPS_PROXY", "PIP_INDEX_URL"))
        root = args["output"]
        if "generate_build_info.py" in command[2]:
            (root / "BUILD_INFO.json").write_text(json.dumps({"git_sha": args["source_sha"]}))
        if "venv" in command:
            binary = root / ".venv/bin"
            binary.mkdir(parents=True)
            (binary / "python").write_bytes(b"synthetic-interpreter")
            (root / ".venv/seed/__pycache__").mkdir(parents=True)
            (root / ".venv/seed/__pycache__/seed.pyc").write_bytes(b"cache")
    monkeypatch.setattr(prep, "run", fake_run)
    result = prep.prepare(**args)
    install = next(cmd for cmd in commands if "install" in cmd)
    for flag in ("--no-index", "--no-deps", "--require-hashes", "--no-compile", "--no-cache-dir", "--only-binary=:all:"):
        assert flag in install
    assert any(cmd[3:5] == ["pip", "--isolated"] and "check" in cmd for cmd in commands)
    assert result["status"] == "prepared_only"
    assert result["activation_authorized"] is False
    assert "backup_restore" in result["outstanding_verification"]
    assert result["installed_manifest_sha256"] == prep.digest(args["output"] / "INSTALLED_MANIFEST.json")
    assert not list(args["output"].rglob("*.pyc"))
    assert not any("systemctl" in cmd or "asterisk" in cmd for cmd in commands)
    assert not (tmp_path / "current").exists()


@pytest.mark.parametrize("problem", ["escaping_symlink", "bytecode", "wrong_shebang", "wrong_identity"])
def test_final_layout_rejects_unusable_predecessor_properties(tmp_path, problem):
    root = tmp_path / "release"
    root.mkdir()
    fixture_source(root)
    (root / "requirements.lock").write_text("example-pkg==1.2.3\n")
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").write_bytes(b"binary")
    (root / "SOURCE.COMMIT").write_text("a" * 40 + "\n")
    (root / "BUILD_INFO.json").write_text(json.dumps({"git_sha": "a" * 40}))
    if problem == "escaping_symlink":
        (root / ".venv/foreign").symlink_to(tmp_path)
    elif problem == "bytecode":
        (root / "src/module.pyc").write_bytes(b"cache")
    elif problem == "wrong_shebang":
        (root / ".venv/bin/uvicorn").write_text("#!/old/release/.venv/bin/python\n")
    else:
        (root / "SOURCE.COMMIT").write_text("b" * 40 + "\n")
    with pytest.raises(prep.PreparationError):
        prep.verify_layout(root, "a" * 40, "2026-09-10T19:00:00Z")
    assert not (root / "INSTALLED_MANIFEST.json").exists()
