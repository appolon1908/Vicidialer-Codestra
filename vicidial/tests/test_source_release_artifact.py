import hashlib, json, subprocess, sys, tarfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "vicidial/scripts/build_source_release.py"
SHA = "75f48ffe9f4a48f76bc5ed0ecb3dc5770933d98a"

def build(tmp_path, name):
    output = tmp_path / name; metadata = tmp_path / f"{name}.json"
    subprocess.run([sys.executable, str(SCRIPT), "--source-root", str(ROOT / "vicidial"), "--source-sha", SHA, "--builder-sha", "b" * 40, "--output", str(output), "--metadata", str(metadata)], check=True)
    return output, json.loads(metadata.read_text())

def test_archive_matches_existing_installer_contract(tmp_path):
    archive, metadata = build(tmp_path, "release.tar.gz")
    with tarfile.open(archive, "r:gz") as bundle:
        names = set(bundle.getnames()); assert {"./pyproject.toml", "./src/codestra_vicidial/app.py", "./scripts/validate_runtime.py"} <= names
        assert bundle.extractfile("./SOURCE_SHA").read() == (SHA + "\n").encode()
        manifest_bytes = bundle.extractfile("./SOURCE_RELEASE_MANIFEST.json").read(); manifest = json.loads(manifest_bytes)
        assert manifest["source_sha"] == SHA and manifest["installer_contract"] == "systemd-python-source-v1"
        sums = bundle.extractfile("./SHA256SUMS").read().decode(); assert "  pyproject.toml\n" in sums and "  scripts/validate_runtime.py\n" in sums
        assert all(".git" not in name and "evidence/" not in name and "reports/" not in name for name in names)
    assert metadata["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert metadata["manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()

def test_archive_is_deterministic(tmp_path):
    first, _ = build(tmp_path, "first.tar.gz"); second, _ = build(tmp_path, "second.tar.gz"); assert first.read_bytes() == second.read_bytes()

def test_archive_is_accepted_by_existing_pip_install_step(tmp_path):
    archive, _ = build(tmp_path, "release.tar.gz"); extracted = tmp_path / "release"; extracted.mkdir()
    with tarfile.open(archive, "r:gz") as bundle: bundle.extractall(extracted, filter="data")
    target = tmp_path / "installed"
    result = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(extracted)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    # The host installer reuses its release venv (with the locked modern
    # setuptools) and executes this exact no-dependency local-project shape.
    # Older distribution pip may label PEP 621 metadata UNKNOWN, so package
    # content is asserted from the extracted input rather than pip internals.
    assert (extracted / "src/codestra_vicidial/app.py").is_file()
