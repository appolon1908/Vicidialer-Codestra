from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path

import pytest
from codestra_vicidial.app import create_app
from codestra_vicidial.curation import content_categories
from codestra_vicidial.models import (
    AgentCommand,
    CallbackCommand,
    CampaignCommand,
    DisableAgentCommand,
    LeadCommand,
    TransferPolicyCommand,
)
from codestra_vicidial.repository import (
    CollisionError,
    MemoryRepository,
    normalize_allowed_campaigns,
    owned_user_group,
)
from codestra_vicidial.security import RequestAuthenticator
from codestra_vicidial.service import AdapterService, FeatureFlags, ReadbackMismatch
from codestra_vicidial.state import StateStore
from pydantic import ValidationError


def context(
    *, expected_version: int = 0, campaign_id: str = "CODT0001",
    business_unit: str = "COD", tenant_id: str = "tenant-cod",
    supervisor_subject: str = "supervisor-cod",
) -> dict:
    return {
        "correlation_id": "corr-12345678", "actor": "middleware-test",
        "tenant_id": tenant_id, "business_unit": business_unit,
        "campaign_id": campaign_id, "supervisor_subject": supervisor_subject,
        "expected_version": expected_version,
        "reason": "synthetic disabled provisioning test",
        "requested_at": "2026-07-27T20:00:00Z",
    }


def test_user_group_namespace_has_only_exact_test_syn_exception() -> None:
    assert owned_user_group("TEST_SYN", "TEST", "WEBTEST_SYN")
    assert owned_user_group("COD-SERVICE-OUT", "COD", "COD_AGENTS")
    assert not owned_user_group("TEST-SERVICE-OUT", "TEST", "WEBTEST_SYN")
    assert not owned_user_group("TEST_SYN", "TEST", "WEBTEST_OTHER")
    assert not owned_user_group("COD-SERVICE-OUT", "COD", "WEBTEST_SYN")


@pytest.mark.parametrize(
    ("canonical_id", "business_unit", "group_code"),
    [
        ("CAL-TEST-OUT", "CAL", "TST_CAL"),
        ("COD-TEST-OUT", "COD", "TST_COD"),
        ("FTP-TEST-OUT", "FTP", "TST_FTP"),
        ("MBL-TEST-OUT", "MBL", "TST_MBL"),
        ("MOY-TEST-OUT", "MOY", "TST_MOY"),
        ("RLP-TEST-OUT", "RLP", "TST_RLP"),
        ("SCP-TEST-OUT", "SCP", "TST_SCP"),
        ("TRX-TEST-OUT", "TRX", "TST_TRX"),
    ],
)
def test_certified_test_fixture_groups_are_exact(
    canonical_id: str, business_unit: str, group_code: str
) -> None:
    assert owned_user_group(canonical_id, business_unit, group_code)
    assert not owned_user_group(canonical_id, business_unit, group_code + "X")
    assert not owned_user_group(canonical_id.replace("-TEST", "-LIVE"), business_unit, group_code)


def campaign_payload() -> dict:
    return {
        "context": context(),
        "campaign": {
            "canonical_id": "COD-TEST-OUT", "actual_id": "CODT0001",
            "name": "STG COD test outbound", "business_unit": "COD",
            "direction": "outbound", "active": False, "dial_method": "MANUAL",
            "call_time": "9am-9pm", "recording_policy": "ONDEMAND",
            "callback_enabled": True, "default_list_id": 21001,
            "lists": [{"list_id": 21001, "code": "COD_TEST_PRIMARY_001", "active": False}],
            "groups": [
                {"code": "COD_TEST_SDR", "name": "COD test SDR", "kind": "user", "active": False},
                {"code": "COD_TEST_CLOSERS", "name": "COD test closers", "kind": "closer", "active": False},
            ],
            "scripts": [{
                "code": "COD_TEST_SDR_V1", "name": "COD test script", "text": "Synthetic only",
                "version": 1, "active": False,
            }],
            "dispositions": [{
                "code": "TNA", "name": "Test No Answer", "selectable": True,
                "human_answered": False, "category": "UNDEFINED",
            }],
            "transfer_targets": ["COD_TEST_CLOSERS"],
            "reporting_mapping": "STG-COD-TEST-V1",
            "recording_policy_reference": "policy:test-recording-v1",
        },
    }


@pytest.mark.parametrize("canonical_id", [
    "COD-SERVICE-OUT",
    "FTP-DONOR-SUPPORT-IN",
    "COD-AI-OUT",
    "CAL-WHOLESALE-OUT",
    "TEST_SYN",
])
def test_canonical_campaign_accepts_approved_multi_segment_ids(canonical_id):
    payload = campaign_payload()
    payload["campaign"]["canonical_id"] = canonical_id
    business_unit = "TEST" if canonical_id == "TEST_SYN" else canonical_id.split("-", 1)[0]
    payload["campaign"]["business_unit"] = business_unit
    payload["campaign"]["default_list_id"] = None
    payload["campaign"]["lists"] = []
    CampaignCommand.model_validate(payload)


