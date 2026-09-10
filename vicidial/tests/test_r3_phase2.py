from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codestra_vicidial import __version__
from codestra_vicidial.app import (
    create_app,
    parse_allowed_lead_list_ids,
)
from codestra_vicidial.models import LeadCommand
from codestra_vicidial.release import (
    APPLICATION,
    build_release_document,
    load_release_info,
)
from codestra_vicidial.repository import MemoryRepository
from codestra_vicidial.security import RequestAuthenticator
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore


SHA = "a" * 40
BUILT_AT = "2026-08-25T16:00:00Z"


def request_context() -> dict:
    return {
        "correlation_id": "r3-phase2-correlation",
        "actor": "r3-phase2-test",
        "tenant_id": "tenant-cod", "business_unit": "COD",
        "campaign_id": "CODT0001", "supervisor_subject": "supervisor-cod",
        "expected_version": 0,
        "reason": "validate explicit lead publication boundary",
        "requested_at": "2026-08-25T16:00:00Z",
    }


def lead_command(list_id: int = 21001) -> LeadCommand:
    return LeadCommand.model_validate({
        "context": request_context(),
        "lead": {
            "external_id": f"r3-lead-{list_id}",
            "list_id": list_id,
            "phone_code": "1",
            "phone_number": "2025550199",
            "first_name": "Synthetic",
            "last_name": "R3",
            "status": "NEW",
        },
    })


def service(
    tmp_path: Path,
    *,
    writes: bool = True,
    lead_publication: bool = True,
    allowed: frozenset[int] = frozenset(),
) -> tuple[AdapterService, MemoryRepository]:
    repository = MemoryRepository()
    state = StateStore(tmp_path / "state.sqlite3")
    state.put_campaign_authority(
        "CODT0001", "tenant-cod", "COD", "supervisor-cod")
    repository.campaigns["CODT0001"] = {"campaign_id": "CODT0001"}
    repository.lists[21001] = {
        "list_id": 21001, "campaign_id": "CODT0001", "active": "N"
    }
    adapter = AdapterService(
        repository,
        state,
        FeatureFlags(
            writes=writes,
            lead_publication=lead_publication,
            allowed_lead_list_ids=allowed,
        ),
    )
    return adapter, repository


def verified_release() -> dict:
    document = build_release_document(
        git_sha=SHA,
        built_at=BUILT_AT,
        environment="production-candidate",
    )
    document["verified"] = True
    document["metadata_source"] = "BUILD_INFO.json"
    return document


def test_lead_publication_default_deny_is_independent(
    tmp_path: Path,
) -> None:
    adapter, repository = service(tmp_path)
    with pytest.raises(
        PermissionError,
        match="lead list is not authorized",
    ):
        adapter.publish_lead("r3-denied", lead_command())
    assert repository.leads == {}


def test_lead_publication_requires_exact_allowed_list(
    tmp_path: Path,
) -> None:
    adapter, repository = service(
        tmp_path,
        allowed=frozenset({21001}),
    )
    result = adapter.publish_lead(
        "r3-allowed",
        lead_command(21001),
    )
    assert result["status"] == "published"
    assert len(repository.leads) == 1
    with pytest.raises(
        PermissionError,
        match="lead list is not authorized",
    ):
        adapter.publish_lead(
            "r3-wrong-list",
            lead_command(21002),
        )


def test_lead_allowlist_parser_is_fail_closed() -> None:
    assert parse_allowed_lead_list_ids("") == frozenset()
    assert parse_allowed_lead_list_ids(
        "21001, 21002,21001"
    ) == frozenset({21001, 21002})
    for value in ("21001,,21002", "abc", "9001", "92000"):
        with pytest.raises(ValueError):
            parse_allowed_lead_list_ids(value)


