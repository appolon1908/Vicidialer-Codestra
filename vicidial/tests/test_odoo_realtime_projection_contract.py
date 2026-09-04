from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from codestra_vicidial.ami_gateway import (
    AMIMessage,
    AgentBinding,
    AgentDirectory,
    LifecycleStore,
    MIDDLEWARE_EVENT_PREFIX,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "config" / "odoo-realtime-projection-contract.json"
EXTENSION = "6101"


def _message(event: str, linkedid: str, uniqueid: str, **fields: str) -> AMIMessage:
    values = {
        "Event": event,
        "Linkedid": linkedid,
        "Uniqueid": uniqueid,
        "Channel": f"PJSIP/{EXTENSION}-00000001",
        **fields,
    }
    return AMIMessage({key: (str(value),) for key, value in values.items()})


def _gateway_events(tmp_path: Path) -> list[dict[str, object]]:
    store = LifecycleStore(tmp_path / "lifecycle.sqlite3")
    directory = AgentDirectory(
        [
            AgentBinding(
                extension=EXTENSION,
                tenant_id="COD",
                business_unit_id="COD",
                campaign_id="TEST_SYN",
                agent_id="SYN6101",
                keycloak_subject="00000000-0000-0000-0000-000000006101",
                default_direction="inbound",
            )
        ]
    )
    now = datetime(2026, 9, 4, 18, 0, tzinfo=timezone.utc)
    events: list[dict[str, object]] = []

    def process(
        offset: int,
        event: str,
        linkedid: str,
        uniqueid: str,
        **fields: str,
    ) -> None:
        events.extend(
            store.process(
                _message(event, linkedid, uniqueid, **fields),
                directory,
                observed_at=now + timedelta(seconds=offset),
            )
        )

    # Normal answered call: created through completed, including hold/resume.
    process(0, "AgentCalled", "linked-normal", "uid-normal")
    process(
        1,
        "Newstate",
        "linked-normal",
        "uid-normal",
        ChannelStateDesc="Ringing",
    )
    process(2, "AgentConnect", "linked-normal", "uid-normal")
    process(3, "Hold", "linked-normal", "uid-normal")
    process(4, "Unhold", "linked-normal", "uid-normal")
    process(5, "AgentComplete", "linked-normal", "uid-normal")

    # Failed before answer.
    process(10, "DialBegin", "linked-failed", "uid-failed")
    process(
        11,
        "DialEnd",
        "linked-failed",
        "uid-failed",
        DialStatus="CHANUNAVAIL",
    )

    # Ring-no-answer terminal state.
    process(20, "AgentCalled", "linked-missed", "uid-missed")
    process(21, "AgentRingNoAnswer", "linked-missed", "uid-missed")

    # Successful blind transfer emits both transfer lifecycle events.
    process(30, "AgentCalled", "linked-transfer", "uid-transfer")
    process(31, "AgentConnect", "linked-transfer", "uid-transfer")
    process(
        32,
        "BlindTransfer",
        "linked-transfer",
        "uid-transfer",
        Result="Success",
        Extension="6901",
    )

    return events


def test_current_ami_gateway_matches_cross_repository_contract(tmp_path: Path) -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    emitted = _gateway_events(tmp_path)
    emitted_types = sorted({str(event["event_type"]) for event in emitted})

    assert value["event_prefix"] == MIDDLEWARE_EVENT_PREFIX
    assert value["event_types"] == emitted_types
    assert all(item.startswith(MIDDLEWARE_EVENT_PREFIX) for item in emitted_types)
    assert value["producer"] == "vicidial-adapter"
    assert value["consumer"] == "middleware-api"
    assert value["path"] == "/api/v1/vicidial/events"
    assert value["scope"] == "telephony.events.publish"

    required_payload_fields = set(value["required_payload_fields"])
    assert required_payload_fields
    for envelope in emitted:
        assert required_payload_fields <= set(envelope["payload"])


def test_contract_remains_read_only_and_never_carries_audio() -> None:
    value = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert value["transport"]["mtls"] == {
        "mode": "optional",
        "client_certificate_pair_required_when_enabled": True,
    }
    safety = value["safety"]
    assert safety == {
        "read_only_ami": True,
        "carries_audio": False,
        "allows_originate": False,
        "allows_ari_command": False,
        "production_dialing_default": False,
    }