@pytest.mark.parametrize("canonical_id", [
    "FTP--DONOR-IN", "FTP-DONOR-", "FTP-DONOR-LIVE", "ftp-DONOR-IN",
    "FTP/DONOR/IN", "OTHER-DONOR-IN",
])
def test_canonical_campaign_still_rejects_unapproved_grammar(canonical_id):
    payload = campaign_payload()
    payload["campaign"]["canonical_id"] = canonical_id
    payload["campaign"]["business_unit"] = "FTP"
    with pytest.raises(ValidationError):
        CampaignCommand.model_validate(payload)


def test_all_certified_reconciliation_campaign_ids_match_contract():
    report = Path(__file__).parents[1] / "reports/full-campaign-reconciliation.json"
    rows = json.loads(report.read_text(encoding="utf-8"))["rows"]
    assert len(rows) == 101
    for row in rows:
        payload = campaign_payload()
        payload["campaign"].update({
            "canonical_id": row["canonical"],
            "actual_id": row["physical"],
            "name": row["canonical"],
            "business_unit": row["canonical"].split("-", 1)[0],
            "default_list_id": None,
            "lists": [],
        })
        CampaignCommand.model_validate(payload)


@pytest.fixture
def harness(tmp_path: Path):
    repo = MemoryRepository()
    state = StateStore(tmp_path / "state.sqlite3")
    state.put_campaign_authority(
        "CODT0001", "tenant-cod", "COD", "supervisor-cod")
    service = AdapterService(
        repo, state,
        FeatureFlags(
            writes=True,
            provisioning=True,
            agent_sync=True,
            lead_publication=True,
            allowed_lead_list_ids=frozenset({21001}),
            callback_writes=True,
            transfer_policy_sync=True,
            production_dialing=False,
        ),
    )
    return service, repo


def service_for(repo: MemoryRepository, tmp_path: Path) -> AdapterService:
    state = StateStore(tmp_path / "state.sqlite3")
    state.put_campaign_authority(
        "CODT0001", "tenant-cod", "COD", "supervisor-cod")
    return AdapterService(
        repo, state,
        FeatureFlags(
            writes=True,
            provisioning=True,
            agent_sync=True,
            lead_publication=True,
            allowed_lead_list_ids=frozenset({21001}),
            callback_writes=True,
            transfer_policy_sync=True,
            production_dialing=False,
        ),
    )


def compatible_user_group(allowed_campaigns: str) -> dict:
    return {
        "code": "COD_TEST_SDR", "name": "COD test SDR", "kind": "user",
        "active": "N", "allowed_campaigns": allowed_campaigns,
    }


def provision_callback_dependencies(service: AdapterService) -> None:
    service.provision_campaign(
        "callback-owned-campaign", CampaignCommand.model_validate(campaign_payload()))
    service.publish_lead(
        "callback-owned-lead", LeadCommand.model_validate({
            "context": context(),
            "lead": {
                "external_id": "callback-lead", "list_id": 21001,
                "phone_code": "1", "phone_number": "5550100001",
            },
        }))
    service.sync_agent(
        "callback-owned-agent", AgentCommand.model_validate({
            "context": context(),
            "agent": {
                "user_id": "COD1001", "full_name": "Synthetic Agent",
                "user_group": "COD_TEST_SDR", "campaigns": ["CODT0001"],
                "inbound_groups": [], "active": False,
            },
        }))


def test_route_contract_is_present(harness):
    service, _ = harness
    auth = RequestAuthenticator("codestra-middleware", b"x" * 32, frozenset({"127.0.0.1"}))
    routes = {(method, route.path) for route in create_app(service, auth).routes
              for method in getattr(route, "methods", set())}
    required = {
        ("GET", "/health"), ("GET", "/ready"), ("GET", "/version"),
        ("POST", "/v1/idempotency/lookup"),
        ("POST", "/v1/campaigns/validate"),
        ("POST", "/v1/campaigns/provision-disabled"),
        ("POST", "/v1/campaigns/disable"),
        ("POST", "/v1/reconciliation/campaigns/provision-disabled"),
        ("GET", "/v1/campaigns/{campaign_id}/actual-state"),
        ("POST", "/v1/agents/sync"), ("POST", "/v1/agents/disable"),
        ("GET", "/v1/agents/{user_id}/actual-state"),
        ("GET", "/v1/agents/availability/{user_id}/{extension}"),
        ("POST", "/v1/calls/internal/originate"),
        ("GET", "/v1/calls/internal/{operation_id}"),
        ("POST", "/v1/calls/internal/{operation_id}/hangup"),
        ("POST", "/v1/leads/publish"),
        ("GET", "/v1/leads/by-external-id/{external_id}"),
        ("POST", "/v1/callbacks/create"), ("GET", "/v1/callbacks/{callback_id}"),
        ("POST", "/v1/transfer-policies/sync"),
        ("POST", "/v1/reconciliation/run"), ("GET", "/v1/reconciliation/{run_id}"),
    }
    assert required <= routes
    removed = {
        ("POST", "/v1/campaigns/update-disabled"),
        ("POST", "/v1/scripts/sync"),
        ("POST", "/v1/dispositions/sync"),
        ("GET", "/v1/leads/by-idempotency/{key}"),
    }
    assert routes.isdisjoint(removed)
    prohibited_fragments = {"/sql", "/shell", "/command", "/files", "/exec"}
    assert not any(any(fragment in path for fragment in prohibited_fragments)
                   for _method, path in routes)


