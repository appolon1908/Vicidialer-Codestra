#!/usr/bin/env python3
"""Render deployment preparation only. Never contact a host, registry or provider.

The input is an inventory manifest, NOT trusted deployment authorization. Signature,
provenance, source-CI and runtime gates must be verified separately by the operator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

REPOSITORY = "appolon1908-hue/Vicidialer-Codestra"
REGISTRY = "ghcr.io/appolon1908-hue/vicidialer-codestra"
FLAGS = (
    "ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING", "ENABLE_AGENT_SYNC",
    "ENABLE_AGENT_PROVISIONING", "ENABLE_LEAD_PUBLICATION", "ENABLE_LIVE_CALL_CONTROL",
    "ENABLE_WARM_TRANSFER", "ENABLE_IVR_ROUTING", "ENABLE_IVR_CALLBACKS",
    "ENABLE_INTERNAL_TEST_CALLING", "ENABLE_PRODUCTION_DIALING", "ENABLE_SMS_DELIVERY",
    "ENABLE_EMAIL_DELIVERY", "ENABLE_EXTERNAL_DELIVERY", "ENABLE_AI_ACTIONS",
)
GATES = (
    "independent_exact_source_review", "source_and_container_ci",
    "image_signature_sbom_provenance_and_vulnerability_verification",
    "source_archive_signature_and_installer_contract",
    "approved_isolated_staging_host", "isolated_staging_runtime_certification",
    "middleware_identity_and_private_mtls", "server_b_source_runtime_inventory",
    "mariadb_local_only_and_least_privilege", "backup_checksum_and_isolated_restore",
    "rollback_proof", "monitoring_and_reconciliation",
    "protected_production_change_authorization",
)


def validate_manifest(value: object) -> dict:
    """Reject mutable identity, coercible booleans, unsafe policy and missing gates."""
    if not isinstance(value, dict):
        raise ValueError("manifest must be an object")
    exact = {
        "schema_version": 2, "contract_version": "2", "repository": REPOSITORY,
        "repository_class": "service", "artifact_kind": "oci-image",
        "runtime_deployment_authorized": False, "external_effects_authorized": False,
        "high_critical_vulnerabilities_allowed": False, "signature_required": True,
        "sbom_required": True, "provenance_required": True,
        "staging_environment": "staging-readonly",
        "production_environment": "production-readonly-canary",
        "production_allowed_methods": ["GET", "HEAD"],
        "health_paths": ["/health", "/ready", "/version"],
    }
    for key, expected in exact.items():
        actual = value.get(key)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError(f"invalid manifest field: {key}")
    for key in ("source_sha", "source_tree"):
        if not isinstance(value.get(key), str) or not re.fullmatch(r"[0-9a-f]{40}", value[key]):
            raise ValueError(f"invalid exact Git identity: {key}")
    digest = value.get("artifact_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("invalid immutable artifact digest")
    if value.get("artifact_reference") != f"{REGISTRY}@{digest}":
        raise ValueError("artifact reference must match the owned GHCR repository and digest")
    percent = value.get("production_canary_max_percent")
    if type(percent) not in (int, float) or not 0 < percent <= 1:
        raise ValueError("production canary limit must be greater than zero and at most one")
    return value


def preparation_documents(manifest: dict) -> dict[str, str]:
    manifest = validate_manifest(manifest)
    sha = manifest["source_sha"]
    identity = {k: manifest[k] for k in (
        "repository", "source_sha", "source_tree", "artifact_reference", "artifact_digest"
    )}
    flags = dict.fromkeys(FLAGS, "false")
    # Network mode none prevents even accidental connections to production or providers.
    # This first phase is only a memory-backed API smoke test, never DB/integration proof.
    service = {
        "image": manifest["artifact_reference"], "pull_policy": "never",
        "network_mode": "none", "user": "10001:10001", "read_only": True,
        "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"], "init": True,
        "restart": "no", "pids_limit": 128, "mem_limit": "512m", "cpus": 1.0,
        "stop_grace_period": "10s",
        "tmpfs": [
            "/tmp:rw,noexec,nosuid,nodev,size=16m,uid=10001,gid=10001",
            "/run/codestra:rw,noexec,nosuid,nodev,size=4m,uid=10001,gid=10001",
        ],
        "volumes": ["staging-state:/var/lib/codestra-vicidial"],
        "secrets": [{"source": "staging-hmac", "target": "adapter-hmac"}],
        "environment": {
            **flags, "SOURCE_SHA": sha, "CODESTRA_ADAPTER_BACKEND": "memory",
            "CODESTRA_ADAPTER_DEPLOYMENT": "production-candidate",
            "CODESTRA_ADAPTER_HMAC_FILE": "/run/secrets/adapter-hmac",
            "AUTHORIZED_PRIVATE_SOURCE_IP": "10.40.0.1",
        },
    }
    compose = {
        "name": f"vicidial-staging-{sha[:12]}",
        "services": {"restricted-adapter-staging": service},
        "volumes": {"staging-state": {}},
        "secrets": {"staging-hmac": {
            "file": "${VICIDIAL_STAGING_HMAC_FILE:?dedicated synthetic staging secret required}"
        }},
    }
    plan = {
        "schema_version": 1, "phase": "preparation-only", **identity,
        "runtime_deployment_authorized": False, "external_effects_authorized": False,
        "runtime_certification": "NOT_RUN", "production_ready": False,
        "staging_scope": "isolated-memory-backed-api-smoke-only",
        "staging_host": None,
        "production_host_role": "existing-dedicated-server-b",
        "production_private_ingress": "10.40.0.2:8443",
        "production_backend": "127.0.0.1:8097",
        "production_runtime_authority": "existing-reviewed-server-b-operator",
        "production_compose_reference_only": "vicidial/docker/compose.production.yml",
        "production_source_archive_requirement": {
            "source_sha": sha, "installer_contract": "systemd-python-source-v1",
            "archive_sha256": None, "builder_sha": None, "verification": "NOT_VERIFIED",
        },
        "safety_flags": flags,
        "required_gates": {name: "NOT_VERIFIED" for name in GATES},
        "activation": "SEPARATE_EXPLICIT_CHANGE_REQUIRED",
    }
    # Blank paths are deliberate. No production credentials are copied to staging.
    production_env = (
        "# Preparation only. Not a deploy command or production certification.\n"
        "# Keep existing Server B operator; do not replace systemd with Docker implicitly.\n"
        f"VICIDIAL_ADAPTER_IMAGE={manifest['artifact_reference']}\n"
        f"SOURCE_SHA={sha}\n"
        "CODESTRA_ADAPTER_DEPLOYMENT=production-candidate\n"
        "CODESTRA_ADAPTER_BACKEND=mysql\n"
        "CODESTRA_ADAPTER_HMAC_SECRET_FILE=\n"
        "CODESTRA_VICIDIAL_DB_SECRET_FILE=\n"
        "VICIDIAL_DB_SOCKET_DIR=\n"
        + "".join(f"{key}=false\n" for key in FLAGS)
    )
    return {
        "staging.compose.json": json.dumps(compose, indent=2, sort_keys=True) + "\n",
        "production.env.example": production_env,
        "deployment-plan.json": json.dumps(plan, indent=2, sort_keys=True) + "\n",
    }


def write_preparation(manifest: dict, output: Path) -> None:
    """Use a fresh directory, never overwrite an operator's package or secret."""
    documents = preparation_documents(manifest)
    output.mkdir(mode=0o750, parents=False, exist_ok=False)
    checksums = []
    for name, content in sorted(documents.items()):
        with (output / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        (output / name).chmod(0o640)
        checksums.append(f"{hashlib.sha256(content.encode()).hexdigest()}  {name}\n")
    (output / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8")
    (output / "SHA256SUMS").chmod(0o640)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        # Reject duplicate JSON keys rather than letting a later value override policy.
        def unique_object(pairs: list[tuple]) -> dict:
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate manifest field")
                result[key] = value
            return result
        manifest = json.loads(args.manifest.read_text(), object_pairs_hook=unique_object)
        write_preparation(manifest, args.output)
    except (OSError, ValueError, TypeError) as exc:
        # Do not echo raw manifest content, arbitrary field values or secret paths.
        print(f"preparation failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    print("PREPARATION_GENERATED=true RUNTIME_CERTIFICATION=NOT_RUN PRODUCTION_READY=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
