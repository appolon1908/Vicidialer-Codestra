"""Bounded synthetic automatic-campaign lifecycle orchestration.

This module is staging-only. It has no dialing or messaging implementation and
refuses production manifests. Runtime credentials are read from protected file
paths by the adapter client; secret values are never persisted in lifecycle
evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import re
import sqlite3
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .design import CampaignDesignRequest, CampaignDesignService, canonical_json


class LifecycleError(RuntimeError):
    """A synthetic lifecycle gate failed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AdapterRequestError(LifecycleError):
    """A redacted transport/HTTP failure; no automatic mutation retry."""

    def __init__(self, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(
            f"adapter request rejected with HTTP {status_code}"
            if status_code is not None else "adapter response unavailable; reconciliation required"
        )


LOOKUP_OPERATIONS = frozenset({
    "campaign.provision-disabled", "campaign.disable", "agent.sync",
    "agent.provision-disabled", "agent.disable", "lead.publish",
    "callback.create", "transfer-policy.sync",
})
RECEIPT_MAX_CLOCK_SKEW_SECONDS = 60


class Adapter(Protocol):
    def provision_campaign(self, key: str, command: dict[str, Any]) -> dict[str, Any]: ...
    def campaign_actual(self, campaign_id: str) -> dict[str, Any]: ...
    def sync_agent(self, key: str, command: dict[str, Any]) -> dict[str, Any]: ...
    def agent_actual(self, user_id: str) -> dict[str, Any]: ...
    def publish_lead(self, key: str, command: dict[str, Any]) -> dict[str, Any]: ...
    def sync_transfer_policy(self, key: str, command: dict[str, Any]) -> dict[str, Any]: ...
    def reconcile(self, key: str) -> dict[str, Any]: ...
    def disable_campaign(self, key: str, command: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PrivateAdapterConfig:
    base_url: str
    ca_file: str
    client_cert_file: str
    client_key_file: str
    hmac_file: str
    identity: str = "codestra-middleware"


class PrivateAdapterClient:
    """mTLS + signed-request client for the restricted private adapter."""

    def _signature(
        self, method: str, path: str, scopes: set[str], timestamp: int,
        nonce: str, idempotency_key: str, body: bytes,
    ) -> str:
        canonical = "\n".join((
            "v2", method.upper(), path, self.identity,
            " ".join(sorted(set(scopes))), str(timestamp), nonce,
            idempotency_key, hashlib.sha256(body).hexdigest(),
        ))
        return hmac.new(self.key, canonical.encode(), hashlib.sha256).hexdigest()

    def __init__(self, config: PrivateAdapterConfig):
        from urllib.parse import urlsplit

        parsed = urlsplit(config.base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "edge.internal.codestra.agency"
            or parsed.port != 8443
            or parsed.path.rstrip("/") != "/restricted-vicidial"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise LifecycleError("approved private adapter endpoint required")
        self.host = parsed.hostname
        self.port = parsed.port
        self.prefix = parsed.path.rstrip("/")
        self.identity = config.identity
        self.key = Path(config.hmac_file).read_bytes().strip()
        if len(self.key) < 32:
            raise LifecycleError("adapter HMAC key must contain at least 32 bytes")
        self.context = ssl.create_default_context(cafile=config.ca_file)
        self.context.load_cert_chain(
            config.client_cert_file, config.client_key_file
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        scope: str,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        try:
            encoded = canonical_json(body).encode() if body is not None else b""
        except (TypeError, ValueError):
            raise LifecycleError("adapter command is not JSON serializable") from None
        if len(encoded) > 65536:
            raise LifecycleError("adapter request exceeded size limit")
        timestamp = int(time.time())
        nonce = hashlib.sha256(
            f"{timestamp}:{time.time_ns()}:{path}".encode()
        ).hexdigest()[:32]
        scopes = {item for item in scope.split(" ") if item}
        signature = self._signature(
            method, path, scopes, timestamp, nonce, idempotency_key or "", encoded,
        )
        headers = {
            "Content-Type": "application/json",
            "X-Service-Identity": self.identity,
            "X-Service-Scopes": scope,
            "X-Request-Timestamp": str(timestamp),
            "X-Request-Nonce": nonce,
            "X-Request-Signature": signature,
            "X-Signature-Version": "v2",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        connection = http.client.HTTPSConnection(
            self.host, self.port, context=self.context, timeout=8
        )
        try:
            connection.request(
                method, self.prefix + path, body=encoded, headers=headers
            )
            response = connection.getresponse()
            payload = response.read(65537)
        except (OSError, http.client.HTTPException):
            raise AdapterRequestError() from None
        finally:
            connection.close()
        if not 200 <= response.status < 300:
            # Error bodies can contain identifiers or rejected command values.
            raise AdapterRequestError(response.status)
        if len(payload) > 65536:
            raise AdapterRequestError()
        try:
            document = json.loads(payload)
        except (ValueError, UnicodeError):
            raise AdapterRequestError() from None
        if not isinstance(document, dict):
            raise AdapterRequestError()
        return document

    def lookup_command(
        self, operation: str, key: str, command: dict[str, Any]
    ) -> dict[str, Any]:
        """Read a historical receipt; missing/uncertain results never trigger a write."""
        if operation not in LOOKUP_OPERATIONS:
            raise LifecycleError("unsupported command lookup operation")
        if not isinstance(key, str) or not 8 <= len(key) <= 200:
            raise LifecycleError("invalid original command key")
        document = self._request(
            "POST", "/v1/idempotency/lookup", scope="telephony:read",
            body={"operation": operation, "idempotency_key": key, "command": command},
        )
        result = document.get("result")
        try:
            recorded = datetime.fromisoformat(
                str(document.get("recorded_at", "")).replace("Z", "+00:00")
            )
        except ValueError:
            raise AdapterRequestError() from None
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if (
            recorded.tzinfo is None
            or recorded.utcoffset() is None
            or recorded > now + timedelta(seconds=RECEIPT_MAX_CLOCK_SKEW_SECONDS)
        ):
            raise AdapterRequestError()
        if (
            document.get("status") != "recorded"
            or document.get("operation") != operation
            or document.get("idempotency_key") != key
            or not isinstance(result, dict)
            or type(result.get("resource_version")) is not int
            or result["resource_version"] < 1
        ):
            raise AdapterRequestError()
        return document

    def provision_campaign(self, key: str, command: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/campaigns/provision-disabled",
            scope="telephony:provision", body=command, idempotency_key=key,
        )

    def reconcile_campaign(self, key: str, command: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/reconciliation/campaigns/provision-disabled",
            scope="telephony:reconcile", body=command, idempotency_key=key,
        )

    def campaign_actual(self, campaign_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Z0-9_]{2,8}", campaign_id):
            raise LifecycleError("invalid campaign identity")
        return self._request(
            "GET", f"/v1/campaigns/{campaign_id}/actual-state",
            scope="telephony:read",
        )

    def sync_agent(self, key: str, command: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/agents/sync", scope="telephony:agent-sync",
            body=command, idempotency_key=key,
        )

    def agent_actual(self, user_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Z]{3}[0-9]{4,12}", user_id):
            raise LifecycleError("invalid synthetic agent identity")
        return self._request(
            "GET", f"/v1/agents/{user_id}/actual-state", scope="telephony:read",
        )

    def publish_lead(self, key: str, command: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/leads/publish", scope="telephony:lead-publish",
            body=command, idempotency_key=key,
        )

    def sync_transfer_policy(
        self, key: str, command: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/transfer-policies/sync",
            scope="telephony:transfer-policy", body=command,
            idempotency_key=key,
        )

    def reconcile(self, key: str) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/reconciliation/run", scope="telephony:read",
            idempotency_key=key,
        )

    def disable_campaign(self, key: str, command: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", "/v1/campaigns/disable",
            scope="telephony:provision", body=command, idempotency_key=key,
        )


class LifecycleStore:
    """Small staging control-plane ledger for Odoo/n8n projections and audit."""

    def __init__(self, path: str):
        self.path = path
        with sqlite3.connect(path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS outbox_event (
                    event_key TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS command_intent (
                    operation TEXT NOT NULL,
                    command_key TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    PRIMARY KEY (operation, command_key)
                );
                CREATE TABLE IF NOT EXISTS approval (
                    integration_uuid TEXT PRIMARY KEY,
                    actor TEXT NOT NULL,
                    state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS odoo_projection (
                    integration_uuid TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    actual_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS n8n_scope (
                    scope TEXT PRIMARY KEY,
                    active INTEGER NOT NULL CHECK(active = 0),
                    integration_uuid TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_event (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    correlation_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    detail_hash TEXT NOT NULL
                );
                """
            )

    def event_once(
        self, key: str, event_type: str, correlation_id: str, payload: dict[str, Any]
    ) -> bool:
        digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        with sqlite3.connect(self.path) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO outbox_event VALUES (?, ?, ?, ?)",
                (key, event_type, correlation_id, digest),
            )
            return cursor.rowcount == 1

    def has_command_intents(self, key_prefix: str) -> bool:
        """Distinguish a local preparation retry from an uncertain dispatch."""
        with sqlite3.connect(self.path) as db:
            return db.execute(
                "SELECT 1 FROM command_intent "
                "WHERE substr(command_key, 1, ?) = ? LIMIT 1",
                (len(key_prefix), key_prefix),
            ).fetchone() is not None

    def remember_command(
        self, operation: str, key: str, command: dict[str, Any]
    ) -> dict[str, Any]:
        """Commit the original synthetic body before sending any mutation."""
        encoded = canonical_json(command)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR IGNORE INTO command_intent VALUES (?, ?, ?, ?)",
                (operation, key, encoded, digest),
            )
            row = db.execute(
                "SELECT request_json,request_hash FROM command_intent "
                "WHERE operation=? AND command_key=?", (operation, key),
            ).fetchone()
            if row != (encoded, digest):
                raise LifecycleError("original command key/body conflict")
        return json.loads(encoded)

    def original_command(self, operation: str, key: str) -> dict[str, Any]:
        """Recover the exact body after a restart, without deciding its outcome."""
        with sqlite3.connect(self.path) as db:
            row = db.execute(
                "SELECT request_json,request_hash FROM command_intent "
                "WHERE operation=? AND command_key=?", (operation, key),
            ).fetchone()
        if row is None:
            raise LifecycleError("original command missing; reconciliation required")
        if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
            raise LifecycleError("original command integrity check failed")
        try:
            command = json.loads(row[0])
        except (ValueError, TypeError):
            raise LifecycleError("original command is invalid") from None
        if not isinstance(command, dict):
            raise LifecycleError("original command is invalid")
        return command

    def approve(self, integration_uuid: str, actor: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO approval VALUES (?, ?, 'approved')",
                (integration_uuid, actor),
            )

    def project_odoo(
        self, integration_uuid: str, status: str, actual_id: str, correlation_id: str
    ) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO odoo_projection VALUES (?, ?, ?, ?)",
                (integration_uuid, status, actual_id, correlation_id),
            )

    def project_n8n(self, scope: str, integration_uuid: str) -> None:
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT OR REPLACE INTO n8n_scope VALUES (?, 0, ?)",
                (scope, integration_uuid),
            )

    def audit(
        self, correlation_id: str, event_type: str, detail: dict[str, Any]
    ) -> None:
        digest = hashlib.sha256(canonical_json(detail).encode()).hexdigest()
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO audit_event(correlation_id,event_type,detail_hash) "
                "VALUES (?, ?, ?)",
                (correlation_id, event_type, digest),
            )

    def counts(self) -> dict[str, int]:
        with sqlite3.connect(self.path) as db:
            return {
                table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in (
                    "outbox_event", "approval", "odoo_projection",
                    "n8n_scope", "audit_event", "command_intent",
                )
            }


def campaign_command(
    manifest: dict[str, Any], correlation_id: str, *,
    tenant_id: str, supervisor_subject: str,
) -> dict[str, Any]:
    prefix = "TEST_E2E"
    campaign = manifest["vicidial"]
    list_id = campaign["default_list_id"]
    return {
        "context": {
            "correlation_id": correlation_id,
            "actor": supervisor_subject,
            "tenant_id": tenant_id,
            "business_unit": "TEST",
            "campaign_id": campaign["actual_campaign_id"],
            "supervisor_subject": supervisor_subject,
            "expected_version": 0,
            "reason": "approved staging-only automatic campaign acceptance",
            "requested_at": utc_now(),
        },
        "campaign": {
            "canonical_id": manifest["odoo"]["campaign_code"],
            "actual_id": campaign["actual_campaign_id"],
            "name": "Synthetic E2E Disabled",
            "business_unit": "TEST",
            "direction": "outbound",
            "active": False,
            "dial_method": "MANUAL",
            "call_time": "TESTTIME",
            "recording_policy": "NEVER",
            "callback_enabled": False,
            "default_list_id": list_id,
            "lists": [
                {"list_id": list_id, "code": f"{prefix}_PRIMARY_001", "active": False}
            ],
            "groups": [
                {"code": f"{prefix}_AGENT", "name": "Synthetic Agents",
                 "kind": "user", "active": False},
                {"code": f"{prefix}_CLOSERS", "name": "Synthetic Closers",
                 "kind": "closer", "active": False},
                {"code": f"{prefix}_INBOUND", "name": "Synthetic Inbound",
                 "kind": "inbound", "active": False},
            ],
            "scripts": [
                {"code": f"{prefix}_AGENT_V1", "name": "Synthetic Agent Script",
                 "text": "Synthetic staging-only script. No customer contact.",
                 "version": 1, "active": False}
            ],
            "dispositions": [
                {"code": "TEST", "name": "Synthetic Test",
                 "selectable": True, "human_answered": False,
                 "category": "TEST"},
                {"code": "DNC", "name": "Do Not Call",
                 "selectable": True, "human_answered": False,
                 "category": "COMPLIANCE"},
            ],
            "transfer_targets": [f"{prefix}_CLOSERS", f"{prefix}_INBOUND"],
            "reporting_mapping": "TEST-E2E",
            "recording_policy_reference": "STAGING-NO-RECORDING",
        },
    }


def verify_actual(command: dict[str, Any], actual: dict[str, Any]) -> None:
    desired = command["campaign"]
    if actual.get("campaign_id") != desired["actual_id"] or actual.get("active") != "N":
        raise LifecycleError("campaign read-back mismatch")
    expected_lists = {item["list_id"] for item in desired["lists"]}
    actual_lists = {item["list_id"] for item in actual.get("lists", [])}
    if actual_lists != expected_lists or any(
        item.get("active") != "N" for item in actual.get("lists", [])
    ):
        raise LifecycleError("list read-back mismatch")
    # The restricted read-back contract always returns campaign, lists and
    # dispositions. Group and script state is reported by the atomic
    # provisioning response on the memory adapter, so validate those here only
    # when the deployed adapter includes them in actual-state.
    if "dispositions" not in actual:
        raise LifecycleError("disposition read-back missing")
    for field in ("groups", "scripts", "dispositions"):
        if field not in actual:
            continue
        expected = {item["code"] for item in desired[field]}
        observed = {
            item.get("status", item.get("code"))
            if field == "dispositions"
            else item["code"]
            for item in actual[field]
        }
        if expected != observed:
            raise LifecycleError(f"{field} read-back mismatch")


def run_synthetic_lifecycle(
    design_service: CampaignDesignService,
    store: LifecycleStore,
    adapter: Adapter,
    request: CampaignDesignRequest,
    correlation_id: str,
    *,
    tenant_id: str,
    supervisor_subject: str,
) -> dict[str, Any]:
    if request.environment != "staging" or request.business_unit != "TEST":
        raise LifecycleError("synthetic lifecycle requires staging TEST identity")
    request.validate()
    if request.direction != "outbound":
        raise LifecycleError("synthetic lifecycle requires the disabled outbound fixture")
    if not re.fullmatch(r"[A-Za-z0-9._:-]{2,128}", tenant_id) or not re.fullmatch(
        r"[A-Za-z0-9._:@-]{3,128}", supervisor_subject
    ):
        raise LifecycleError("explicit synthetic tenant and supervisor authority required")
    event_payload = {
        "integration_uuid": request.integration_uuid,
        "odoo_campaign_id": request.odoo_campaign_id,
    }
    event_key = f"campaign.design.requested.v1:{request.integration_uuid}"
    store.event_once(
        event_key, "campaign.design.requested.v1", correlation_id, event_payload
    )
    if store.has_command_intents(f"staging:{request.integration_uuid}:1:"):
        raise LifecycleError("existing synthetic run requires receipt recovery and reconciliation")
    manifest = design_service.preview(request)
    store.approve(request.integration_uuid, supervisor_subject)
    store.audit(correlation_id, "campaign.approved.v1", {"revision": 1})

    command = campaign_command(
        manifest, correlation_id, tenant_id=tenant_id,
        supervisor_subject=supervisor_subject,
    )
    provision_key = f"staging:{request.integration_uuid}:1:provision-disabled"
    command = store.remember_command("campaign.provision-disabled", provision_key, command)
    provisioned = adapter.provision_campaign(provision_key, command)
    actual = adapter.campaign_actual(command["campaign"]["actual_id"])
    verify_actual(command, actual)

    transfer_key = f"staging:{request.integration_uuid}:1:transfer-policy"
    transfer = adapter.sync_transfer_policy(
        transfer_key,
        store.remember_command("transfer-policy.sync", transfer_key, {
            "context": command["context"],
            "policy": {
                "campaign_id": command["campaign"]["actual_id"],
                "allowed_targets": command["campaign"]["transfer_targets"],
            },
        }),
    )
    agent_key = f"staging:{request.integration_uuid}:1:agent"
    agent = adapter.sync_agent(
        agent_key,
        store.remember_command("agent.sync", agent_key, {
            "context": command["context"],
            "agent": {
                "user_id": "TST6101",
                "full_name": "Synthetic Test Agent",
                "user_group": "TEST_E2E_AGENT",
                "campaigns": [command["campaign"]["actual_id"]],
                "inbound_groups": ["TEST_E2E_INBOUND", "TEST_E2E_CLOSERS"],
                "active": False,
            },
        }),
    )
    observed_agent = adapter.agent_actual("TST6101")
    agent_actual = agent.get("actual", {})
    if (
        agent.get("status") != "disabled"
        or observed_agent.get("user_id") != "TST6101"
        or observed_agent.get("active") not in (False, "N")
        or observed_agent.get("campaigns") != [command["campaign"]["actual_id"]]
        or observed_agent.get("user_group") != "TEST_E2E_AGENT"
        or sorted(observed_agent.get("inbound_groups", []))
        != ["TEST_E2E_CLOSERS", "TEST_E2E_INBOUND"]
        or type(observed_agent.get("resource_version")) is not int
        or observed_agent["resource_version"] < 1
        or agent_actual.get("user_id") != observed_agent.get("user_id")
    ):
        raise LifecycleError("agent campaign isolation read-back mismatch")
    lead_command = {
        "context": command["context"],
        "lead": {
            "external_id": f"synthetic-{request.integration_uuid}",
            "list_id": manifest["vicidial"]["default_list_id"],
            "phone_code": "1",
            "phone_number": "0006101",
            "first_name": "Synthetic",
            "last_name": "NoContact",
            "status": "NEW",
        },
    }
    lead_key = f"staging:{request.integration_uuid}:1:lead"
    lead_command = store.remember_command("lead.publish", lead_key, lead_command)
    lead = adapter.publish_lead(lead_key, lead_command)

    store.project_n8n(manifest["n8n"]["scope"], request.integration_uuid)
    store.project_odoo(
        request.integration_uuid, "provisioned_disabled",
        command["campaign"]["actual_id"], correlation_id,
    )
    store.audit(correlation_id, "campaign.provision.completed.v1", {
        "manifest_hash": hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    })

    replay_event = store.event_once(
        event_key, "campaign.design.requested.v1", correlation_id, event_payload
    )
    replay = adapter.provision_campaign(provision_key, command)
    lead_replay = adapter.publish_lead(lead_key, lead_command)
    reconciliation = adapter.reconcile(
        f"staging:{request.integration_uuid}:1:reconcile"
    )
    # The disable is a new command, so bind it to current read-back, preserving
    # the original provisioning body for exact-key replay or receipt recovery.
    current = adapter.campaign_actual(command["campaign"]["actual_id"])
    verify_actual(command, current)
    version = current.get("resource_version")
    if type(version) is not int or version < 1:
        raise LifecycleError("managed campaign version missing from read-back")
    rollback_command = {
        **command, "context": {**command["context"], "expected_version": version},
    }
    rollback_key = f"staging:{request.integration_uuid}:1:rollback"
    rollback_command = store.remember_command("campaign.disable", rollback_key, rollback_command)
    rollback = adapter.disable_campaign(rollback_key, rollback_command)
    rolled_back = adapter.campaign_actual(command["campaign"]["actual_id"])
    verify_actual(command, rolled_back)
    if (
        rollback.get("status") != "disabled"
        or rollback.get("resource_version") != version + 1
        or rolled_back.get("resource_version") != version + 1
    ):
        raise LifecycleError("campaign disable version read-back mismatch")
    store.project_odoo(
        request.integration_uuid, "rolled_back_disabled",
        command["campaign"]["actual_id"], correlation_id,
    )
    store.audit(correlation_id, "campaign.rolled_back.v1", {
        "state": "disabled"
    })

    if replay_event:
        raise LifecycleError("design event was not exactly-once")
    if replay.get("idempotent_replay") is not True:
        raise LifecycleError("campaign retry was not idempotent")
    if lead_replay.get("idempotent_replay") is not True:
        raise LifecycleError("lead retry was not idempotent")
    if reconciliation.get("status") != "complete":
        raise LifecycleError("reconciliation did not complete")
    if transfer.get("allowed_targets") != sorted(
        command["campaign"]["transfer_targets"]
    ):
        raise LifecycleError("same-campaign transfer policy mismatch")

    result = {
        "status": "PASS",
        "verification_scope": "synthetic_adapter_lifecycle",
        "downstream_projections": "local_staging_ledger_only",
        "correlation_id": correlation_id,
        "integration_uuid": request.integration_uuid,
        "manifest_sha256": hashlib.sha256(
            canonical_json(manifest).encode()
        ).hexdigest(),
        "campaign": {
            "canonical_id": manifest["odoo"]["campaign_code"],
            "actual_id": command["campaign"]["actual_id"],
            "active": False,
        },
        "list": {
            "list_id": manifest["vicidial"]["default_list_id"],
            "active": False,
        },
        "design_event_count": store.counts()["outbox_event"],
        "n8n": {"scope": manifest["n8n"]["scope"], "active": False},
        "agent": {"user_id": "TST6101", "active": False},
        "lead": {
            "external_id_sha256": hashlib.sha256(
                lead_command["lead"]["external_id"].encode()
            ).hexdigest(),
            "synthetic": True,
            "customer_number_used": False,
            "adapter_lead_id_present": bool(
                lead.get("actual", {}).get("lead_id")
            ),
        },
        "groups": sorted(item["code"] for item in command["campaign"]["groups"]),
        "scripts": sorted(item["code"] for item in command["campaign"]["scripts"]),
        "dispositions": sorted(
            item["code"] for item in command["campaign"]["dispositions"]
        ),
        "transfer_policy": "same-campaign-only",
        "idempotent_campaign_replay": True,
        "idempotent_lead_replay": True,
        "reconciliation": "no_unexplained_drift",
        "rollback": rollback.get("status"),
        "odoo_status": "rolled_back_disabled",
        "external_communication": False,
        "production_dialing": False,
        "audit_counts": store.counts(),
        "resource_counts": provisioned.get("resources", {}),
        "campaign_resource_version": rolled_back["resource_version"],
        "agent_resource_version": observed_agent["resource_version"],
    }
    result["evidence_sha256"] = hashlib.sha256(
        canonical_json(result).encode()
    ).hexdigest()
    return result