def test_auth_source_identity_scope_signature_and_replay():
    auth = RequestAuthenticator(
        "codestra-middleware", b"x" * 32,
        frozenset({"65.109.65.169", "10.40.0.1"}), max_clock_skew_seconds=60,
    )
    body = b'{"safe":true}'
    now = int(time.time())
    signature = auth.sign(
        now, "nonce-1234567890", body,
        scopes={"telephony:provision"},
    )
    auth.verify(source="65.109.65.169", identity="codestra-middleware",
                scopes={"telephony:provision"}, timestamp=now,
                nonce="nonce-1234567890", body=body, signature=signature,
                required_scope="telephony:provision", now=now)
    private_nonce = "private-peer-1234567890"
    auth.verify(
        source="10.40.0.1", identity="codestra-middleware",
        scopes={"telephony:provision"}, timestamp=now, nonce=private_nonce,
        body=body, signature=auth.sign(
            now, private_nonce, body, scopes={"telephony:provision"}
        ),
        required_scope="telephony:provision", now=now,
    )
    with pytest.raises(PermissionError, match="replay"):
        auth.verify(source="65.109.65.169", identity="codestra-middleware",
                    scopes={"telephony:provision"}, timestamp=now,
                    nonce="nonce-1234567890", body=body, signature=signature,
                    required_scope="telephony:provision", now=now)
    for override, message in [
        ({"source": "203.0.113.1"}, "source"),
        ({"identity": "intruder"}, "identity"),
        ({"scopes": {"telephony:read"}}, "scope"),
        ({"signature": "0" * 64}, "signature"),
    ]:
        values = {
            "source": "65.109.65.169", "identity": "codestra-middleware",
            "scopes": {"telephony:provision"}, "timestamp": now,
            "nonce": "unique-" + message + "-123456", "body": body,
            "signature": signature, "required_scope": "telephony:provision", "now": now,
        } | override
        if "nonce" not in override:
            values["signature"] = auth.sign(
                now, values["nonce"], body,
                identity=values["identity"], scopes=values["scopes"],
            ) if message != "signature" else "0" * 64
        with pytest.raises(PermissionError, match=message):
            auth.verify(**values)


def test_schema_rejects_extra_active_and_wrong_range():
    payload = campaign_payload()
    payload["campaign"]["active"] = True
    with pytest.raises(ValidationError):
        CampaignCommand.model_validate(payload)
    payload = campaign_payload()
    payload["campaign"]["lists"][0]["list_id"] = 31001
    payload["campaign"]["default_list_id"] = 31001
    with pytest.raises(ValidationError, match="business-unit range"):
        CampaignCommand.model_validate(payload)
    payload = campaign_payload()
    payload["shell"] = "id"
    with pytest.raises(ValidationError, match="Extra inputs"):
        CampaignCommand.model_validate(payload)


def test_provision_disabled_readback_and_retry(harness):
    service, repo = harness
    command = CampaignCommand.model_validate(campaign_payload())
    first = service.provision_campaign("idem-12345678", command)
    assert first["status"] == "complete_disabled"
    assert first["actual"]["active"] == "N"
    assert repo.lists[21001]["active"] == "N"
    snapshot = service.state.get_snapshot(first["snapshot_id"])
    assert snapshot is not None
    assert snapshot["state"]["resources"]["campaign"] is None
    second = service.provision_campaign("idem-12345678", command)
    assert second["idempotent_replay"] is True
    assert len(repo.campaigns) == len(repo.lists) == 1


def test_idempotency_conflict(harness):
    service, _ = harness
    service.provision_campaign("same-key", CampaignCommand.model_validate(campaign_payload()))
    changed = campaign_payload()
    changed["campaign"]["name"] = "Different"
    with pytest.raises(ValueError, match="idempotency conflict"):
        service.provision_campaign("same-key", CampaignCommand.model_validate(changed))


def test_compatible_adoption(harness):
    service, _ = harness
    command = CampaignCommand.model_validate(campaign_payload())
    service.provision_campaign("first-key", command)
    payload = campaign_payload()
    payload["context"] = context(expected_version=1)
    result = service.provision_campaign(
        "second-key", CampaignCommand.model_validate(payload))
    assert {row["result"] for row in result["resources"]} == {"adopted"}


def test_secret_detectors_match_real_whitespace_not_literal_backslashes():
    assert content_categories(
        b"Authorization: Bearer synthetic-token-value\n"
    ) == {
        "authorization header"
    }
    assert "credential assignment" in content_categories(
        b"client_secret: synthetic-secret-value\n"
    )
    assert "credential assignment" in content_categories(
        b"password=synthetic-secret-value\n"
    )
    assert content_categories(b"client_secret = ${PROTECTED_FILE}") == set()
    assert content_categories(b"password=\nsynthetic-public-value\n") == set()


