from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from codestra_vicidial.ami_gateway import (
    AMIEventGateway, AMIFrameParser, AMIParseError, EventOutbox,
    normalize_event,
)
from codestra_vicidial.grants import ExecutionGrantValidator
from codestra_vicidial.app import create_app
from codestra_vicidial.repository import MemoryRepository
from codestra_vicidial.security import RequestAuthenticator
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore


def test_execution_grant_binds_operation_key_body_target_and_expiry():
    validator = ExecutionGrantValidator(
        key=b"g" * 32, expected_target="vicidial:staging")
    body = b'{"safe":true}'
    now = int(time.time())
    token = validator.issue(
        grant_id="grant-12345678", operation="agent.sync",
        idempotency_key="stable-key", body=body, expires_at=now + 60)
    grant = validator.verify(
        token, operation="agent.sync", idempotency_key="stable-key",
        body=body, now=now)
    assert grant.target == "vicidial:staging"
    for override, message in [
        ({"operation": "agent.disable"}, "operation"),
        ({"idempotency_key": "other-key"}, "idempotency"),
        ({"body": b"{}"}, "body"),
    ]:
        values = {
            "operation": "agent.sync", "idempotency_key": "stable-key",
            "body": body, "now": now,
        } | override
        with pytest.raises(PermissionError, match=message):
            validator.verify(token, **values)
    with pytest.raises(PermissionError, match="expired"):
        validator.verify(
            token, operation="agent.sync", idempotency_key="stable-key",
            body=body, now=now + 61)


def test_operation_lifecycle_and_interrupted_recovery(tmp_path: Path):
    state = StateStore(tmp_path / "state.db")
    digest = state.digest({"request": 1})
    operation_id = state.begin_operation(
        "campaign.provision-disabled", "stable-key", digest, "corr-12345678")
    assert state.operation(operation_id)["status"] == "running"
    recovered = state.recover_interrupted()
    assert recovered[0]["operation_id"] == operation_id
    assert state.operation(operation_id)["status"] == "recovery_required"
    same_id = state.begin_operation(
        "campaign.provision-disabled", "stable-key", digest, "corr-12345678")
    assert same_id == operation_id
    state.finish_operation(operation_id, {"status": "complete_disabled"})
    result = state.operation(operation_id)
    assert result["status"] == "succeeded"
    assert result["attempt"] == 2


def test_ami_parser_normalization_redaction_and_deduplication(tmp_path: Path):
    raw = (
        b"Event: Hangup\r\nUniqueid: 123.4\r\nLinkedid: 123.4\r\n"
        b"Channel: PJSIP/1001-0001\r\nCallerIDNum: 2025550199\r\n"
        b"Cause: 16\r\n\r\n"
    )
    frames = AMIFrameParser().feed(raw)
    event = normalize_event(frames[0], observation_epoch="epoch-12345678")
    assert event["event_type"] == "Hangup"
    assert "CallerIDNum" not in event["payload"]
    gateway = AMIEventGateway(
        EventOutbox(tmp_path / "events.db"), "epoch-12345678")
    assert gateway.ingest(raw) == 1
    assert gateway.ingest(raw) == 0
    assert gateway.status()["duplicates"] == 1
    assert len(gateway.outbox.pending()) == 1


def test_ami_parser_is_bounded_and_rejects_malformed_frames():
    with pytest.raises(AMIParseError, match="buffer"):
        AMIFrameParser(max_buffer_bytes=4).feed(b"12345")
    with pytest.raises(AMIParseError, match="malformed"):
        AMIFrameParser().feed(b"not-a-header\r\n\r\n")


def test_unknown_ami_event_is_ignored():
    assert normalize_event(
        {"Event": "OriginateResponse", "ActionID": "unsafe"},
        observation_epoch="epoch-12345678") is None


