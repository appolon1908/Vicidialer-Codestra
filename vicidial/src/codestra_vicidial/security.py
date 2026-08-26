from __future__ import annotations

import hashlib
import hmac
import ipaddress
import time
import re
from collections.abc import Callable
from dataclasses import dataclass, field


NonceStore = Callable[[str, int, int], bool]
RateLimitStore = Callable[[str, int, int, int], bool]
SIGNATURE_VERSION = "v2"
_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%{}/-]*$")


def normalize_request_path(path: str) -> str:
    if not _PATH_PATTERN.fullmatch(path) or "//" in path:
        raise PermissionError("invalid request path")
    segments = path.split("/")
    if any(segment in {".", ".."} for segment in segments):
        raise PermissionError("invalid request path")
    return path


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

    def sign(
        self, timestamp: int, nonce: str, body: bytes, *,
        method: str = "POST", path: str = "/", identity: str | None = None,
        scopes: set[str] | frozenset[str] = frozenset(),
        idempotency_key: str = "", signature_version: str = SIGNATURE_VERSION,
    ) -> str:
        if signature_version != SIGNATURE_VERSION:
            raise PermissionError("unsupported signature version")
        digest = hashlib.sha256(body).hexdigest()
        canonical = "\n".join((
            signature_version,
            method.upper(),
            normalize_request_path(path),
            identity if identity is not None else self.identity,
            " ".join(sorted(scopes)),
            str(timestamp),
            nonce,
            idempotency_key,
            digest,
        ))

        return hmac.new(
            self.key,
            canonical.encode(),
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
        timestamp: int,
    ) -> None:
        expiry = timestamp + self.max_clock_skew_seconds

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
        method: str = "POST",
        path: str = "/",
        idempotency_key: str = "",
        signature_version: str = SIGNATURE_VERSION,
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
            method=method,
            path=path,
            identity=identity,
            scopes=scopes,
            idempotency_key=idempotency_key,
            signature_version=signature_version,
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
            timestamp,
        )
