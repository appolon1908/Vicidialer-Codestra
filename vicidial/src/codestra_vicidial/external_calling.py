"""One-call, policy-bound external PSTN execution through the verified DIDWW dialplan."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .ami_gateway import rfc3339
from .internal_call_transport import digest, load_protected_json
from .state import StateStore


class ExternalCallError(RuntimeError):
    pass


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("recording_requested", mode="before", check_fields=False)
    @classmethod
    def require_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("safety controls require JSON booleans")
        return value


class ExternalCallPolicy(StrictModel):
    schema_version: Literal["1.0"]
    release_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    authorization_reference: str = Field(pattern=r"^CHG-[A-Z0-9-]{10,80}$")
    expires_at: datetime
    campaign: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    destination_e164: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    dialplan_context: Literal["outbound-didww"]
    dial_prefix: Literal["8"]
    trunk: Literal["didww-out"]
    caller_id: str = Field(pattern=r"^[1-9][0-9]{9,14}$")
    max_calls: Literal[1]
    max_connected_seconds: int = Field(ge=5, le=60)
    recording_requested: Literal[False]

    @field_validator("max_calls", mode="before")
    @classmethod
    def require_integer_limit(cls, value):
        if type(value) is not int:
            raise ValueError("call limit requires an integer")
        return value

    @classmethod
    def from_file(cls, path: Path, release_sha: str) -> "ExternalCallPolicy":
        try:
            policy = cls.model_validate(load_protected_json(path))
        except (OSError, ValueError, TypeError, PermissionError):
            raise ExternalCallError("invalid protected external call policy") from None
        if policy.release_sha != release_sha:
            raise ExternalCallError("external call policy release mismatch")
        if policy.expires_at.tzinfo is None or policy.expires_at <= datetime.now(UTC):
            raise ExternalCallError("external call policy is expired")
        return policy


class ExternalOriginateCommand(StrictModel):
    operation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    correlation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
    campaign: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    destination: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    authorization_reference: str = Field(pattern=r"^CHG-[A-Z0-9-]{10,80}$")
    purpose: Literal["controlled_test"]
    recording_requested: Literal[False]


class ExternalAmiTransport(Protocol):
    def verify_external_route(self, policy: ExternalCallPolicy) -> None: ...
    def originate_external(
        self, policy: ExternalCallPolicy, operation_id: str, uniqueid: str
    ) -> None: ...


class ExternalCallExecutor:
    """Execute at most one exact policy-authorized PSTN call.

    The durable intent is committed before AMI dispatch. Any uncertain dispatch blocks
    redial; an operator must reconcile it rather than retrying and risking duplicates.
    """

    def __init__(
        self,
        policy: ExternalCallPolicy,
        state_path: Path,
        transport: ExternalAmiTransport,
        *,
        policy_provider=None,
        enabled=None,
        audit_path: Path | None = None,
    ) -> None:
        if not state_path.is_absolute():
            raise ExternalCallError("external call state path must be absolute")
        self.policy = policy
        self.state_path = state_path
        self.transport = transport
        self.policy_provider = policy_provider or (lambda: self.policy)
        self.enabled = enabled or (lambda: False)
        self.audit_path = audit_path or state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(state_path)) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS external_calls("
                "operation_id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,"
                "correlation_id TEXT NOT NULL,campaign TEXT NOT NULL,"
                "destination_hash TEXT NOT NULL,uniqueid TEXT NOT NULL UNIQUE,"
                "state TEXT NOT NULL,created_at TEXT NOT NULL,policy_hash TEXT NOT NULL)"
            )
            db.commit()
        audit = StateStore(self.audit_path)
        audit.db.close()

    def _live_policy(self) -> ExternalCallPolicy:
        policy = self.policy_provider()
        if policy.expires_at.tzinfo is None or policy.expires_at <= datetime.now(UTC):
            raise PermissionError("external call authorization expired")
        if digest(policy.model_dump(mode="json")) != digest(
            self.policy.model_dump(mode="json")
        ):
            raise PermissionError("external call policy changed from startup authorization")
        return policy

    def _authorize(self, command: ExternalOriginateCommand, policy: ExternalCallPolicy) -> None:
        if command.campaign != policy.campaign:
            raise PermissionError("campaign is not authorized")
        if command.destination != policy.destination_e164:
            raise PermissionError("destination is not authorized")
        if command.authorization_reference != policy.authorization_reference:
            raise PermissionError("external call authorization mismatch")
        if command.recording_requested is not False:
            raise PermissionError("recording is not authorized")

    def _audit(self, command: ExternalOriginateCommand, actor: str, outcome: str) -> None:
        store = StateStore.__new__(StateStore)
        store.db = sqlite3.connect(
            self.audit_path.as_uri() + "?mode=rw", uri=True, timeout=5
        )
        try:
            store.audit(
                command.correlation_id,
                actor,
                "external-call.originate",
                command.destination,
                outcome,
                {
                    "operation_id": command.operation_id,
                    "campaign": command.campaign,
                    "authorization_reference": command.authorization_reference,
                    "outcome": outcome,
                },
            )
        finally:
            store.db.close()

    def originate(
        self, command: ExternalOriginateCommand, actor: str = "codestra-middleware"
    ) -> dict:
        self._audit(command, actor, "requested")
        try:
            result = self._originate(command)
        except PermissionError:
            self._audit(command, actor, "denied")
            raise
        except Exception as exc:
            self._audit(command, actor, "failed_or_uncertain")
            if isinstance(exc, ExternalCallError):
                raise
            raise ExternalCallError(
                "external call outcome requires reconciliation"
            ) from exc
        self._audit(command, actor, "duplicate" if result["duplicate"] else "accepted")
        return result

    def _originate(self, command: ExternalOriginateCommand) -> dict:
        policy = self._live_policy()
        self._authorize(command, policy)
        if self.enabled() is not True:
            raise PermissionError("production external dialing is disabled")

        request_hash = digest(command.model_dump(mode="json"))
        policy_hash = digest(policy.model_dump(mode="json"))
        destination_hash = digest(command.destination)
        uniqueid = "codestra-pstn-" + __import__("hashlib").sha256(
            command.operation_id.encode()
        ).hexdigest()[:28]
        created = rfc3339(datetime.now(UTC))

        with closing(sqlite3.connect(self.state_path, timeout=5)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT request_hash,state,uniqueid,created_at,policy_hash "
                "FROM external_calls WHERE operation_id=?",
                (command.operation_id,),
            ).fetchone()
            if row:
                if row[0] != request_hash or row[4] != policy_hash:
                    raise ExternalCallError(
                        "operation identity was reused with different content or policy"
                    )
                return {
                    "status": row[1],
                    "operation_id": command.operation_id,
                    "asterisk_uniqueid": row[2],
                    "created_at": row[3],
                    "duplicate": True,
                }
            if db.execute("SELECT count(*) FROM external_calls").fetchone()[0] >= policy.max_calls:
                raise PermissionError(
                    "one-call authorization already consumed; reconciliation required"
                )
            self.transport.verify_external_route(policy)
            db.execute(
                "INSERT INTO external_calls VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    command.operation_id,
                    request_hash,
                    command.correlation_id,
                    command.campaign,
                    destination_hash,
                    uniqueid,
                    "dispatch_unknown",
                    created,
                    policy_hash,
                ),
            )
            db.commit()

        self.transport.originate_external(policy, command.operation_id, uniqueid)
        with closing(sqlite3.connect(self.state_path)) as db:
            db.execute(
                "UPDATE external_calls SET state='submitted_unknown' "
                "WHERE operation_id=? AND state='dispatch_unknown'",
                (command.operation_id,),
            )
            db.commit()
        return {
            "status": "submitted_unknown",
            "operation_id": command.operation_id,
            "asterisk_uniqueid": uniqueid,
            "created_at": created,
            "duplicate": False,
        }
