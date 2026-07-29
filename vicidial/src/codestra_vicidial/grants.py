from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionGrant:
    grant_id: str
    operation: str
    idempotency_key: str
    body_sha256: str
    target: str
    expires_at: int


@dataclass(frozen=True)
class ExecutionGrantValidator:
    key: bytes
    expected_target: str
    max_future_seconds: int = 300

    @staticmethod
    def _encode(payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    @staticmethod
    def _decode(value: str) -> dict:
        padding = "=" * (-len(value) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(value + padding))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise PermissionError("invalid execution grant") from exc
        if not isinstance(payload, dict):
            raise PermissionError("invalid execution grant")
        return payload

    def issue(self, *, grant_id: str, operation: str, idempotency_key: str,
              body: bytes, expires_at: int) -> str:
        payload = {
            "grant_id": grant_id, "operation": operation,
            "idempotency_key": idempotency_key,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "target": self.expected_target, "expires_at": expires_at,
        }
        encoded = self._encode(payload)
        signature = hmac.new(self.key, encoded.encode(), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def verify(self, token: str, *, operation: str, idempotency_key: str,
               body: bytes, now: int | None = None) -> ExecutionGrant:
        try:
            encoded, signature = token.split(".", 1)
        except ValueError as exc:
            raise PermissionError("invalid execution grant") from exc
        expected = hmac.new(self.key, encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise PermissionError("invalid execution grant signature")
        payload = self._decode(encoded)
        required = {
            "grant_id", "operation", "idempotency_key", "body_sha256",
            "target", "expires_at",
        }
        if set(payload) != required:
            raise PermissionError("invalid execution grant claims")
        current = int(time.time()) if now is None else now
        expires_at = int(payload["expires_at"])
        if expires_at < current:
            raise PermissionError("execution grant expired")
        if expires_at > current + self.max_future_seconds:
            raise PermissionError("execution grant lifetime exceeds policy")
        comparisons = (
            (payload["operation"], operation, "operation"),
            (payload["idempotency_key"], idempotency_key, "idempotency key"),
            (payload["body_sha256"], hashlib.sha256(body).hexdigest(), "body"),
            (payload["target"], self.expected_target, "target"),
        )
        for actual, expected_value, name in comparisons:
            if not hmac.compare_digest(str(actual), str(expected_value)):
                raise PermissionError(f"execution grant {name} mismatch")
        return ExecutionGrant(**payload)
