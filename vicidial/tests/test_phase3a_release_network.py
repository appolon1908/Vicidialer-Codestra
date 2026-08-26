from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
VICIDIAL = ROOT / "vicidial"
DEPLOY = ROOT / "deploy/vicidial-restricted-adapter"
SHA = "a" * 40


def load_script(name: str):
    path = VICIDIAL / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("canonical,deployment", [
    ("ops/codestra-vicidial-restricted-adapter.service", "codestra-vicidial-restricted-adapter.service"),
    ("ops/codestra-vicidial-restricted-adapter-health.service", "codestra-vicidial-restricted-adapter-health.service"),
    ("ops/codestra-vicidial-restricted-adapter-health.timer", "codestra-vicidial-restricted-adapter-health.timer"),
    ("ops/apache/codestra-vicidial-restricted-adapter.include", "apache-private-ingress.include"),
    ("ops/firewalld/codestra-vicidial-adapter-rich-rule.xml.example", "firewalld-private-ingress.xml.example"),
    ("config/adapter.env.example", "adapter.env.example"),
])
def test_deployment_copy_matches_canonical(canonical: str, deployment: str) -> None:
    assert (VICIDIAL / canonical).read_bytes() == (DEPLOY / deployment).read_bytes()


def test_production_port_and_process_contract() -> None:
    files = [path for path in DEPLOY.rglob("*") if path.is_file()]
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "9443" not in text
    unit = (VICIDIAL / "ops/codestra-vicidial-restricted-adapter.service").read_text()
    assert "python -m uvicorn" in unit
    assert "--host 127.0.0.1 --port 8097" in unit
    assert ".venv/bin/uvicorn" not in unit
    apache = (VICIDIAL / "ops/apache/codestra-vicidial-restricted-adapter.include").read_text()
    assert "10.40.0.2:8443" in apache
    assert "Require ip 10.40.0.1" in apache


def test_staging_unit_tolerates_absent_optional_reconciliation_policy() -> None:
    unit = (VICIDIAL / "ops/codestra-vicidial-restricted-adapter.service").read_text()
    assert "ReadOnlyPaths=-/etc/codestra/reconciliation-policy.json" in unit
    assert "ReadOnlyPaths=/etc/codestra/reconciliation-policy.json" not in unit


def test_certificate_renewal_pins_python_311() -> None:
    unit = (VICIDIAL / "ops/certificate-renewal/codestra-vicidial-certificate-renewal.service").read_text()
    assert unit.count("ExecStart=/usr/bin/python3.11 ") == 2
    assert "ExecStart=/usr/bin/python3 " not in unit


def test_deployment_authority_is_immutable() -> None:
    config = (VICIDIAL / "config/vicidial-server.env.example").read_text()
    assert "DEPLOYMENT_BRANCH" not in config
    assert "DEPLOYMENT_PROTECTED_SHA=" in config
    assert "DEPLOYMENT_ARTIFACT_DIGEST=sha256:" in config


def test_manifest_is_complete_reproducible_and_detects_change(tmp_path: Path) -> None:
    generator = load_script("generate_release_manifest.py")
    first = generator.build(ROOT, SHA, "2026-08-25T00:00:00Z")
    second = generator.build(ROOT, SHA, "2026-08-25T00:00:00Z")
    assert first == second
    paths = {item["path"] for item in first["files"]}
    assert "vicidial/src/codestra_vicidial/security.py" in paths
    assert "vicidial/ops/codestra-vicidial-restricted-adapter.service" in paths
    assert "deploy/vicidial-restricted-adapter/codestra-vicidial-restricted-adapter.service" in paths
    assert "vicidial/tests/test_phase3a_release_network.py" in paths
    assert ".github/workflows/vicidial-adapter-container.yml" in paths
    assert all("size" in item and "executable" in item and "classification" in item
               for item in first["files"])
    document = json.loads(json.dumps(first))
    document["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="does not match"):
        generator.verify(ROOT, document)


def test_installed_layout_manifest_is_flattened_and_tamper_evident(tmp_path: Path) -> None:
    generator = load_script("generate_installed_manifest.py")
    for directory in generator.INCLUDED:
        target = tmp_path / directory
        target.mkdir()
        (target / "controlled.txt").write_text(directory + "\n")
    document = generator.build(tmp_path, SHA, "2026-08-25T00:00:00Z")
    paths = {item["path"] for item in document["files"]}
    assert "src/controlled.txt" in paths
    assert all(not path.startswith("vicidial/") for path in paths)
    unit = (VICIDIAL / "ops/codestra-vicidial-restricted-adapter.service").read_text()
    assert "scripts/verify_installed_manifest.py" in unit
    assert "INSTALLED_MANIFEST.json" in unit


def test_release_tuple_requires_every_identity(tmp_path: Path) -> None:
    verifier = load_script("verify_release_tuple.py")
    release = tmp_path / SHA
    release.mkdir()
    (release / "SOURCE.COMMIT").write_text(SHA + "\n")
    (release / "BUILD_INFO.json").write_text(json.dumps({"git_sha": SHA}))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"source_sha": SHA}))
    version = tmp_path / "version.json"
    version.write_text(json.dumps({"git_sha": SHA}))
    assert verifier.verify(release, manifest, version) == SHA
    version.write_text(json.dumps({"git_sha": "b" * 40}))
    with pytest.raises(ValueError, match="tuple mismatch"):
        verifier.verify(release, manifest, version)
