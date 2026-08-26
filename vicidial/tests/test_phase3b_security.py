from __future__ import annotations

import pytest
import time
from fastapi.testclient import TestClient

from codestra_vicidial.app import create_app
from codestra_vicidial.repository import MemoryRepository
from codestra_vicidial.security import RequestAuthenticator
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore
from campaign_provisioning.lifecycle import PrivateAdapterClient
from test_adapter import campaign_payload


KEY = b"phase3b-signature-key-32-bytes!!"
IDENTITY = "codestra-middleware"
SCOPES = {"telephony:provision", "telephony:read"}
NOW = 1_800_000_000
NONCE = "phase3b-nonce-00000001"
BODY = b'{"synthetic":true}'
METHOD = "POST"
PATH = "/v1/campaigns/validate"
IDEMPOTENCY_KEY = "test-key"


def authenticator() -> RequestAuthenticator:
    return RequestAuthenticator(IDENTITY, KEY, frozenset({"127.0.0.1"}))


def signed(auth: RequestAuthenticator) -> str:
    return auth.sign(
        NOW, NONCE, BODY, method=METHOD, path=PATH, identity=IDENTITY,
        scopes=SCOPES, idempotency_key=IDEMPOTENCY_KEY,
    )


def verify(auth: RequestAuthenticator, signature: str, **changes) -> None:
    values = {
        "source": "127.0.0.1", "identity": IDENTITY, "scopes": SCOPES,
        "timestamp": NOW, "nonce": NONCE, "body": BODY,
        "signature": signature, "required_scope": "telephony:read",
        "method": METHOD, "path": PATH,
        "idempotency_key": IDEMPOTENCY_KEY, "signature_version": "v2",
        "now": NOW,
    }
    values.update(changes)
    auth.verify(**values)


def test_signature_v2_accepts_exact_request() -> None:
    auth = authenticator()
    verify(auth, signed(auth))


@pytest.mark.parametrize("method,path,body,key,scopes", [
    ("GET", "/v1/campaigns/TEST/actual-state", b"", "", {"telephony:read"}),
    ("POST", "/v1/campaigns/validate", BODY, IDEMPOTENCY_KEY,
     {"telephony:read", "telephony:provision"}),
])
def test_gateway_and_adapter_share_signature_v2_vectors(method, path, body, key, scopes):
    client = PrivateAdapterClient.__new__(PrivateAdapterClient)
    client.identity = IDENTITY
    client.key = KEY
    gateway = client._signature(method, path, scopes, NOW, NONCE, key, body)
    adapter = authenticator().sign(
        NOW, NONCE, body, method=method, path=path, identity=IDENTITY,
        scopes=scopes, idempotency_key=key,
    )
    assert gateway == adapter


@pytest.mark.parametrize("change", [
    {"method": "PUT"},
    {"path": "/v1/campaigns/provision-disabled"},
    {"identity": "another-service"},
    {"scopes": {"telephony:read", "telephony:reconcile"}},
    {"idempotency_key": "another-key"},
])
def test_signature_v2_rejects_substitution_and_cross_route_replay(change) -> None:
    auth = authenticator()
    with pytest.raises(PermissionError):
        verify(auth, signed(auth), **change)


def test_signature_v1_is_rejected() -> None:
    auth = authenticator()
    with pytest.raises(PermissionError, match="unsupported signature version"):
        verify(auth, "0" * 64, signature_version="v1")


@pytest.mark.parametrize("path", [
    "v1/campaigns/validate", "/v1//campaigns/validate",
    "/v1/../campaigns/validate", "/v1/campaigns/validate?x=1",
])
def test_noncanonical_paths_are_rejected(path: str) -> None:
    auth = authenticator()
    with pytest.raises(PermissionError, match="request path"):
        auth.sign(NOW, NONCE, BODY, method=METHOD, path=path, scopes=SCOPES)


def test_http_route_enforces_complete_v2_binding(tmp_path) -> None:
    auth = authenticator()
    service = AdapterService(
        MemoryRepository(), StateStore(tmp_path / "state.sqlite3"), FeatureFlags()
    )
    client = TestClient(create_app(service, auth), client=("127.0.0.1", 50000))
    body = __import__("json").dumps(
        campaign_payload(), separators=(",", ":")
    ).encode()
    nonce = "phase3b-http-nonce-0001"
    path = "/v1/campaigns/validate"
    scopes = {"telephony:read"}
    key = "phase3b-http-idempotency"
    current = int(time.time())
    signature = auth.sign(
        current, nonce, body, method="POST", path=path, scopes=scopes,
        idempotency_key=key,
    )
    headers = {
        "Content-Type": "application/json", "Idempotency-Key": key,
        "X-Service-Identity": IDENTITY, "X-Service-Scopes": "telephony:read",
        "X-Request-Timestamp": str(current), "X-Request-Nonce": nonce,
        "X-Request-Signature": signature, "X-Signature-Version": "v2",
    }
    assert client.post(path, content=body, headers=headers).status_code == 200
    changed = dict(headers)
    changed["X-Request-Nonce"] = "phase3b-http-nonce-0002"
    assert client.post(
        "/v1/campaigns/provision-disabled", content=body, headers=changed
    ).status_code == 403
