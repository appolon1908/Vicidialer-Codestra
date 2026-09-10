"""Exercise the shipped client against real adapter HTTP routes and durable state."""
from __future__ import annotations

import io
import json

import pytest
from codestra_vicidial.app import create_app
from codestra_vicidial.repository import MemoryRepository
from codestra_vicidial.security import RequestAuthenticator
from codestra_vicidial.service import AdapterService, FeatureFlags
from codestra_vicidial.state import StateStore
from fastapi.testclient import TestClient
from test_synthetic_campaign_lifecycle import request as design_request

from campaign_provisioning import (
    CampaignDesignService,
    LifecycleStore,
    PrivateAdapterClient,
    SQLiteDesignRepository,
    run_synthetic_lifecycle,
)


@pytest.fixture
def signed_adapter(tmp_path, monkeypatch):
    state = StateStore(tmp_path / "adapter.sqlite3")
    repository = MemoryRepository()
    service = AdapterService(repository, state, FeatureFlags(
        writes=True, provisioning=True, agent_sync=True, lead_publication=True,
        allowed_lead_list_ids=frozenset({91000}), transfer_policy_sync=True,
    ))
    auth = RequestAuthenticator(
        "codestra-middleware", b"synthetic-client-contract-key",
        frozenset({"127.0.0.1"}),
    )
    connections = []
    requests = []
    with TestClient(create_app(service, auth), client=("127.0.0.1", 55000)) as api:
        class Connection:
            def __init__(self, *_args, **_kwargs):
                self.closed = False
                connections.append(self)

            def request(self, method, path, *, body, headers):
                requests.append((method, path, json.loads(body) if body else None, headers))
                assert path.startswith("/restricted-vicidial/")
                response = api.request(
                    method, path.removeprefix("/restricted-vicidial"),
                    content=body, headers=headers,
                )
                self.response = io.BytesIO(response.content)
                self.response.status = response.status_code

            def getresponse(self):
                return self.response

            def close(self):
                self.closed = True

        monkeypatch.setattr("campaign_provisioning.lifecycle.http.client.HTTPSConnection", Connection)
        client = PrivateAdapterClient.__new__(PrivateAdapterClient)
        client.host = "edge.internal.codestra.agency"
        client.port = 8443
        client.prefix = "/restricted-vicidial"
        client.identity = auth.identity
        client.key = b"synthetic-client-contract-key"
        client.context = None  # TLS is the simulated boundary; HTTP auth is real.
        yield client, service, requests, connections, Connection
    state.db.close()


def test_shipped_synthetic_lifecycle_uses_the_current_signed_http_contract(tmp_path, signed_adapter):
    client, service, requests, connections, _connection_class = signed_adapter
    result = run_synthetic_lifecycle(
        CampaignDesignService(SQLiteDesignRepository(str(tmp_path / "design.sqlite3"))),
        LifecycleStore(str(tmp_path / "lifecycle.sqlite3")),
        client, design_request(), "synthetic-real-http-contract",
        tenant_id="tenant-synthetic", supervisor_subject="supervisor-synthetic",
    )
    assert result["status"] == "PASS"
    assert result["production_dialing"] is False
    assert len(service.repository.campaigns) == 1
    assert len(service.repository.agents) == 1
    assert len(service.repository.leads) == 1
    assert all(connection.closed for connection in connections)
    assert requests


def lifecycle(tmp_path, client, *, store=None, request=None):
    return run_synthetic_lifecycle(
        CampaignDesignService(SQLiteDesignRepository(str(tmp_path / "design.sqlite3"))),
        store or LifecycleStore(str(tmp_path / "lifecycle.sqlite3")),
        client, request or design_request(), "synthetic-real-http-contract",
        tenant_id="tenant-synthetic", supervisor_subject="supervisor-synthetic",
    )


def command_for(tmp_path):
    from campaign_provisioning.lifecycle import campaign_command
    manifest = CampaignDesignService(
        SQLiteDesignRepository(str(tmp_path / "design.sqlite3"))
    ).preview(design_request())
    return campaign_command(
        manifest, "synthetic-receipt-recovery",
        tenant_id="tenant-synthetic", supervisor_subject="supervisor-synthetic",
    )


