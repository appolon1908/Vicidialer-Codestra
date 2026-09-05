"""Regression evidence for the exact PR26 authorization and concurrency findings."""

import json
import os
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
    results = race(
        lambda: service.originate(command()),
        lambda: service.originate(command(operation_id="second-operation-0001")),
    )
    assert results.count("denied") == 1
    assert len(transport.actions) == 1
    with pytest.raises(PermissionError, match="consumed"):
        service.originate(command(operation_id="third-operation-0001"))


def test_same_call_hangup_claim_is_atomic(tmp_path):
    service, transport, lifecycle = executor(tmp_path)
    accepted = service.originate(command())
    seed_connected(lifecycle, accepted["asterisk_uniqueid"])
    results = race(
        lambda: service.hangup(command().operation_id),
        lambda: service.hangup(command().operation_id),
    )
    assert len([r for r in results if r.get("duplicate")]) == 1
    assert [a["Action"] for a in transport.actions] == ["Originate", "Hangup"]


def test_durable_audit_records_actor_denial_success_and_uncertain(tmp_path):
    service, transport, _ = executor(tmp_path)
    with pytest.raises(PermissionError):
        service.originate(
            command(subject="other-subject"), actor="authenticated-middleware"
        )
    transport.action = lambda _: (_ for _ in ()).throw(TimeoutError("synthetic-only"))
    with pytest.raises(InternalCallError, match="reconciliation"):
        service.originate(command(), actor="authenticated-middleware")
    assert service.originate(command(), actor="authenticated-middleware")["duplicate"]
    with sqlite3.connect(service.audit_path) as db:
        rows = db.execute(
            "SELECT actor,outcome,correlation_id FROM audit ORDER BY sequence"
        ).fetchall()
    assert {r[1] for r in rows} >= {
        "requested",
        "denied",
        "failed_or_uncertain",
        "duplicate",
    }
    assert {r[0] for r in rows} == {"authenticated-middleware"}
    assert {r[2] for r in rows} == {command().correlation_id}


def test_rebound_start_policy_does_not_rebind_existing_call(tmp_path):
    service, transport, lifecycle = executor(tmp_path)
    accepted = service.originate(command())
    seed_connected(lifecycle, accepted["asterisk_uniqueid"])
    service.policy_provider = lambda: policy().model_copy(
        update={"tenant_id": "different-tenant"}
    )
    assert service.readback(command().operation_id)["tenant_id"] == "tenant-test"
    assert service.hangup(command().operation_id)["hangup"] == "requested"
    assert len(transport.actions) == 2


def test_expired_start_policy_denies_new_start_but_preserves_read_and_end(tmp_path):
    service, transport, lifecycle = executor(tmp_path)
    accepted = service.originate(command())
    seed_connected(lifecycle, accepted["asterisk_uniqueid"])
    service.policy_provider = lambda: service.policy.model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    with pytest.raises(PermissionError):
        service.originate(command())
    assert service.readback(command().operation_id)["call_state"] == "connected"
    assert service.hangup(command().operation_id)["hangup"] == "requested"
    assert [item["Action"] for item in transport.actions] == ["Originate", "Hangup"]


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
    directory = AgentDirectory(
        [
            AgentBinding(
                extension="6901",
                tenant_id="tenant-test",
                business_unit_id="synthetic-unit",
                campaign_id="TEST_SYN",
                agent_id="appolon",
                keycloak_subject="subject-appolon",
            )
        ]
    )
    event = parse_ami_block(
        (
            f"Event: Hangup\r\nUniqueid: {uniqueid}\r\nLinkedid: {uniqueid}\r\n"
            "Channel: PJSIP/6901-00000001\r\nCause: 16"
        ).encode()
    )
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
    return [
        {
            "Context": "codestra-internal-test",
            "Extension": "6000",
            "Priority": str(i),
            "Application": app,
            "AppData": "",
        }
        for i, app in enumerate(("Answer", "Echo", "Hangup"), 1)
    ]


