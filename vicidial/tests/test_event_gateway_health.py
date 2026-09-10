"""Health must reflect durable event delivery outcomes, including after restart."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from codestra_vicidial import ami_gateway
from codestra_vicidial.ami_gateway import AMIMessage, AgentBinding, AgentDirectory, LifecycleStore
from codestra_vicidial.middleware_events import (
    EventDeliveryWorker, PermanentDeliveryError, RetryableDeliveryError,
)
from codestra_vicidial.run_ami_gateway import GatewayRuntime, StatusWriter


@pytest.fixture
def outbox(tmp_path):
    store = LifecycleStore(tmp_path / "events.sqlite3")
    directory = AgentDirectory([AgentBinding(
        extension="6101", tenant_id="COD", business_unit_id="COD",
        campaign_id="TEST_SYN", agent_id="SYN6101",
        keycloak_subject="00000000-0000-0000-0000-000000006101",
        default_direction="inbound",
    )])
    message = AMIMessage({key: (value,) for key, value in {
        "Event": "AgentCalled", "Linkedid": "synthetic-linked",
        "Uniqueid": "synthetic-unique", "Channel": "PJSIP/6101-00000001",
    }.items()})
    store.process(message, directory, observed_at=datetime.now(timezone.utc))
    assert store.pending_count() > 0
    return store


def worker(store, error=None):
    def publish(envelope):
        if error is not None:
            raise error
    return EventDeliveryWorker(
        store=store, publisher=SimpleNamespace(publish=publish),
        minimum_retry_seconds=1,
    )


def snapshot(writer):
    return json.loads(writer.path.read_text())


def test_dead_letters_survive_restart_and_cannot_be_hidden_by_ami_ready(outbox, tmp_path):
    delivery = worker(outbox, PermanentDeliveryError("synthetic denial"))
    while delivery.run_once():
        pass
    assert outbox.pending_count() == 0
    reopened = LifecycleStore(outbox.path)
    health = reopened.delivery_health()
    assert health["pending_events"] == health["retrying_events"] == 0
    assert health["dead_letter_events"] > 0

    status = StatusWriter(tmp_path / "status.json", source_sha="a" * 40)
    status.update(status="ready", ami_connected=True, **health)
    status.update(status="ready", last_ami_event_at="2026-09-10T00:00:00Z")
    value = snapshot(status)
    assert value["status"] == "degraded"
    assert value["delivery_status"] == "dead_letter"
    assert value["ami_connected"] is True
    assert not any(text in status.path.read_text() for text in (
        "synthetic denial", "synthetic-linked", "SYN6101", "envelope",
    ))


def test_retry_recovery_clears_delivery_degradation_without_new_ami_event(
    outbox, tmp_path, monkeypatch,
):
    clock = [1000.0]
    monkeypatch.setattr(ami_gateway.time, "time", lambda: clock[0])
    status = StatusWriter(tmp_path / "status.json", source_sha="a" * 40)
    status.update(status="ready", ami_connected=True, **outbox.delivery_health())
    assert snapshot(status)["status"] == "ready"
    assert snapshot(status)["delivery_status"] == "pending"

    assert worker(outbox, RetryableDeliveryError("synthetic unavailable")).run_once()
    status.update(**outbox.delivery_health())
    assert snapshot(status)["status"] == "degraded"
    assert snapshot(status)["retrying_events"] == 1
    assert snapshot(status)["delivery_status"] == "retrying"

    clock[0] += 10
    delivery = worker(outbox)
    while delivery.run_once():
        pass
    status.update(**outbox.delivery_health())
    value = snapshot(status)
    assert value["status"] == "ready"
    assert value["delivery_status"] == "idle"
    assert outbox.delivery_health() == {
        "pending_events": 0, "retrying_events": 0, "dead_letter_events": 0,
    }


@pytest.mark.parametrize("runtime_status", ["starting", "connecting", "degraded", "failed", "stopped"])
def test_delivery_recovery_preserves_ami_and_process_health(tmp_path, runtime_status):
    status = StatusWriter(tmp_path / "status.json", source_sha="a" * 40)
    status.update(status=runtime_status, retrying_events=1, pending_events=1)
    status.update(retrying_events=0, pending_events=0)
    assert snapshot(status)["status"] == runtime_status


def test_dead_letter_takes_precedence_over_retrying(tmp_path):
    status = StatusWriter(tmp_path / "status.json", source_sha="a" * 40)
    status.update(status="ready", pending_events=3, retrying_events=2, dead_letter_events=1)
    value = snapshot(status)
    assert value["status"] == "degraded"
    assert value["delivery_status"] == "dead_letter"
    assert value["pending_events"] == 3


def test_runtime_delivery_loop_publishes_durable_failure_counts(outbox, tmp_path):
    status = StatusWriter(tmp_path / "status.json", source_sha="a" * 40)
    runtime = GatewayRuntime(
        config=SimpleNamespace(delivery_idle_seconds=0.01),
        directory=None, store=outbox, ami_client=None,
        delivery_worker=worker(outbox, PermanentDeliveryError("synthetic denial")),
        status=status,
    )
    # AMI can authenticate before the delivery thread gets its first turn.
    status.update(status="ready", ami_connected=True)
    original_update = status.update

    def stop_after_cycle(**values):
        original_update(**values)
        if "last_delivery_cycle_at" in values:
            runtime.request_stop()

    status.update = stop_after_cycle
    runtime._delivery_loop()
    value = snapshot(status)
    assert value["dead_letter_events"] == 1
    assert value["delivery_status"] == "dead_letter"
    assert value["status"] == "degraded"
    assert value["delivery_worker_running"] is False
    assert value["last_delivery_cycle_at"] is not None
