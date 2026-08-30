from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / "vicidial" / "scripts" / "validate_codestra_telephony_fabric.py"
CONTRACT_ROOT = ROOT / "docs" / "integrations" / "codestra-fabric"

spec = importlib.util.spec_from_file_location("codestra_telephony_fabric_validator", VALIDATOR_PATH)
assert spec and spec.loader
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def contracts() -> tuple[dict, dict]:
    manifest = json.loads((CONTRACT_ROOT / "manifest.v1.json").read_text(encoding="utf-8"))
    openapi = yaml.safe_load(
        (CONTRACT_ROOT / "telephony-api.openapi.yaml").read_text(encoding="utf-8")
    )
    return manifest, openapi


def test_telephony_fabric_contract_and_markers() -> None:
    manifest, openapi = contracts()
    validator.validate_manifest(manifest)
    validator.validate_openapi(openapi)

    result = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "VICIDIAL_CODESTRA_TELEPHONY_FABRIC=PASS" in result.stdout
    assert "VICIDIAL_CAMPAIGN_ISOLATION=PASS" in result.stdout
    assert "VICIDIAL_UNKNOWN_OUTCOME_ZERO_REDIAL=PASS" in result.stdout
    assert "VICIDIAL_LIVE_EFFECT_FLAGS_DISABLED=PASS" in result.stdout


def test_live_capability_or_direct_access_fails_closed() -> None:
    manifest, _ = contracts()
    unsafe = copy.deepcopy(manifest)
    unsafe["capabilities"]["PRODUCTION_DIALING"] = True
    with pytest.raises(ValueError, match="capabilities"):
        validator.validate_manifest(unsafe)

    bypass = copy.deepcopy(manifest)
    bypass["ami_direct_access"] = True
    with pytest.raises(ValueError, match="ami_direct_access"):
        validator.validate_manifest(bypass)


def test_campaign_isolation_cannot_be_weakened() -> None:
    manifest, _ = contracts()
    cross_campaign = copy.deepcopy(manifest)
    cross_campaign["campaign_isolation"]["cross_campaign_transfer"] = True
    with pytest.raises(ValueError, match="cross-campaign"):
        validator.validate_manifest(cross_campaign)

    multi_campaign = copy.deepcopy(manifest)
    multi_campaign["campaign_isolation"]["agent_campaign_membership"] = "MANY"
    with pytest.raises(ValueError, match="exactly one campaign"):
        validator.validate_manifest(multi_campaign)


def test_unknown_outcome_cannot_redial_or_commit_without_readback() -> None:
    manifest, _ = contracts()
    redial = copy.deepcopy(manifest)
    redial["unknown_outcome"]["blind_redial"] = True
    with pytest.raises(ValueError, match="redial"):
        validator.validate_manifest(redial)

    no_readback = copy.deepcopy(manifest)
    no_readback["unknown_outcome"]["authoritative_readback_required"] = False
    with pytest.raises(ValueError, match="read-back"):
        validator.validate_manifest(no_readback)


def test_private_api_requires_tenant_campaign_idempotency_and_mtls() -> None:
    _, openapi = contracts()

    missing_path = copy.deepcopy(openapi)
    del missing_path["paths"]["/v1/telephony/calls/{call_id}/events"]
    with pytest.raises(ValueError, match="path registry"):
        validator.validate_openapi(missing_path)

    missing_campaign = copy.deepcopy(openapi)
    missing_campaign["paths"]["/v1/telephony/calls"]["post"]["parameters"] = [
        item
        for item in missing_campaign["paths"]["/v1/telephony/calls"]["post"]["parameters"]
        if item.get("$ref") != "#/components/parameters/CampaignId"
    ]
    with pytest.raises(ValueError, match="campaign isolation"):
        validator.validate_openapi(missing_campaign)

    missing_idempotency = copy.deepcopy(openapi)
    missing_idempotency["paths"]["/v1/telephony/calls"]["post"]["parameters"] = [
        item
        for item in missing_idempotency["paths"]["/v1/telephony/calls"]["post"]["parameters"]
        if item.get("$ref") != "#/components/parameters/IdempotencyKey"
    ]
    with pytest.raises(ValueError, match="idempotency"):
        validator.validate_openapi(missing_idempotency)

    public_webhook = copy.deepcopy(openapi)
    public_webhook["paths"]["/v1/webhooks/telephony/vicidial"]["post"]["security"] = []
    with pytest.raises(ValueError, match="mTLS"):
        validator.validate_openapi(public_webhook)