def msg(event, **fields):
    return parse_ami_block(
        (
            f"Event: {event}\r\n" + "\r\n".join(f"{k}: {v}" for k, v in fields.items())
        ).encode()
    )


@pytest.mark.parametrize(
    "change",
    [
        {"Application": "Dial"},
        {"AppData": "PJSIP/trunk/123"},
        {"Extension": "_X."},
        {"Context": "default"},
        {"IncludeContext": "from-trunk"},
        {"Priority": "9"},
    ],
)
def test_live_dialplan_drift_is_denied(change):
    transport = SocketAmiTransport(Path("/unused"))
    rows = route_rows()
    approved = policy().model_copy(update={"route_sha256": digest(rows)})
    transport._request = lambda *args: [msg("ListDialplan", **r) for r in rows]
    transport.verify_route(approved)
    rows[1].update(change)
    with pytest.raises(CallDenied):
        transport.verify_route(approved)


@pytest.mark.parametrize(
    "channel,uniqueid",
    [
        ("PJSIP/6902-0001", "correct"),
        ("PJSIP/6901-0001", "wrong"),
        ("/PJSIP/.*/", "correct"),
    ],
)
def test_live_hangup_status_must_match_exact_agent_and_uniqueid(channel, uniqueid):
    transport = SocketAmiTransport(Path("/unused"))
    uid = "codestra-" + "a" * 32
    actions = []

    def perform(action, fields, complete=None):
        actions.append(action)
        return [
            msg(
                "Status",
                Channel=channel,
                Uniqueid=uid if uniqueid == "correct" else "other",
            )
        ]

    transport._request = perform
    with pytest.raises(CallDenied):
        transport.action({
            "Action": "Hangup", "Channel": "PJSIP/6901-0001",
            "ExpectedUniqueid": uid,
        })
    assert actions == ["Status"]


def test_hangup_uses_uniqueid_after_live_status():
    transport = SocketAmiTransport(Path("/unused"))
    uid = "codestra-" + "a" * 32
    actions = []

    def perform(action, fields, complete=None):
        actions.append((action, fields))
        return (
            [msg("Status", Channel="PJSIP/6901-0001", Uniqueid=uid)]
            if action == "Status"
            else []
        )

    transport._request = perform
    transport.action({
        "Action": "Hangup", "Channel": "PJSIP/6901-0001",
        "ExpectedUniqueid": uid,
    })
    assert actions == [
        ("Status", {"Channel": "PJSIP/6901-0001"}),
        ("Hangup", {"Channel": "PJSIP/6901-0001"}),
    ]