def test_broader_user_group_campaign_access_is_rejected(harness):
    service, repo = harness
    repo.groups[("user", "COD_TEST_SDR")] = compatible_user_group(
        " LEGACY01 CODT0001 -")
    with pytest.raises(CollisionError, match="exactly one campaign"):
        service.provision_campaign(
            "group-present", CampaignCommand.model_validate(campaign_payload()))


def test_user_group_missing_campaign_is_not_rewritten(harness):
    service, repo = harness
    repo.groups[("user", "COD_TEST_SDR")] = compatible_user_group(
        " LEGACY01 OTHER002 -")
    before = repo.groups[("user", "COD_TEST_SDR")].copy()
    with pytest.raises(CollisionError, match="exactly one campaign"):
        service.provision_campaign(
            "group-add", CampaignCommand.model_validate(campaign_payload()))
    assert repo.groups[("user", "COD_TEST_SDR")] == before


def test_user_group_repeated_request_does_not_duplicate_campaign(harness):
    service, repo = harness
    repo.groups[("user", "COD_TEST_SDR")] = compatible_user_group(" CODT0001 -")
    command = CampaignCommand.model_validate(campaign_payload())
    service.provision_campaign("group-repeat-one", command)
    payload = campaign_payload()
    payload["context"] = context(expected_version=1)
    service.provision_campaign(
        "group-repeat-two", CampaignCommand.model_validate(payload))
    memberships = normalize_allowed_campaigns(
        repo.groups[("user", "COD_TEST_SDR")]["allowed_campaigns"])
    assert memberships == ["CODT0001"]
    assert memberships.count("CODT0001") == 1


@pytest.mark.parametrize("malformed", [
    "LEGACY01", " LEGACY01 BAD-CODE -", " LEGACY01 LEGACY01 -",
    "-ALL-CAMPAIGNS-",
])
def test_user_group_malformed_or_unrestricted_membership_blocks(
        harness, malformed):
    service, repo = harness
    repo.groups[("user", "COD_TEST_SDR")] = compatible_user_group(malformed)
    before = deepcopy(repo.groups)
    with pytest.raises(CollisionError):
        service.provision_campaign(
            "group-malformed-" + str(abs(hash(malformed))),
            CampaignCommand.model_validate(campaign_payload()))
    assert repo.groups == before
    assert repo.campaigns == {}


def test_user_group_incompatible_ownership_blocks(harness):
    service, repo = harness
    payload = campaign_payload()
    payload["campaign"]["groups"][0]["code"] = "SCP_TEST_SDR"
    with pytest.raises(CollisionError, match="ownership"):
        service.provision_campaign(
            "group-owner", CampaignCommand.model_validate(payload))
    assert repo.campaigns == {}


def test_complete_readback_matches_every_required_resource(harness):
    service, _ = harness
    result = service.provision_campaign(
        "full-readback", CampaignCommand.model_validate(campaign_payload()))
    assert result["status"] == "complete_disabled"
    assert all(item["match"] for item in result["readback"])
    assert {
        item["type"] for item in result["readback"]
    } == {
        "campaign", "list", "user_group", "closer_group", "script",
        "disposition", "transfer_policy", "recording_policy_reference",
        "callback_policy", "reporting_mapping", "canonical_mapping",
    }


class CorruptReadbackRepository(MemoryRepository):
    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self.corrupt = True
        self.armed = False

    def provision_campaign_disabled(self, spec):
        result = super().provision_campaign_disabled(spec)
        self.armed = True
        return result

    def campaign_resources(self, spec):
        result = super().campaign_resources(spec)
        if not self.corrupt or not self.armed:
            return result
        if self.kind == "group":
            result["groups"][1]["name"] = "Mismatched closer"
        elif self.kind == "script":
            result["scripts"][0]["script_text"] = (
                result["scripts"][0]["script_text"][:4])
        elif self.kind == "disposition":
            result["campaign"]["dispositions"][0]["category"] = "OTHER"
        elif self.kind == "disabled":
            result["groups"][1]["active"] = "Y"
        elif self.kind == "partial":
            result["scripts"] = []
        return result


