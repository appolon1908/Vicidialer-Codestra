"""Regression evidence for the exact PR26 authorization and concurrency findings."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from codestra_vicidial.ami_gateway import (
    AgentBinding,
    AgentDirectory,
    LifecycleStore,
    parse_ami_block,
)
from codestra_vicidial.internal_call_transport import (
    CallDenied,
    SocketAmiTransport,
    digest,
)
from codestra_vicidial.internal_calling import InternalCallError, InternalCallExecutor
from test_internal_calling import command, executor, policy, seed_connected


def race(first, second):
    barrier = Barrier(2)
    def run(fn):
        barrier.wait(timeout=5)
        try:
            return fn()
        except PermissionError:
            return "denied"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, fn) for fn in (first, second)]
        return [f.result(timeout=10) for f in futures]


def test_one_call_quota_is_atomic_across_distinct_operation_ids(tmp_path):
    service, transport, _ = executor(tmp_path)
    results = race(lambda: service.originate(command()), lambda: service.originate(command(operation_id="second-operation-0001")))
    assert results.count("denied") == 1
    assert len(transport.actions) == 1
    with pytest.raises(PermissionError, match="exhausted"):
        service.originate(command(operation_id="third-operation-0001"))


def test_same_call_hangup_claim_is_atomic(tmp_path):
    service, transport, lifecycle = executor(tmp_path)
    accepted = service.originate(command())
    seed_connected(lifecycle, accepted["asterisk_uniqueid"])
    results = race(lambda: service.hangup(command().operation_id), lambda: service.hangup(command().operation_id))
    assert len([r for r in results if r.get("duplicate")]) == 1
    assert [a["Action"] for a in transport.actions] == ["Originate", "Hangup"]


def test_durable_audit_records_actor_denial_success_and_uncertain(tmp_path):
    service, transport, _ = executor(tmp_path)
    with pytest.raises(PermissionError):
        service.originate(command(subject="other-subject"), actor="authenticated-middleware")
    transport.action = lambda _: (_ for _ in ()).throw(TimeoutError("synthetic-only"))
    with pytest.raises(TimeoutError):
        service.originate(command(), actor="authenticated-middleware")
    assert service.originate(command(), actor="authenticated-middleware")["duplicate"]
    with sqlite3.connect(service.audit_path) as db:
        rows = db.execute("SELECT actor,outcome,correlation_id FROM audit ORDER BY sequence").fetchall()
    assert {r[1] for r in rows} >= {"requested", "denied", "failed_or_uncertain", "duplicate"}
    assert {r[0] for r in rows} == {"authenticated-middleware"}
    assert {r[2] for r in rows} == {command().correlation_id}


def test_revoked_or_rebound_policy_denies_readback_and_hangup(tmp_path):
    service, transport, lifecycle = executor(tmp_path)
    accepted = service.originate(command())
    seed_connected(lifecycle, accepted["asterisk_uniqueid"])
    service.policy_provider = lambda: policy().model_copy(update={"tenant_id": "different-tenant"})
    with pytest.raises(PermissionError):
        service.readback(command().operation_id)
    with pytest.raises(PermissionError):
        service.hangup(command().operation_id)
    assert len(transport.actions) == 1


def test_expired_policy_denies_all_operations(tmp_path):
    service, _, _ = executor(tmp_path)
    service.originate(command())
    service.policy_provider = lambda: service.policy.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    for action in [lambda: service.originate(command()), lambda: service.readback(command().operation_id), lambda: service.hangup(command().operation_id)]:
        with pytest.raises(PermissionError):
            action()


def test_kill_switch_blocks_originate_but_preserves_same_call_cleanup(tmp_path):
    service, transport, lifecycle = executor(tmp_path)
    service.enabled = lambda: False
    with pytest.raises(PermissionError):
        service.originate(command())
    assert not transport.actions
    service.enabled = lambda: True
    accepted = service.originate(command())
    seed_connected(lifecycle, accepted["asterisk_uniqueid"])
    service.enabled = lambda: False
    assert service.hangup(command().operation_id)["hangup"] == "requested"


def test_inactive_binding_rejected_before_transport(tmp_path):
    service, transport, _ = executor(tmp_path)
    service.identity_ready = lambda: False
    with pytest.raises(PermissionError):
        service.originate(command())
    assert not transport.actions


def test_terminal_readback_requires_matching_gateway_terminal_event(tmp_path):
    service, _, lifecycle = executor(tmp_path)
    uniqueid = service.originate(command())["asterisk_uniqueid"]
    seed_connected(lifecycle, uniqueid)
    directory = AgentDirectory([AgentBinding(extension="6901", tenant_id="tenant-test",
        business_unit_id="synthetic-unit", campaign_id="TEST_SYN", agent_id="appolon",
        keycloak_subject="subject-appolon")])
    event = parse_ami_block((f"Event: Hangup\r\nUniqueid: {uniqueid}\r\nLinkedid: {uniqueid}\r\n"
                            "Channel: PJSIP/6901-00000001\r\nCause: 16").encode())
    LifecycleStore(lifecycle).process(event, directory)
    readback = service.readback(command().operation_id)
    assert readback["terminal"]
    assert readback["evidence"]["terminal_event_id"]
    assert readback["evidence"]["terminal_details"]["hangup_cause_code"] == 16
    with sqlite3.connect(lifecycle) as db:
        db.execute("DELETE FROM event_outbox")
    with pytest.raises(InternalCallError, match="terminal"):
        service.readback(command().operation_id)


def test_cross_tenant_gateway_evidence_is_rejected(tmp_path):
    service, _, lifecycle = executor(tmp_path)
    uniqueid = service.originate(command())["asterisk_uniqueid"]
    seed_connected(lifecycle, uniqueid)
    with sqlite3.connect(lifecycle) as db:
        binding = json.loads(db.execute("SELECT binding_json FROM calls").fetchone()[0])
        binding["tenant_id"] = "another-tenant"
        db.execute("UPDATE calls SET binding_json=?", (json.dumps(binding),))
    with pytest.raises(PermissionError):
        service.readback(command().operation_id)


def route_rows():
    return [{"Context": "codestra-internal-test", "Extension": "6000", "Priority": str(i),
             "Application": app, "AppData": ""} for i, app in enumerate(("Answer", "Echo", "Hangup"), 1)]


def msg(event, **fields):
    return parse_ami_block((f"Event: {event}\r\n" + "\r\n".join(f"{k}: {v}" for k, v in fields.items())).encode())


@pytest.mark.parametrize("change", [{"Application": "Dial"}, {"AppData": "PJSIP/trunk/123"},
    {"Extension": "_X."}, {"Context": "default"}, {"IncludeContext": "from-trunk"}, {"Priority": "9"}])
def test_live_dialplan_drift_is_denied(change):
    transport = SocketAmiTransport(Path("/unused"))
    rows = route_rows()
    approved = policy().model_copy(update={"route_sha256": digest(rows)})
    transport._request = lambda *args: [msg("ListDialplan", **r) for r in rows]
    transport.verify_route(approved)
    rows[1].update(change)
    with pytest.raises(CallDenied):
        transport.verify_route(approved)


@pytest.mark.parametrize("channel,uniqueid", [("PJSIP/6902-0001", "correct"),
    ("PJSIP/6901-0001", "wrong"), ("/PJSIP/.*/", "correct")])
def test_live_hangup_status_must_match_exact_agent_and_uniqueid(channel, uniqueid):
    transport = SocketAmiTransport(Path("/unused"))
    uid = "codestra-" + "a" * 32
    actions = []
    def perform(action, fields, complete=None):
        actions.append(action)
        return [msg("Status", Channel=channel, Uniqueid=uid if uniqueid == "correct" else "other")]
    transport._request = perform
    with pytest.raises(CallDenied):
        transport.action({"Action": "Hangup", "Channel": uid})
    assert actions == ["Status"]


def test_hangup_uses_uniqueid_after_live_status():
    transport = SocketAmiTransport(Path("/unused"))
    uid = "codestra-" + "a" * 32
    actions = []
    def perform(action, fields, complete=None):
        actions.append((action, fields))
        return [msg("Status", Channel="PJSIP/6901-0001", Uniqueid=uid)] if action == "Status" else []
    transport._request = perform
    transport.action({"Action": "Hangup", "Channel": uid})
    assert actions == [("Status", {"Channel": uid}), ("Hangup", {"Channel": uid})]


def test_legacy_unbound_intent_store_cannot_be_silently_adopted(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE internal_calls(operation_id TEXT PRIMARY KEY)")
    with pytest.raises(InternalCallError, match="legacy"):
        InternalCallExecutor(policy(), path, tmp_path / "events.sqlite3", object())


@pytest.mark.parametrize("profile", ["production-candidate", "staging-read-only", "production-reconciliation"])
def test_readiness_rejects_internal_calling_in_ordinary_profiles(monkeypatch, profile):
    from codestra_vicidial.production_app import _kill_switch_ready
    monkeypatch.setenv("ENABLE_INTERNAL_TEST_CALLING", "true")
    assert not _kill_switch_ready(profile, True)


def test_dedicated_profile_does_not_enable_other_capabilities(monkeypatch):
    from codestra_vicidial.production_app import _kill_switch_ready
    monkeypatch.setenv("ENABLE_INTERNAL_TEST_CALLING", "true")
    assert _kill_switch_ready("production-internal-test", False)
    monkeypatch.setenv("ENABLE_LIVE_CALL_CONTROL", "true")
    assert not _kill_switch_ready("production-internal-test", False)
