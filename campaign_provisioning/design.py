"""Idempotent campaign design previews.

This module deliberately stops at desired-state design.  It has no VICIdial,
n8n, or Odoo client and therefore cannot accidentally perform a remote write.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
import sqlite3
from typing import Any
from uuid import UUID


LIST_RANGES = {
    "MOY": (11000, 11999),
    "COD": (21000, 21999),
    "SCP": (31000, 31999),
    "MBL": (41000, 41999),
    "RLP": (51000, 51999),
    "FTP": (61000, 61999),
    "TRX": (71000, 71999),
    "CAL": (81000, 81999),
}
TEST_LIST_RANGE = (91000, 91999)
VALID_DIRECTIONS = {"inbound": "IN", "outbound": "OUT", "blended": "BLEND"}
VALID_ENVIRONMENTS = {"development", "staging", "production"}
CODE_PART = re.compile(r"^[A-Z0-9]+$")


class DesignValidationError(ValueError):
    """The requested design violates the canonical contract."""


class DesignConflict(RuntimeError):
    """An idempotency identity was reused for different desired state."""


@dataclass(frozen=True)
class CampaignDesignRequest:
    integration_uuid: str
    odoo_campaign_id: int
    business_unit: str
    purpose: str
    direction: str
    environment: str
    default_language: str
    time_zone: str
    calling_hours: str
    consent_policy: str
    dnc_policy: str
    recording_policy: str
    lead_source_policy: str
    callback_policy: str
    appointment_policy: str
    disposition_family: str
    script_template: str
    n8n_template: str
    reporting_category: str
    owner_user_id: int
    supervisor_user_id: int
    activation_policy: str
    agent_roles: tuple[str, ...] = ("SDR",)
    transfer_roles: tuple[str, ...] = ("CLOSERS", "SUPPORT", "SUPERVISORS")

    def validate(self) -> None:
        try:
            UUID(self.integration_uuid)
        except (ValueError, TypeError) as exc:
            raise DesignValidationError("integration_uuid must be a UUID") from exc
        if self.odoo_campaign_id <= 0:
            raise DesignValidationError("odoo_campaign_id must be positive")
        if self.business_unit not in LIST_RANGES:
            raise DesignValidationError("unknown business-unit code")
        if not CODE_PART.fullmatch(self.purpose):
            raise DesignValidationError("purpose must contain only A-Z and 0-9")
        if self.direction not in VALID_DIRECTIONS:
            raise DesignValidationError("invalid campaign direction")
        if self.environment not in VALID_ENVIRONMENTS:
            raise DesignValidationError("invalid environment")
        required = (
            "default_language",
            "time_zone",
            "calling_hours",
            "consent_policy",
            "dnc_policy",
            "recording_policy",
            "lead_source_policy",
            "callback_policy",
            "appointment_policy",
            "disposition_family",
            "script_template",
            "n8n_template",
            "reporting_category",
            "activation_policy",
        )
        if any(not getattr(self, field).strip() for field in required):
            raise DesignValidationError("all policy and template fields are required")
        if self.owner_user_id <= 0 or self.supervisor_user_id <= 0:
            raise DesignValidationError("owner and supervisor are required")
        roles = self.agent_roles + self.transfer_roles
        if not roles or any(not CODE_PART.fullmatch(role) for role in roles):
            raise DesignValidationError("roles must contain only A-Z and 0-9")

    def desired_hash(self) -> str:
        body = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return sha256(body.encode()).hexdigest()


class SQLiteDesignRepository:
    """SQLite implementation with serialized allocation and unique ownership."""

    def __init__(self, path: str):
        self.path = path
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS campaign_design_revision (
                    integration_uuid TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    request_hash TEXT NOT NULL,
                    manifest_hash TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    approval_state TEXT NOT NULL DEFAULT 'preview',
                    PRIMARY KEY (integration_uuid, revision)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS design_request_once
                    ON campaign_design_revision(integration_uuid, request_hash);
                CREATE TABLE IF NOT EXISTS campaign_resource_allocation (
                    environment TEXT NOT NULL,
                    business_unit TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    reserved_identifier INTEGER NOT NULL,
                    integration_uuid TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'reserved',
                    UNIQUE(environment, resource_type, reserved_identifier),
                    UNIQUE(environment, integration_uuid, revision, resource_type)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def create_preview(self, request: CampaignDesignRequest) -> dict[str, Any]:
        request_hash = request.desired_hash()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute(
                    """
                    SELECT request_hash, manifest_json
                    FROM campaign_design_revision
                    WHERE integration_uuid = ?
                    ORDER BY revision DESC LIMIT 1
                    """,
                    (request.integration_uuid,),
                ).fetchone()
                if existing:
                    if existing["request_hash"] != request_hash:
                        raise DesignConflict(
                            "integration UUID already has a different design; "
                            "create an explicit new revision"
                        )
                    db.commit()
                    return json.loads(existing["manifest_json"])

                revision = 1
                list_id = self._allocate_list(db, request, revision)
                manifest = build_manifest(request, revision, list_id)
                manifest_json = canonical_json(manifest)
                manifest_hash = sha256(manifest_json.encode()).hexdigest()
                db.execute(
                    """
                    INSERT INTO campaign_design_revision
                    (integration_uuid, revision, request_hash, manifest_hash, manifest_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        request.integration_uuid,
                        revision,
                        request_hash,
                        manifest_hash,
                        manifest_json,
                    ),
                )
                db.commit()
                return manifest
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _allocate_list(
        db: sqlite3.Connection, request: CampaignDesignRequest, revision: int
    ) -> int:
        start, end = (
            TEST_LIST_RANGE
            if request.environment != "production"
            else LIST_RANGES[request.business_unit]
        )
        row = db.execute(
            """
            SELECT MIN(candidate) AS available FROM (
                WITH RECURSIVE ids(candidate) AS (
                    SELECT ? UNION ALL SELECT candidate + 1 FROM ids WHERE candidate < ?
                )
                SELECT candidate FROM ids
                WHERE candidate NOT IN (
                    SELECT reserved_identifier FROM campaign_resource_allocation
                    WHERE environment = ? AND resource_type = 'vicidial_list'
                )
            )
            """,
            (start, end, request.environment),
        ).fetchone()
        if not row or row["available"] is None:
            raise DesignConflict("business-unit list range is exhausted")
        list_id = int(row["available"])
        db.execute(
            """
            INSERT INTO campaign_resource_allocation
            (environment, business_unit, resource_type, reserved_identifier,
             integration_uuid, revision)
            VALUES (?, ?, 'vicidial_list', ?, ?, ?)
            """,
            (
                request.environment,
                request.business_unit,
                list_id,
                request.integration_uuid,
                revision,
            ),
        )
        return list_id


