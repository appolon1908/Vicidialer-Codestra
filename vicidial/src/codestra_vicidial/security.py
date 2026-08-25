from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time
from collections.abc import Callable
from dataclasses import dataclass, field


NonceStore = Callable[[str, int, int], bool]
RateLimitStore = Callable[[str, int, int, int], bool]


@dataclass
class RequestAuthenticator:
    identity: str
    key: bytes
    authorized_sources: frozenset[str]
    max_clock_skew_seconds: int = 60

    nonce_store: NonceStore | None = None
    rate_limit_store: RateLimitStore | None = None

    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60

    _nonces: dict[str, int] = field(default_factory=dict)
    _requests: dict[str, list[int]] = field(default_factory=dict)

    def sign(self, timestamp: int, nonce: str, body: bytes) -> str:
        digest = hashlib.sha256(body).hexdigest()
        payload = f"{timestamp}\n{nonce}\n{digest}".encode()

        return hmac.new(
            self.key,
            payload,
            hashlib.sha256,
        ).hexdigest()

    def _verify_source(self, source: str) -> str:
        try:
            normalized = str(ipaddress.ip_address(source))
        except ValueError as exc:
            raise PermissionError("invalid source address") from exc

        if normalized not in self.authorized_sources:
            raise PermissionError("source is not authorized")

        return normalized

    def _rate_limit(self, source: str, current: int) -> None:
        if self.rate_limit_store is not None:
            allowed = self.rate_limit_store(
                source,
                current,
                self.rate_limit_window_seconds,
                self.rate_limit_requests,
            )

            if not allowed:
                raise PermissionError(
                    "request rate limit exceeded"
                )

            return

        cutoff = current - self.rate_limit_window_seconds

        recent = [
            seen
            for seen in self._requests.get(source, [])
            if seen > cutoff
        ]

        if len(recent) >= self.rate_limit_requests:
            raise PermissionError(
                "request rate limit exceeded"
            )

        recent.append(current)
        self._requests[source] = recent

    def _consume_nonce(
        self,
        nonce: str,
        current: int,
    ) -> None:
        expiry = current + self.max_clock_skew_seconds

        if self.nonce_store is not None:
            if not self.nonce_store(
                nonce,
                current,
                expiry,
            ):
                raise PermissionError("request replay")

            return

        self._nonces = {
            key: stored_expiry
            for key, stored_expiry in self._nonces.items()
            if stored_expiry >= current
        }

        if nonce in self._nonces:
            raise PermissionError("request replay")

        self._nonces[nonce] = expiry

    def verify(
        self,
        *,
        source: str,
        identity: str,
        scopes: set[str],
        timestamp: int,
        nonce: str,
        body: bytes,
        signature: str,
        required_scope: str,
        now: int | None = None,
    ) -> None:
        normalized = self._verify_source(source)

        if not hmac.compare_digest(
            identity,
            self.identity,
        ):
            raise PermissionError(
                "wrong service identity"
            )

        if required_scope not in scopes:
            raise PermissionError("missing scope")

        current = (
            int(time.time())
            if now is None
            else now
        )

        if (
            abs(current - timestamp)
            > self.max_clock_skew_seconds
        ):
            raise PermissionError(
                "request timestamp outside window"
            )

        expected = self.sign(
            timestamp,
            nonce,
            body,
        )

        if not hmac.compare_digest(
            expected,
            signature,
        ):
            raise PermissionError(
                "invalid request signature"
            )

        self._rate_limit(
            normalized,
            current,
        )

        self._consume_nonce(
            nonce,
            current,
        )
