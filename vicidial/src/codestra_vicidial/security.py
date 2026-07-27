from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time
from dataclasses import dataclass, field


@dataclass
class RequestAuthenticator:
    identity: str
    key: bytes
    authorized_sources: frozenset[str]
    max_clock_skew_seconds: int = 60
    _nonces: dict[str, int] = field(default_factory=dict)

    def sign(self, timestamp: int, nonce: str, body: bytes) -> str:
        digest = hashlib.sha256(body).hexdigest()
        return hmac.new(self.key, f"{timestamp}\n{nonce}\n{digest}".encode(), hashlib.sha256).hexdigest()

    def verify(self, *, source: str, identity: str, scopes: set[str], timestamp: int,
               nonce: str, body: bytes, signature: str, required_scope: str,
               now: int | None = None) -> None:
        try:
            normalized = str(ipaddress.ip_address(source))
        except ValueError as exc:
            raise PermissionError("invalid source address") from exc
        if normalized not in self.authorized_sources:
            raise PermissionError("source is not authorized")
        if not hmac.compare_digest(identity, self.identity):
            raise PermissionError("wrong service identity")
        if required_scope not in scopes:
            raise PermissionError("missing scope")
        current = int(time.time()) if now is None else now
        if abs(current - timestamp) > self.max_clock_skew_seconds:
            raise PermissionError("request timestamp outside window")
        self._nonces = {key: expiry for key, expiry in self._nonces.items() if expiry >= current}
        if nonce in self._nonces:
            raise PermissionError("request replay")
        expected = self.sign(timestamp, nonce, body)
        if not hmac.compare_digest(expected, signature):
            raise PermissionError("invalid request signature")
        self._nonces[nonce] = current + self.max_clock_skew_seconds