def test_receipts_recover_original_versions_after_disable_without_writes(tmp_path, signed_adapter):
    client, service, requests, _connections, _connection_class = signed_adapter
    result = lifecycle(tmp_path, client)
    assert result["campaign_resource_version"] == 2
    assert result["agent_resource_version"] == 1
    assert result["downstream_projections"] == "local_staging_ledger_only"
    original = next(item for item in requests if item[1].endswith("/campaigns/provision-disabled"))
    command, headers = original[2], original[3]
    service.flags = FeatureFlags()
    versions = list(service.state.db.execute("SELECT * FROM resource_versions"))
    receipts = [
        client.lookup_command(
            "campaign.provision-disabled", headers["Idempotency-Key"], command
        ) for _ in range(2)
    ]
    assert receipts[0] == receipts[1]
    assert receipts[0]["result"]["resource_version"] == 1
    assert list(service.state.db.execute("SELECT * FROM resource_versions")) == versions
    lookup_requests = requests[-2:]
    assert all(item[1].endswith("/idempotency/lookup") for item in lookup_requests)
    assert all("Idempotency-Key" not in item[3] for item in lookup_requests)
    assert all(item[3]["X-Service-Scopes"] == "telephony:read" for item in lookup_requests)
    assert lookup_requests[0][3]["X-Request-Nonce"] != lookup_requests[1][3]["X-Request-Nonce"]


@pytest.mark.parametrize(("mode", "expected_status"), [
    ("missing", 404), ("changed", 409), ("unauthorized", 403),
])
def test_inconclusive_or_denied_lookup_never_resubmits_a_mutation(
    tmp_path, signed_adapter, mode, expected_status
):
    from copy import deepcopy

    from campaign_provisioning import AdapterRequestError
    client, service, requests, _connections, _connection_class = signed_adapter
    command = command_for(tmp_path)
    key = "receipt-original-command"
    client.provision_campaign(key, command)
    service.flags = FeatureFlags()
    query = deepcopy(command)
    if mode == "changed":
        query["campaign"]["name"] = "Different synthetic name"
    if mode == "unauthorized":
        query["context"]["tenant_id"] = "other-tenant"
    if mode == "missing":
        key = "unknown-receipt-command"
    before = len(requests)
    with pytest.raises(AdapterRequestError) as error:
        client.lookup_command("campaign.provision-disabled", key, query)
    assert error.value.status_code == expected_status
    assert len(requests) == before + 1
    assert requests[-1][1].endswith("/idempotency/lookup")
    assert len(service.repository.campaigns) == 1


def test_lost_mutation_response_closes_connection_and_explicit_lookup_recovers(
    tmp_path, signed_adapter, monkeypatch
):
    from campaign_provisioning import AdapterRequestError
    client, service, requests, connections, connection_class = signed_adapter
    store = LifecycleStore(str(tmp_path / "intents.sqlite3"))
    request = design_request()
    key = f"staging:{request.integration_uuid}:1:provision-disabled"
    original_getresponse = connection_class.getresponse

    def lost_response(_self):
        raise TimeoutError("private-response-material-must-not-leak")

    monkeypatch.setattr(connection_class, "getresponse", lost_response)
    with pytest.raises(AdapterRequestError) as error:
        lifecycle(tmp_path, client, store=store, request=request)
    assert "private-response-material" not in str(error.value)
    assert error.value.status_code is None
    assert len(requests) == 1
    assert connections[-1].closed is True
    monkeypatch.setattr(connection_class, "getresponse", original_getresponse)
    service.flags = FeatureFlags()
    restarted = LifecycleStore(store.path)
    command = restarted.original_command("campaign.provision-disabled", key)
    assert command == requests[0][2]
    receipt = client.lookup_command("campaign.provision-disabled", key, command)
    assert receipt["result"]["resource_version"] == 1
    assert len(service.repository.campaigns) == 1
    assert len(requests) == 2
    assert requests[-1][1].endswith("/idempotency/lookup")


def test_existing_synthetic_run_blocks_before_new_remote_commands(tmp_path, signed_adapter):
    from campaign_provisioning import LifecycleError
    client, _service, requests, _connections, _connection_class = signed_adapter
    store = LifecycleStore(str(tmp_path / "lifecycle.sqlite3"))
    request = design_request()
    lifecycle(tmp_path, client, store=store, request=request)
    before = len(requests)
    with pytest.raises(LifecycleError, match="requires receipt recovery"):
        lifecycle(tmp_path, client, store=store, request=request)
    assert len(requests) == before