def test_release_document_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = build_release_document(
        git_sha=SHA,
        built_at=BUILT_AT,
        environment="production-candidate",
    )
    (tmp_path / "BUILD_INFO.json").write_text(
        json.dumps(document),
        encoding="utf-8",
    )
    (tmp_path / "SOURCE.COMMIT").write_text(SHA + "\n", encoding="utf-8")
    monkeypatch.delenv("CODESTRA_BUILD_INFO_FILE", raising=False)
    monkeypatch.delenv(
        "CODESTRA_RELEASE_METADATA_REQUIRED",
        raising=False,
    )
    loaded = load_release_info(required=True, root=tmp_path)
    assert loaded["verified"] is True
    assert loaded["git_sha"] == SHA
    assert loaded["build_id"] == f"vicidial-r3-{SHA[:12]}"
    assert loaded["version"] == "0.2.0rc1"


def test_required_release_verifies_installed_manifest_before_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib
    document = build_release_document(
        git_sha=SHA, built_at=BUILT_AT, environment="production-candidate"
    )
    (tmp_path / "BUILD_INFO.json").write_text(json.dumps(document))
    (tmp_path / "SOURCE.COMMIT").write_text(SHA + "\n")
    controlled = tmp_path / "src" / "adapter.py"
    controlled.parent.mkdir()
    controlled.write_text("verified = True\n")
    raw = controlled.read_bytes()
    manifest_file = tmp_path / "INSTALLED_MANIFEST.json"
    manifest_file.write_text(json.dumps({
        "source_sha": SHA,
        "files": [{"path": "src/adapter.py", "size": len(raw),
                   "sha256": hashlib.sha256(raw).hexdigest()}],
    }))
    monkeypatch.delenv("CODESTRA_BUILD_INFO_FILE", raising=False)
    monkeypatch.setenv("CODESTRA_RELEASE_MANIFEST_ROOT", str(tmp_path))
    monkeypatch.setenv("CODESTRA_RELEASE_MANIFEST_FILE", str(manifest_file))
    assert load_release_info(required=True, root=tmp_path)["verified"] is True
    controlled.write_text("verified = False\n")
    with pytest.raises(ValueError, match="release manifest file mismatch"):
        load_release_info(required=True, root=tmp_path)


def test_release_metadata_missing_or_invalid_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODESTRA_BUILD_INFO_FILE", raising=False)
    with pytest.raises(
        RuntimeError,
        match="release metadata missing",
    ):
        load_release_info(required=True, root=tmp_path)
    with pytest.raises(ValueError, match="git_sha"):
        build_release_document(
            git_sha="not-a-sha",
            built_at=BUILT_AT,
            environment="production-candidate",
        )


def test_version_ready_and_openapi_contract(
    tmp_path: Path,
) -> None:
    adapter, _ = service(
        tmp_path,
        writes=False,
        lead_publication=False,
    )
    authenticator = RequestAuthenticator(
        "codestra-middleware",
        b"x" * 32,
        frozenset({"127.0.0.1"}),
    )
    app = create_app(
        adapter,
        authenticator,
        release_info=verified_release(),
    )
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "live"}
    assert client.get("/ready").json() == {
        "status": "ready",
        "writes_enabled": False,
        "release_verified": True,
        "deployment_profile": "staging-read-only",
        "reconciliation_policy_verified": False,
    }
    version = client.get("/version").json()
    assert version["application"] == APPLICATION
    assert version["version"] == "0.2.0rc1"
    assert version["git_sha"] == SHA
    assert version["verified"] is True

    schema = app.openapi()
    schemes = schema["components"]["securitySchemes"]
    assert schemes["ServiceIdentity"]["name"] == (
        "X-Service-Identity"
    )
    assert schemes["RequestSignature"]["name"] == (
        "X-Request-Signature"
    )

    protected = []
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method not in {
                "get", "post", "put", "patch", "delete"
            }:
                continue
            if path.startswith("/v1/"):
                protected.append((method, path))
                assert operation["security"] == [{
                    "ServiceIdentity": [],
                    "RequestSignature": [],
                }]
                assert operation[
                    "x-codestra-authentication"
                ] == "hmac-sha256"
                assert operation[
                    "x-codestra-required-scope"
                ].startswith("telephony:")

    assert len(protected) == 23
    assert ("post", "/v1/idempotency/lookup") in protected
    assert schema["paths"]["/v1/idempotency/lookup"]["post"][
        "x-codestra-required-scope"
    ] == "telephony:read"
    assert ("get", "/v1/callbacks/by-external-id/{external_id}") in protected
    assert ("post", "/v1/calls/originate") in protected
    for public_path in ("/health", "/ready", "/version"):
        operation = schema["paths"][public_path]["get"]
        assert not operation.get("security")
    assert "/healthz" not in schema["paths"]
    assert "/status" not in schema["paths"]