def test_legacy_unbound_intent_store_cannot_be_silently_adopted(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE internal_calls(operation_id TEXT PRIMARY KEY)")
    with pytest.raises(InternalCallError, match="legacy"):
        InternalCallExecutor(policy(), path, tmp_path / "events.sqlite3", object())


@pytest.mark.parametrize(
    "profile",
    ["production-candidate", "staging-read-only", "production-reconciliation"],
)
def test_readiness_rejects_internal_calling_in_ordinary_profiles(monkeypatch, profile):
    from codestra_vicidial.production_app import _kill_switch_ready

    monkeypatch.setenv("ENABLE_INTERNAL_TEST_CALLING", "true")
    assert not _kill_switch_ready(profile, True)


def test_dedicated_profile_does_not_enable_other_capabilities(monkeypatch):
    from codestra_vicidial.production_app import _kill_switch_ready

    monkeypatch.setenv("ENABLE_INTERNAL_TEST_CALLING", "true")
    from codestra_vicidial.production_app import app

    monkeypatch.setattr(app.state, "internal_call_executor", object(), raising=False)
    assert _kill_switch_ready("production-internal-test", False)
    monkeypatch.setenv("ENABLE_LIVE_CALL_CONTROL", "true")
    assert not _kill_switch_ready("production-internal-test", False)


def test_http_contract_keeps_v2_scopes_and_audits_invalid_payload(tmp_path):
    import time
    from uuid import uuid4

    from codestra_vicidial.app import create_app
    from codestra_vicidial.repository import MemoryRepository
    from codestra_vicidial.security import RequestAuthenticator
    from codestra_vicidial.service import AdapterService, FeatureFlags
    from codestra_vicidial.state import StateStore
    from fastapi.testclient import TestClient

    executor_service, transport, _ = executor(tmp_path)
    auth = RequestAuthenticator(
        "codestra-middleware",
        b"synthetic-test-key-with-32-bytes!!",
        frozenset({"127.0.0.1"}),
    )
    service = AdapterService(
        MemoryRepository(), StateStore(tmp_path / "http.sqlite3"), FeatureFlags()
    )
    client = TestClient(
        create_app(service, auth, internal_call_executor=executor_service),
        client=("127.0.0.1", 50000),
    )
    path = "/v1/calls/internal/originate"

    def send(scope, changes=None, signature_path=None):
        body = json.dumps(command().model_dump(mode="json") | (changes or {})).encode()
        stamp, nonce, key = int(time.time()), str(uuid4()), command().operation_id
        signature = auth.sign(
            stamp,
            nonce,
            body,
            method="POST",
            path=signature_path or path,
            scopes={scope},
            idempotency_key=key,
        )
        return client.post(
            path,
            content=body,
            headers={
                "Idempotency-Key": key,
                "X-Service-Identity": "codestra-middleware",
                "X-Service-Scopes": scope,
                "X-Request-Timestamp": str(stamp),
                "X-Request-Nonce": nonce,
                "X-Request-Signature": signature,
                "X-Signature-Version": "v2",
            },
        )

    assert send("telephony:read").status_code == 403
    assert (
        send("telephony:internal-call", signature_path=path + "/hangup").status_code
        == 403
    )
    assert (
        send("telephony:internal-call", {"destination": "PJSIP/trunk/123"}).status_code
        == 422
    )
    assert not transport.actions
    assert send("telephony:internal-call").status_code == 200
    assert len(transport.actions) == 1
    with sqlite3.connect(executor_service.audit_path) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM audit WHERE outcome='invalid_request'"
            ).fetchone()[0]
            == 1
        )


def test_unknown_hangup_preserves_not_found_semantics(tmp_path):
    service, transport, _ = executor(tmp_path)
    with pytest.raises(KeyError):
        service.hangup("missing-operation")
    assert transport.actions == []
    with sqlite3.connect(service.audit_path) as db:
        assert db.execute(
            "SELECT outcome FROM audit WHERE operation='internal-call.hangup' "
            "ORDER BY sequence"
        ).fetchall() == [("requested",), ("not_found",)]


