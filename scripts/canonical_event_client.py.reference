#!/usr/bin/env python3
"""Fail-closed VICIdial canonical event client (Python 3.6 compatible)."""

import hashlib
import hmac
import json
import logging
import os
import random
import re
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import requests

CANONICAL_URL = "https://middleware.internal.codestra.agency/api/v1/events/vicidial"
CANONICAL_PATH = "/api/v1/events/vicidial"
APPROVED_HOST = "middleware.internal.codestra.agency"
CONTENT_TYPE = "application/json"
MAX_BODY_BYTES = 262144
MAX_RESPONSE_BYTES = 65536
MAX_BATCH_SIZE = 100
REPLAY_WINDOW_SECONDS = 300
APPROVED_NON_SYNTHETIC_EVENT_TYPES = frozenset(("vicidial.call.ended",))
EVENT_TYPES = frozenset(
    (
        "vicidial.test.synthetic",
        "vicidial.call.ended",
        "vicidial.disposition.recorded",
        "vicidial.recording.available",
        "vicidial.callback.scheduled",
        "vicidial.agent.status.changed",
    )
)
TRANSIENT_STATUSES = frozenset((408, 425, 429, 500, 502, 503, 504))
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


class ContractError(ValueError):
    pass


class UnsupportedCapabilityError(ContractError):
    pass


class DeliveryError(RuntimeError):
    def __init__(self, message, retryable=False):
        RuntimeError.__init__(self, message)
        self.retryable = retryable


def canonical_json(value):
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ContractError("event is not canonically serializable")
    body = text.encode("utf-8")
    if len(body) > MAX_BODY_BYTES:
        raise ContractError("event body exceeds limit")
    return body


