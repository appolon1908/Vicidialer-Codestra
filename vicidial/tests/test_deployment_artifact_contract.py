import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "vicidial/docker/compose.production.yml"
SCRIPT = ROOT / "vicidial/scripts/validate_deployment_artifact.py"
spec = importlib.util.spec_from_file_location("deployment_artifact", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def manifest(**changes):
    digest = "sha256:" + "b" * 64
    value = {
        "artifact_kind": "oci-image",
        "artifact_reference": f"ghcr.io/appolon1908-hue/vicidialer-codestra@{digest}",
        "artifact_digest": digest,
        "source_sha": "a" * 40,
    }
    value.update(changes)
    return value


def test_verified_manifest_reference_renders_into_production_compose(tmp_path):
    reference = module.validate(manifest(), COMPOSE.read_text(encoding="utf-8"))
    rendered = re.sub(r"\$\{VICIDIAL_ADAPTER_IMAGE:[^}]+\}", reference, COMPOSE.read_text())
    assert f"image: {reference}" in rendered
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode == 0:
        env = os.environ | {
            "VICIDIAL_ADAPTER_IMAGE": reference,
            "VICIDIAL_DB_SOCKET_DIR": str(tmp_path),
            "CODESTRA_ADAPTER_HMAC_SECRET_FILE": str(tmp_path / "hmac"),
            "CODESTRA_VICIDIAL_DB_SECRET_FILE": str(tmp_path / "db"),
        }
        result = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE), "config"], env=env,
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert reference in result.stdout


@pytest.mark.parametrize("change", [
    {"artifact_reference": "ghcr.io/other/repo@sha256:" + "b" * 64},
    {"artifact_reference": "ghcr.io/appolon1908-hue/vicidialer-codestra:latest"},
    {"artifact_digest": "sha256:" + "c" * 64},
    {"artifact_kind": "source-bundle"},
    {"source_sha": "short"},
])
def test_wrong_repository_mutability_or_binding_is_rejected(change):
    with pytest.raises(ValueError):
        module.validate(manifest(**change), COMPOSE.read_text(encoding="utf-8"))


def test_cli_emits_only_complete_reference(tmp_path):
    release = tmp_path / "release.json"
    release.write_text(json.dumps(manifest()), encoding="utf-8")
    result = subprocess.run(
        ["python", str(SCRIPT), "--release-manifest", str(release), "--compose", str(COMPOSE)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("@sha256:" + "b" * 64)