@pytest.mark.parametrize(("kind", "resource_type", "field"), [
    ("group", "closer_group", "name"),
    ("script", "script", "script_text"),
    ("disposition", "disposition", "category"),
    ("disabled", "closer_group", "active"),
])
def test_resource_mismatch_requires_reconciliation_and_preserves_evidence(
        tmp_path, kind, resource_type, field):
    repo = CorruptReadbackRepository(kind)
    service = service_for(repo, tmp_path)
    command = CampaignCommand.model_validate(campaign_payload())
    with pytest.raises(ReadbackMismatch) as caught:
        service.provision_campaign("mismatch-" + kind, command)
    mismatch = next(item for item in caught.value.results
                    if item["type"] == resource_type)
    assert mismatch["match"] is False
    assert field in mismatch["mismatches"]
    assert repo.campaigns["CODT0001"]["active"] == "N"
    assert repo.groups[("closer", "COD_TEST_CLOSERS")]["active"] == "N"
    assert repo.scripts["COD_TEST_SDR_V1"]["active"] == "N"
    evidence = service.state.latest_evidence("campaign.provision-disabled")
    assert evidence["outcome"] == "reconciliation_required"
    assert "Synthetic only" not in json.dumps(evidence)
    assert service.state.db.execute(
        "SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_transfer_policy_mismatch_requires_reconciliation(tmp_path):
    repo = MemoryRepository()
    service = service_for(repo, tmp_path)
    original = service.state.campaign_policy

    def mismatched_policy(campaign_id):
        result = original(campaign_id)
        if result:
            result["transfer_targets"] = ["OTHER_GROUP"]
        return result

    service.state.campaign_policy = mismatched_policy
    with pytest.raises(ReadbackMismatch) as caught:
        service.provision_campaign(
            "transfer-mismatch",
            CampaignCommand.model_validate(campaign_payload()))
    mismatch = next(item for item in caught.value.results
                    if item["type"] == "transfer_policy")
    assert mismatch["mismatches"] == ["transfer_targets"]


def test_retry_after_partial_readback_failure_and_reconciliation(tmp_path):
    repo = CorruptReadbackRepository("partial")
    service = service_for(repo, tmp_path)
    command = CampaignCommand.model_validate(campaign_payload())
    with pytest.raises(ReadbackMismatch):
        service.provision_campaign("partial-retry", command)
    assert service.state.db.execute(
        "SELECT COUNT(*) FROM requests").fetchone()[0] == 0
    repo.corrupt = False
    corrected = service.provision_campaign("partial-retry", command)
    assert corrected["status"] == "complete_disabled"
    assert all(item["match"] for item in corrected["readback"])
    outcomes = [
        row[0] for row in service.state.db.execute(
            "SELECT outcome FROM evidence ORDER BY evidence_id").fetchall()
    ]
    assert outcomes == ["reconciliation_required", "complete_disabled"]


def test_incompatible_collision_blocks(harness):
    service, repo = harness
    repo.campaigns["CODT0001"] = {
        "campaign_id": "CODT0001", "canonical_id": "SCP-TEST-OUT",
        "campaign_name": "Other owner", "active": "N", "dial_method": "MANUAL",
        "local_call_time": "9am-9pm",
    }
    with pytest.raises(CollisionError, match="incompatible collision"):
        service.provision_campaign(
            "collision-key", CampaignCommand.model_validate(campaign_payload()))
    assert repo.lists == {}


def test_collision_is_compensating_rollback_safe(harness):
    service, repo = harness
    repo.lists[21001] = {
        "list_id": 21001, "list_name": "OTHER_OWNER",
        "campaign_id": "OTHER001", "active": "N",
    }
    before = json.dumps({
        "campaigns": repo.campaigns, "lists": repo.lists,
        "groups": repo.groups, "scripts": repo.scripts,
        "dispositions": repo.dispositions,
    }, sort_keys=True)
    with pytest.raises(CollisionError):
        service.provision_campaign(
            "rollback-collision", CampaignCommand.model_validate(campaign_payload()))
    after = json.dumps({
        "campaigns": repo.campaigns, "lists": repo.lists,
        "groups": repo.groups, "scripts": repo.scripts,
        "dispositions": repo.dispositions,
    }, sort_keys=True)
    # Memory repository exposes a real weakness if creation happens before all
    # compatibility checks: this assertion requires all-or-nothing behavior.
    assert after == before


def test_kill_switch_blocks_mutation(tmp_path):
    repo = MemoryRepository()
    service = AdapterService(repo, StateStore(tmp_path / "state.db"),
                             FeatureFlags(writes=False, provisioning=True))
    with pytest.raises(PermissionError, match="kill switch"):
        service.provision_campaign(
            "kill-key", CampaignCommand.model_validate(campaign_payload()))
    assert repo.campaigns == {}


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("tenant_id", "tenant-other", PermissionError),
        ("business_unit", "SCP", PermissionError),
        ("campaign_id", "OTHER001", PermissionError),
        ("supervisor_subject", "supervisor-other", PermissionError),
    ],
)
def test_campaign_authority_claims_fail_closed_without_mutation(
    harness, field, value, error
):
    service, repo = harness
    payload = campaign_payload()
    payload["context"] = context(**{field: value})
    with pytest.raises(error):
        service.disable_campaign(
            "cross-authority", CampaignCommand.model_validate(payload)
        )
    assert repo.campaigns == {}


def test_stale_expected_version_rejects_before_second_mutation(harness):
    service, repo = harness
    command = CampaignCommand.model_validate(campaign_payload())
    first = service.provision_campaign("version-first", command)
    before = deepcopy(repo.campaigns)
    assert first["resource_version"] == 1
    with pytest.raises(ValueError, match="expected version is stale"):
        service.disable_campaign("version-stale", command)
    assert repo.campaigns == before


