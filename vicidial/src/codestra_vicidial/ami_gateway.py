from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import stat
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


GATEWAY_VERSION = "1.0"
MIDDLEWARE_EVENT_PREFIX = "codestra.vicidial.call.lifecycle."
SUPPORTED_AMI_EVENTS = frozenset(
    {
        "AgentCalled",
        "AgentComplete",
        "AgentConnect",
        "AgentRingNoAnswer",
        "AttendedTransfer",
        "BlindTransfer",
        "BridgeEnter",
        "DialBegin",
        "DialEnd",
        "Hangup",
        "Hold",
        "Newchannel",
        "Newstate",
        "Unhold",
    }
)
TERMINAL_STATES = frozenset({"completed", "failed", "missed", "transferred"})
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")
_SAFE_EVENT_ID = re.compile(r"^[A-Za-z0-9._-]+$")
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")
_CHANNEL_ENDPOINT = re.compile(
    r"^(?:Local|SIP|PJSIP|IAX2)/([^@;/-]+)",
    re.IGNORECASE,
)
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off", "disabled"})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})


class AMIProtocolError(RuntimeError):
    """AMI stream or authentication data is malformed."""


class GatewayConfigurationError(RuntimeError):
    """The local event gateway configuration is unsafe or incomplete."""


class EventBindingError(RuntimeError):
    """An AMI event cannot be bound to one authoritative agent/campaign."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_bool(value: str | None, *, name: str) -> bool:
    normalized = (value or "").strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise GatewayConfigurationError(f"{name} must be an explicit boolean")


def require_false(source: Mapping[str, str], *names: str) -> None:
    enabled = [
        name
        for name in names
        if parse_bool(source.get(name), name=name)
    ]
    if enabled:
        raise GatewayConfigurationError(
            "AMI event observation cannot start while effect flags are enabled: "
            + ", ".join(sorted(enabled))
        )


def read_private_text(path: str | Path, *, minimum_bytes: int = 1) -> str:
    target = Path(path)
    if not target.is_absolute():
        raise GatewayConfigurationError("secret paths must be absolute")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise GatewayConfigurationError(
            f"protected file cannot be opened: {target}"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise GatewayConfigurationError(
                f"protected path is not a regular file: {target}"
            )
        if info.st_uid not in {0, os.geteuid()}:
            raise GatewayConfigurationError(
                f"protected file owner is not trusted: {target}"
            )
        if info.st_mode & 0o077:
            raise GatewayConfigurationError(
                f"protected file must not be group/world accessible: {target}"
            )
        limit = max(65536, minimum_bytes + 1)
        chunks: list[bytes] = []
        total = 0
        while total <= limit:
            chunk = os.read(descriptor, min(16384, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        raw = b"".join(chunks)
        if len(raw) > limit:
            raise GatewayConfigurationError(
                f"protected file exceeds its bounded size: {target}"
            )
    finally:
        os.close(descriptor)
    if len(raw) < minimum_bytes:
        raise GatewayConfigurationError(
            f"protected file is shorter than {minimum_bytes} bytes"
        )
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise GatewayConfigurationError(
            f"protected file is not UTF-8 text: {target}"
        ) from exc
    if len(value.encode("utf-8")) < minimum_bytes:
        raise GatewayConfigurationError(
            f"protected value is shorter than {minimum_bytes} bytes"
        )
    return value


@dataclass(frozen=True, slots=True)
class AMIMessage:
    fields: Mapping[str, tuple[str, ...]]

    @property
    def event(self) -> str:
        return self.get("Event")

    @property
    def response(self) -> str:
        return self.get("Response")

    def get(self, key: str, default: str = "") -> str:
        values = self.fields.get(key)
        if not values:
            return default
        return values[-1]

    def all(self, key: str) -> tuple[str, ...]:
        return self.fields.get(key, ())

    def canonical_fingerprint(self) -> str:
        normalized = {
            key: list(values)
            for key, values in sorted(self.fields.items())
            if key not in {"Privilege", "Timestamp"}
        }
        encoded = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class AMIStreamParser:
    """Incrementally parse CRLF-delimited AMI messages."""

    def __init__(self, *, maximum_buffer_bytes: int = 1_048_576) -> None:
        self.maximum_buffer_bytes = maximum_buffer_bytes
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[AMIMessage]:
        if b"\x00" in data:
            raise AMIProtocolError("AMI stream contains a NUL byte")
        self._buffer.extend(data)
        if len(self._buffer) > self.maximum_buffer_bytes:
            raise AMIProtocolError("AMI receive buffer exceeded its limit")
        messages: list[AMIMessage] = []
        delimiter = b"\r\n\r\n"
        while True:
            offset = self._buffer.find(delimiter)
            if offset < 0:
                break
            raw = bytes(self._buffer[:offset])
            del self._buffer[: offset + len(delimiter)]
            if not raw:
                continue
            messages.append(parse_ami_block(raw))
        return messages


def parse_ami_block(raw: bytes) -> AMIMessage:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AMIProtocolError("AMI message is not UTF-8/ASCII") from exc
    values: dict[str, list[str]] = {}
    for line in text.split("\r\n"):
        if not line:
            continue
        if ": " not in line:
            raise AMIProtocolError("AMI header line is malformed")
        key, value = line.split(": ", 1)
        if not key or len(key) > 128 or len(value) > 8192:
            raise AMIProtocolError("AMI header is outside bounded limits")
        values.setdefault(key, []).append(value)
    if not values:
        raise AMIProtocolError("AMI message contains no headers")
    return AMIMessage({key: tuple(items) for key, items in values.items()})


class AgentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    extension: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=128)
    business_unit_id: str = Field(min_length=1, max_length=128)
    campaign_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    keycloak_subject: str = Field(min_length=1, max_length=128)
    default_direction: Literal["inbound", "outbound"] = "inbound"
    queue_aliases: tuple[str, ...] = ()
    endpoint_aliases: tuple[str, ...] = ()
    country_code: str = Field(default="1", pattern=r"^[1-9][0-9]{0,3}$")

    @field_validator(
        "extension",
        "tenant_id",
        "business_unit_id",
        "campaign_id",
        "agent_id",
        "keycloak_subject",
    )
    @classmethod
    def identifiers_are_safe(cls, value: str) -> str:
        normalized = value.strip()
        if not _SAFE_ID.fullmatch(normalized):
            raise ValueError("binding identifiers must use the safe character set")
        return normalized

    @field_validator("queue_aliases", "endpoint_aliases")
    @classmethod
    def aliases_are_safe(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for item in value:
            candidate = item.strip()
            if (
                not candidate
                or len(candidate) > 128
                or any(ord(char) < 32 for char in candidate)
            ):
                raise ValueError("binding aliases must be bounded printable strings")
            normalized.append(candidate)
        return tuple(normalized)


class AgentDirectoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    agents: tuple[AgentBinding, ...] = Field(min_length=1)


class AgentDirectory:
    """Resolve AMI channel/interface evidence to one immutable agent binding."""

    CHANNEL_FIELDS = (
        "Interface",
        "DestChannel",
        "Channel",
        "TransfererChannel",
        "TransfereeChannel",
        "OrigTransfererChannel",
        "SecondTransfererChannel",
    )
    DIRECT_FIELDS = (
        "Extension",
        "Exten",
        "DestExten",
        "MemberName",
    )

    def __init__(self, agents: Iterable[AgentBinding]) -> None:
        entries = tuple(agents)
        if not entries:
            raise GatewayConfigurationError(
                "agent directory must contain at least one binding"
            )
        by_extension: dict[str, AgentBinding] = {}
        by_alias: dict[str, AgentBinding] = {}
        for entry in entries:
            if entry.extension in by_extension:
                raise GatewayConfigurationError(
                    f"duplicate agent extension: {entry.extension}"
                )
            by_extension[entry.extension] = entry
            for alias in entry.endpoint_aliases:
                key = alias.lower()
                prior = by_alias.get(key)
                if prior and prior.extension != entry.extension:
                    raise GatewayConfigurationError(
                        f"endpoint alias belongs to multiple agents: {alias}"
                    )
                by_alias[key] = entry
        self._by_extension = by_extension
        self._by_alias = by_alias

    @classmethod
    def from_file(cls, path: str | Path) -> "AgentDirectory":
        target = Path(path)
        if not target.is_absolute():
            raise GatewayConfigurationError(
                "agent directory path must be absolute"
            )
        try:
            raw = read_private_text(target, minimum_bytes=2)
            document = AgentDirectoryDocument.model_validate_json(raw)
        except (OSError, UnicodeError, ValidationError) as exc:
            raise GatewayConfigurationError(
                "agent directory cannot be loaded"
            ) from exc
        return cls(document.agents)

    def by_extension(self, extension: str) -> AgentBinding | None:
        return self._by_extension.get(extension)

    def resolve(self, message: AMIMessage) -> AgentBinding | None:
        matches: dict[str, AgentBinding] = {}
        for field in self.CHANNEL_FIELDS:
            raw = message.get(field)
            if not raw:
                continue
            alias = self._by_alias.get(raw.lower())
            if alias:
                matches[alias.extension] = alias
            endpoint = channel_endpoint(raw)
            if endpoint and endpoint in self._by_extension:
                binding = self._by_extension[endpoint]
                matches[binding.extension] = binding
        for field in self.DIRECT_FIELDS:
            raw = message.get(field).strip()
            if raw in self._by_extension:
                binding = self._by_extension[raw]
                matches[binding.extension] = binding
        if len(matches) > 1:
            raise EventBindingError(
                "AMI event resolves to more than one agent extension"
            )
        return next(iter(matches.values()), None)

    @staticmethod
    def validate_queue(message: AMIMessage, binding: AgentBinding) -> None:
        queue = message.get("Queue").strip()
        if queue and binding.queue_aliases and queue not in binding.queue_aliases:
            raise EventBindingError(
                "AMI queue does not belong to the bound campaign"
            )


def channel_endpoint(value: str) -> str | None:
    match = _CHANNEL_ENDPOINT.match(value.strip())
    if not match:
        return None
    return match.group(1)


def event_time(message: AMIMessage, *, fallback: datetime | None = None) -> datetime:
    raw = message.get("Timestamp").strip()
    if raw:
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        except (OverflowError, ValueError):
            pass
    return fallback or utc_now()


def linked_id(message: AMIMessage) -> str:
    for key in (
        "Linkedid",
        "DestLinkedid",
        "TransfererLinkedid",
        "TransfereeLinkedid",
        "OrigTransfererLinkedid",
        "SecondTransfererLinkedid",
    ):
        value = message.get(key).strip()
        if value:
            return safe_asterisk_id(value)
    return ""


def unique_id(message: AMIMessage) -> str:
    for key in (
        "Uniqueid",
        "DestUniqueid",
        "TransfererUniqueid",
        "TransfereeUniqueid",
        "OrigTransfererUniqueid",
        "SecondTransfererUniqueid",
    ):
        value = message.get(key).strip()
        if value:
            return safe_asterisk_id(value)
    return ""


def safe_asterisk_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        return ""
    if _SAFE_ID.fullmatch(normalized):
        return normalized
    return "ast-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def normalize_phone(value: str, country_code: str) -> str | None:
    candidate = (value or "").strip()
    if not candidate or candidate.lower() in {"unknown", "anonymous", "restricted"}:
        return None
    if candidate.startswith("+"):
        return candidate if _E164.fullmatch(candidate) else None
    digits = re.sub(r"\D", "", candidate)
    if not digits:
        return None
    if len(digits) == 10:
        digits = country_code + digits
    elif len(digits) < 8:
        return None
    normalized = "+" + digits
    return normalized if _E164.fullmatch(normalized) else None


@dataclass(slots=True)
class CallState:
    call_key: str
    linkedid: str
    call_id: str
    binding: AgentBinding
    direction: Literal["inbound", "outbound"]
    asterisk_uniqueid: str
    sequence: int
    state: str
    answered: bool
    terminal: bool
    caller_number: str | None
    destination_number: str | None
    created_at: str
    answered_at: str | None


class LifecycleStore:
    """Durable per-agent call state and idempotent outbound event queue."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            raise GatewayConfigurationError(
                "gateway state database path must be absolute"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialize()
        os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=30,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS calls (
                    call_key TEXT PRIMARY KEY,
                    linkedid TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    call_id TEXT NOT NULL UNIQUE,
                    binding_json TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    asterisk_uniqueid TEXT NOT NULL UNIQUE,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL,
                    answered INTEGER NOT NULL DEFAULT 0,
                    terminal INTEGER NOT NULL DEFAULT 0,
                    caller_number TEXT,
                    destination_number TEXT,
                    created_at TEXT NOT NULL,
                    answered_at TEXT,
                    ended_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS channels (
                    call_key TEXT NOT NULL,
                    linkedid TEXT NOT NULL,
                    uniqueid TEXT NOT NULL,
                    channel TEXT,
                    active INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (call_key, uniqueid),
                    FOREIGN KEY (call_key) REFERENCES calls(call_key)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS seen_ami (
                    fingerprint TEXT PRIMARY KEY,
                    event_name TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS event_outbox (
                    event_id TEXT PRIMARY KEY,
                    call_key TEXT NOT NULL,
                    linkedid TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_until REAL,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    UNIQUE (call_key, sequence),
                    FOREIGN KEY (call_key) REFERENCES calls(call_key)
                        ON DELETE RESTRICT
                );

                CREATE TABLE IF NOT EXISTS rejected_ami (
                    fingerprint TEXT PRIMARY KEY,
                    event_name TEXT NOT NULL,
                    linkedid_hash TEXT,
                    reason TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS calls_linkedid_idx
                    ON calls(linkedid, terminal, updated_at);
                CREATE INDEX IF NOT EXISTS calls_agent_idx
                    ON calls(linkedid, extension, terminal, updated_at);
                CREATE INDEX IF NOT EXISTS event_outbox_due_idx
                    ON event_outbox(status, next_attempt_at, lease_until);
                CREATE INDEX IF NOT EXISTS channels_active_idx
                    ON channels(call_key, active);
                CREATE INDEX IF NOT EXISTS channels_uniqueid_idx
                    ON channels(uniqueid, active);
                """
            )

    def process(
        self,
        message: AMIMessage,
        directory: AgentDirectory,
        *,
        observed_at: datetime | None = None,
    ) -> list[dict[str, Any]]:
        name = message.event
        if name not in SUPPORTED_AMI_EVENTS:
            return []
        fingerprint = message.canonical_fingerprint()
        occurred = event_time(message, fallback=observed_at)
        now_text = rfc3339(observed_at or utc_now())
        linkedid_value = linked_id(message)
        if not linkedid_value:
            return []

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                inserted = conn.execute(
                    """
                    INSERT OR IGNORE INTO seen_ami(
                        fingerprint, event_name, observed_at
                    ) VALUES(?,?,?)
                    """,
                    (fingerprint, name, now_text),
                ).rowcount
                if not inserted:
                    conn.commit()
                    return []

                try:
                    binding = directory.resolve(message)
                    state = self._select_call(
                        conn,
                        linkedid_value=linkedid_value,
                        message=message,
                        binding=binding,
                    )
                    if state and binding and (
                        state.binding.extension != binding.extension
                    ):
                        raise EventBindingError(
                            "linked call attempt cannot change its agent binding"
                        )
                    if binding is None and state is not None:
                        binding = state.binding
                    if binding is None:
                        conn.commit()
                        return []
                    directory.validate_queue(message, binding)
                except EventBindingError as exc:
                    self._record_rejection(
                        conn,
                        fingerprint=fingerprint,
                        event_name=name,
                        linkedid_value=linkedid_value,
                        reason=str(exc),
                        observed_at=now_text,
                    )
                    conn.commit()
                    return []

                if state is None:
                    if not self._can_establish_attempt(message):
                        conn.commit()
                        return []
                    state = self._create_call(
                        conn,
                        linkedid_value=linkedid_value,
                        message=message,
                        binding=binding,
                        occurred=occurred,
                    )

                self._record_channels(conn, state, message, now_text)
                state = self._refresh_numbers(conn, state, message)
                emitted: list[dict[str, Any]] = []
                if state.sequence == 0:
                    emitted.append(
                        self._emit(
                            conn,
                            state,
                            "created",
                            message,
                            occurred,
                        )
                    )
                    state = self._load_call(conn, state.call_key)
                    assert state is not None

                emitted.extend(
                    self._apply_event(
                        conn,
                        state,
                        message,
                        occurred,
                    )
                )
                conn.commit()
                return emitted
            except Exception:
                conn.rollback()
                raise

    @staticmethod
    def _can_establish_attempt(message: AMIMessage) -> bool:
        if message.event in {
            "AgentCalled",
            "AgentConnect",
            "BridgeEnter",
            "DialBegin",
            "Newstate",
        }:
            return True
        return False

    def _select_call(
        self,
        conn: sqlite3.Connection,
        *,
        linkedid_value: str,
        message: AMIMessage,
        binding: AgentBinding | None,
    ) -> CallState | None:
        channel_ids = [
            identifier
            for identifier in (
                safe_asterisk_id(message.get("Uniqueid")),
                safe_asterisk_id(message.get("DestUniqueid")),
                safe_asterisk_id(message.get("TransfererUniqueid")),
                safe_asterisk_id(message.get("TransfereeUniqueid")),
                safe_asterisk_id(message.get("OrigTransfererUniqueid")),
                safe_asterisk_id(message.get("SecondTransfererUniqueid")),
            )
            if identifier
        ]
        if channel_ids:
            placeholders = ",".join("?" for _ in channel_ids)
            parameters: list[object] = [linkedid_value, *channel_ids]
            binding_filter = ""
            if binding is not None:
                binding_filter = " AND c.extension=?"
                parameters.append(binding.extension)
            rows = conn.execute(
                f"""
                SELECT DISTINCT c.call_key
                FROM calls c
                JOIN channels ch ON ch.call_key=c.call_key
                WHERE c.linkedid=?
                  AND ch.uniqueid IN ({placeholders})
                  AND c.terminal=0
                  {binding_filter}
                ORDER BY c.updated_at DESC
                """,
                parameters,
            ).fetchall()
            if len(rows) == 1:
                return self._load_call(conn, rows[0]["call_key"])
            if len(rows) > 1:
                if binding is None:
                    connected = [
                        self._load_call(conn, row["call_key"])
                        for row in rows
                    ]
                    connected = [
                        item
                        for item in connected
                        if item
                        and item.state
                        in {"answering", "connected", "held", "transferring"}
                    ]
                    if len(connected) == 1:
                        return connected[0]
                raise EventBindingError(
                    "AMI channel evidence resolves to multiple live call attempts"
                )

        if binding is not None:
            row = conn.execute(
                """
                SELECT call_key
                FROM calls
                WHERE linkedid=? AND extension=? AND terminal=0
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (linkedid_value, binding.extension),
            ).fetchone()
            if row:
                return self._load_call(conn, row["call_key"])

        if binding is None:
            rows = conn.execute(
                """
                SELECT call_key
                FROM calls
                WHERE linkedid=? AND terminal=0
                ORDER BY
                    CASE state
                        WHEN 'connected' THEN 0
                        WHEN 'held' THEN 1
                        WHEN 'transferring' THEN 2
                        WHEN 'answering' THEN 3
                        ELSE 4
                    END,
                    updated_at DESC
                LIMIT 2
                """,
                (linkedid_value,),
            ).fetchall()
            if len(rows) == 1:
                return self._load_call(conn, rows[0]["call_key"])
            if len(rows) > 1:
                first = self._load_call(conn, rows[0]["call_key"])
                second = self._load_call(conn, rows[1]["call_key"])
                if (
                    first
                    and second
                    and first.state
                    in {"connected", "held", "transferring", "answering"}
                    and second.state
                    not in {"connected", "held", "transferring", "answering"}
                ):
                    return first
                raise EventBindingError(
                    "AMI event lacks enough evidence to select one live agent attempt"
                )
        return None

    def _load_call(
        self,
        conn: sqlite3.Connection,
        call_key: str,
    ) -> CallState | None:
        row = conn.execute(
            "SELECT * FROM calls WHERE call_key=?",
            (call_key,),
        ).fetchone()
        if row is None:
            return None
        binding = AgentBinding.model_validate_json(row["binding_json"])
        return CallState(
            call_key=row["call_key"],
            linkedid=row["linkedid"],
            call_id=row["call_id"],
            binding=binding,
            direction=row["direction"],
            asterisk_uniqueid=row["asterisk_uniqueid"],
            sequence=int(row["sequence"]),
            state=row["state"],
            answered=bool(row["answered"]),
            terminal=bool(row["terminal"]),
            caller_number=row["caller_number"],
            destination_number=row["destination_number"],
            created_at=row["created_at"],
            answered_at=row["answered_at"],
        )

    def _create_call(
        self,
        conn: sqlite3.Connection,
        *,
        linkedid_value: str,
        message: AMIMessage,
        binding: AgentBinding,
        occurred: datetime,
    ) -> CallState:
        direction = self._direction(message, binding)
        agent_uniqueid = agent_leg_uniqueid(message, binding)
        if not agent_uniqueid:
            seed = (
                linkedid_value
                + "\n"
                + binding.extension
                + "\n"
                + message.canonical_fingerprint()
            )
            agent_uniqueid = (
                "agent-leg-"
                + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
            )
        call_material = (
            binding.tenant_id
            + "\n"
            + binding.campaign_id
            + "\n"
            + linkedid_value
            + "\n"
            + binding.extension
            + "\n"
            + agent_uniqueid
        )
        digest = hashlib.sha256(call_material.encode("utf-8")).hexdigest()
        call_key = "attempt-" + digest[:40]
        call_id = "vici-" + digest[:32]
        caller, destination = self._numbers(message, binding, direction)
        timestamp = rfc3339(occurred)
        conn.execute(
            """
            INSERT INTO calls(
                call_key, linkedid, extension, call_id, binding_json,
                direction, asterisk_uniqueid, sequence, state, answered,
                terminal, caller_number, destination_number, created_at,
                updated_at
            ) VALUES(?,?,?,?,?,?,?,0,'new',0,0,?,?,?,?)
            """,
            (
                call_key,
                linkedid_value,
                binding.extension,
                call_id,
                binding.model_dump_json(),
                direction,
                agent_uniqueid,
                caller,
                destination,
                timestamp,
                timestamp,
            ),
        )
        state = self._load_call(conn, call_key)
        assert state is not None
        return state

    @staticmethod
    def _direction(
        message: AMIMessage,
        binding: AgentBinding,
    ) -> Literal["inbound", "outbound"]:
        value = message.get("Direction").strip().lower()
        if value in {"inbound", "outbound"}:
            return value  # type: ignore[return-value]
        return binding.default_direction

    @staticmethod
    def _numbers(
        message: AMIMessage,
        binding: AgentBinding,
        direction: Literal["inbound", "outbound"],
    ) -> tuple[str | None, str | None]:
        caller_raw = message.get("CallerIDNum")
        connected_raw = (
            message.get("DestCallerIDNum")
            or message.get("ConnectedLineNum")
            or message.get("DestConnectedLineNum")
        )
        caller = normalize_phone(caller_raw, binding.country_code)
        connected = normalize_phone(connected_raw, binding.country_code)
        if direction == "inbound":
            return caller, connected
        return connected or caller, caller if connected else None

    def _refresh_numbers(
        self,
        conn: sqlite3.Connection,
        state: CallState,
        message: AMIMessage,
    ) -> CallState:
        caller, destination = self._numbers(
            message,
            state.binding,
            state.direction,
        )
        conn.execute(
            """
            UPDATE calls
            SET caller_number=?, destination_number=?, updated_at=?
            WHERE call_key=?
            """,
            (
                state.caller_number or caller,
                state.destination_number or destination,
                rfc3339(utc_now()),
                state.call_key,
            ),
        )
        refreshed = self._load_call(conn, state.call_key)
        assert refreshed is not None
        return refreshed

    @staticmethod
    def _record_channels(
        conn: sqlite3.Connection,
        state: CallState,
        message: AMIMessage,
        observed_at: str,
    ) -> None:
        pairs = (
            ("Uniqueid", "Channel"),
            ("DestUniqueid", "DestChannel"),
            ("TransfererUniqueid", "TransfererChannel"),
            ("TransfereeUniqueid", "TransfereeChannel"),
            ("OrigTransfererUniqueid", "OrigTransfererChannel"),
            ("SecondTransfererUniqueid", "SecondTransfererChannel"),
        )
        for unique_field, channel_field in pairs:
            identifier = safe_asterisk_id(message.get(unique_field))
            if not identifier:
                continue
            active = (
                0
                if message.event == "Hangup" and unique_field == "Uniqueid"
                else 1
            )
            conn.execute(
                """
                INSERT INTO channels(
                    call_key, linkedid, uniqueid, channel, active, updated_at
                ) VALUES(?,?,?,?,?,?)
                ON CONFLICT(call_key, uniqueid) DO UPDATE SET
                    channel=excluded.channel,
                    active=excluded.active,
                    updated_at=excluded.updated_at
                """,
                (
                    state.call_key,
                    state.linkedid,
                    identifier,
                    message.get(channel_field)[:255] or None,
                    active,
                    observed_at,
                ),
            )

    def _apply_event(
        self,
        conn: sqlite3.Connection,
        state: CallState,
        message: AMIMessage,
        occurred: datetime,
    ) -> list[dict[str, Any]]:
        if state.terminal:
            return []
        name = message.event
        emitted: list[dict[str, Any]] = []

        if name in {"AgentCalled", "DialBegin"}:
            self._append_state(
                conn, emitted, state, "offered", message, occurred
            )
        elif name == "Newstate":
            description = message.get("ChannelStateDesc").strip().lower()
            if description in {"ring", "ringing"}:
                self._append_state(
                    conn, emitted, state, "ringing", message, occurred
                )
            elif description == "up":
                self._append_answer_and_connect(
                    conn, emitted, state, message, occurred
                )
        elif name in {"AgentConnect", "BridgeEnter"}:
            self._append_answer_and_connect(
                conn, emitted, state, message, occurred
            )
        elif name == "DialEnd":
            status = message.get("DialStatus").strip().upper()
            if status == "ANSWER":
                self._append_answer_and_connect(
                    conn, emitted, state, message, occurred
                )
            elif status in {
                "BUSY",
                "CHANUNAVAIL",
                "CONGESTION",
                "NOANSWER",
            }:
                latest = self._load_call(conn, state.call_key) or state
                suffix = "missed" if status in {"BUSY", "NOANSWER"} else "failed"
                self._append_state(
                    conn,
                    emitted,
                    latest,
                    suffix,
                    message,
                    occurred,
                    terminal=True,
                )
        elif name == "Hold":
            latest = self._load_call(conn, state.call_key) or state
            if latest.state == "connected":
                self._append_state(
                    conn, emitted, latest, "held", message, occurred
                )
        elif name == "Unhold":
            latest = self._load_call(conn, state.call_key) or state
            if latest.state == "held":
                self._append_state(
                    conn, emitted, latest, "resumed", message, occurred
                )
        elif name in {"BlindTransfer", "AttendedTransfer"}:
            destination = transfer_destination(message)
            if transfer_succeeded(message) and destination:
                latest = self._load_call(conn, state.call_key) or state
                if latest.state in {"connected", "held"}:
                    self._append_state(
                        conn,
                        emitted,
                        latest,
                        "transfer.started",
                        message,
                        occurred,
                    )
                    latest = self._load_call(conn, state.call_key) or latest
                    self._append_state(
                        conn,
                        emitted,
                        latest,
                        "transfer.completed",
                        message,
                        occurred,
                        terminal=True,
                    )
        elif name == "AgentRingNoAnswer":
            self._append_state(
                conn,
                emitted,
                state,
                "missed",
                message,
                occurred,
                terminal=True,
            )
        elif name == "AgentComplete":
            latest = self._load_call(conn, state.call_key) or state
            emitted.extend(
                self._finish_call(conn, latest, message, occurred)
            )
        elif name == "Hangup":
            latest = self._load_call(conn, state.call_key) or state
            if self._active_channels(conn, state.call_key) == 0:
                emitted.extend(
                    self._finish_call(conn, latest, message, occurred)
                )
        return emitted

    def _append_answer_and_connect(
        self,
        conn: sqlite3.Connection,
        emitted: list[dict[str, Any]],
        state: CallState,
        message: AMIMessage,
        occurred: datetime,
    ) -> None:
        latest = self._load_call(conn, state.call_key) or state
        if latest.state in {"new", "offered", "ringing"}:
            self._append_state(
                conn, emitted, latest, "answered", message, occurred
            )
        latest = self._load_call(conn, state.call_key) or latest
        if latest.state in {"answering", "new", "offered", "ringing"}:
            self._append_state(
                conn, emitted, latest, "connected", message, occurred
            )

    def _finish_call(
        self,
        conn: sqlite3.Connection,
        state: CallState,
        message: AMIMessage,
        occurred: datetime,
    ) -> list[dict[str, Any]]:
        if state.terminal:
            return []
        emitted: list[dict[str, Any]] = []
        latest = self._load_call(conn, state.call_key) or state
        if latest.answered or latest.state in {
            "connected",
            "held",
            "transferring",
        }:
            if latest.state in {"connected", "held", "transferring"}:
                self._append_state(
                    conn, emitted, latest, "hangup", message, occurred
                )
                latest = self._load_call(conn, state.call_key) or latest
            self._append_state(
                conn,
                emitted,
                latest,
                "completed",
                message,
                occurred,
                terminal=True,
            )
            return emitted

        cause = parse_int(message.get("Cause"))
        suffix = "missed" if cause in {16, 17, 18, 19, 21} else "failed"
        self._append_state(
            conn,
            emitted,
            latest,
            suffix,
            message,
            occurred,
            terminal=True,
        )
        return emitted

    def _append_state(
        self,
        conn: sqlite3.Connection,
        emitted: list[dict[str, Any]],
        state: CallState,
        event_suffix: str,
        message: AMIMessage,
        occurred: datetime,
        *,
        terminal: bool = False,
    ) -> None:
        latest = self._load_call(conn, state.call_key) or state
        target_state = {
            "created": "new",
            "offered": "offered",
            "ringing": "ringing",
            "answered": "answering",
            "connected": "connected",
            "held": "held",
            "resumed": "connected",
            "transfer.started": "transferring",
            "transfer.completed": "transferred",
            "hangup": "ending",
            "completed": "completed",
            "failed": "failed",
            "missed": "missed",
        }[event_suffix]
        if latest.terminal or (
            latest.state == target_state and event_suffix != "created"
        ):
            return
        emitted.append(
            self._emit(
                conn,
                latest,
                event_suffix,
                message,
                occurred,
                terminal=terminal,
            )
        )

    def _emit(
        self,
        conn: sqlite3.Connection,
        state: CallState,
        event_suffix: str,
        message: AMIMessage,
        occurred: datetime,
        *,
        terminal: bool = False,
    ) -> dict[str, Any]:
        latest = self._load_call(conn, state.call_key) or state
        sequence = latest.sequence + 1
        event_type = MIDDLEWARE_EVENT_PREFIX + event_suffix
        identity_material = (
            latest.call_key
            + "\n"
            + event_type
            + "\n"
            + str(sequence)
            + "\n"
            + message.canonical_fingerprint()
        )
        event_id = (
            "vici-evt-"
            + hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:40]
        )
        if not _SAFE_EVENT_ID.fullmatch(event_id):
            raise RuntimeError("generated event ID is not URL-safe")

        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "business_unit_id": latest.binding.business_unit_id,
            "campaign_id": latest.binding.campaign_id,
            "call_id": latest.call_id,
            "asterisk_uniqueid": latest.asterisk_uniqueid,
            "linkedid": latest.linkedid,
            "agent_id": latest.binding.agent_id,
            "extension": latest.binding.extension,
            "keycloak_subject": latest.binding.keycloak_subject,
            "sequence": sequence,
            "direction": latest.direction,
        }
        if latest.caller_number:
            payload["caller_number"] = latest.caller_number
        if latest.destination_number:
            payload["destination_number"] = latest.destination_number

        duration = duration_seconds(latest.created_at, occurred)
        talk_duration = (
            duration_seconds(latest.answered_at, occurred)
            if latest.answered_at
            else 0
        )
        if event_suffix in {"hangup", "completed", "failed", "missed"}:
            payload["duration"] = duration
            payload["talk_duration"] = talk_duration
            cause = parse_int(message.get("Cause"))
            if cause is not None:
                payload["hangup_cause_code"] = cause
            cause_text = message.get("Cause-txt").strip()
            if cause_text:
                payload["hangup_cause"] = cause_text[:128]
        if event_suffix == "transfer.completed":
            destination = transfer_destination(message)
            if not destination:
                raise EventBindingError(
                    "successful transfer has no bounded destination"
                )
            payload["transfer_destination"] = destination
            payload["transfer_type"] = (
                "blind" if message.event == "BlindTransfer" else "attended"
            )

        now = utc_now()
        envelope = {
            "event_id": event_id,
            "event_type": event_type,
            "event_version": "1.0",
            "occurred_at": rfc3339(occurred),
            "received_at": rfc3339(now),
            "source": "vicidial-adapter",
            "tenant_id": latest.binding.tenant_id,
            "correlation_id": latest.call_id,
            "causation_id": (
                "ami-" + message.canonical_fingerprint()[:32]
            ),
            "idempotency_key": event_id,
            "payload": payload,
            "metadata": {
                "transport": "ami",
                "ami_event": message.event,
                "gateway_version": GATEWAY_VERSION,
            },
        }
        target_state = {
            "created": "new",
            "offered": "offered",
            "ringing": "ringing",
            "answered": "answering",
            "connected": "connected",
            "held": "held",
            "resumed": "connected",
            "transfer.started": "transferring",
            "transfer.completed": "transferred",
            "hangup": "ending",
            "completed": "completed",
            "failed": "failed",
            "missed": "missed",
        }[event_suffix]
        answered = latest.answered or event_suffix in {
            "answered",
            "connected",
        }
        answered_at = latest.answered_at or (
            rfc3339(occurred)
            if event_suffix in {"answered", "connected"}
            else None
        )
        ended_at = rfc3339(occurred) if terminal else None
        conn.execute(
            """
            UPDATE calls
            SET sequence=?, state=?, answered=?, terminal=?,
                answered_at=?, ended_at=COALESCE(?, ended_at), updated_at=?
            WHERE call_key=?
            """,
            (
                sequence,
                target_state,
                int(answered),
                int(terminal),
                answered_at,
                ended_at,
                rfc3339(now),
                latest.call_key,
            ),
        )
        conn.execute(
            """
            INSERT INTO event_outbox(
                event_id, call_key, linkedid, sequence, event_type,
                envelope_json, status, next_attempt_at, created_at
            ) VALUES(?,?,?,?,?,?,'pending',0,?)
            """,
            (
                event_id,
                latest.call_key,
                latest.linkedid,
                sequence,
                event_type,
                json.dumps(
                    envelope,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                rfc3339(now),
            ),
        )
        return envelope

    @staticmethod
    def _active_channels(
        conn: sqlite3.Connection,
        call_key: str,
    ) -> int:
        return int(
            conn.execute(
                """
                SELECT count(*)
                FROM channels
                WHERE call_key=? AND active=1
                """,
                (call_key,),
            ).fetchone()[0]
        )

    @staticmethod
    def _record_rejection(
        conn: sqlite3.Connection,
        *,
        fingerprint: str,
        event_name: str,
        linkedid_value: str,
        reason: str,
        observed_at: str,
    ) -> None:
        linkedid_hash = hashlib.sha256(
            linkedid_value.encode("utf-8")
        ).hexdigest()
        conn.execute(
            """
            INSERT OR REPLACE INTO rejected_ami(
                fingerprint, event_name, linkedid_hash, reason, observed_at
            ) VALUES(?,?,?,?,?)
            """,
            (
                fingerprint,
                event_name,
                linkedid_hash,
                reason[:512],
                observed_at,
            ),
        )

    def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 30,
    ) -> dict[str, Any] | None:
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT event_id, envelope_json, attempt_count
                    FROM event_outbox
                    WHERE status IN ('pending','retrying')
                      AND next_attempt_at <= ?
                      AND (lease_until IS NULL OR lease_until < ?)
                    ORDER BY created_at, call_key, sequence
                    LIMIT 1
                    """,
                    (now, now),
                ).fetchone()
                if row is None:
                    conn.commit()
                    return None
                updated = conn.execute(
                    """
                    UPDATE event_outbox
                    SET lease_owner=?, lease_until=?,
                        attempt_count=attempt_count+1
                    WHERE event_id=?
                      AND status IN ('pending','retrying')
                      AND (lease_until IS NULL OR lease_until < ?)
                    """,
                    (
                        worker_id,
                        now + lease_seconds,
                        row["event_id"],
                        now,
                    ),
                ).rowcount
                if not updated:
                    conn.commit()
                    return None
                conn.commit()
                return {
                    "event_id": row["event_id"],
                    "envelope": json.loads(row["envelope_json"]),
                    "attempt_count": int(row["attempt_count"]) + 1,
                }
            except Exception:
                conn.rollback()
                raise

    def mark_delivered(self, event_id: str, *, worker_id: str) -> None:
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE event_outbox
                SET status='delivered', delivered_at=?, lease_owner=NULL,
                    lease_until=NULL, last_error=NULL
                WHERE event_id=? AND lease_owner=?
                """,
                (rfc3339(utc_now()), event_id, worker_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError("outbox delivery lease was lost")

    def mark_retry(
        self,
        event_id: str,
        *,
        worker_id: str,
        error: Exception,
        delay_seconds: float,
    ) -> None:
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE event_outbox
                SET status='retrying', next_attempt_at=?, lease_owner=NULL,
                    lease_until=NULL, last_error=?
                WHERE event_id=? AND lease_owner=?
                """,
                (
                    time.time() + max(0.1, delay_seconds),
                    (error.__class__.__name__ + ": " + str(error))[:1024],
                    event_id,
                    worker_id,
                ),
            ).rowcount
            if updated != 1:
                raise RuntimeError("outbox retry lease was lost")

    def pending_count(self) -> int:
        with self._connect() as conn:
            return int(
                conn.execute(
                    """
                    SELECT count(*)
                    FROM event_outbox
                    WHERE status IN ('pending','retrying')
                    """
                ).fetchone()[0]
            )

    def mark_dead_letter(
        self,
        event_id: str,
        *,
        worker_id: str,
        error: Exception,
    ) -> None:
        with self._connect() as conn:
            updated = conn.execute(
                """
                UPDATE event_outbox
                SET status='dead_letter', lease_owner=NULL, lease_until=NULL,
                    last_error=?
                WHERE event_id=? AND lease_owner=?
                """,
                (
                    (error.__class__.__name__ + ": " + str(error))[:1024],
                    event_id,
                    worker_id,
                ),
            ).rowcount
            if updated != 1:
                raise RuntimeError("outbox dead-letter lease was lost")

    def outbox_rows(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, call_key, linkedid, sequence, event_type,
                       envelope_json, status, attempt_count, last_error
                FROM event_outbox
                ORDER BY call_key, sequence
                """
            ).fetchall()
        return [
            {
                **dict(row),
                "envelope": json.loads(row["envelope_json"]),
            }
            for row in rows
        ]

    def call(
        self,
        linkedid_value: str,
        extension: str | None = None,
    ) -> CallState | None:
        with self._connect() as conn:
            if extension is None:
                row = conn.execute(
                    """
                    SELECT call_key FROM calls
                    WHERE linkedid=?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (linkedid_value,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT call_key FROM calls
                    WHERE linkedid=? AND extension=?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (linkedid_value, extension),
                ).fetchone()
            return self._load_call(conn, row["call_key"]) if row else None


def agent_leg_uniqueid(
    message: AMIMessage,
    binding: AgentBinding,
) -> str:
    candidates = (
        ("DestChannel", "DestUniqueid"),
        ("Channel", "Uniqueid"),
        ("Interface", "DestUniqueid"),
        ("TransfererChannel", "TransfererUniqueid"),
        ("TransfereeChannel", "TransfereeUniqueid"),
        ("OrigTransfererChannel", "OrigTransfererUniqueid"),
        ("SecondTransfererChannel", "SecondTransfererUniqueid"),
    )
    for channel_field, unique_field in candidates:
        channel = message.get(channel_field)
        endpoint = channel_endpoint(channel)
        alias_match = channel.lower() in {
            alias.lower() for alias in binding.endpoint_aliases
        }
        if endpoint == binding.extension or alias_match:
            identifier = safe_asterisk_id(message.get(unique_field))
            if identifier:
                return identifier
    return ""


def parse_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def duration_seconds(start: str | None, end: datetime) -> int:
    if not start:
        return 0
    try:
        parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, min(86400, int((end - parsed).total_seconds())))


def transfer_succeeded(message: AMIMessage) -> bool:
    result = message.get("Result").strip().lower()
    return result in {"success", "succeeded", "yes", "1"}


def transfer_destination(message: AMIMessage) -> str | None:
    for key in (
        "Extension",
        "DestExten",
        "TransfereeExten",
        "SecondTransfererExten",
        "Context",
    ):
        value = message.get(key).strip()
        if value:
            safe = re.sub(r"[^A-Za-z0-9._:-]", "-", value)[:255]
            if safe:
                return safe
    return None


@dataclass(frozen=True, slots=True)
class AMIConnectionConfig:
    host: str
    port: int
    username: str
    secret: str
    connect_timeout_seconds: float = 5.0
    receive_timeout_seconds: float = 1.0

    def validate(self) -> None:
        if not 1 <= self.port <= 65535:
            raise GatewayConfigurationError("AMI port is invalid")
        if not self.username or len(self.username) > 128:
            raise GatewayConfigurationError("AMI username is invalid")
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                self.host,
                self.port,
                type=socket.SOCK_STREAM,
            )
        }
        if not addresses:
            raise GatewayConfigurationError("AMI host does not resolve")
        if any(not ipaddress.ip_address(address).is_loopback for address in addresses):
            raise GatewayConfigurationError(
                "AMI event gateway may connect only to a loopback address"
            )


class AMIClient:
    """Minimal receive-only AMI client. Login is its only AMI action."""

    def __init__(self, config: AMIConnectionConfig) -> None:
        config.validate()
        self.config = config

    def messages(
        self,
        stop: threading.Event,
    ) -> Iterable[AMIMessage]:
        with socket.create_connection(
            (self.config.host, self.config.port),
            timeout=self.config.connect_timeout_seconds,
        ) as connection:
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            connection.settimeout(self.config.receive_timeout_seconds)
            banner = self._read_line(connection)
            if not banner.startswith(b"Asterisk Call Manager/"):
                raise AMIProtocolError("AMI greeting is invalid")
            action = (
                "Action: Login\r\n"
                f"Username: {self.config.username}\r\n"
                f"Secret: {self.config.secret}\r\n"
                "Events: call,agent\r\n\r\n"
            ).encode("utf-8")
            connection.sendall(action)
            login, pending = self._read_block(connection)
            response = parse_ami_block(login)
            if response.response.lower() != "success":
                raise AMIProtocolError("AMI login was rejected")

            parser = AMIStreamParser()
            for message in parser.feed(pending):
                if message.event:
                    yield message
            while not stop.is_set():
                try:
                    chunk = connection.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    raise ConnectionError("AMI connection closed")
                for message in parser.feed(chunk):
                    if message.event:
                        yield message

    @staticmethod
    def _read_line(connection: socket.socket) -> bytes:
        value = bytearray()
        while len(value) <= 4096:
            chunk = connection.recv(1)
            if not chunk:
                raise ConnectionError("AMI connection closed during greeting")
            value.extend(chunk)
            if value.endswith(b"\r\n"):
                return bytes(value[:-2])
        raise AMIProtocolError("AMI greeting exceeded its limit")

    @staticmethod
    def _read_block(connection: socket.socket) -> tuple[bytes, bytes]:
        value = bytearray()
        delimiter = b"\r\n\r\n"
        while len(value) <= 65536:
            chunk = connection.recv(4096)
            if not chunk:
                raise ConnectionError("AMI connection closed during login")
            value.extend(chunk)
            offset = value.find(delimiter)
            if offset >= 0:
                return (
                    bytes(value[:offset]),
                    bytes(value[offset + len(delimiter) :]),
                )
        raise AMIProtocolError("AMI login response exceeded its limit")
