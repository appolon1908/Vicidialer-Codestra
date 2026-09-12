"""API-layer coverage for the Mission 4 delivery endpoints
(GET /v1/webrtc/pjsip-config/pending, POST /v1/webrtc/pjsip-config/ack) --
end-to-end through FastAPI's TestClient with real HMAC-signed requests,
same harness convention as test_command_lookup.py.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from codestra_vicidial.app import create_app
from codestra_vicidial.credential_authority import IssuedCredential
from codestra_vicidial.models import (
    AgentCommand,
    ExtensionReserveCommand,
    WebrtcProvisionCommand,
)
from codestra_vicidial.repository import MemoryRepository
from codestra_vicidial.security import RequestAuthenticator
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore

PENDING_PATH = "/v1/webrtc/pjsip-config/pending"
ACK_PATH = "/v1/webrtc/pjsip-config/ack"


class SequencedAuthority:
    def __init__(self) -> None:
        self._count = 0

    def issue(self, **values) -> IssuedCredential:
        self._count += 1
        return IssuedCredential(f"synthetic-credential-{self._count}", f"ref-{self._count}")


def context(*, expected_version: int = 0) -> dict:
    return {
        "correlation_id": "corr-12345678", "actor": "middleware-test",
        "tenant_id": "tenant-cod", "business_unit": "COD",
        "campaign_id": "CODT0001", "supervisor_subject": "supervisor-cod",
        "expected_version": expected_version,
        "reason": "synthetic delivery api test",
        "requested_at": "2026-09-12T00:00:00Z",
    }


@pytest.fixture
def api(tmp_path):
    repo = MemoryRepository()
    state = StateStore(tmp_path / "state.sqlite3")
    state.put_campaign_authority("CODT0001", "tenant-cod", "COD", "supervisor-cod")
    service = AdapterService(
        repo, state,
        FeatureFlags(
            writes=True, provisioning=True, agent_sync=True,
            extension_provisioning=True, webrtc_provisioning=True,
        ),
        credential_authority=SequencedAuthority(),
    )
    auth = RequestAuthenticator("middleware-test", b"synthetic-test-key", frozenset({"127.0.0.1"}))
    with TestClient(create_app(service, auth), client=("127.0.0.1", 50000)) as client:
        yield client, auth, service
    service.state.db.close()


def signed_get(api, path, *, scope="telephony:read"):
    client, auth, _service = api
    timestamp, nonce = int(time.time()), uuid.uuid4().hex
    signature = auth.sign(timestamp, nonce, b"", method="GET", path=path, scopes={scope})
    headers = {
        "X-Service-Identity": auth.identity, "X-Service-Scopes": scope,
        "X-Request-Timestamp": str(timestamp), "X-Request-Nonce": nonce,
        "X-Request-Signature": signature, "X-Signature-Version": "v2",
    }
    return client.get(path, headers=headers)


def signed_post(api, path, payload, *, scope, key):
    client, auth, _service = api
    body = json.dumps(payload).encode()
    timestamp, nonce = int(time.time()), uuid.uuid4().hex
    signature = auth.sign(timestamp, nonce, body, method="POST", path=path,
                          scopes={scope}, idempotency_key=key)
    headers = {
        "Content-Type": "application/json",
        "X-Service-Identity": auth.identity, "X-Service-Scopes": scope,
        "X-Request-Timestamp": str(timestamp), "X-Request-Nonce": nonce,
        "X-Request-Signature": signature, "X-Signature-Version": "v2",
        "Idempotency-Key": key,
    }
    return client.post(path, content=body, headers=headers)


def provision(api, user_id="COD1001"):
    _client, _auth, service = api
    service.sync_agent(f"agent-sync-{user_id}", AgentCommand.model_validate({
        "context": context(),
        "agent": {"user_id": user_id, "full_name": "Synthetic Agent",
                  "user_group": "COD_TEST_SDR", "campaigns": ["CODT0001"],
                  "inbound_groups": [], "active": False},
    }))
    service.reserve_extension(f"reserve-{user_id}", ExtensionReserveCommand.model_validate({
        "context": context(), "reservation": {"user_id": user_id, "pool": "web_ai"},
    }))
    service.provision_webrtc(f"webrtc-{user_id}", WebrtcProvisionCommand.model_validate({
        "context": context(), "webrtc": {"user_id": user_id},
    }))


def test_pending_endpoint_lists_generated_artifact(api):
    provision(api)
    response = signed_get(api, PENDING_PATH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["pending"]) == 1
    assert body["pending"][0]["user_id"] == "COD1001"


def test_ack_endpoint_clears_pending_item(api):
    provision(api)
    pending = signed_get(api, PENDING_PATH).json()["pending"]
    content_hash = pending[0]["content_hash"]

    response = signed_post(
        api, ACK_PATH,
        {"context": context(expected_version=1), "user_id": "COD1001",
         "content_hash": content_hash},
        scope="telephony:webrtc-provision", key="ack-api-1",
    )
    assert response.status_code == 200, response.text

    remaining = signed_get(api, PENDING_PATH).json()["pending"]
    assert remaining == []


def test_ack_endpoint_rejects_stale_hash_with_422(api):
    provision(api)
    response = signed_post(
        api, ACK_PATH,
        {"context": context(expected_version=1), "user_id": "COD1001",
         "content_hash": "0" * 64},
        scope="telephony:webrtc-provision", key="ack-api-2",
    )
    assert response.status_code == 422


def test_pending_endpoint_rejects_wrong_scope(api):
    """telephony:read must not be usable to acquire a webrtc-provision
    scope's authority -- this is a plain scope-check regression guard."""
    response = signed_get(api, PENDING_PATH, scope="telephony:external-call")
    assert response.status_code == 403