def test_resource_version_claim_prevents_overlapping_mutations(tmp_path):
    state = StateStore(tmp_path / "version-claim.sqlite3")
    state.claim_resource_version("campaign:COD-TEST-OUT", 0, "first")
    with pytest.raises(RuntimeError, match="already in progress"):
        state.claim_resource_version("campaign:COD-TEST-OUT", 0, "second")
    state.release_resource_claim("campaign:COD-TEST-OUT", 0, "first")
    state.claim_resource_version("campaign:COD-TEST-OUT", 0, "second")
    assert state.complete_resource_version(
        "campaign:COD-TEST-OUT", 0, "second"
    ) == 1


def test_agent_sync_disable(harness):
    service, repo = harness
    command = AgentCommand.model_validate({
        "context": context(),
        "agent": {
            "user_id": "COD1001", "full_name": "Synthetic Agent",
            "user_group": "COD_TEST_SDR", "campaigns": ["CODT0001"],
            "inbound_groups": ["COD_TEST_CLOSERS"], "active": False,
        },
    })
    assert service.sync_agent("agent-sync", command)["actual"]["active"] is False
    disabled = service.disable_agent(
        "agent-disable", DisableAgentCommand.model_validate(
            {"context": context(expected_version=1), "user_id": "COD1001"}))
    assert disabled["actual"]["active"] is False
    assert repo.agents["COD1001"]["full_name"] == "Synthetic Agent"


def test_lead_publication_duplicate_retry_and_collision(harness):
    service, repo = harness
    repo.campaigns["CODT0001"] = {"campaign_id": "CODT0001"}
    repo.lists[21001] = {
        "list_id": 21001, "campaign_id": "CODT0001", "active": "N"
    }
    payload = {
        "context": context(),
        "lead": {
            "external_id": "synthetic-lead-1", "list_id": 21001,
            "phone_code": "1", "phone_number": "2025550199",
            "first_name": "Synthetic", "last_name": "Lead", "status": "NEW",
        },
    }
    command = LeadCommand.model_validate(payload)
    first = service.publish_lead("lead-one", command)
    payload["context"] = context(expected_version=1)
    command = LeadCommand.model_validate(payload)
    second = service.publish_lead("lead-two", command)
    assert first["actual"]["lead_id"] == second["actual"]["lead_id"]
    assert len(repo.leads) == 1
    payload["lead"]["phone_number"] = "2025550100"
    payload["context"] = context(expected_version=2)
    with pytest.raises(CollisionError):
        service.publish_lead("lead-three", LeadCommand.model_validate(payload))


def test_callback_and_transfer_policy(harness):
    service, repo = harness
    provision_callback_dependencies(service)
    callback = CallbackCommand.model_validate({
        "context": context(),
        "callback": {
            "external_id": "callback-1", "lead_id": 1, "campaign_id": "CODT0001",
            "user_id": "COD1001", "callback_time": "2026-07-28T14:00:00Z",
            "recipient": "USERONLY", "comments": "Synthetic",
        },
    })
    created = service.create_callback("callback-one", callback)
    assert repo.callback(created["actual"]["callback_id"]) is not None
    policy = TransferPolicyCommand.model_validate({
        "context": context(),
        "policy": {"campaign_id": "CODT0001", "allowed_targets": ["COD_TEST_CLOSERS"]},
    })
    result = service.sync_transfer_policy("policy-one", policy)
    assert result["allowed_targets"] == ["COD_TEST_CLOSERS"]
    bad = TransferPolicyCommand.model_validate({
        "context": context(expected_version=1),
        "policy": {"campaign_id": "CODT0001", "allowed_targets": ["SCP_OTHER_CLOSER"]},
    })
    with pytest.raises(ValueError, match="target missing"):
        service.sync_transfer_policy("policy-two", bad)


def test_disable_and_reconciliation(harness):
    service, _ = harness
    command = CampaignCommand.model_validate(campaign_payload())
    service.provision_campaign("provision-first", command)
    payload = campaign_payload()
    payload["context"] = context(expected_version=1)
    disabled = service.disable_campaign(
        "disable-next", CampaignCommand.model_validate(payload))
    assert disabled["actual"]["active"] == "N"
    assert service.reconciliation()["inventory"]["campaigns"] == 1


def test_disable_requires_mapping_and_ownership_before_touching_campaign(tmp_path):
    repo = MemoryRepository()
    service = service_for(repo, tmp_path)
    command = CampaignCommand.model_validate(campaign_payload())
    repo.campaigns["CODT0001"] = {
        "campaign_id": "CODT0001", "canonical_id": "COD-TEST-OUT",
        "campaign_name": "Codestra synthetic test", "active": "Y",
        "dial_method": "MANUAL", "local_call_time": "9am-9pm",
    }
    with pytest.raises(CollisionError, match="mapping missing"):
        service.disable_campaign("disable-no-mapping", command)
    assert repo.campaigns["CODT0001"]["active"] == "Y"
    service.state.put_mapping(
        "campaign", "COD-TEST-OUT", "CODT0001",
        service.state.digest(command.campaign.model_dump(mode="json")))
    repo.campaigns["CODT0001"]["canonical_id"] = "SCP-TEST-OUT"
    with pytest.raises(CollisionError, match="marker mismatch"):
        service.disable_campaign("disable-wrong-owner", command)
    assert repo.campaigns["CODT0001"]["active"] == "Y"
    repo.campaigns["CODT0001"]["canonical_id"] = "COD-TEST-OUT"
    assert service.disable_campaign("disable-valid-owner", command)["status"] == "disabled"