def test_agent_readback_mismatch_prevents_success_projection(tmp_path, signed_adapter, monkeypatch):
    from campaign_provisioning import LifecycleError
    client, _service, _requests, _connections, _connection_class = signed_adapter
    original = client.agent_actual
    monkeypatch.setattr(
        client, "agent_actual",
        lambda user: {**original(user), "campaigns": ["OTHER001"]},
    )
    store = LifecycleStore(str(tmp_path / "lifecycle.sqlite3"))
    with pytest.raises(LifecycleError, match="campaign isolation"):
        lifecycle(tmp_path, client, store=store)
    assert store.counts()["odoo_projection"] == 0
    assert store.counts()["n8n_scope"] == 0


@pytest.mark.parametrize("change", [
    {"status": "unknown"}, {"operation": "agent.sync"},
    {"idempotency_key": "different-key"}, {"recorded_at": "not-a-time"},
    {"recorded_at": "2026-09-10T17:00:00"},
    {"result": {"resource_version": 0}},
    {"result": {"resource_version": True}},
    {"result": {"resource_version": "1"}}, {"result": None},
])
def test_lookup_rejects_unbound_or_malformed_receipts(signed_adapter, monkeypatch, change):
    from campaign_provisioning import AdapterRequestError
    client, *_rest = signed_adapter
    document = {
        "status": "recorded", "operation": "campaign.disable",
        "idempotency_key": "original-receipt-key",
        "recorded_at": "2026-09-10T17:00:00Z",
        "result": {"resource_version": 1},
        **change,
    }
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: document)
    with pytest.raises(AdapterRequestError):
        client.lookup_command("campaign.disable", "original-receipt-key", {})


@pytest.mark.parametrize(("status", "body"), [
    (403, b'{"detail":"private-provider-material"}'),
    (503, b"<html>private-provider-material</html>"),
    (200, b'{"private-provider-material":'),
    (200, b"[]"), (200, b"null"), (200, b"x" * 65537),
])
def test_transport_rejects_errors_and_invalid_json_without_leaking_body(
    signed_adapter, monkeypatch, status, body
):
    from campaign_provisioning import AdapterRequestError
    client, _service, _requests, connections, connection_class = signed_adapter
    def response(_self):
        data = io.BytesIO(body)
        data.status = status
        return data
    monkeypatch.setattr(connection_class, "getresponse", response)
    with pytest.raises(AdapterRequestError) as error:
        client.lookup_command("campaign.disable", "original-receipt-key", {})
    assert "private-provider-material" not in str(error.value)
    assert connections[-1].closed is True


@pytest.mark.parametrize("operation", [
    "call.originate", "reconciliation.run", "shell.execute", "campaign.activate",
])
def test_unsupported_lookup_is_rejected_before_transport(signed_adapter, operation):
    from campaign_provisioning import LifecycleError
    client, _service, requests, _connections, _connection_class = signed_adapter
    with pytest.raises(LifecycleError, match="unsupported"):
        client.lookup_command(operation, "original-receipt-key", {})
    assert requests == []


def test_command_intent_conflict_preserves_original_for_recovery(tmp_path):
    from campaign_provisioning import LifecycleError
    store = LifecycleStore(str(tmp_path / "intents.sqlite3"))
    original = {"context": {"requested_at": "2026-09-10T17:00:00Z"}, "synthetic": True}
    store.remember_command("campaign.disable", "intent-original-key", original)
    with pytest.raises(LifecycleError, match="key/body conflict"):
        store.remember_command("campaign.disable", "intent-original-key", {"synthetic": False})
    restarted = LifecycleStore(store.path)
    assert restarted.original_command("campaign.disable", "intent-original-key") == original


def test_corrupt_original_command_is_not_used_for_recovery(tmp_path):
    import sqlite3

    from campaign_provisioning import LifecycleError
    store = LifecycleStore(str(tmp_path / "intents.sqlite3"))
    store.remember_command("campaign.disable", "intent-original-key", {"synthetic": True})
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE command_intent SET request_json=?", ('{"synthetic": false}',))
    with pytest.raises(LifecycleError, match="integrity"):
        store.original_command("campaign.disable", "intent-original-key")


def test_concurrent_campaign_version_change_blocks_disable(tmp_path, signed_adapter, monkeypatch):
    from campaign_provisioning import AdapterRequestError
    client, _service, _requests, _connections, _connection_class = signed_adapter
    original = client.disable_campaign
    def raced_disable(key, command):
        original("independent-synthetic-disable", command)
        return original(key, command)
    monkeypatch.setattr(client, "disable_campaign", raced_disable)
    with pytest.raises(AdapterRequestError) as error:
        lifecycle(tmp_path, client)
    assert error.value.status_code == 409