def test_hangup_readback_race_preserves_not_found_semantics(tmp_path, monkeypatch):
    service, transport, _ = executor(tmp_path)
    service.originate(command())
    before = list(transport.actions)

    def disappeared(_operation_id):
        raise KeyError("operation disappeared before mutation")

    monkeypatch.setattr(service, "_hangup", disappeared)
    with pytest.raises(KeyError):
        service.hangup(command().operation_id)
    assert transport.actions == before
    with sqlite3.connect(service.audit_path) as db:
        assert db.execute(
            "SELECT outcome FROM audit WHERE operation='internal-call.hangup' "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()[0] == "not_found"


@pytest.mark.parametrize("field,value", [
    ("binding_json", "not-json"),
    ("binding_json", "[]"),
])
def test_malformed_lifecycle_binding_requires_reconciliation(tmp_path, field, value):
    service, _, lifecycle = executor(tmp_path)
    accepted = service.originate(command())
    seed_connected(lifecycle, accepted["asterisk_uniqueid"])
    with sqlite3.connect(lifecycle) as db:
        db.execute(f"UPDATE calls SET {field}=?", (value,))
    with pytest.raises(InternalCallError, match="evidence store unavailable"):
        service.readback(command().operation_id)


def test_socket_transport_matches_action_id_and_never_opens_remote_ami(monkeypatch):
    import codestra_vicidial.internal_call_transport as module

    class FakeConnection:
        def __init__(self):
            self.pending = b"Asterisk Call Manager/5.0\r\n"
            self.actions = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def settimeout(self, timeout):
            assert timeout == 3

        def recv(self, size):
            result, self.pending = self.pending[:size], self.pending[size:]
            return result

        def sendall(self, data):
            values = dict(
                line.split(": ", 1) for line in data.decode().strip().split("\r\n")
            )
            self.actions.append(values["Action"])
            if values["Action"] == "Login":
                self.pending += b"Response: Success\r\n\r\n"
            else:
                self.pending += (
                    "Response: Error\r\nActionID: unrelated\r\n\r\n"
                    f"Response: Success\r\nActionID: {values['ActionID']}\r\n\r\n"
                ).encode()

    connection = FakeConnection()
    monkeypatch.setattr(
        module,
        "load_protected_json",
        lambda _: {"username": "synthetic", "secret": "synthetic-only"},
    )

    def connect(address, timeout):
        assert address == ("127.0.0.1", 5038)
        return connection

    monkeypatch.setattr(module.socket, "create_connection", connect)
    SocketAmiTransport(Path("/unused")).action(
        {"Action": "Originate", "Channel": "PJSIP/6901"}
    )
    assert connection.actions == ["Login", "Originate"]
    monkeypatch.setattr(
        module, "load_protected_json", lambda _: {"host": "203.0.113.1"}
    )
    with pytest.raises(CallDenied):
        SocketAmiTransport(Path("/unused")).action({"Action": "Originate"})


@pytest.mark.parametrize("mode,uid,gid", [
    (0o100644, 0, 0), (0o100660, 0, 1000), (0o100600, 1000, 2000),
])
def test_protected_policy_permissions_fail_closed(tmp_path, monkeypatch, mode, uid, gid):
    from types import SimpleNamespace

    import codestra_vicidial.internal_call_transport as module

    path = tmp_path / "policy.json"
    path.write_text(policy().model_dump_json())
    monkeypatch.setattr(
        module.os, "fstat", lambda fd: SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid)
    )
    with pytest.raises(CallDenied):
        module.load_protected_json(path)


def test_root_owned_group_readonly_policy_is_service_readable(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import codestra_vicidial.internal_call_transport as module

    path = tmp_path / "policy.json"
    path.write_text('{"scope":"synthetic"}')
    monkeypatch.setattr(
        module.os, "fstat", lambda fd: SimpleNamespace(
            st_mode=0o100440, st_uid=0, st_gid=os.getegid(),
        ),
    )
    assert module.load_protected_json(path) == {"scope": "synthetic"}


def test_protected_policy_rejects_duplicate_keys_and_symlinks(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import codestra_vicidial.internal_call_transport as module

    path = tmp_path / "policy.json"
    path.write_text('{"max_calls":1,"max_calls":10}')
    monkeypatch.setattr(
        module.os, "fstat", lambda fd: SimpleNamespace(
            st_mode=0o100600, st_uid=os.geteuid(), st_gid=os.getegid(),
        )
    )
    with pytest.raises(CallDenied, match="duplicate"):
        module.load_protected_json(path)
    link = tmp_path / "policy-link.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        module.load_protected_json(link)


@pytest.mark.parametrize("target", ["entrypoint", "validator"])
def test_startup_entrypoints_reject_ordinary_profile_call_enablement(tmp_path, target):
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "CODESTRA_ADAPTER_DEPLOYMENT": "production-candidate",
        "ENABLE_INTERNAL_TEST_CALLING": "true",
        "CODESTRA_ADAPTER_BACKEND": "memory",
        "CODESTRA_RELEASE_METADATA_REQUIRED": "false",
    }
    args = (
        ["sh", str(root / "docker/entrypoint.sh"), "true"]
        if target == "entrypoint"
        else [sys.executable, str(root / "scripts/validate_runtime.py")]
    )
    result = subprocess.run(
        args, env=environment, capture_output=True, text=True, timeout=10, check=False
    )
    assert result.returncode != 0
    if target == "validator":
        assert "ENABLE_INTERNAL_TEST_CALLING must remain false" in result.stderr
