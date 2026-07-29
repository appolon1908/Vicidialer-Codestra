from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from codestra_vicidial.ami_gateway import (
    AMIEventGateway, AMIFrameParser, AMIParseError, EventOutbox,
    normalize_event,
)
from codestra_vicidial.grants import ExecutionGrantValidator
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
