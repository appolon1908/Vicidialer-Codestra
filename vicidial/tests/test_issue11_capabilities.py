from __future__ import annotations

from codestra_vicidial.app import create_app
from codestra_vicidial.security import RequestAuthenticator
from fastapi.testclient import TestClient
from test_adapter import harness


def test_capabilities_are_secret_free_and_fail_closed(harness):
    service, _ = harness
    auth = RequestAuthenticator("codestra-middleware", b"x" * 32, frozenset({"127.0.0.1"}))
    client = TestClient(create_app(service, auth))
    payload = client.get("/capabilities")
    assert payload.status_code == 200
    body = payload.json()
    assert body["schema_version"] == "vicidial-capabilities.v1"
    assert body["external_effects"] == {
        "production_dialing": False,
        "telephony_admin_write": False,
        "provider_credentials_exposed": False,
    }
    assert all("secret" not in str(route).lower() for route in body["routes"])
    assert {route["path"] for route in body["routes"]} >= {
        "/v1/campaigns/provision-disabled",
        "/v1/calls/originate",
        "/v1/callbacks/create",
    }
