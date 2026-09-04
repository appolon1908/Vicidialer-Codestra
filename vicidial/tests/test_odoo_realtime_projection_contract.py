from __future__ import annotations

import json
from pathlib import Path

from codestra_vicidial.ami_gateway import MIDDLEWARE_EVENT_PREFIX

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "config" / "odoo-realtime-projection-contract.json"
EXPECTED_EVENT_TYPES = (
    "codestra.vicidial.call.lifecycle.answered",
    "codestra.vicidial.call.lifecycle.completed",
    "codestra.vicidial.call.lifecycle.connected",
    "codestra.vicidial.call.lifecycle.created",
    "codestra.vicidial.call.lifecycle.failed",
    "codestra.vicidial.call.lifecycle.hangup",
    "codestra.vicidial.call.lifecycle.held",
    "codestra.vicidial.call.lifecycle.missed",
    "codestra.vicidial.call.lifecycle.offered",
    "codestra.vicidial.call.lifecycle.resumed",
    "codestra.vicidial.call.lifecycle.ringing",
    "codestra.vicidial.call.lifecycle.transfer.completed",
    "codestra.vicidial.call.lifecycle.transfer.started",
)


def test_current_ami_gateway_matches_cross_repository_contract() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert value["event_prefix"] == MIDDLEWARE_EVENT_PREFIX
    assert tuple(value["event_types"]) == EXPECTED_EVENT_TYPES
    assert all(item.startswith(MIDDLEWARE_EVENT_PREFIX) for item in value["event_types"])
    assert value["producer"] == "vicidial-adapter"
    assert value["consumer"] == "middleware-api"
    assert value["path"] == "/api/v1/vicidial/events"
    assert value["scope"] == "telephony.events.publish"


def test_contract_remains_read_only_and_never_carries_audio() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    safety = value["safety"]
    assert safety == {
        "read_only_ami": True,
        "carries_audio": False,
        "allows_originate": False,
        "allows_ari_command": False,
        "production_dialing_default": False,
    }