def test_complete_vicidial_asterisk_route_contract(tmp_path: Path):
    service = AdapterService(
        MemoryRepository(), StateStore(tmp_path / "contract.db"), FeatureFlags())
    app = create_app(
        service,
        RequestAuthenticator(
            "codestra-middleware", b"a" * 32, frozenset({"127.0.0.1"})),
        ExecutionGrantValidator(b"g" * 32, "local-vicidial"),
    )
    routes = {
        (method, route.path) for route in app.routes
        for method in getattr(route, "methods", set())
    }
    required = {
        ("GET", "/health/live"), ("GET", "/health/ready"),
        ("GET", "/.well-known/codestra-service"), ("GET", "/metrics"),
        ("GET", "/api/v1/telephony/capabilities"),
        ("GET", "/api/v1/telephony/effective-flags"),
        ("POST", "/api/v1/telephony/policy/evaluations"),
        ("PUT", "/api/v1/vicidial/users/{agent_public_id}"),
        ("GET", "/api/v1/vicidial/users/{agent_public_id}"),
        ("POST", "/api/v1/vicidial/users/{agent_public_id}/enable"),
        ("POST", "/api/v1/vicidial/users/{agent_public_id}/disable"),
        ("POST", "/api/v1/vicidial/users/{agent_public_id}/reconcile"),
        ("PUT", "/api/v1/vicidial/phones/{phone_public_id}"),
        ("GET", "/api/v1/vicidial/phones/{phone_public_id}"),
        ("POST", "/api/v1/vicidial/phones/{phone_public_id}/enable"),
        ("POST", "/api/v1/vicidial/phones/{phone_public_id}/disable"),
        ("POST", "/api/v1/vicidial/phones/{phone_public_id}/reconcile"),
        ("GET", "/api/v1/vicidial/campaigns/{campaign_public_id}"),
        ("GET", "/api/v1/vicidial/lists/{list_public_id}"),
        ("GET", "/api/v1/vicidial/leads/{lead_public_id}"),
        ("GET", "/api/v1/vicidial/agents/{agent_public_id}/state"),
        ("GET", "/api/v1/vicidial/runtime"),
        ("PUT", "/api/v1/asterisk/endpoints/{endpoint_public_id}"),
        ("GET", "/api/v1/asterisk/endpoints/{endpoint_public_id}"),
        ("POST", "/api/v1/asterisk/endpoints/{endpoint_public_id}/enable"),
        ("POST", "/api/v1/asterisk/endpoints/{endpoint_public_id}/disable"),
        ("POST", "/api/v1/asterisk/endpoints/{endpoint_public_id}/reconcile"),
        ("GET", "/api/v1/asterisk/endpoints/{endpoint_public_id}/contacts"),
        ("GET", "/api/v1/asterisk/endpoints/{endpoint_public_id}/contacts/{contact_public_id}"),
        ("POST", "/api/v1/asterisk/endpoints/{endpoint_public_id}/contacts/{contact_public_id}/revoke"),
        ("POST", "/api/v1/asterisk/endpoints/{endpoint_public_id}/contacts/revoke-all"),
        ("GET", "/api/v1/asterisk/dialplan/contexts"),
        ("GET", "/api/v1/asterisk/dialplan/contexts/{context_key}"),
        ("GET", "/api/v1/asterisk/dialplan/routes/{route_key}"),
        ("POST", "/api/v1/asterisk/dialplan/validations"),
        ("GET", "/api/v1/asterisk/runtime"),
        ("GET", "/api/v1/asterisk/runtime/active"),
        ("GET", "/api/v1/asterisk/runtime/calls"),
        ("GET", "/api/v1/asterisk/runtime/channels"),
        ("GET", "/api/v1/asterisk/runtime/bridges"),
        ("POST", "/api/v1/asterisk/internal-calls"),
        ("GET", "/api/v1/asterisk/calls/{call_public_id}"),
        ("POST", "/api/v1/asterisk/calls/{call_public_id}/hangup"),
        ("POST", "/api/v1/asterisk/calls/{call_public_id}/dtmf"),
        ("POST", "/api/v1/asterisk/calls/{call_public_id}/hold"),
        ("POST", "/api/v1/asterisk/calls/{call_public_id}/resume"),
    }
    assert required <= routes


def test_required_extended_flag_defaults_are_fail_closed():
    flags = FeatureFlags()
    assert flags.telephony_adapter_api
    assert flags.vicidial_reads
    assert flags.asterisk_reads
    assert flags.reconciliation
    assert flags.metrics
    assert flags.local_kill_switch
    assert not any([
        flags.vicidial_user_writes, flags.vicidial_phone_writes,
        flags.vicidial_campaign_writes, flags.vicidial_lead_writes,
        flags.asterisk_endpoint_writes, flags.asterisk_contact_revocation,
        flags.asterisk_reload, flags.internal_calls, flags.call_hangup,
        flags.call_dtmf, flags.call_hold, flags.call_resume, flags.transfers,
        flags.external_calls, flags.customer_calls,
    ])


def test_restricted_call_contract_rejects_while_kill_switch_active(tmp_path: Path):
    key = b"a" * 32
    grant_key = b"g" * 32
    auth = RequestAuthenticator(
        "codestra-middleware", key, frozenset({"127.0.0.1"}))
    grants = ExecutionGrantValidator(grant_key, "local-vicidial")
    app = create_app(
        AdapterService(
            MemoryRepository(), StateStore(tmp_path / "calls.db"), FeatureFlags()),
        auth, grants)
    body = b"{}"
    now = int(time.time())
    nonce = "call-control-12345678"
    operation = "asterisk.call.hangup"
    headers = {
        "X-Service-Identity": "codestra-middleware",
        "X-Service-Scopes": "telephony:write",
        "X-Request-Timestamp": str(now),
        "X-Request-Nonce": nonce,
        "X-Request-Signature": auth.sign(now, nonce, body),
        "Idempotency-Key": "call-hangup-stable-key",
        "X-Execution-Grant": grants.issue(
            grant_id="grant-call-12345678", operation=operation,
            idempotency_key="call-hangup-stable-key", body=body,
            expires_at=now + 60),
        "Content-Type": "application/json",
    }
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.post(
        "/api/v1/asterisk/calls/call-12345678/hangup",
        content=body, headers=headers)
    assert response.status_code == 403
    assert response.json()["detail"] == "local kill switch is active"


def test_effective_flags_use_contract_names_and_defaults(tmp_path: Path):
    key = b"a" * 32
    auth = RequestAuthenticator(
        "codestra-middleware", key, frozenset({"127.0.0.1"}))
    app = create_app(
        AdapterService(
            MemoryRepository(), StateStore(tmp_path / "flags.db"), FeatureFlags()),
        auth, ExecutionGrantValidator(b"g" * 32, "local-vicidial"))
    now = int(time.time())
    nonce = "effective-flags-12345678"
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.get(
        "/api/v1/telephony/effective-flags",
        headers={
            "X-Service-Identity": "codestra-middleware",
            "X-Service-Scopes": "telephony:read",
            "X-Request-Timestamp": str(now),
            "X-Request-Nonce": nonce,
            "X-Request-Signature": auth.sign(now, nonce, b""),
        })
    assert response.status_code == 200
    values = response.json()
    assert values["TELEPHONY_ADAPTER_API_ENABLED"] is True
    assert values["VICIDIAL_READS_ENABLED"] is True
    assert values["ASTERISK_READS_ENABLED"] is True
    assert values["LOCAL_KILL_SWITCH"] is True
    assert values["INTERNAL_CALLS_ENABLED"] is False
    assert values["CALL_HANGUP_ENABLED"] is False
