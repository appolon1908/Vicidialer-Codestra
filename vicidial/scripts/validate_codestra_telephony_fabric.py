#!/usr/bin/env python3
"""Fail-closed validator for the private Codestra telephony fabric contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "docs" / "integrations" / "codestra-fabric"

REQUIRED_PATH_METHODS = {
    "/v1/telephony/calls/preview": {"post"},
    "/v1/telephony/calls": {"get", "post"},
    "/v1/telephony/calls/{call_id}": {"get"},
    "/v1/telephony/calls/{call_id}/cancel": {"post"},
    "/v1/telephony/calls/{call_id}/events": {"get"},
    "/v1/telephony/calls/{call_id}/disposition": {"post"},
    "/v1/telephony/callbacks": {"get", "post"},
    "/v1/telephony/callbacks/{callback_id}": {"get"},
    "/v1/telephony/callbacks/{callback_id}/cancel": {"post"},
    "/v1/telephony/agents/status": {"get"},
    "/v1/telephony/campaigns/status": {"get"},
    "/v1/telephony/recordings/{recording_id}/metadata": {"get"},
    "/v1/telephony/provider-health": {"get"},
    "/v1/telephony/usage": {"get"},
    "/v1/webhooks/telephony/vicidial": {"post"},
}

REQUIRED_COMMANDS = {
    "voice.call.preview.v1",
    "voice.call.place.v1",
    "voice.call.cancel.v1",
    "voice.callback.schedule.v1",
    "voice.callback.cancel.v1",
    "voice.disposition.record.v1",
}

REQUIRED_EVENTS = {
    "voice.call.requested.v1",
    "voice.call.queued.v1",
    "voice.call.ringing.v1",
    "voice.call.answered.v1",
    "voice.call.completed.v1",
    "voice.call.failed.v1",
    "voice.call.indeterminate.v1",
    "voice.callback.scheduled.v1",
    "voice.callback.completed.v1",
    "voice.disposition.recorded.v1",
    "voice.recording.available.v1",
}

FORBIDDEN_PATH_TOKENS = {"/admin", "/sql", "/shell", "/ami", "/ari", "/carrier", "/dialplan"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_contracts(root: Path = CONTRACT_ROOT) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads((root / "manifest.v1.json").read_text(encoding="utf-8"))
    openapi = yaml.safe_load((root / "telephony-api.openapi.yaml").read_text(encoding="utf-8"))
    require(isinstance(manifest, dict), "manifest must be a JSON object")
    require(isinstance(openapi, dict), "OpenAPI document must be a mapping")
    return manifest, openapi


def validate_manifest(manifest: dict[str, Any]) -> None:
    require(manifest.get("schema_version") == "1.1", "unexpected manifest schema version")
    require(manifest.get("system") == "vicidial-asterisk", "unexpected system authority")
    require(
        manifest.get("integration_boundary") == "MIDDLEWARE_RESTRICTED_ADAPTER_ONLY",
        "Middleware/restricted-adapter boundary is required",
    )
    require(
        manifest.get("adapter_path")
        == "10.40.0.1 -> 10.40.0.2:8443 -> 127.0.0.1:8097",
        "private adapter path changed",
    )

    for key in (
        "public_api",
        "n8n_direct_access",
        "database_direct_access",
        "ami_direct_access",
        "ari_direct_access",
        "carrier_direct_access",
        "deployment_authorized",
        "production_authorized",
    ):
        require(manifest.get(key) is False, f"{key} must remain false")

    require(manifest.get("service_identity") == "vicidial-adapter", "service identity changed")
    require(
        manifest.get("success_authority")
        == "RESTRICTED_ADAPTER_AUTHORITATIVE_READBACK",
        "authoritative read-back must remain the success authority",
    )
    require(manifest.get("calls_placed") == 0, "source contract must record zero calls placed")

    capabilities = manifest.get("capabilities")
    require(isinstance(capabilities, dict) and capabilities, "capability map is required")
    require(not any(capabilities.values()), "all telephony capabilities must remain disabled")

    unknown = manifest.get("unknown_outcome")
    require(isinstance(unknown, dict), "unknown-outcome policy is required")
    require(
        unknown.get("command_execution_attempts_after_uncertainty") == 1,
        "uncertain commands must remain one-attempt",
    )
    require(unknown.get("blind_redial") is False, "blind redial is forbidden")
    require(
        unknown.get("automatic_resubmission") is False,
        "automatic resubmission is forbidden",
    )
    require(
        unknown.get("authoritative_readback_required") is True,
        "authoritative read-back is required",
    )
    require(
        unknown.get("intermediate_state") == "reconciliation_required",
        "unexpected intermediate unknown state",
    )
    require(
        unknown.get("unresolved_state") == "manual_review",
        "unresolved outcomes must require manual review",
    )

    isolation = manifest.get("campaign_isolation")
    require(isinstance(isolation, dict), "campaign-isolation policy is required")
    require(
        isolation.get("agent_campaign_membership") == "EXACTLY_ONE",
        "each agent must belong to exactly one campaign",
    )
    require(
        isolation.get("primary_supervisor_per_campaign") == 1,
        "each campaign must have exactly one primary supervisor",
    )
    false_keys = (
        "cross_campaign_login",
        "cross_campaign_lead_access",
        "cross_campaign_call_access",
        "cross_campaign_callback_access",
        "cross_campaign_transfer",
        "cross_campaign_recording_access",
        "cross_campaign_reporting",
        "cross_campaign_email",
    )
    require(
        all(isolation.get(key) is False for key in false_keys),
        "cross-campaign access must remain denied",
    )

    event_delivery = manifest.get("event_delivery")
    require(isinstance(event_delivery, dict), "event-delivery policy is required")
    require(event_delivery.get("mode") == "SIGNED_ADAPTER_EVENTS", "signed events required")
    require(
        event_delivery.get("durable_acceptance_required") is True,
        "durable event acceptance required",
    )
    require(
        event_delivery.get("replay_rejection_required") is True,
        "event replay rejection required",
    )
    require(
        event_delivery.get("provider_callback_cannot_choose_tenant") is True,
        "provider callbacks cannot choose tenant authority",
    )

    require(set(manifest.get("commands", ())) == REQUIRED_COMMANDS, "command registry drift")
    require(set(manifest.get("events", ())) == REQUIRED_EVENTS, "event registry drift")


def _parameter_names(operation: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for parameter in operation.get("parameters", []):
        if "$ref" in parameter:
            names.add(str(parameter["$ref"]).rsplit("/", 1)[-1])
        elif "name" in parameter:
            names.add(str(parameter["name"]))
    return names


def validate_openapi(openapi: dict[str, Any]) -> None:
    require(openapi.get("openapi") == "3.1.0", "OpenAPI 3.1.0 is required")
    info = openapi.get("info")
    require(isinstance(info, dict) and info.get("version") == "1.1.0", "API version drift")

    paths = openapi.get("paths")
    require(isinstance(paths, dict), "OpenAPI paths are required")
    require(set(paths) == set(REQUIRED_PATH_METHODS), "private API path registry drift")
    require(
        not any(token in path.lower() for path in paths for token in FORBIDDEN_PATH_TOKENS),
        "generic administration or provider-control path is forbidden",
    )

    for path, methods in REQUIRED_PATH_METHODS.items():
        item = paths[path]
        require(isinstance(item, dict), f"{path} must be a path item")
        actual_methods = {key for key in item if key in {"get", "post", "put", "patch", "delete"}}
        require(actual_methods == methods, f"{path} method registry drift")

        for method in methods:
            operation = item[method]
            require(operation.get("operationId"), f"{method.upper()} {path} needs operationId")
            require(operation.get("responses"), f"{method.upper()} {path} needs responses")
            if path == "/v1/webhooks/telephony/vicidial":
                require(
                    operation.get("security") == [{"mutualTLS": []}],
                    "webhook ingress must require mTLS",
                )
                names = _parameter_names(operation)
                require(
                    {"EventId", "EventTimestamp", "EventSignature"} <= names,
                    "signed webhook headers are required",
                )
            else:
                security = operation.get("security")
                require(
                    isinstance(security, list) and security and "oauth2" in security[0],
                    f"{method.upper()} {path} must require OAuth2",
                )
                names = _parameter_names(operation)
                require("TenantId" in names, f"{method.upper()} {path} must require tenant")
                if path not in {"/v1/telephony/provider-health"}:
                    require(
                        "CampaignId" in names,
                        f"{method.upper()} {path} must require campaign isolation",
                    )

            if method in {"post", "put", "patch", "delete"} and path != "/v1/webhooks/telephony/vicidial":
                require(
                    "IdempotencyKey" in _parameter_names(operation),
                    f"{method.upper()} {path} must require idempotency",
                )

    schemes = openapi.get("components", {}).get("securitySchemes", {})
    require(schemes.get("mutualTLS", {}).get("type") == "mutualTLS", "mTLS scheme required")
    scopes = (
        schemes.get("oauth2", {})
        .get("flows", {})
        .get("clientCredentials", {})
        .get("scopes", {})
    )
    required_scopes = {
        "telephony.call.preview",
        "telephony.call.write",
        "telephony.call.read",
        "telephony.callback.read",
        "telephony.callback.write",
        "telephony.disposition.write",
        "telephony.agent.read",
        "telephony.campaign.read",
        "telephony.recording.read",
        "telephony.provider.read",
        "telephony.usage.read",
    }
    require(set(scopes) == required_scopes, "OAuth scope registry drift")

    call_preview = openapi["components"]["schemas"]["CallPreview"]
    require(
        call_preview.get("properties", {}).get("liveEffect", {}).get("const") is False,
        "call preview must remain no-effect",
    )


def validate(root: Path = CONTRACT_ROOT) -> None:
    manifest, openapi = load_contracts(root)
    validate_manifest(manifest)
    validate_openapi(openapi)


if __name__ == "__main__":
    validate()
    print("VICIDIAL_CODESTRA_TELEPHONY_FABRIC=PASS")
    print("VICIDIAL_CAMPAIGN_ISOLATION=PASS")
    print("VICIDIAL_UNKNOWN_OUTCOME_ZERO_REDIAL=PASS")
    print("VICIDIAL_LIVE_EFFECT_FLAGS_DISABLED=PASS")