def test_build_info_generator(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    output = tmp_path / "BUILD_INFO.json"
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/generate_build_info.py"),
            "--git-sha", SHA,
            "--built-at", BUILT_AT,
            "--environment", "production-candidate",
            "--output", str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["git_sha"] == SHA
    assert document["version"] == "0.2.0rc1"


def _operational_module():
    path = (
        Path(__file__).parents[1]
        / "scripts/verify_operational_contract.py"
    )
    spec = importlib.util.spec_from_file_location(
        "verify_operational_contract",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_operational_document_validation() -> None:
    module = _operational_module()
    version = verified_release()
    module.validate_documents(
        health={"status": "live"},
        ready={
            "status": "ready",
            "writes_enabled": False,
            "release_verified": True,
            "deployment_profile": "staging-read-only",
        },
        version=version,
        expected_sha=SHA,
        deployment_profile="staging-read-only",
    )
    with pytest.raises(ValueError, match="SHA mismatch"):
        module.validate_documents(
            health={"status": "live"},
            ready={
                "status": "ready",
                "writes_enabled": False,
                "release_verified": True,
                "deployment_profile": "staging-read-only",
            },
            version=version,
            expected_sha="b" * 40,
            deployment_profile="staging-read-only",
        )

    module.validate_documents(
        health={"status": "live"},
        ready={"status": "ready", "writes_enabled": True,
               "release_verified": True,
               "deployment_profile": "production-reconciliation",
               "reconciliation_policy_verified": True},
        version=version, expected_sha=SHA,
        deployment_profile="production-reconciliation",
    )
    module.validate_documents(
        health={"status": "live"},
        ready={"status": "ready", "writes_enabled": False,
               "release_verified": True,
               "deployment_profile": "production-internal-test",
               "internal_test_calling": True,
               "checks": {"internal_call_policy": True}},
        version=version, expected_sha=SHA,
        deployment_profile="production-internal-test",
    )


def test_systemd_uses_complete_operational_contract() -> None:
    root = Path(__file__).parents[1]
    unit = (
        root / "ops/codestra-vicidial-restricted-adapter.service"
    ).read_text(encoding="utf-8")
    health_unit = (
        root
        / "ops/codestra-vicidial-restricted-adapter-health.service"
    ).read_text(encoding="utf-8")
    assert "CODESTRA_RELEASE_METADATA_REQUIRED=true" in unit
    assert "current/BUILD_INFO.json" in unit
    assert "verify_operational_contract.py" in unit
    assert "current/SOURCE.COMMIT" in unit
    assert "--deployment-profile ${CODESTRA_ADAPTER_DEPLOYMENT}" in unit
    assert "current/.venv/bin/python -m uvicorn" in unit
    assert "/healthz" not in unit
    assert "/status" not in unit
    assert "verify_operational_contract.py" in health_unit
    assert "current/SOURCE.COMMIT" in health_unit
    assert "--deployment-profile ${CODESTRA_ADAPTER_DEPLOYMENT}" in health_unit


def test_package_versions_are_synchronized() -> None:
    root = Path(__file__).parents[1]
    pyproject = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert pyproject["project"]["version"] == __version__
    assert __version__ == "0.2.0rc1"
