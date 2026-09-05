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
    "ENABLE_VICIDIAL_WRITES",
    "ENABLE_PROVISIONING",
    "ENABLE_AGENT_SYNC",
    "ENABLE_AGENT_PROVISIONING",
    "ENABLE_LEAD_PUBLICATION",
    "ENABLE_LIVE_CALL_CONTROL",
    "ENABLE_WARM_TRANSFER",
    "ENABLE_IVR_ROUTING",
    "ENABLE_IVR_CALLBACKS",
    "ENABLE_PRODUCTION_DIALING",
    "ENABLE_SMS_DELIVERY",
    "ENABLE_EMAIL_DELIVERY",
    "ENABLE_EXTERNAL_DELIVERY",
    "ENABLE_AI_ACTIONS",
)
DEPRECATED_FLAGS = (
    "ENABLE_CALLBACK_WRITES",
    "ENABLE_TRANSFER_POLICY_SYNC",
    "ENABLE_PSTN_DIALING",
)


def test_container_workflow_runs_for_gateway_signer_changes():
    workflow = (ROOT / ".github/workflows/vicidial-adapter-container.yml").read_text()
    assert '- "campaign_provisioning/**"' in workflow


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
    assert '"libuuid=${LIBUUID_PACKAGE_VERSION}"' in text
    assert "OPENSSL_PACKAGE_VERSION=3.5.8-r0" in text
    assert "LIBUUID_PACKAGE_VERSION=2.42.3-r0" in text
    assert "https://github.com/appolon1908-hue/Vicidialer-Codestra" in text
    assert "codestra_vicidial.production_app:app" in text
    for flag in LIVE_FLAGS:
        assert f"{flag}=false" in text
    for flag in DEPRECATED_FLAGS:
        assert f"{flag}=false" not in text


def test_runtime_security_regression_is_bounded_and_version_pinned():
    text = (DOCKER / "security_regression.py").read_text()
    assert "RLIMIT_CPU" in text and "RLIMIT_AS" in text
    assert "process.join(5)" in text
    assert "(3, 11, 16)" in text
    assert "(2, 8, 0)" in text
    assert "Morsel" in text and "pickle" in text and "js_output" in text


def test_provenance_build_type_records_selected_engine():
    text = (DOCKER / "generate_provenance.py").read_text()
    assert 'buildtypes/{engine}/v1' in text
    assert 'buildtypes/podman/v1' not in text


def test_vulnerability_policy_is_exact_and_verifier_fails_closed(tmp_path):
    from vicidial.scripts.verify_vulnerability_adjudication import verify

    policy = json.loads((ROOT / "vicidial/config/vulnerability-adjudication.json").read_text())
    assert {(x["vulnerability_id"], x["package"], x["installed_version"])
            for x in policy["adjudications"]} == {
        ("CVE-2026-4224", "python", "3.11.16"),
        ("CVE-2026-7210", "python", "3.11.16"),
        ("CVE-2026-3644", "python", "3.11.16"),
    }
    image_id = "b" * 64
    metadata = {"Id": image_id,
                "Config": {"Labels": {"org.opencontainers.image.revision": "a" * 40}}}
    regression = {"python_short": "3.11.16", "implementation": "cpython",
                      "expat_short": "2.8.3", "openssl_short": "3.5.8",
                      "libcrypto3": "3.5.8-r0", "libssl3": "3.5.8-r0",
                      "libuuid": "2.42.3-r0",
                  **{x["required_regression"]: "PASS" for x in policy["adjudications"]}}
    match = {"vulnerability": {"id": "CVE-2026-4224", "severity": "High"},
             "artifact": {"name": "python", "version": "3.11.16"}}
    grype = {"matches": [match],
             "source": {"type": "image", "target": {
                 "imageID": f"sha256:{image_id}", "manifestDigest": f"sha256:{'c' * 64}"}},
             "descriptor": {"name": "grype", "version": "0.104.0",
                            "timestamp": "2026-08-26T00:00:00Z"}}
    trivy = {"SchemaVersion": 2, "ArtifactName": "codestra/vicidial:test",
             "ArtifactType": "container_image", "CreatedAt": "2026-08-26T00:00:00Z",
             "Metadata": {"ImageID": f"sha256:{image_id}"},
             "Results": [{"Target": "runtime", "Class": "os-pkgs", "Type": "alpine",
                          "Vulnerabilities": []}]}
    assert verify(policy, grype, metadata, regression, "a" * 40,
                  today=__import__("datetime").date(2026, 8, 26),
                  trivy=trivy)["status"] == "PASS"
    assert verify(policy, grype, [metadata], regression, "a" * 40,
                  today=__import__("datetime").date(2026, 8, 26),
                  trivy=trivy)["status"] == "PASS"
    for bad_grype, bad_trivy, error in (
        ({}, trivy, "Grype report is incomplete"),
        (grype, {}, "Trivy report identity is incomplete"),
        (grype, {**trivy, "Results": []}, "Trivy result sections are incomplete"),
        ({**grype, "source": {"type": "image", "target": {
            "imageID": f"sha256:{'e' * 64}",
            "manifestDigest": f"sha256:{'c' * 64}"}}}, trivy,
         "Grype target does not equal exact image"),
    ):
        with pytest.raises(ValueError, match=error):
            verify(policy, bad_grype, metadata, regression, "a" * 40,
                   today=__import__("datetime").date(2026, 8, 26),
                   trivy=bad_trivy)
    match["artifact"]["version"] = "3.11.15"
    with pytest.raises(ValueError, match="unadjudicated High"):
        verify(policy, grype, metadata, regression, "a" * 40,
               today=__import__("datetime").date(2026, 8, 26), trivy=trivy)


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
    for flag in DEPRECATED_FLAGS:
        assert flag not in service["environment"]
    assert document["secrets"]
    if name == "compose.production.yml":
        network = document["networks"]["adapter-ingress"]
        assert network["ipam"]["config"] == [{
            "subnet": "172.30.97.0/29", "gateway": "172.30.97.1"
        }]
        assert service["networks"]["adapter-ingress"]["ipv4_address"] == "172.30.97.2"
        assert service["environment"]["AUTHORIZED_CONTAINER_PROXY_IP"] == "172.30.97.1"


