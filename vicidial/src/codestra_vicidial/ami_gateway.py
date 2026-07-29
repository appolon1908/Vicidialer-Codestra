from __future__ import annotations

import hashlib
import json
import sqlite3
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


class AMIParseError(ValueError):
    pass


class AMIFrameParser:
    def __init__(self, max_frame_bytes: int = 65536,
                 max_buffer_bytes: int = 262144):
        self.max_frame_bytes = max_frame_bytes
        self.max_buffer_bytes = max_buffer_bytes
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[dict[str, str]]:
        self._buffer.extend(data)
        if len(self._buffer) > self.max_buffer_bytes:
            self._buffer.clear()
            raise AMIParseError("AMI buffer limit exceeded")
        frames: list[dict[str, str]] = []
        marker = b"\r\n\r\n"
        while marker in self._buffer:
            raw, _, remainder = self._buffer.partition(marker)
            self._buffer = bytearray(remainder)
            if len(raw) > self.max_frame_bytes:
                raise AMIParseError("AMI frame limit exceeded")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AMIParseError("AMI frame is not UTF-8") from exc
            frame: dict[str, str] = {}
            for line in text.split("\r\n"):
                if not line or ": " not in line:
                    raise AMIParseError("malformed AMI header")
                key, value = line.split(": ", 1)
                if not key or key in frame:
                    raise AMIParseError("invalid AMI header")
                frame[key] = value
            frames.append(frame)
        return frames


ALLOWED_EVENTS = frozenset({
    "FullyBooted", "Newchannel", "Newstate", "Hangup", "BridgeEnter",
    "BridgeLeave", "DialBegin", "DialEnd", "QueueCallerJoin",
    "QueueCallerLeave", "AgentConnect", "AgentComplete", "PeerStatus",
    "EndpointDetail", "ContactStatus", "DeviceStateChange",
})
SENSITIVE_KEYS = frozenset({
    "Secret", "Password", "Authorization", "CallerIDNum", "ConnectedLineNum",
})


def normalize_event(frame: dict[str, str], *, observation_epoch: str) -> dict | None:
    event_name = frame.get("Event")
    if event_name not in ALLOWED_EVENTS:
        return None
    safe = {
        key: value for key, value in frame.items()
        if key not in SENSITIVE_KEYS and len(key) <= 64 and len(value) <= 2048
    }
    identity = {
        "epoch": observation_epoch, "event": event_name,
        "uniqueid": frame.get("Uniqueid", ""),
        "linkedid": frame.get("Linkedid", ""),
        "channel": frame.get("Channel", ""),
        "sequence": frame.get("SequenceNumber", ""),
        "timestamp": frame.get("Timestamp", ""),
    }
    event_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {
        "schema": "codestra.telephony.ami.v1", "event_id": event_id,
        "event_type": event_name, "observed_at": datetime.now(timezone.utc).isoformat(),
        "observation_epoch": observation_epoch, "payload": safe,
    }


class EventOutbox:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS events(
          event_id TEXT PRIMARY KEY, event_json TEXT NOT NULL,
          status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt_at TEXT NOT NULL, created_at TEXT NOT NULL,
          delivered_at TEXT);
        CREATE INDEX IF NOT EXISTS events_delivery
          ON events(status,next_attempt_at,created_at);
        """)
        self.db.commit()

    def append(self, event: dict) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO events(event_id,event_json,status,next_attempt_at,created_at)"
            " VALUES(?,?,'pending',?,?)",
            (event["event_id"], json.dumps(event, sort_keys=True), now, now),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def pending(self, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 1000:
            raise ValueError("invalid outbox batch size")
        now = datetime.now(timezone.utc).isoformat()
        rows = self.db.execute(
            "SELECT event_json FROM events WHERE status IN ('pending','retry') "
            "AND next_attempt_at<=? ORDER BY created_at LIMIT ?", (now, limit)
        ).fetchall()
        return [json.loads(row["event_json"]) for row in rows]

    def delivered(self, event_id: str) -> None:
        self.db.execute(
            "UPDATE events SET status='delivered',delivered_at=? WHERE event_id=?",
            (datetime.now(timezone.utc).isoformat(), event_id),
        )
        self.db.commit()

    def retry(self, event_id: str, next_attempt_at: datetime) -> None:
        self.db.execute(
            "UPDATE events SET status='retry',attempts=attempts+1,next_attempt_at=? "
            "WHERE event_id=?", (next_attempt_at.astimezone(timezone.utc).isoformat(), event_id)
        )
        self.db.commit()


@dataclass(frozen=True)
class TLSDeliveryConfig:
    endpoint: str
    ca_file: Path
    certificate_file: Path
    private_key_file: Path

    def context(self) -> ssl.SSLContext:
        parsed = urlparse(self.endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("event delivery endpoint must use HTTPS")
        for path in (self.ca_file, self.certificate_file, self.private_key_file):
            if not path.is_absolute() or not path.is_file():
                raise ValueError("TLS material must be an existing absolute file")
        context = ssl.create_default_context(cafile=str(self.ca_file))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(
            certfile=str(self.certificate_file), keyfile=str(self.private_key_file))
        return context


class AMIEventGateway:
    def __init__(self, outbox: EventOutbox, observation_epoch: str):
        if len(observation_epoch) < 8:
            raise ValueError("observation epoch is too short")
        self.parser = AMIFrameParser()
        self.outbox = outbox
        self.observation_epoch = observation_epoch
        self.accepted = 0
        self.duplicates = 0
        self.ignored = 0

    def ingest(self, data: bytes) -> int:
        accepted = 0
        for frame in self.parser.feed(data):
            event = normalize_event(frame, observation_epoch=self.observation_epoch)
            if event is None:
                self.ignored += 1
                continue
            if self.outbox.append(event):
                accepted += 1
                self.accepted += 1
            else:
                self.duplicates += 1
        return accepted

    def status(self) -> dict:
        return {
            "status": "ready", "observation_epoch": self.observation_epoch,
            "accepted": self.accepted, "duplicates": self.duplicates,
            "ignored": self.ignored,
        }