def test_canonical_mapping_is_immutable_and_actual_id_is_unique(tmp_path):
    state = StateStore(tmp_path / "mapping.sqlite3")
    state.put_mapping("campaign", "COD-TEST-OUT", "CODT0001", "a" * 64)
    state.put_mapping("campaign", "COD-TEST-OUT", "CODT0001", "a" * 64)
    with pytest.raises(ValueError, match="canonical mapping changed"):
        state.put_mapping("campaign", "COD-TEST-OUT", "CODT0002", "a" * 64)
    with pytest.raises(ValueError, match="desired hash conflict"):
        state.put_mapping("campaign", "COD-TEST-OUT", "CODT0001", "b" * 64)
    with pytest.raises(ValueError, match="actual mapping already claimed"):
        state.put_mapping("campaign", "SCP-TEST-OUT", "CODT0001", "a" * 64)


def test_provision_rejects_canonical_remap_before_creating_second_campaign(harness):
    service, repo = harness
    command = CampaignCommand.model_validate(campaign_payload())
    service.provision_campaign("mapping-first", command)
    changed = campaign_payload()
    changed["campaign"]["actual_id"] = "CODT0002"
    changed["context"] = context(campaign_id="CODT0002", expected_version=1)
    with pytest.raises(CollisionError, match="mapping collision"):
        service.provision_campaign(
            "mapping-second", CampaignCommand.model_validate(changed))
    assert "CODT0002" not in repo.campaigns


def test_list_readback_matches_by_identifier_not_row_order(tmp_path):
    payload = campaign_payload()
    payload["campaign"]["lists"].append({
        "list_id": 21002, "code": "COD_TEST_SECOND", "active": False,
    })
    repo = MemoryRepository()
    original = repo.campaign

    def reversed_lists(actual_id):
        result = original(actual_id)
        if result:
            result["lists"].reverse()
        return result

    repo.campaign = reversed_lists
    service = service_for(repo, tmp_path)
    result = service.provision_campaign(
        "list-order", CampaignCommand.model_validate(payload))
    assert all(row["match"] for row in result["readback"])


def test_transfer_target_requires_exact_campaign_and_business_unit(harness):
    service, repo = harness
    service.provision_campaign(
        "transfer-owner", CampaignCommand.model_validate(campaign_payload()))
    repo.groups[("closer", "COD_OTHER_CLOSER")] = {
        "code": "COD_OTHER_CLOSER", "name": "Other closer",
        "kind": "closer", "active": "N",
    }
    service.state.put_group_ownership(
        "COD_OTHER_CLOSER", "OTHER001", "COD", "closer")
    command = TransferPolicyCommand.model_validate({
        "context": context(),
        "policy": {
            "campaign_id": "CODT0001",
            "allowed_targets": ["COD_OTHER_CLOSER"],
        },
    })
    with pytest.raises(ValueError, match="ownership mismatch"):
        service.sync_transfer_policy("transfer-cross-campaign", command)


def test_user_group_ownership_allows_same_business_unit_sharing(tmp_path):
    state = StateStore(tmp_path / "shared-groups.sqlite3")
    state.put_group_ownership("CAL_AGENTS", "CAL00001", "CAL", "user")
    state.put_group_ownership("CAL_AGENTS", "CAL00002", "CAL", "user")
    owner = state.group_owner("CAL_AGENTS")
    assert owner is not None
    assert owner["campaign_id"] == "CAL00001"
    with pytest.raises(ValueError, match="group ownership collision"):
        state.put_group_ownership("CAL_AGENTS", "COD00001", "COD", "user")
    with pytest.raises(ValueError, match="group ownership collision"):
        state.put_group_ownership("CAL_AGENTS", "CAL00003", "CAL", "inbound")


def test_callback_external_id_requires_identical_immutable_payload(harness):
    service, repo = harness
    provision_callback_dependencies(service)
    payload = {
        "context": context(),
        "callback": {
            "external_id": "callback-collision", "lead_id": 1,
            "campaign_id": "CODT0001", "user_id": "COD1001",
            "callback_time": "2026-07-28T14:00:00Z",
            "recipient": "USERONLY", "comments": "Synthetic",
        },
    }
    first = service.create_callback(
        "callback-original", CallbackCommand.model_validate(payload))
    retry_payload = deepcopy(payload)
    retry_payload["context"] = context(expected_version=1)
    retry = service.create_callback(
        "callback-retry", CallbackCommand.model_validate(retry_payload))
    assert first["actual"]["callback_id"] == retry["actual"]["callback_id"]
    payload["callback"]["lead_id"] = 2
    payload["context"] = context(expected_version=2)
    with pytest.raises(PermissionError, match="lead does not belong"):
        service.create_callback(
            "callback-collision", CallbackCommand.model_validate(payload))
    assert len(repo.callbacks) == 1


