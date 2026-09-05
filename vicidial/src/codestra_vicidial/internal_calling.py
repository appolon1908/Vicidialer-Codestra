"""Policy-bound internal Asterisk call execution; never accepts dialplan input."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from .ami_gateway import AMIStreamParser, read_private_text, rfc3339
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
    employee_id: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
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

    @classmethod
    def from_file(cls, path: Path, release_sha: str) -> "InternalCallPolicy":
        if not path.is_absolute() or path.is_symlink():
            raise InternalCallError("internal call policy path is unsafe")
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o037:
            raise InternalCallError("internal call policy permissions are unsafe")
        policy = cls.model_validate_json(path.read_text(encoding="utf-8"))
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


class SocketAmiTransport:
    def __init__(self, credential_file: Path) -> None:
        document = json.loads(read_private_text(credential_file, minimum_bytes=20))
        self.host = document.get("host", "127.0.0.1")
        self.port = int(document.get("port", 5038))
        self.username = document["username"]
        self.secret = document["secret"]
        if self.host not in {"127.0.0.1", "localhost"}:
            raise InternalCallError("AMI must remain loopback-only")

    @staticmethod
    def _receive(stream) -> dict[str, str]:
        parser = AMIStreamParser(maximum_buffer_bytes=65536)
        while True:
            data = stream.recv(8192)
            if not data:
                raise InternalCallError("AMI closed before response")
            for message in parser.feed(data):
                if message.response:
                    return {key: values[-1] for key, values in message.fields.items()}

    def action(self, fields: dict[str, str]) -> dict[str, str]:
        with socket.create_connection((self.host, self.port), timeout=5) as stream:
            stream.settimeout(5)
            banner = stream.recv(256)
            if not banner.startswith(b"Asterisk Call Manager/"):
                raise InternalCallError("AMI banner is invalid")
            login = {"Action": "Login", "Username": self.username, "Secret": self.secret, "Events": "off"}
            stream.sendall(("\r\n".join(f"{k}: {v}" for k, v in login.items()) + "\r\n\r\n").encode())
            if self._receive(stream).get("Response") != "Success":
                raise InternalCallError("AMI authentication rejected")
            stream.sendall(("\r\n".join(f"{k}: {v}" for k, v in fields.items()) + "\r\n\r\n").encode())
            result = self._receive(stream)
            if result.get("Response") != "Success":
                raise InternalCallError("bounded AMI action rejected")
            return result


class InternalCallExecutor:
    def __init__(self, policy: InternalCallPolicy, state_path: Path, lifecycle_path: Path,
                 transport: AmiTransport, audit: StateStore) -> None:
        self.policy, self.state_path, self.lifecycle_path, self.transport, self.audit = policy, state_path, lifecycle_path, transport, audit
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(state_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS internal_calls(operation_id TEXT PRIMARY KEY,request_hash TEXT NOT NULL,correlation_id TEXT NOT NULL,authorization_reference TEXT NOT NULL UNIQUE,uniqueid TEXT NOT NULL UNIQUE,state TEXT NOT NULL,hangup_state TEXT,created_at TEXT NOT NULL)")

    def _audit(self, command: OriginateCommand, operation: str, outcome: str, detail: object) -> None:
        self.audit.audit(command.correlation_id, command.subject, operation,
                         command.operation_id, outcome, detail)

    def _authorize(self, command: OriginateCommand) -> None:
        p = self.policy
        for field in ("tenant_id", "subject", "employee_id", "username", "extension", "campaign", "authorization_reference"):
            if getattr(command, field) != getattr(p, field):
                raise PermissionError("internal call identity or authorization mismatch")
        if command.destination != p.destination_alias:
            raise PermissionError("internal destination is not authorized")
        if p.expires_at <= datetime.now(UTC):
            raise PermissionError("internal call authorization expired")

    def originate(self, command: OriginateCommand) -> dict:
        try:
            self._authorize(command)
        except PermissionError:
            self._audit(command, "internal-call.originate", "rejected", {"reason": "policy_mismatch"})
            raise
        digest = __import__("hashlib").sha256(command.model_dump_json().encode()).hexdigest()
        with sqlite3.connect(self.state_path) as db:
            row = db.execute("SELECT request_hash,uniqueid,state,created_at FROM internal_calls WHERE operation_id=?", (command.operation_id,)).fetchone()
            if row:
                if row[0] != digest:
                    raise InternalCallError("operation identity was reused with different content")
                self._audit(command, "internal-call.originate", "duplicate", {"dispatch_state": row[2]})
                return {"status": row[2], "operation_id": command.operation_id, "asterisk_uniqueid": row[1], "created_at": row[3], "duplicate": True}
            consumed = db.execute("SELECT 1 FROM internal_calls WHERE authorization_reference=? LIMIT 1", (command.authorization_reference,)).fetchone()
            if consumed:
                self._audit(command, "internal-call.originate", "rejected", {"reason": "one_call_consumed"})
                raise PermissionError("one-call authorization was already consumed")
            uniqueid = "codestra-" + __import__("hashlib").sha256(command.operation_id.encode()).hexdigest()[:32]
            created = rfc3339(datetime.now(UTC))
            # Commit the immutable intent before the external effect. A crash or
            # timeout remains unknown and is read back; it is never re-originated.
            try:
                db.execute("INSERT INTO internal_calls VALUES(?,?,?,?,?,?,?,?)", (command.operation_id, digest, command.correlation_id, command.authorization_reference, uniqueid, "dispatch_unknown", None, created))
            except sqlite3.IntegrityError as exc:
                self._audit(command, "internal-call.originate", "rejected", {"reason": "one_call_consumed"})
                raise PermissionError("one-call authorization was already consumed") from exc
            db.commit()
        try:
            self.transport.action({"Action": "Originate", "ActionID": command.operation_id,
                "Channel": f"PJSIP/{self.policy.extension}", "Context": self.policy.dialplan_context,
                "Exten": self.policy.destination_extension, "Priority": "1", "Async": "true",
                "ChannelId": uniqueid, "Variable": f"CODESTRA_OPERATION_ID={command.operation_id}"})
        except Exception:
            self._audit(command, "internal-call.originate", "unknown", {"dispatch_state": "dispatch_unknown"})
            raise
        with sqlite3.connect(self.state_path) as db:
            db.execute("UPDATE internal_calls SET state='accepted' WHERE operation_id=? AND state='dispatch_unknown'", (command.operation_id,))
            db.commit()
        self._audit(command, "internal-call.originate", "accepted", {"asterisk_uniqueid": uniqueid})
        return {"status": "accepted", "operation_id": command.operation_id, "asterisk_uniqueid": uniqueid, "created_at": created, "duplicate": False}

    def readback(self, operation_id: str) -> dict:
        with sqlite3.connect(self.state_path) as db:
            row = db.execute("SELECT correlation_id,uniqueid,state,created_at FROM internal_calls WHERE operation_id=?", (operation_id,)).fetchone()
        if not row:
            raise KeyError(operation_id)
        with sqlite3.connect(f"file:{self.lifecycle_path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            call = db.execute("SELECT linkedid,call_id,state,answered_at,ended_at,terminal FROM calls WHERE asterisk_uniqueid=?", (row[1],)).fetchone()
        return {"operation_id": operation_id, "correlation_id": row[0], "dispatch_state": row[2], "asterisk_uniqueid": row[1],
                "linkedid": call["linkedid"] if call else None, "call_id": call["call_id"] if call else None,
                "call_state": call["state"] if call else "unknown", "answered_at": call["answered_at"] if call else None,
                "ended_at": call["ended_at"] if call else None, "terminal": bool(call["terminal"]) if call else False,
                "internal_only": True, "external_dialing": False}

    def hangup(self, operation_id: str) -> dict:
        current = self.readback(operation_id)
        if current["terminal"]:
            self.audit.audit(current["correlation_id"], self.policy.subject,
                             "internal-call.hangup", operation_id,
                             "already_terminal", {"call_state": current["call_state"]})
            return {**current, "hangup": "already_terminal"}
        with sqlite3.connect(self.state_path) as db:
            prior = db.execute("SELECT hangup_state FROM internal_calls WHERE operation_id=?", (operation_id,)).fetchone()
            if prior and prior[0]:
                self.audit.audit(current["correlation_id"], self.policy.subject,
                                 "internal-call.hangup", operation_id,
                                 "duplicate", {"hangup_state": prior[0]})
                return {**current, "hangup": prior[0], "duplicate": True}
        with sqlite3.connect(f"file:{self.lifecycle_path}?mode=ro", uri=True) as db:
            row = db.execute("SELECT ch.channel FROM channels ch JOIN calls c ON c.call_key=ch.call_key WHERE c.asterisk_uniqueid=? AND ch.active=1 ORDER BY ch.updated_at DESC LIMIT 1", (current["asterisk_uniqueid"],)).fetchone()
        if not row or not row[0]:
            self.audit.audit(current["correlation_id"], self.policy.subject,
                             "internal-call.hangup", operation_id, "rejected",
                             {"reason": "active_channel_unavailable"})
            raise InternalCallError("authoritative active channel is unavailable; reconcile before hangup")
        with sqlite3.connect(self.state_path) as db:
            claimed = db.execute("UPDATE internal_calls SET hangup_state='dispatch_unknown' WHERE operation_id=? AND hangup_state IS NULL", (operation_id,)).rowcount
            db.commit()
        if claimed != 1:
            self.audit.audit(current["correlation_id"], self.policy.subject,
                             "internal-call.hangup", operation_id, "duplicate",
                             {"hangup_state": "dispatch_unknown"})
            return {**self.readback(operation_id), "hangup": "dispatch_unknown", "duplicate": True}
        try:
            self.transport.action({"Action": "Hangup", "ActionID": operation_id + ":hangup", "Channel": row[0]})
        except Exception:
            self.audit.audit(current["correlation_id"], self.policy.subject,
                             "internal-call.hangup", operation_id, "unknown",
                             {"hangup_state": "dispatch_unknown"})
            raise
        with sqlite3.connect(self.state_path) as db:
            db.execute("UPDATE internal_calls SET hangup_state='requested' WHERE operation_id=? AND hangup_state='dispatch_unknown'", (operation_id,))
            db.commit()
        self.audit.audit(current["correlation_id"], self.policy.subject,
                         "internal-call.hangup", operation_id, "requested",
                         {"asterisk_uniqueid": current["asterisk_uniqueid"]})
        return {**current, "hangup": "requested", "duplicate": False}
