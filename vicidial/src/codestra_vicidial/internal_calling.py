"""Policy-bound internal Asterisk call execution; never accepts dialplan input."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .ami_gateway import rfc3339
from .internal_call_transport import digest, load_protected_json
from .state import StateStore


class InternalCallError(RuntimeError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InternalCallPolicy(StrictModel):
    schema_version: Literal["1.0"]
    release_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    authorization_reference: str = Field(pattern=r"^CHG-[A-Z0-9-]{10,80}$")
    expires_at: datetime
    tenant_id: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    subject: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    employee_id: str = Field(
        min_length=3, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    )
    username: Literal["appolon"]
    extension: Literal["6901"]
    campaign: Literal["TEST_SYN"]
    destination_alias: Literal["internal:TEST_ECHO"]
    destination_extension: Literal["6000"]
    dialplan_context: Literal["codestra-internal-test"]
    internal_only: Literal[True]
    external_dialing: Literal[False]
    recording: Literal[False]
    max_calls: Literal[1]
    route_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @classmethod
    def from_file(cls, path: Path, release_sha: str) -> InternalCallPolicy:
        try:
            policy = cls.model_validate(load_protected_json(path))
        except (ValueError, TypeError):
            raise InternalCallError("invalid protected internal call policy") from None
        if policy.release_sha != release_sha:
            raise InternalCallError("internal call policy release mismatch")
        if policy.expires_at.tzinfo is None or policy.expires_at <= datetime.now(UTC):
            raise InternalCallError("internal call policy is expired")
        return policy


class OriginateCommand(StrictModel):
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    correlation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    tenant_id: str
    subject: str
    employee_id: str
    username: Literal["appolon"]
    extension: Literal["6901"]
    campaign: Literal["TEST_SYN"]
    destination: Literal["internal:TEST_ECHO"]
    internal_only: Literal[True]
    external_dialing: Literal[False]
    recording_requested: Literal[False]
    authorization_reference: str


class AmiTransport(Protocol):
    def action(self, fields: dict[str, str]) -> dict[str, str]: ...
    def verify_route(self, policy: InternalCallPolicy) -> None: ...


class InternalCallExecutor:
    def __init__(
        self,
        policy: InternalCallPolicy,
        state_path: Path,
        lifecycle_path: Path,
        transport: AmiTransport,
        audit: StateStore | None = None,
        *,
        policy_provider=None,
        identity_ready=None,
        enabled=None,
        audit_path=None,
    ) -> None:
        if (
            not state_path.is_absolute()
            or not lifecycle_path.is_absolute()
            or state_path.resolve() == lifecycle_path.resolve()
        ):
            raise InternalCallError(
                "execution and lifecycle paths must be absolute and distinct"
            )
        self.policy, self.state_path, self.lifecycle_path, self.transport = (
            policy,
            state_path,
            lifecycle_path,
            transport,
        )
        self.policy_provider = policy_provider or (lambda: self.policy)
        self.identity_ready = identity_ready or (lambda: False)
        self.enabled = enabled or (lambda: False)
        self.audit = audit
        audit_file = (
            Path(audit.db.execute("PRAGMA database_list").fetchone()[2])
            if audit
            else state_path
        )
        self.audit_path = audit_path or audit_file
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(state_path)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS internal_calls(operation_id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,correlation_id TEXT NOT NULL,uniqueid TEXT NOT NULL UNIQUE,state TEXT NOT NULL,hangup_state TEXT,created_at TEXT NOT NULL,policy_hash TEXT NOT NULL)"
            )
            columns = {r[1] for r in db.execute("PRAGMA table_info(internal_calls)")}
            if "policy_hash" not in columns:
                raise InternalCallError(
                    "unbound legacy call intents require administrator reconciliation"
                )
            db.commit()
        # Initialize schema/WAL once, before any concurrent request can audit.
        initialized_audit = StateStore(self.audit_path)
        initialized_audit.db.close()

    def _policy(self):
        policy = self.policy_provider()
        if policy.expires_at.tzinfo is None or policy.expires_at <= datetime.now(UTC):
            raise PermissionError("internal call authorization expired")
        if policy.release_sha != self.policy.release_sha:
            raise PermissionError("internal call policy release changed")
        return policy

    def _authorize(self, command, policy):
        for field in (
            "tenant_id",
            "subject",
            "employee_id",
            "username",
            "extension",
            "campaign",
            "authorization_reference",
        ):
            if getattr(command, field) != getattr(policy, field):
                raise PermissionError(
                    "internal call identity or authorization mismatch"
                )
        if command.destination != policy.destination_alias:
            raise PermissionError("internal destination is not authorized")

    def _audit(self, operation, operation_id, correlation_id, actor, outcome):
        # Reuse StateStore.audit without re-running its schema initializer or
        # changing journal mode while another request holds a transaction.
        store = StateStore.__new__(StateStore)
        store.db = sqlite3.connect(self.audit_path.as_uri() + "?mode=rw", uri=True, timeout=5)
        try:
            store.audit(
                correlation_id,
                actor,
                "internal-call." + operation,
                operation_id,
                outcome,
                {"operation_id": operation_id, "outcome": outcome},
            )
        finally:
            store.db.close()

    def _execute(self, operation, operation_id, correlation_id, actor, handler):
        self._audit(operation, operation_id, correlation_id, actor, "requested")
        try:
            result = handler()
        except PermissionError:
            self._audit(operation, operation_id, correlation_id, actor, "denied")
            raise
        except Exception as exc:
            self._audit(
                operation, operation_id, correlation_id, actor, "failed_or_uncertain"
            )
            if isinstance(exc, InternalCallError):
                raise
            raise InternalCallError(
                "internal call outcome requires reconciliation"
            ) from exc
        self._audit(
            operation,
            operation_id,
            correlation_id,
            actor,
            "duplicate"
            if result.get("duplicate")
            else ("requested" if operation == "hangup" else "accepted"),
        )
        return result

    def originate(self, command: OriginateCommand, actor="codestra-middleware") -> dict:
        return self._execute(
            "originate",
            command.operation_id,
            command.correlation_id,
            actor,
            lambda: self._originate(command),
        )

    def _originate(self, command):
        policy = self._policy()
        self._authorize(command, policy)
        if not self.enabled():
            raise PermissionError("internal call originate disabled")
        request_hash = digest(command.model_dump(mode="json"))
        policy_hash = digest(policy.model_dump(mode="json"))
        with closing(sqlite3.connect(self.state_path, timeout=5)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT request_hash,uniqueid,state,created_at,policy_hash FROM internal_calls WHERE operation_id=?",
                (command.operation_id,),
            ).fetchone()
            if row:
                if row[0] != request_hash or row[4] != policy_hash:
                    raise InternalCallError(
                        "operation identity was reused with different content or policy"
                    )
                return {
                    "status": row[2],
                    "operation_id": command.operation_id,
                    "asterisk_uniqueid": row[1],
                    "created_at": row[3],
                    "duplicate": True,
                }
            # One operation for the lifetime of this execution store. A new
            # authorization cannot conceal an unresolved call by changing policy.
            if (
                db.execute("SELECT count(*) FROM internal_calls").fetchone()[0]
                >= policy.max_calls
            ):
                raise PermissionError(
                    "one-call authorization already consumed; administrator reconciliation required"
                )
            if not self.identity_ready():
                raise PermissionError(
                    "live appolon/6901 activation and campaign binding not verified"
                )
            self.transport.verify_route(policy)
            LifecycleEvidence(self.lifecycle_path).read(
                "schema-readiness-probe", policy
            )
            uniqueid = (
                "codestra-"
                + __import__("hashlib")
                .sha256(command.operation_id.encode())
                .hexdigest()[:32]
            )
            created = rfc3339(datetime.now(UTC))
            db.execute(
                "INSERT INTO internal_calls VALUES(?,?,?,?,?,?,?,?)",
                (
                    command.operation_id,
                    request_hash,
                    command.correlation_id,
                    uniqueid,
                    "dispatch_unknown",
                    None,
                    created,
                    policy_hash,
                ),
            )
            db.commit()
        self.transport.action(
            {
                "Action": "Originate",
                "ActionID": command.operation_id,
                "Channel": "PJSIP/6901",
                "Context": policy.dialplan_context,
                "Exten": "6000",
                "Priority": "1",
                "Async": "true",
                "ChannelId": uniqueid,
                "Variable": f"CODESTRA_OPERATION_ID={command.operation_id}",
            }
        )
        with closing(sqlite3.connect(self.state_path)) as db:
            db.execute(
                "UPDATE internal_calls SET state='accepted' WHERE operation_id=? AND state='dispatch_unknown'",
                (command.operation_id,),
            )
            db.commit()
        return {
            "status": "accepted",
            "operation_id": command.operation_id,
            "asterisk_uniqueid": uniqueid,
            "created_at": created,
            "duplicate": False,
        }

    def readback(self, operation_id: str) -> dict:
        policy = self._policy()
        with closing(sqlite3.connect(self.state_path)) as db:
            row = db.execute(
                "SELECT correlation_id,uniqueid,state,created_at,policy_hash FROM internal_calls WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        if not row:
            raise KeyError(operation_id)
        if row[4] != digest(policy.model_dump(mode="json")):
            raise PermissionError("operation does not belong to current exact policy")
        evidence = LifecycleEvidence(self.lifecycle_path).read(row[1], policy)
        return {
            "operation_id": operation_id,
            "correlation_id": row[0],
            "dispatch_state": row[2],
            "asterisk_uniqueid": row[1],
            "linkedid": evidence["linkedid"] if evidence else None,
            "call_id": evidence["gateway_call_id"] if evidence else None,
            "call_state": evidence["state"] if evidence else "unknown",
            "answered_at": evidence["answered_at"] if evidence else None,
            "ended_at": evidence["ended_at"] if evidence else None,
            "terminal": bool(evidence and evidence["terminal"]),
            "evidence": evidence,
            "internal_only": True,
            "external_dialing": False,
        }

    def hangup(self, operation_id: str, actor="codestra-middleware") -> dict:
        # Resolve correlation only from durable intent, not from a caller body.
        with closing(sqlite3.connect(self.state_path)) as db:
            row = db.execute(
                "SELECT correlation_id FROM internal_calls WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        return self._execute(
            "hangup",
            operation_id,
            row[0] if row else operation_id,
            actor,
            lambda: self._hangup(operation_id),
        )

    def _hangup(self, operation_id):
        current = self.readback(operation_id)
        if current["terminal"]:
            return {**current, "hangup": "already_terminal"}
        with closing(sqlite3.connect(self.state_path, timeout=5)) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT hangup_state FROM internal_calls WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if prior and prior[0]:
                return {**current, "hangup": prior[0], "duplicate": True}
            with closing(
                sqlite3.connect(self.lifecycle_path.as_uri() + "?mode=ro", uri=True)
            ) as evidence:
                rows = evidence.execute(
                    "SELECT ch.channel FROM channels ch JOIN calls c ON c.call_key=ch.call_key WHERE c.asterisk_uniqueid=? AND ch.uniqueid=? AND ch.active=1",
                    (current["asterisk_uniqueid"], current["asterisk_uniqueid"]),
                ).fetchall()
            if len(rows) != 1 or not __import__("re").fullmatch(
                r"PJSIP/6901-[a-fA-F0-9]+", rows[0][0] or ""
            ):
                raise InternalCallError(
                    "authoritative active channel is unavailable; reconcile before hangup"
                )
            claimed = db.execute(
                "UPDATE internal_calls SET hangup_state='dispatch_unknown' WHERE operation_id=? AND hangup_state IS NULL",
                (operation_id,),
            ).rowcount
            db.commit()
            if claimed != 1:
                raise InternalCallError("hangup claim was not acquired")
        self.transport.action(
            {
                "Action": "Hangup",
                "ActionID": operation_id + ":hangup",
                "Channel": current["asterisk_uniqueid"],
            }
        )
        with closing(sqlite3.connect(self.state_path)) as db:
            db.execute(
                "UPDATE internal_calls SET hangup_state='requested' WHERE operation_id=? AND hangup_state='dispatch_unknown'",
                (operation_id,),
            )
            db.commit()
        return {**current, "hangup": "requested", "duplicate": False}


class LifecycleEvidence:
    """Read the existing AMI gateway's durable database without modifying it."""

    def __init__(self, path: Path):
        self.path = path

    def read(self, uniqueid: str, policy: InternalCallPolicy) -> dict | None:
        try:
            with closing(
                sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
            ) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(
                    "SELECT c.* FROM calls c JOIN channels ch ON ch.call_key=c.call_key "
                    "WHERE ch.uniqueid=? AND c.extension='6901'",
                    (uniqueid,),
                ).fetchall()
                if not rows:
                    return None
                if len(rows) != 1:
                    raise PermissionError("ambiguous Asterisk call evidence")
                row = rows[0]
                binding = json.loads(row["binding_json"])
                if (
                    any(
                        binding.get(k) != v
                        for k, v in {
                            "agent_id": "appolon",
                            "extension": "6901",
                            "campaign_id": "TEST_SYN",
                            "tenant_id": policy.tenant_id,
                            "keycloak_subject": policy.subject,
                        }.items()
                    )
                    or row["asterisk_uniqueid"] != uniqueid
                ):
                    raise PermissionError("Asterisk call binding mismatch")
                terminal = row["terminal"] == 1
                events = db.execute(
                    "SELECT event_id,event_type,envelope_json FROM event_outbox "
                    "WHERE call_key=? ORDER BY sequence DESC LIMIT 1",
                    (row["call_key"],),
                ).fetchall()
                if terminal and (
                    not row["ended_at"]
                    or not events
                    or events[0]["event_type"].rsplit(".", 1)[-1]
                    not in {"completed", "failed", "missed"}
                ):
                    raise InternalCallError("terminal AMI event evidence missing")
                terminal_details = None
                if terminal:
                    envelope = json.loads(events[0]["envelope_json"])
                    payload = envelope.get("payload", {})
                    expected = {
                        "call_id": row["call_id"],
                        "asterisk_uniqueid": uniqueid,
                        "linkedid": row["linkedid"],
                        "agent_id": "appolon",
                        "extension": "6901",
                        "campaign_id": "TEST_SYN",
                        "keycloak_subject": policy.subject,
                    }
                    if (
                        envelope.get("tenant_id") != policy.tenant_id
                        or envelope.get("event_id") != events[0]["event_id"]
                        or envelope.get("event_type") != events[0]["event_type"]
                        or any(payload.get(k) != v for k, v in expected.items())
                    ):
                        raise PermissionError(
                            "terminal Asterisk event binding mismatch"
                        )
                    terminal_details = {
                        k: payload.get(k)
                        for k in ("duration", "talk_duration", "hangup_cause_code")
                    }
                return {
                    "asterisk_uniqueid": uniqueid,
                    "linkedid": row["linkedid"],
                    "gateway_call_id": row["call_id"],
                    "state": row["state"],
                    "answered": row["answered"] == 1,
                    "terminal": terminal,
                    "started_at": row["created_at"],
                    "answered_at": row["answered_at"],
                    "ended_at": row["ended_at"],
                    "terminal_event_id": events[0]["event_id"] if terminal else None,
                    "terminal_details": terminal_details,
                }
        except PermissionError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise InternalCallError("AMI evidence store unavailable") from exc