def callback_command(**changes) -> CallbackCommand:
    callback = {
        "external_id": "callback-isolated", "lead_id": 1,
        "campaign_id": "CODT0001", "user_id": "COD1001",
        "callback_time": "2026-07-28T14:00:00Z",
        "recipient": "USERONLY", "comments": "Synthetic",
    }
    callback.update(changes)
    return CallbackCommand.model_validate({"context": context(), "callback": callback})


def test_callback_schema_rejects_shared_recipient():
    with pytest.raises(ValueError):
        callback_command(recipient="ANYONE")


@pytest.mark.parametrize("drift", ["policy", "lead", "agent", "desired_agent"])
def test_callback_rejects_missing_or_cross_campaign_authority(harness, drift):
    service, repo = harness
    provision_callback_dependencies(service)
    if drift == "policy":
        service.state.put_campaign_policy(
            "CODT0001", [], "recording-policy", "reporting-map", False)
    elif drift == "lead":
        repo.leads[1]["list_id"] = 31001
    elif drift == "agent":
        repo.agents["COD1001"]["campaigns"] = ["OTHER001"]
    else:
        service.state.put_agent_desired(
            "COD1001", service.state.agent_desired("COD1001")["desired"]
            | {"campaigns": ["OTHER001"]})
    with pytest.raises(PermissionError):
        service.create_callback("callback-isolation-" + drift, callback_command())
    assert repo.callbacks == {}


def test_callback_rejects_postwrite_readback_drift(harness, monkeypatch):
    service, repo = harness
    provision_callback_dependencies(service)
    original = repo.callback

    def drifted(callback_id):
        row = original(callback_id)
        row["campaign_id"] = "OTHER001"
        return row

    monkeypatch.setattr(repo, "callback", drifted)
    with pytest.raises(ReadbackMismatch, match="read-back mismatch"):
        service.create_callback("callback-postwrite-drift", callback_command())
    assert service.state.mapping("callback", "callback-isolated") is None


def test_reconciliation_compares_desired_and_actual_and_reports_drift(harness):
    service, repo = harness
    command = CampaignCommand.model_validate(campaign_payload())
    service.provision_campaign("reconcile-desired", command)
    complete = service.reconciliation()
    assert complete["status"] == "complete"
    assert all(row["desired_hash"] and row["match"]
               for row in complete["resources"])
    repo.scripts["COD_TEST_SDR_V1"]["script_text"] = "drift"
    drift = service.reconciliation()
    assert drift["status"] == "drift_detected"
    script = next(row for row in drift["resources"]
                  if row["type"] == "script")
    assert script["status"] == "mismatch"
    assert script["repair_classification"] == "approval_required"


@pytest.mark.parametrize("drift", ["policy", "lead", "agent"])
def test_callback_exact_replay_survives_mutable_policy_drift(harness, drift):
    service, repo = harness
    provision_callback_dependencies(service)
    command = callback_command()
    first = service.create_callback("callback-stable-replay", command)
    if drift == "policy":
        service.state.put_campaign_policy("CODT0001", [], "recording-policy", "reporting-map", False)
    elif drift == "lead":
        repo.leads[1]["list_id"] = 31001
    else:
        repo.agents["COD1001"]["campaigns"] = ["OTHER001"]
    replay = service.create_callback("callback-stable-replay", command)
    assert replay == first | {"idempotent_replay": True}
    assert len(repo.callbacks) == 1
    new_command = command.model_copy(update={"context": command.context.model_copy(update={"expected_version": 1})})
    with pytest.raises(PermissionError):
        service.create_callback("callback-new-attempt", new_command)
    assert len(repo.callbacks) == 1


@pytest.mark.parametrize("membership", [None, " CODT0001 OTHER001 -", " OTHER001 -"])
def test_mysql_sync_preflights_target_group_before_update(membership):
    from types import SimpleNamespace
    from codestra_vicidial.models import AgentSpec
    from codestra_vicidial.repository import MySQLRepository

    calls = []
    repo = MySQLRepository.__new__(MySQLRepository)
    repo.db = SimpleNamespace(commit=lambda: calls.append("commit"), rollback=lambda: calls.append("rollback"))
    repo._agent = lambda user: {"user_id": user, "active": "N"}
    repo._one = lambda sql, args: None if membership is None else {"allowed_campaigns": membership}
    repo._execute = lambda *args: calls.append("write") or 1
    spec = AgentSpec(user_id="COD1001", full_name="Synthetic Agent", user_group="COD_TEST_SDR",
                     campaigns=["CODT0001"], inbound_groups=[], active=False)
    with pytest.raises(CollisionError):
        repo.sync_agent_disabled(spec)
    assert calls == ["rollback"]