def rfc3339_utc(value):
    if isinstance(value, (int, float)):
        return (
            datetime.fromtimestamp(value, timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ContractError("source timestamp timezone is unavailable")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, str) and value.endswith("Z"):
        parsed = False
        for pattern in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
            try:
                datetime.strptime(value, pattern)
                parsed = True
                break
            except ValueError:
                pass
        if not parsed:
            raise ContractError("invalid RFC3339 UTC timestamp")
        return value
    raise ContractError("timestamp must be an epoch or timezone-aware value")


def rfc3339_epoch_seconds(value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError("source epoch must be an integer")
    if value < 0:
        raise ContractError("source epoch is outside the supported range")
    try:
        converted = datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise ContractError("source epoch is outside the supported range")
    return converted.strftime("%Y-%m-%dT%H:%M:%SZ")


def _bounded(value, name, maximum, nonempty=True):
    if not isinstance(value, str) or len(value) > maximum or (nonempty and not value):
        raise ContractError("invalid {}".format(name))
    return value


def validate_destination(url):
    parsed = urlsplit(url)
    if (
        url != CANONICAL_URL
        or parsed.scheme != "https"
        or parsed.hostname != APPROVED_HOST
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != CANONICAL_PATH
    ):
        raise ContractError("callback destination is not approved")
    return url


def validate_envelope(event):
    allowed = {
        "schema_version",
        "event_id",
        "event_type",
        "occurred_at",
        "correlation_id",
        "test_syn",
        "payload",
    }
    if not isinstance(event, dict) or set(event) != allowed:
        raise ContractError("invalid envelope fields")
    if event["schema_version"] != "1.0" or event["event_type"] not in EVENT_TYPES:
        raise ContractError("unsupported schema or event type")
    try:
        uuid.UUID(event["event_id"])
    except (ValueError, TypeError, AttributeError):
        raise ContractError("invalid event_id")
    rfc3339_utc(event["occurred_at"])
    _bounded(event["correlation_id"], "correlation_id", 128)
    if type(event["test_syn"]) is not bool or not isinstance(event["payload"], dict):
        raise ContractError("invalid policy or payload type")
    if event["test_syn"] is False and event["event_type"] not in APPROVED_NON_SYNTHETIC_EVENT_TYPES:
        raise ContractError("non-synthetic event type is not approved")
    if event["event_type"] == "vicidial.test.synthetic":
        if set(event["payload"]) != {"probe_id", "marker", "generated_at"}:
            raise ContractError("invalid synthetic payload fields")
        _bounded(event["payload"]["probe_id"], "probe_id", 128)
        if event["payload"]["marker"] != "TEST_SYN":
            raise ContractError("invalid synthetic marker")
        rfc3339_utc(event["payload"]["generated_at"])
    if event["event_type"] == "vicidial.call.ended":
        allowed_payload = {
            "call_id", "ended_at", "duration_seconds", "lead_id", "campaign_id",
            "agent_user", "started_at", "disposition_code", "direction",
        }
        payload = event["payload"]
        if not {"call_id", "ended_at", "duration_seconds"}.issubset(payload):
            raise ContractError("missing call-ended payload field")
        if not set(payload).issubset(allowed_payload):
            raise ContractError("invalid call-ended payload field")
        _bounded(payload["call_id"], "call_id", 128)
        rfc3339_utc(payload["ended_at"])
        duration = payload["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, int) or not 0 <= duration <= 86400:
            raise ContractError("invalid call duration")
        if "lead_id" in payload and (isinstance(payload["lead_id"], bool) or not isinstance(payload["lead_id"], int) or payload["lead_id"] < 0):
            raise ContractError("invalid lead_id")
        for key, maximum in (("campaign_id", 64), ("agent_user", 64), ("disposition_code", 32)):
            if key in payload:
                _bounded(payload[key], key, maximum)
        if "started_at" in payload:
            rfc3339_utc(payload["started_at"])
        if payload.get("direction") not in (None, "inbound", "outbound", "manual", "unknown"):
            raise ContractError("invalid direction")
    return event


def synthetic_event(probe_id, event_id, correlation_id, occurred_at):
    timestamp = rfc3339_utc(occurred_at)
    event = {
        "schema_version": "1.0",
        "event_id": str(event_id),
        "event_type": "vicidial.test.synthetic",
        "occurred_at": timestamp,
        "correlation_id": correlation_id,
        "test_syn": True,
        "payload": {
            "probe_id": probe_id,
            "marker": "TEST_SYN",
            "generated_at": timestamp,
        },
    }
    return validate_envelope(event)


def deterministic_idempotency_key(event_id):
    try:
        normalized = str(uuid.UUID(str(event_id)))
    except (ValueError, TypeError, AttributeError):
        raise ContractError("missing idempotency identity")
    return "vicidial:{}".format(normalized)


def signature(secret, timestamp, body):
    if not isinstance(secret, str) or not secret:
        raise ContractError("HMAC secret is unavailable")
    try:
        ts = str(int(timestamp)).encode("ascii")
    except (ValueError, TypeError, UnicodeEncodeError):
        raise ContractError("invalid signature timestamp")
    return hmac.new(
        secret.encode("utf-8"), ts + b"." + body, hashlib.sha256
    ).hexdigest()


def load_secret_file(path):
    if not path:
        raise ContractError("HMAC secret file is required")
    try:
        with open(path, "r") as handle:
            value = handle.read().strip()
    except IOError:
        raise ContractError("HMAC secret file is unreadable")
    if not value:
        raise ContractError("HMAC secret file is empty")
    return value


class PreparedEvent(object):
    def __init__(self, event, secret, client_instance, timestamp):
        validate_envelope(event)
        self.event_id = event["event_id"]
        self.body = canonical_json(event)
        self.idempotency_key = deterministic_idempotency_key(self.event_id)
        self.timestamp = str(int(timestamp))
        _bounded(client_instance, "client_instance", 128)
        if re.match(r"^[A-Za-z0-9._:-]+$", client_instance) is None:
            raise ContractError("invalid client_instance")
        self.headers = {
            "Content-Type": CONTENT_TYPE,
            "Idempotency-Key": self.idempotency_key,
            "X-Client-Instance-ID": client_instance,
            "X-Timestamp": self.timestamp,
            "X-Signature": signature(secret, self.timestamp, self.body),
        }




def validate_persistence_acknowledgement(body, prepared):
    """Require a bounded acknowledgement of durable middleware persistence."""
    if not body:
        raise DeliveryError("durable persistence acknowledgement is missing", False)
    try:
        acknowledgement = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        raise DeliveryError("durable persistence acknowledgement is invalid", False)

    expected_fields = {
        "persisted",
        "status",
        "event_id",
        "idempotency_key",
    }
    if not isinstance(acknowledgement, dict) or set(acknowledgement) != expected_fields:
        raise DeliveryError("durable persistence acknowledgement fields are invalid", False)
    if acknowledgement["persisted"] is not True or acknowledgement["status"] != "persisted":
        raise DeliveryError("middleware did not acknowledge durable persistence", False)
    if acknowledgement["event_id"] != prepared.event_id:
        raise DeliveryError("persistence acknowledgement event identity mismatch", False)
    if acknowledgement["idempotency_key"] != prepared.idempotency_key:
        raise DeliveryError("persistence acknowledgement idempotency mismatch", False)
    return acknowledgement


class EventClient(object):
    def __init__(
        self,
        secret,
        client_instance,
        send_events=False,
        session=None,
        connect_timeout=3.0,
        read_timeout=8.0,
        max_attempts=3,
        backoff_base=0.25,
        backoff_cap=4.0,
        client_cert_file=None,
        client_key_file=None,
        ca_bundle_file=None,
        sleeper=time.sleep,
        jitter=random.uniform,
    ):
        validate_destination(CANONICAL_URL)
        if not secret:
            raise ContractError("HMAC secret is unavailable")
        _bounded(client_instance, "client_instance", 128)
        if (
            connect_timeout <= 0
            or read_timeout <= 0
            or max_attempts < 1
            or max_attempts > 5
        ):
            raise ContractError("invalid bounded transport settings")
        self.secret = secret
        self.client_instance = client_instance
        self.send_events = send_events
        self.session = session or requests.Session()
        if bool(client_cert_file) != bool(client_key_file):
            raise ContractError("mTLS client certificate and key are both required")
        if client_cert_file:
            for path, label in (
                (client_cert_file, "mTLS client certificate"),
                (client_key_file, "mTLS client key"),
                (ca_bundle_file, "mTLS CA bundle"),
            ):
                if not path or not os.path.isfile(path):
                    raise ContractError("{} is unavailable".format(label))
            self.session.cert = (client_cert_file, client_key_file)
            self.session.verify = ca_bundle_file
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.sleeper = sleeper
        self.jitter = jitter

    def prepare(self, event, timestamp=None):
        return PreparedEvent(
            event,
            self.secret,
            self.client_instance,
            int(time.time()) if timestamp is None else timestamp,
        )

    def deliver(self, prepared):
        if not self.send_events:
            logging.info("event delivery disabled")
            return {"sent": False, "reason": "disabled"}
        last_error = None
        for attempt in range(self.max_attempts):
            try:
                response = self.session.post(
                    CANONICAL_URL,
                    data=prepared.body,
                    headers=prepared.headers,
                    timeout=(self.connect_timeout, self.read_timeout),
                    allow_redirects=False,
                    stream=True,
                )
                if 300 <= response.status_code < 400:
                    raise DeliveryError("redirect rejected", False)
                size = 0
                response_chunks = []
                for chunk in response.iter_content(8192):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise DeliveryError("response exceeds limit", False)
                    if chunk:
                        response_chunks.append(chunk)
                if 200 <= response.status_code < 300:
                    acknowledgement = validate_persistence_acknowledgement(
                        b"".join(response_chunks), prepared
                    )
                    return {
                        "sent": True,
                        "status_code": response.status_code,
                        "persistence_status": acknowledgement["status"],
                    }
                retryable = response.status_code in TRANSIENT_STATUSES
                last_error = DeliveryError("middleware request failed", retryable)
            except (requests.Timeout, requests.ConnectionError):
                last_error = DeliveryError("middleware transport failed", True)
            if not last_error.retryable or attempt + 1 >= self.max_attempts:
                raise last_error
            delay = min(self.backoff_cap, self.backoff_base * (2**attempt))
            self.sleeper(delay + self.jitter(0, delay * 0.25))
        raise last_error


def _event(event_id, event_type, occurred_at, correlation_id, payload):
    return {
        "schema_version": "1.0",
        "event_id": str(event_id),
        "event_type": event_type,
        "occurred_at": rfc3339_utc(occurred_at),
        "correlation_id": correlation_id,
        "test_syn": False,
        "payload": payload,
    }


def map_call_ended(row, event_id, correlation_id, direction):
    required = ("uniqueid", "end_epoch", "length_in_sec")
    if any(row.get(x) is None for x in required):
        raise ContractError("missing call-ended source field")
    duration = row["length_in_sec"]
    if isinstance(duration, bool):
        raise ContractError("invalid call duration")
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        raise ContractError("invalid call duration")
    if duration < 0 or duration > 86400:
        raise ContractError("invalid call duration")
    ended_at = rfc3339_epoch_seconds(row["end_epoch"])
    payload = {
        "call_id": str(row["uniqueid"]),
        "ended_at": ended_at,
        "duration_seconds": duration,
        "direction": direction,
    }
    if row.get("lead_id") is not None:
        try:
            payload["lead_id"] = int(row["lead_id"])
        except (TypeError, ValueError):
            raise ContractError("invalid lead_id")
    for source, target in (("campaign_id", "campaign_id"), ("user", "agent_user"), ("status", "disposition_code")):
        if row.get(source):
            payload[target] = str(row[source])
    if row.get("start_epoch") is not None:
        payload["started_at"] = rfc3339_epoch_seconds(row["start_epoch"])
    event = _event(event_id, "vicidial.call.ended", ended_at, correlation_id, payload)
    return validate_envelope(event)


def map_disposition(row, event_id, correlation_id):
    required = ("uniqueid", "status", "end_epoch")
    if any(row.get(x) is None for x in required):
        raise ContractError("missing disposition source field")
    payload = {
        "call_id": str(row["uniqueid"]),
        "disposition_code": str(row["status"]),
        "recorded_at": rfc3339_utc(row["end_epoch"]),
    }
    return _event(
        event_id,
        "vicidial.disposition.recorded",
        row["end_epoch"],
        correlation_id,
        payload,
    )


def map_recording(row, event_id, correlation_id):
    required = ("vicidial_id", "recording_id", "end_epoch")
    if any(row.get(x) is None for x in required):
        raise ContractError("missing recording source field")
    payload = {
        "call_id": str(row["vicidial_id"]),
        "recording_id": str(row["recording_id"]),
        "available_at": rfc3339_utc(row["end_epoch"]),
    }
    if row.get("length_in_sec") is not None:
        payload["duration_seconds"] = int(row["length_in_sec"])
    return _event(
        event_id,
        "vicidial.recording.available",
        row["end_epoch"],
        correlation_id,
        payload,
    )


def map_callback(row, event_id, correlation_id):
    raise UnsupportedCapabilityError(
        "callback timezone provenance unavailable"
    )


def map_agent_status(row, event_id, correlation_id):
    required = ("user", "status", "last_update_epoch")
    if any(row.get(x) is None for x in required):
        raise ContractError("missing agent-status source field")
    changed = rfc3339_epoch_seconds(row["last_update_epoch"])
    payload = {
        "agent_user": str(row["user"]),
        "status": str(row["status"]),
        "changed_at": changed,
    }
    return _event(
        event_id, "vicidial.agent.status.changed", changed, correlation_id, payload
    )