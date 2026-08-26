from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
DOCKER = ROOT / "vicidial/docker"
LIVE_FLAGS = (
    "ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING", "ENABLE_AGENT_SYNC",
    "ENABLE_LEAD_PUBLICATION", "ENABLE_CALLBACK_WRITES",
    "ENABLE_TRANSFER_POLICY_SYNC", "ENABLE_PSTN_DIALING",
    "ENABLE_SMS_DELIVERY", "ENABLE_EMAIL_DELIVERY", "ENABLE_EXTERNAL_DELIVERY",
)


def test_dockerfile_is_pinned_multistage_nonroot_and_amd64_buildable():
    text = (DOCKER / "Dockerfile").read_text()
    assert text.count("FROM ${PYTHON_IMAGE}") == 2
    assert "python:3.11.16-alpine@sha256:" in text
    assert "USER 10001:10001" in text
    assert "SOURCE.COMMIT" in text and "RELEASE_MANIFEST.json" in text
    assert "/health" in text and "/ready" in text and "/version" in text
    assert "9443" not in text
    assert '"libcrypto3=${OPENSSL_PACKAGE_VERSION}"' in text
    assert '"libssl3=${OPENSSL_PACKAGE_VERSION}"' in text
    assert "OPENSSL_PACKAGE_VERSION=3.5.8-r0" in text


def test_runtime_security_regression_is_bounded_and_version_pinned():
    text = (DOCKER / "security_regression.py").read_text()
    assert "RLIMIT_CPU" in text and "RLIMIT_AS" in text
    assert "process.join(5)" in text
    assert "(3, 11, 16)" in text
    assert "(2, 8, 0)" in text
    assert "Morsel" in text and "pickle" in text and "js_output" in text


def test_vulnerability_policy_is_exact_and_verifier_fails_closed(tmp_path):
    from vicidial.scripts.verify_vulnerability_adjudication import verify
    policy = json.loads((ROOT / "vicidial/config/vulnerability-adjudication.json").read_text())
    assert {(x["vulnerability_id"], x["package"], x["installed_version"])
            for x in policy["adjudications"]} == {
        ("CVE-2026-4224", "python", "3.11.16"),
        ("CVE-2026-7210", "python", "3.11.16"),
        ("CVE-2026-3644", "python", "3.11.16"),
    }
    metadata = {"Config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}}}
    regression = {"python_short": "3.11.16", "implementation": "cpython",
                  "expat_short": "2.8.3", "openssl_short": "3.5.8",
                  "libcrypto3": "3.5.8-r0", "libssl3": "3.5.8-r0",
                  **{x["required_regression"]: "PASS" for x in policy["adjudications"]}}
    match = {"vulnerability": {"id": "CVE-2026-4224", "severity": "High"},
             "artifact": {"name": "python", "version": "3.11.16"}}
    assert verify(policy, {"matches": [match]}, metadata, regression, "a" * 40,
                  today=__import__("datetime").date(2026, 8, 26))["status"] == "PASS"
    assert verify(policy, {"matches": [match]}, [metadata], regression, "a" * 40,
                  today=__import__("datetime").date(2026, 8, 26))["status"] == "PASS"
    match["artifact"]["version"] = "3.11.15"
    with pytest.raises(ValueError, match="unadjudicated High"):
        verify(policy, {"matches": [match]}, metadata, regression, "a" * 40,
               today=__import__("datetime").date(2026, 8, 26))


@pytest.mark.parametrize("name", ["compose.production.yml", "compose.shadow.yml"])
def test_compose_hardening_and_disabled_capabilities(name):
    document = yaml.safe_load((DOCKER / name).read_text())
    service = next(iter(document["services"].values()))
    expected_port = "127.0.0.1:18097:8097" if "shadow" in name else "127.0.0.1:8097:8097"
    assert service["ports"] == [expected_port]
    assert not any(port.startswith("0.0.0.0:") for port in service["ports"])
    assert service["user"] == "10001:10001"
    assert service["read_only"] is True
    assert service["init"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert all("tmpfs" not in volume for volume in service.get("volumes", []))
    assert service["tmpfs"]
    assert service["pids_limit"] == 128
    assert "/var/run/docker.sock" not in json.dumps(document)
    assert "@${VICIDIAL_ADAPTER_IMAGE_DIGEST" in service["image"]
    for flag in LIVE_FLAGS:
        assert service["environment"][flag] == "false"
    assert document["secrets"]


def test_entrypoint_rejects_enabled_live_capability():
    text = (DOCKER / "entrypoint.sh").read_text()
    assert all(flag in text for flag in LIVE_FLAGS)
    assert "release tuple mismatch" in text


def test_workflow_builds_without_push_and_generates_evidence():
    text = (ROOT / ".github/workflows/vicidial-adapter-container.yml").read_text()
    assert "platforms: linux/amd64" in text
    assert "push: false" in text
    assert "sbom: true" in text
    assert "provenance: mode=max" in text
    assert "upload-artifact" in text


@pytest.mark.skipif(not os.environ.get("VICIDIAL_CONTAINER_TEST_IMAGE"),
                    reason="set VICIDIAL_CONTAINER_TEST_IMAGE after local build")
def test_local_image_health_release_tuple_and_restart(tmp_path):
    engine = os.environ.get("VICIDIAL_CONTAINER_ENGINE", "podman")
    if shutil.which(engine) is None or platform.machine() not in {"x86_64", "amd64"}:
        pytest.skip("a configured linux/amd64 container engine is required")
    image = os.environ["VICIDIAL_CONTAINER_TEST_IMAGE"]
    name = "vicidial-r3-test-" + uuid.uuid4().hex[:10]
    secret = tmp_path / "adapter-hmac"
    secret.write_bytes(b"x" * 32)
    command = [
        engine, "run", "--detach", "--name", name, "--network", "none",
        "--read-only", "--user", "10001:10001", "--cap-drop", "all",
        "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev",
        "--tmpfs", "/run:rw,noexec,nosuid,nodev", "--volume",
        f"{secret}:/run/secrets/adapter-hmac:ro", image,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        probe = (
            "import json,urllib.request; print(json.dumps(json.load("
            "urllib.request.urlopen('http://127.0.0.1:8097%s',timeout=1))))"
        )
        def get(path):
            output = subprocess.check_output(
                [engine, "exec", name, "python", "-c", probe % path], text=True)
            return json.loads(output)

        for _ in range(40):
            try:
                if get("/health") == {"status": "live"}:
                    break
            except subprocess.CalledProcessError:
                time.sleep(0.25)
        else:
            raise AssertionError(subprocess.check_output(
                [engine, "logs", name], text=True, stderr=subprocess.STDOUT))
        version = get("/version")
        ready = get("/ready")
        assert version["verified"] is True and version["git_sha"]
        assert ready["status"] == "ready" and ready["writes_enabled"] is False
        subprocess.run([engine, "restart", name], check=True, capture_output=True)
        time.sleep(1)
        assert get("/version")["git_sha"] == version["git_sha"]
    finally:
        subprocess.run([engine, "rm", "--force", name], capture_output=True)
