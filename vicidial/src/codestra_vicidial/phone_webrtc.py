"""Bounded VICIdial phone and WebRTC lifecycle for the V1 staging lane.

Middleware remains the authoritative allocator for ordinary extensions.  This
adapter only claims an extension after Middleware has supplied an allocation
reference, and the one static exception (6101) can only be adopted for the
TEST_SYN synthetic agent.  The adapter never exposes a long-lived secret and
never enables a production/PSTN route.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .credential_authority import CredentialAuthorityUnavailable
from .models import RequestContext, StrictModel
from .repository import CollisionError
from .state import IdempotencyConflict, StateStore

SYNTHETIC_CAMPAIGN = "TEST_SYN"
SYNTHETIC_BUSINESS_UNIT = "TEST"
SYNTHETIC_EXTENSION = "6101"
SYNTHETIC_DESTINATION = "6000"
SYNTHETIC_EMPLOYEE = "COD.2026.00016"
SYNTHETIC_USER = "cod00016"
SYNTHETIC_TURN_URL = "turns:vicidial-staging.codestra.agency:5349"
DEFAULT_SIP_DOMAIN = "dialer.codestra.agency"
MAX_TTL_SECONDS = 900
STATIC_RESERVATION_EXPIRY = "9999-12-31T23:59:59+00:00"

AgentUserId = str
_KEYCLOAK_SUBJECT = r"^[0-9a-fA-F-]{36}$"
_AGENT_USER = r"^[A-Za-z]{3}[0-9]{4,12}$"
_EXTENSION = r"^[0-9]{4,8}$"
_HASH = r"^[a-f0-9]{64}$"
_REFERENCE = r"^[A-Za-z0-9._:/-]{8,255}$"


class WebRTCSessionAlreadyActive(RuntimeError):
    code = "WEBRTC_SESSION_ALREADY_ACTIVE"


class CredentialResponseNotReplayable(RuntimeError):
    code = "WEBRTC_CREDENTIAL_REPLAY_REQUIRED"


class ExtensionReservationCommand(StrictModel):
    context: RequestContext
    employee_id: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    user_id: str = Field(min_length=7, max_length=15, pattern=_AGENT_USER)
    keycloak_subject: str = Field(min_length=36, max_length=36, pattern=_KEYCLOAK_SUBJECT)
    requested_extension: str | None = Field(default=None, pattern=_EXTENSION)
    pool_start: int = Field(default=6110, ge=6100, le=9999)
    pool_end: int = Field(default=6999, ge=6100, le=9999)
    allocation_reference: str = Field(min_length=8, max_length=255, pattern=_REFERENCE)
    environment: Literal["STAGING"] = "STAGING"
    ttl_seconds: int = Field(default=900, ge=60, le=MAX_TTL_SECONDS)

    @model_validator(mode="after")
    def validate_range(self) -> "ExtensionReservationCommand":
        if self.pool_start > self.pool_end:
            raise ValueError("extension pool range is reversed")
        if self.requested_extension is not None:
            number = int(self.requested_extension)
            if not self.pool_start <= number <= self.pool_end:
                raise ValueError("requested extension is outside the supplied pool")
        return self


class ExtensionAdoptionCommand(StrictModel):
    context: RequestContext
    employee_id: Literal[SYNTHETIC_EMPLOYEE]
    user_id: Literal[SYNTHETIC_USER]
    keycloak_subject: str = Field(min_length=36, max_length=36, pattern=_KEYCLOAK_SUBJECT)
    extension: Literal[SYNTHETIC_EXTENSION]
    existing_state_sha256: str = Field(pattern=_HASH)
    allocation_reference: Literal["STATIC-TEST_SYN-6101"]
    environment: Literal["STAGING"] = "STAGING"


class WebRTCCredentialCommand(StrictModel):
    context: RequestContext
    employee_id: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    user_id: str = Field(min_length=7, max_length=15, pattern=_AGENT_USER)
    keycloak_subject: str = Field(min_length=36, max_length=36, pattern=_KEYCLOAK_SUBJECT)
    extension: str = Field(pattern=_EXTENSION)
    browser_session_binding: str = Field(pattern=_HASH)
    allocation_reference: str = Field(min_length=8, max_length=255, pattern=_REFERENCE)
    environment: Literal["STAGING"] = "STAGING"
    permitted_call_scope: list[Literal[SYNTHETIC_DESTINATION]] = Field(
        default_factory=lambda: [SYNTHETIC_DESTINATION], min_length=1, max_length=1
    )


class WebRTCRevokeCommand(StrictModel):
    context: RequestContext
    employee_id: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    user_id: str = Field(min_length=7, max_length=15, pattern=_AGENT_USER)
    keycloak_subject: str = Field(min_length=36, max_length=36, pattern=_KEYCLOAK_SUBJECT)
    extension: str = Field(pattern=_EXTENSION)
    reason: str = Field(min_length=8, max_length=500)
    environment: Literal["STAGING"] = "STAGING"


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _disabled(value: Any) -> bool:
    return value in (False, "N", "0", 0)


def _safe_phone(row: dict | None) -> dict | None:
    if row is None:
        return None
    allowed = {
        "extension", "dialplan_number", "login", "status", "active",
        "phone_type", "fullname", "company", "protocol", "local_gmt",
    }
    return {key: row[key] for key in sorted(row) if key in allowed}


def _safe_agent(row: dict | None) -> dict | None:
    if row is None:
        return None
    allowed = {
        "user_id", "full_name", "user_level", "user_group", "active",
        "campaigns", "inbound_groups", "extension",
    }
    return {key: row[key] for key in sorted(row) if key in allowed}


class PhoneLedger:
    """Durable local reservation/ticket ledger; it stores only hashes of secrets."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS phone_extension_reservations(
              reservation_id TEXT PRIMARY KEY,
              idempotency_key TEXT NOT NULL UNIQUE,
              request_hash TEXT NOT NULL,
              extension TEXT NOT NULL,
              tenant_id TEXT NOT NULL,
              business_unit TEXT NOT NULL,
              campaign_id TEXT NOT NULL,
              employee_id TEXT NOT NULL,
              user_id TEXT NOT NULL,
              keycloak_subject TEXT NOT NULL,
              allocation_reference TEXT NOT NULL,
              mode TEXT NOT NULL,
              state TEXT NOT NULL,
              actual_state_hash TEXT,
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS
              uq_phone_extension_active
              ON phone_extension_reservations(extension)
              WHERE state IN ('RESERVED','ADOPTED','DISABLED_READY','ACTIVE');
            CREATE TABLE IF NOT EXISTS phone_webrtc_sessions(
              session_id TEXT PRIMARY KEY,
              idempotency_key TEXT NOT NULL UNIQUE,
              reservation_id TEXT NOT NULL,
              tenant_id TEXT NOT NULL,
              campaign_id TEXT NOT NULL,
              employee_id TEXT NOT NULL,
              user_id TEXT NOT NULL,
              keycloak_subject TEXT NOT NULL,
              extension TEXT NOT NULL,
              browser_binding_hash TEXT NOT NULL,
              ticket_hash TEXT NOT NULL UNIQUE,
              credential_hash TEXT NOT NULL,
              credential_reference TEXT NOT NULL,
              state TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              used_at TEXT,
              revoked_at TEXT,
              created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS
              uq_phone_webrtc_active_agent
              ON phone_webrtc_sessions(tenant_id,user_id)
              WHERE state IN ('ISSUING','ISSUED') AND revoked_at IS NULL;
            """
        )
        self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def _purge(self, now: str) -> None:
        self.db.execute(
            "UPDATE phone_extension_reservations SET state='EXPIRED' "
            "WHERE state='RESERVED' AND expires_at<=?",
            (now,),
        )
        self.db.execute(
            "UPDATE phone_webrtc_sessions SET state='EXPIRED' "
            "WHERE state IN ('ISSUING','ISSUED') AND revoked_at IS NULL "
            "AND expires_at<=?",
            (now,),
        )

    def reservation_for_key(self, key: str) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute(
                "SELECT * FROM phone_extension_reservations WHERE idempotency_key=?",
                (key,),
            ).fetchone()

    def reservation_for_extension(self, extension: str) -> sqlite3.Row | None:
        with self._lock:
            self._purge(_iso(_now()))
            self.db.commit()
            return self.db.execute(
                "SELECT * FROM phone_extension_reservations WHERE extension=? "
                "AND state IN ('RESERVED','ADOPTED','DISABLED_READY','ACTIVE')",
                (extension,),
            ).fetchone()

    def put_reservation(
        self, *, key: str, request_hash: str, extension: str, command: Any,
        mode: str, state: str, actual_state_hash: str | None = None,
        expires_at: str,
    ) -> dict:
        with self._lock:
            now = _iso(_now())
            self._purge(now)
            prior = self.db.execute(
                "SELECT * FROM phone_extension_reservations WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise IdempotencyConflict("phone reservation idempotency conflict")
                return self._reservation_document(prior, replay=True)
            reservation_id = str(uuid4())
            try:
                self.db.execute(
                    """
                    INSERT INTO phone_extension_reservations(
                      reservation_id,idempotency_key,request_hash,extension,
                      tenant_id,business_unit,campaign_id,employee_id,user_id,
                      keycloak_subject,allocation_reference,mode,state,
                      actual_state_hash,expires_at,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        reservation_id, key, request_hash, extension,
                        command.context.tenant_id, command.context.business_unit,
                        command.context.campaign_id, command.employee_id,
                        command.user_id, command.keycloak_subject,
                        command.allocation_reference, mode, state,
                        actual_state_hash, expires_at, now,
                    ),
                )
                self.db.commit()
            except sqlite3.IntegrityError as exc:
                self.db.rollback()
                raise CollisionError("extension is already claimed") from exc
            row = self.db.execute(
                "SELECT * FROM phone_extension_reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            return self._reservation_document(row, replay=False)

    @staticmethod
    def _reservation_document(row: sqlite3.Row, *, replay: bool) -> dict:
        return {
            "reservation_id": row["reservation_id"],
            "extension": row["extension"],
            "state": row["state"],
            "mode": row["mode"],
            "allocation_reference": row["allocation_reference"],
            "expires_at": row["expires_at"],
            "replayed": replay,
        }

    def begin_session(
        self, *, key: str, reservation: sqlite3.Row, command: WebRTCCredentialCommand,
        expires_at: str,
    ) -> tuple[str, str]:
        with self._lock:
            now = _iso(_now())
            self._purge(now)
            prior = self.db.execute(
                "SELECT session_id,state,revoked_at FROM phone_webrtc_sessions "
                "WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if prior is not None:
                raise CredentialResponseNotReplayable(
                    "credential response cannot be replayed; use reconciliation"
                )
            active = self.db.execute(
                "SELECT session_id FROM phone_webrtc_sessions "
                "WHERE tenant_id=? AND user_id=? "
                "AND state IN ('ISSUING','ISSUED') AND revoked_at IS NULL "
                "AND expires_at>?",
                (command.context.tenant_id, command.user_id, now),
            ).fetchone()
            if active is not None:
                raise WebRTCSessionAlreadyActive(
                    "the agent already has an active WebRTC session"
                )
            session_id = str(uuid4())
            ticket = secrets.token_urlsafe(32)
            try:
                self.db.execute(
                    """
                    INSERT INTO phone_webrtc_sessions(
                      session_id,idempotency_key,reservation_id,tenant_id,campaign_id,
                      employee_id,user_id,keycloak_subject,extension,
                      browser_binding_hash,ticket_hash,credential_hash,
                      credential_reference,state,expires_at,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        session_id, key, reservation["reservation_id"],
                        command.context.tenant_id, command.context.campaign_id,
                        command.employee_id, command.user_id, command.keycloak_subject,
                        command.extension, command.browser_session_binding,
                        hashlib.sha256(ticket.encode()).hexdigest(),
                        "pending", "pending", "ISSUING", expires_at, now,
                    ),
                )
                self.db.commit()
            except sqlite3.IntegrityError as exc:
                self.db.rollback()
                raise WebRTCSessionAlreadyActive(
                    "the agent already has an active WebRTC session"
                ) from exc
            return session_id, ticket

    def complete_session(
        self, session_id: str, credential: str, credential_reference: str
    ) -> None:
        credential_hash = hashlib.sha256(credential.encode()).hexdigest()
        with self._lock:
            self.db.execute(
                "UPDATE phone_webrtc_sessions SET credential_hash=?,"
                "credential_reference=?,state='ISSUED' WHERE session_id=? "
                "AND state='ISSUING'",
                (credential_hash, credential_reference, session_id),
            )
            self.db.commit()

    def fail_session(self, session_id: str) -> None:
        with self._lock:
            self.db.execute(
                "UPDATE phone_webrtc_sessions SET state='FAILED',revoked_at=? "
                "WHERE session_id=?",
                (_iso(_now()), session_id),
            )
            self.db.commit()

    def revoke_agent(self, tenant_id: str, user_id: str, extension: str) -> int:
        with self._lock:
            now = _iso(_now())
            cursor = self.db.execute(
                "UPDATE phone_webrtc_sessions SET state='REVOKED',revoked_at=? "
                "WHERE tenant_id=? AND user_id=? AND extension=? "
                "AND revoked_at IS NULL",
                (now, tenant_id, user_id, extension),
            )
            self.db.commit()
            return cursor.rowcount

    def consume_ticket(self, ticket: str) -> dict | None:
        digest = hashlib.sha256(ticket.encode()).hexdigest()
        with self._lock:
            now = _iso(_now())
            try:
                self.db.execute("BEGIN IMMEDIATE")
                row = self.db.execute(
                    "SELECT * FROM phone_webrtc_sessions WHERE ticket_hash=? "
                    "AND used_at IS NULL AND state='ISSUED' AND expires_at>?",
                    (digest, now),
                ).fetchone()
                if row is None:
                    self.db.rollback()
                    return None
                self.db.execute(
                    "UPDATE phone_webrtc_sessions SET used_at=? "
                    "WHERE session_id=? AND used_at IS NULL",
                    (now, row["session_id"]),
                )
                self.db.commit()
                return {
                    "session_id": row["session_id"],
                    "user_id": row["user_id"],
                    "extension": row["extension"],
                    "campaign_id": row["campaign_id"],
                    "tenant_id": row["tenant_id"],
                }
            except Exception:
                self.db.rollback()
                raise


class PhoneAdapterService:
    def __init__(
        self,
        repository,
        state: StateStore,
        flags,
        credential_authority=None,
        *,
        sip_domain: str = DEFAULT_SIP_DOMAIN,
    ):
        self.repository = repository
        self.state = state
        self.flags = flags
        self.credential_authority = credential_authority
        self.sip_domain = sip_domain
        self.ledger = PhoneLedger(state.path)

    def _require(self, flag: str, label: str) -> None:
        if not self.flags.writes:
            raise PermissionError("adapter kill switch is active")
        if not getattr(self.flags, flag, False):
            raise PermissionError(f"{label} is disabled")

    @staticmethod
    def _require_synthetic(command: Any, *, static: bool = False) -> None:
        if command.context.campaign_id != SYNTHETIC_CAMPAIGN:
            raise PermissionError("V1 phone adapter is limited to TEST_SYN")
        if command.context.business_unit != SYNTHETIC_BUSINESS_UNIT:
            raise PermissionError("TEST_SYN requires TEST business unit")
        if getattr(command, "environment", "STAGING") != "STAGING":
            raise PermissionError("V1 phone adapter requires STAGING")
        if static and (
            command.employee_id != SYNTHETIC_EMPLOYEE
            or command.user_id != SYNTHETIC_USER
            or command.extension != SYNTHETIC_EXTENSION
        ):
            raise PermissionError("static TEST_SYN extension ownership mismatch")

    def _phone(self, extension: str) -> dict | None:
        reader = getattr(self.repository, "phone", None)
        if callable(reader):
            return reader(extension)
        phones = getattr(self.repository, "phones", None)
        if isinstance(phones, dict):
            value = phones.get(extension)
            if value is None:
                value = phones.get(str(extension))
            return dict(value) if value is not None else None
        return None

    def _phones(self, start: int, end: int) -> list[dict]:
        reader = getattr(self.repository, "phone_rows", None)
        if callable(reader):
            return reader(start, end)
        phones = getattr(self.repository, "phones", None)
        if isinstance(phones, dict):
            return [
                dict(row) for key, row in phones.items()
                if start <= int(str(row.get("extension", key))) <= end
            ]
        return []

    def _agent(self, user_id: str) -> dict | None:
        row = self.repository.agent(user_id)
        return dict(row) if row is not None else None

    def reserve_extension(self, key: str, command: ExtensionReservationCommand) -> dict:
        self._require("extension_reservation", "extension reservation")
        self._require_synthetic(command)
        if command.requested_extension == SYNTHETIC_EXTENSION:
            raise CollisionError("6101 requires the explicit static adoption operation")
        request_hash = self.state.digest(command.model_dump(mode="json"))
        prior = self.ledger.reservation_for_key(key)
        if prior is not None:
            if prior["request_hash"] != request_hash:
                raise IdempotencyConflict("phone reservation idempotency conflict")
            return self.ledger._reservation_document(prior, replay=True)
        occupied = {
            str(row.get("extension"))
            for row in self._phones(command.pool_start, command.pool_end)
            if row.get("extension") is not None
        }
        for extension in range(command.pool_start, command.pool_end + 1):
            value = str(extension)
            if value in {SYNTHETIC_EXTENSION, "1001"}:
                continue
            if value in occupied:
                continue
            if self.ledger.reservation_for_extension(value) is not None:
                continue
            return self.ledger.put_reservation(
                key=key,
                request_hash=request_hash,
                extension=value,
                command=command,
                mode="RESERVED",
                state="RESERVED",
                expires_at=_iso(_now() + timedelta(seconds=command.ttl_seconds)),
            )
        raise CollisionError("no unoccupied extension is available in the supplied pool")

    def adopt_extension(self, key: str, command: ExtensionAdoptionCommand) -> dict:
        self._require("extension_reservation", "extension adoption")
        self._require_synthetic(command, static=True)
        actual = self._phone(SYNTHETIC_EXTENSION)
        if actual is None:
            raise CollisionError("static extension 6101 is not present in VICIdial")
        if not _disabled(actual.get("active")):
            raise CollisionError("static extension 6101 is active and cannot be adopted")
        observed = StateStore.digest(_safe_phone(actual))
        if observed != command.existing_state_sha256:
            raise CollisionError("static extension adoption proof is stale")
        request_hash = self.state.digest(command.model_dump(mode="json"))
        prior = self.ledger.reservation_for_key(key)
        if prior is not None:
            if prior["request_hash"] != request_hash:
                raise IdempotencyConflict("phone adoption idempotency conflict")
            return self.ledger._reservation_document(prior, replay=True)
        return self.ledger.put_reservation(
            key=key,
            request_hash=request_hash,
            extension=SYNTHETIC_EXTENSION,
            command=command,
            mode="ADOPTED",
            state="ADOPTED",
            actual_state_hash=observed,
            expires_at=STATIC_RESERVATION_EXPIRY,
        )

    def readback(self, extension: str, *, user_id: str, campaign_id: str) -> dict:
        phone = _safe_phone(self._phone(extension))
        agent = _safe_agent(self._agent(user_id))
        reservation = self.ledger.reservation_for_extension(extension)
        phone_match = bool(
            phone
            and str(phone.get("extension")) == str(extension)
            and str(phone.get("dialplan_number", extension)) == str(extension)
            and _disabled(phone.get("active"))
        )
        agent_match = bool(
            agent
            and agent.get("user_id") == user_id
            and agent.get("campaigns") == [campaign_id]
            and _disabled(agent.get("active"))
        )
        reservation_match = bool(
            reservation
            and reservation["extension"] == str(extension)
            and reservation["campaign_id"] == campaign_id
            and reservation["user_id"] == user_id
        )
        return {
            "extension": str(extension),
            "campaign_id": campaign_id,
            "agent_user_id": user_id,
            "phone_record": phone,
            "agent_record": agent,
            "reservation_state": reservation["state"] if reservation else None,
            "checks": {
                "vicidial_phone_record": phone_match,
                "vicidial_agent_record": agent_match,
                "extension_binding": reservation_match,
                "pjsip_endpoint": {
                    "status": "delegated_to_middleware",
                    "verified": False,
                },
            },
            "matches": phone_match and agent_match and reservation_match,
        }

    def issue_webrtc(self, key: str, command: WebRTCCredentialCommand) -> dict:
        self._require("webrtc_session_issuance", "WebRTC session issuance")
        self._require_synthetic(command)
        if command.extension != SYNTHETIC_EXTENSION:
            raise PermissionError("V1 WebRTC issuance is limited to endpoint 6101")
        if command.permitted_call_scope != [SYNTHETIC_DESTINATION]:
            raise PermissionError("synthetic call scope is immutable")
        reservation = self.ledger.reservation_for_extension(command.extension)
        if reservation is None or any(
            reservation[field] != expected
            for field, expected in (
                ("tenant_id", command.context.tenant_id),
                ("campaign_id", command.context.campaign_id),
                ("user_id", command.user_id),
                ("allocation_reference", command.allocation_reference),
            )
        ):
            raise PermissionError("exact extension ownership is not durable")
        readback = self.readback(
            command.extension, user_id=command.user_id,
            campaign_id=command.context.campaign_id,
        )
        if not readback["matches"]:
            raise CollisionError("VICIdial phone or disabled agent read-back failed")
        expires_at = _now() + timedelta(seconds=300)
        session_id, ticket = self.ledger.begin_session(
            key=key, reservation=reservation, command=command,
            expires_at=_iso(expires_at),
        )
        authority = getattr(self.credential_authority, "issue_ephemeral", None)
        if not callable(authority):
            self.ledger.fail_session(session_id)
            raise CredentialAuthorityUnavailable(
                "ephemeral PJSIP credential authority is unavailable"
            )
        try:
            issued = authority(
                username=command.user_id,
                extension=command.extension,
                expires_at=_iso(expires_at),
                idempotency_key=key,
            )
            if (
                not isinstance(getattr(issued, "value", None), str)
                or len(issued.value) < 24
                or not isinstance(getattr(issued, "reference", None), str)
                or not issued.reference
            ):
                raise CredentialAuthorityUnavailable(
                    "ephemeral PJSIP credential authority returned an invalid result"
                )
            self.ledger.complete_session(
                session_id, issued.value, issued.reference
            )
        except Exception:
            self.ledger.fail_session(session_id)
            raise
        return {
            "status": "issued",
            "session_id": session_id,
            "registration_ticket": ticket,
            "credential_reference": issued.reference,
            "temporary_sip_authorization_username": command.extension,
            "temporary_sip_credential": issued.value,
            "sip_uri": f"sip:{command.extension}@{self.sip_domain}",
            "approved_turn_url": SYNTHETIC_TURN_URL,
            "expires_at": _iso(expires_at),
            "campaign": SYNTHETIC_CAMPAIGN,
            "endpoint": command.extension,
            "environment": "STAGING",
            "permitted_call_scope": [SYNTHETIC_DESTINATION],
        }

    def revoke_webrtc(self, command: WebRTCRevokeCommand) -> dict:
        self._require("agent_sync", "agent disable")
        self._require_synthetic(command)
        revoked = self.ledger.revoke_agent(
            command.context.tenant_id, command.user_id, command.extension
        )
        return {
            "status": "revoked",
            "user_id": command.user_id,
            "extension": command.extension,
            "sessions_revoked": revoked,
            "tickets_invalidated": revoked,
            "pjsip_contacts": "middleware_reconciliation_required",
        }