def test_entrypoint_rejects_enabled_live_capability_and_legacy_aliases():
    text = (DOCKER / "entrypoint.sh").read_text()
    assert all(flag in text for flag in LIVE_FLAGS)
    assert all(flag in text for flag in DEPRECATED_FLAGS)
    assert "deprecated feature flag" in text
    assert "release tuple mismatch" in text


def test_workflow_builds_exact_image_and_uploads_only_clean_evidence():
    text = (ROOT / ".github/workflows/vicidial-adapter-container.yml").read_text()
    assert "platforms: linux/amd64" in text
    assert text.count("docker/build-push-action@v6") == 2
    assert "Build runnable linux/amd64 review image" in text
    assert "load: true" in text
    assert "sbom: false" in text and "provenance: false" in text
    assert "Export attested linux/amd64 OCI candidate" in text
    assert "outputs: type=oci,dest=evidence/vicidial-adapter.oci.tar" in text
    assert "sbom: true" in text and "provenance: mode=max" in text
    assert "push: false" in text
    assert "ref: ${{ env.SOURCE_SHA }}" in text
    assert "persist-credentials: false" in text
    assert 'git rev-parse HEAD)\" = \"${SOURCE_SHA}' in text
    assert "git status --porcelain --untracked-files=all" in text
    assert "Enforce exact source secret gate" in text
    assert "Enforce exact-image secret gate" in text
    assert "EVIDENCE_SECRET_SCAN.json" in text
    assert "path: evidence/evidence-export/" in text
    assert "vicidial-adapter-oci-${{ env.SOURCE_SHA }}" in text
    assert "vicidial-host-release-${{ env.SOURCE_SHA }}" not in text
    assert "vicidial-host-release.tar.gz" not in text
    assert "if: always()" not in text
    assert text.count("if: success()") == 2
    assert "sha256sum evidence/vicidial-adapter.oci.tar" not in text
    assert "sha256sum vicidial-adapter.oci.tar > vicidial-adapter.oci.tar.sha256" in text
    assert 'find . -type f ! -name CHECKSUMS.sha256' in text
    assert '> "${export_dir}/CHECKSUMS.sha256"' in text
    assert text.count("docker compose -f vicidial/docker/compose.") == 2
    assert "scripts/vicidial_sync.py" in text
    assert "scripts/canonical_event_client.py" in text
    assert "scripts/vicidial_sync.py.candidate" not in text
    assert "scripts/canonical_event_client.py.reference" not in text


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
        assert ready["status"] == "ready"
        assert ready["checks"]["release"] is True
        assert ready["checks"]["hmac"] is True
        assert ready["checks"]["state_store"] is True
        assert ready["checks"]["repository"] is True
        assert ready["checks"]["kill_switches"] is True
        assert ready["production_dialing"] is False
        assert ready["customer_traffic_authorized"] is False
        subprocess.run([engine, "restart", name], check=True, capture_output=True)
        time.sleep(1)
        assert get("/version")["git_sha"] == version["git_sha"]
        assert get("/ready")["status"] == "ready"
    finally:
        subprocess.run([engine, "rm", "--force", name], capture_output=True)