class CampaignDesignService:
    def __init__(self, repository: SQLiteDesignRepository):
        self.repository = repository

    def preview(self, request: CampaignDesignRequest) -> dict[str, Any]:
        request.validate()
        return self.repository.create_preview(request)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def build_manifest(
    request: CampaignDesignRequest, revision: int, list_id: int
) -> dict[str, Any]:
    direction_code = VALID_DIRECTIONS[request.direction]
    campaign_id = (
        f"{request.business_unit}-{request.purpose}-{direction_code}"
    )
    prefix = f"{request.business_unit}_{request.purpose}"
    environment_code = {
        "development": "DEV",
        "staging": "STG",
        "production": "PROD",
    }[request.environment]
    groups = list(dict.fromkeys(request.agent_roles + request.transfer_roles))
    return {
        "schema_version": "campaign-provisioning.v1",
        "environment": request.environment,
        "integration_uuid": request.integration_uuid,
        "design_revision": revision,
        "business_unit": request.business_unit,
        "odoo": {
            "campaign_id": request.odoo_campaign_id,
            "campaign_code": campaign_id,
            "owner_user_id": request.owner_user_id,
            "supervisor_user_id": request.supervisor_user_id,
        },
        "vicidial": {
            "campaign_id": campaign_id,
            "active": False,
            "default_list_id": list_id,
            "lists": [
                {
                    "list_id": list_id,
                    "code": f"{prefix}_PRIMARY_001",
                    "active": False,
                }
            ],
            "user_groups": [f"{prefix}_{role}" for role in groups],
            "inbound_groups": [
                f"{prefix}_{role}" for role in request.transfer_roles
            ],
            "scripts": [
                f"{prefix}_{role}_V{revision}" for role in request.agent_roles
            ],
            "disposition_set": f"{prefix}_{direction_code}_V{revision}",
        },
        "n8n": {
            "scope": f"{environment_code}-{request.business_unit}-{request.purpose}-V{revision}",
            "template": request.n8n_template,
            "workflows_active": False,
        },
        "policies": {
            "calling_hours": request.calling_hours,
            "time_zone": request.time_zone,
            "consent_policy": request.consent_policy,
            "dnc_policy": request.dnc_policy,
            "recording_policy": request.recording_policy,
            "lead_source_policy": request.lead_source_policy,
            "callback_policy": request.callback_policy,
            "appointment_policy": request.appointment_policy,
            "activation_policy": request.activation_policy,
            "transfer_policy": "same-campaign-only",
        },
        "reporting": {"category": request.reporting_category},
        "feature_flags": {
            "lead_publication": False,
            "agent_sync": False,
            "live_call_control": False,
            "production_dialing": False,
            "provisioning": False,
        },
        "test_plan": [
            "read_back",
            "synthetic_lead",
            "duplicate_lead_retry",
            "agent_sync",
            "callback",
            "same_campaign_transfer",
            "cross_campaign_rejection",
            "rollback",
        ],
        "rollback_reference": None,
    }
