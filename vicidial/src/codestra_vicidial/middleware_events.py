from __future__ import annotations

import hashlib
import hmac
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from .ami_gateway import (
    GatewayConfigurationError,
    LifecycleStore,
    read_private_text,
)


MIDDLEWARE_EVENT_PATH = "/api/v1/vicidial/events"
REQUIRED_SCOPE = "telephony.events.publish"
PRODUCER_CLIENT_ID = "vicidial-adapter"
_RETRYABLE_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})


class RetryableDeliveryError(RuntimeError):
    """The event was not accepted and can be retried idempotently."""


class AmbiguousDeliveryError(RuntimeError):
    """The remote outcome is unknown and requires the same event ID replay."""


class PermanentDeliveryError(RuntimeError):
    """The event is invalid or unauthorized and must not be retried blindly."""


@dataclass(frozen=True, slots=True)
class HTTPResult:
    status: int
    headers: Mapping[str, str]
    body: bytes


class EventHTTPTransport(Protocol):
    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HTTPResult: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class URLlibMTLSTransport:
    """Bounded HTTPS client with mandatory trust roots and optional mTLS."""

    def __init__(
        self,
        *,
        ca_file: str | Path,
        client_cert_file: str | Path | None = None,
        client_key_file: str | Path | None = None,
        server_hostname: str | None = None,
        maximum_response_bytes: int = 262144,
    ) -> None:
        self.ca_file = Path(ca_file)
        self.client_cert_file = (
            Path(client_cert_file) if client_cert_file is not None else None
        )
        self.client_key_file = (
            Path(client_key_file) if client_key_file is not None else None
        )
        self.server_hostname = (server_hostname or "").strip() or None
        self.maximum_response_bytes = maximum_response_bytes
        if not self.ca_file.is_absolute():
            raise GatewayConfigurationError("Middleware CA path must be absolute")
        if bool(self.client_cert_file) != bool(self.client_key_file):
            raise GatewayConfigurationError(
                "Middleware client certificate and key must be configured together"
            )
        for value, label in (
            (self.client_cert_file, "Middleware client certificate"),
            (self.client_key_file, "Middleware client key"),
        ):
            if value is not None and not value.is_absolute():
                raise GatewayConfigurationError(f"{label} path must be absolute")
        if maximum_response_bytes < 1024 or maximum_response_bytes > 1048576:
            raise GatewayConfigurationError(
                "maximum response size must be between 1 KiB and 1 MiB"
            )

    def _opener(self) -> urllib.request.OpenerDirector:
        context = ssl.create_default_context(cafile=str(self.ca_file))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        if self.client_cert_file and self.client_key_file:
            context.load_cert_chain(
                certfile=str(self.client_cert_file),
                keyfile=str(self.client_key_file),
            )
        return urllib.request.build_opener(
            _NoRedirectHandler(),
            urllib.request.HTTPSHandler(context=context),
        )

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HTTPResult:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise PermanentDeliveryError("event transport requires HTTPS")
        if self.server_hostname and parsed.hostname != self.server_hostname:
            raise PermanentDeliveryError(
                "event URL hostname does not match the configured TLS identity"
            )
        request = urllib.request.Request(
            url=url,
            data=body if method.upper() != "GET" else None,
            headers=dict(headers),
            method=method.upper(),
        )
        try:
            with self._opener().open(
                request,
                timeout=timeout_seconds,
            ) as response:
                raw = response.read(self.maximum_response_bytes + 1)
                if len(raw) > self.maximum_response_bytes:
                    raise PermanentDeliveryError(
                        "remote response exceeded its bounded size"
                    )
                return HTTPResult(
                    status=int(response.status),
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=raw,
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read(self.maximum_response_bytes + 1)
            if len(raw) > self.maximum_response_bytes:
                raw = b""
            return HTTPResult(
                status=int(exc.code),
                headers={key.lower(): value for key, value in exc.headers.items()},
                body=raw,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AmbiguousDeliveryError(
                "HTTPS request outcome is unknown"
            ) from exc


def _parse_https_endpoint(value: str, *, label: str):
    # Reject controls before urlsplit can silently remove them.
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise GatewayConfigurationError(f"{label} must be a clean HTTPS endpoint")
    cleaned = value.strip()
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(c.isspace() for c in cleaned)
            or "\\" in cleaned
            or parsed.query
            or parsed.fragment
            or port == 0
            or parsed.netloc.endswith(":")
        ):
            raise ValueError("invalid endpoint")
    except ValueError:
        raise GatewayConfigurationError(
            f"{label} must be a clean HTTPS endpoint"
        ) from None
    return cleaned, parsed


def clean_https_origin(value: str, *, label: str) -> str:
    cleaned, parsed = _parse_https_endpoint(value, label=label)
    if parsed.path not in {"", "/"}:
        raise GatewayConfigurationError(f"{label} must be a clean HTTPS origin")
    return cleaned.rstrip("/")


def clean_https_url(value: str, *, label: str) -> str:
    cleaned, parsed = _parse_https_endpoint(value, label=label)
    if not parsed.path.startswith("/"):
        raise GatewayConfigurationError(f"{label} must be a clean HTTPS URL")
    return cleaned


@dataclass(frozen=True, slots=True)
class EventDeliveryConfig:
    middleware_base_url: str
    keycloak_token_url: str
    client_id: str
    client_secret: str
    webhook_secret: bytes
    request_timeout_seconds: float = 10.0
    maximum_attempts: int = 10
    maximum_pending_events: int = 10000
    minimum_retry_seconds: float = 1.0
    maximum_retry_seconds: float = 60.0

    def validate(self) -> None:
        clean_https_origin(
            self.middleware_base_url,
            label="MIDDLEWARE_BASE_URL",
        )
        clean_https_url(
            self.keycloak_token_url,
            label="KEYCLOAK_TOKEN_URL",
        )
        if self.client_id != PRODUCER_CLIENT_ID:
            raise GatewayConfigurationError(
                "event client ID must be exactly vicidial-adapter"
            )
        if len(self.client_secret.encode("utf-8")) < 32:
            raise GatewayConfigurationError(
                "Keycloak client secret must contain at least 32 bytes"
            )
        if len(self.webhook_secret) < 32:
            raise GatewayConfigurationError(
                "Middleware webhook secret must contain at least 32 bytes"
            )
        if not 1 <= self.request_timeout_seconds <= 60:
            raise GatewayConfigurationError(
                "event request timeout must be between 1 and 60 seconds"
            )
        if not 1 <= self.maximum_attempts <= 100:
            raise GatewayConfigurationError(
                "event delivery maximum attempts must be between 1 and 100"
            )
        if not 1 <= self.maximum_pending_events <= 1000000:
            raise GatewayConfigurationError(
                "event backlog limit must be between 1 and 1,000,000"
            )
        if not 0.1 <= self.minimum_retry_seconds <= 60:
            raise GatewayConfigurationError(
                "minimum retry interval must be between 0.1 and 60 seconds"
            )
        if not self.minimum_retry_seconds <= self.maximum_retry_seconds <= 3600:
            raise GatewayConfigurationError(
                "maximum retry interval is invalid"
            )

    @property
    def event_url(self) -> str:
        return self.middleware_base_url.rstrip("/") + MIDDLEWARE_EVENT_PATH


@dataclass(slots=True)
class AccessToken:
    value: str
    expires_at: float


class ClientCredentialsTokenProvider:
    def __init__(
        self,
        *,
        config: EventDeliveryConfig,
        transport: EventHTTPTransport,
        clock=time.time,
    ) -> None:
        self.config = config
        self.transport = transport
        self.clock = clock
        self._token: AccessToken | None = None
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._token = None

    def token(self, *, force_refresh: bool = False) -> str:
        with self._lock:
            now = self.clock()
            if (
                not force_refresh
                and self._token is not None
                and self._token.expires_at - now > 15
            ):
                return self._token.value
            token = self._request_token(now)
            self._token = token
            return token.value

    def _request_token(self, now: float) -> AccessToken:
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "scope": REQUIRED_SCOPE,
            }
        ).encode("ascii")
        response = self.transport.request(
            method="POST",
            url=self.config.keycloak_token_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "codestra-vicidial-ami-events/1.0",
            },
            body=body,
            timeout_seconds=self.config.request_timeout_seconds,
        )
        if response.status in _RETRYABLE_HTTP:
            raise RetryableDeliveryError(
                f"Keycloak token endpoint returned HTTP {response.status}"
            )
        if response.status != 200:
            raise PermanentDeliveryError(
                f"Keycloak token endpoint returned HTTP {response.status}"
            )
        try:
            value = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PermanentDeliveryError(
                "Keycloak token response is not valid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise PermanentDeliveryError(
                "Keycloak token response must be an object"
            )
        token_type = value.get("token_type")
        access_token = value.get("access_token")
        expires_in = value.get("expires_in")
        if token_type != "Bearer":
            raise PermanentDeliveryError(
                "Keycloak token response must use Bearer token type"
            )
        if not isinstance(access_token, str) or not access_token.strip():
            raise PermanentDeliveryError(
                "Keycloak token response has no access_token"
            )
        if (
            not isinstance(expires_in, int)
            or isinstance(expires_in, bool)
            or not 30 <= expires_in <= 600
        ):
            raise PermanentDeliveryError(
                "Keycloak token lifetime must be between 30 and 600 seconds"
            )
        return AccessToken(
            value=access_token.strip(),
            expires_at=now + expires_in,
        )


def canonical_event_body(envelope: Mapping[str, Any]) -> bytes:
    return json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_event(
    secret: bytes,
    *,
    timestamp: str,
    event_id: str,
    body: bytes,
) -> str:
    body_sha = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(
        (
            "v1",
            "POST",
            MIDDLEWARE_EVENT_PATH,
            timestamp,
            event_id,
            PRODUCER_CLIENT_ID,
            body_sha,
        )
    ).encode("utf-8")
    return "sha256=" + hmac.new(
        secret,
        canonical,
        hashlib.sha256,
    ).hexdigest()


class MiddlewareEventPublisher:
    def __init__(
        self,
        *,
        config: EventDeliveryConfig,
        transport: EventHTTPTransport,
        token_provider: ClientCredentialsTokenProvider,
        clock=time.time,
    ) -> None:
        config.validate()
        self.config = config
        self.transport = transport
        self.token_provider = token_provider
        self.clock = clock

    def publish(self, envelope: Mapping[str, Any]) -> None:
        event_id = envelope.get("event_id")
        event_type = envelope.get("event_type")
        tenant_id = envelope.get("tenant_id")
        correlation_id = envelope.get("correlation_id")
        source = envelope.get("source")
        idempotency_key = envelope.get("idempotency_key")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                event_id,
                event_type,
                tenant_id,
                correlation_id,
                source,
                idempotency_key,
            )
        ):
            raise PermanentDeliveryError(
                "event envelope is missing canonical identifiers"
            )
        if source != PRODUCER_CLIENT_ID:
            raise PermanentDeliveryError(
                "event source is not vicidial-adapter"
            )
        if event_id != idempotency_key:
            raise PermanentDeliveryError(
                "event idempotency key must equal event ID"
            )
        if not event_type.startswith("codestra.vicidial.call.lifecycle."):
            raise PermanentDeliveryError(
                "event type is not a VICIdial lifecycle event"
            )
        body = canonical_event_body(envelope)
        token = self.token_provider.token()
        response = self._post(
            body=body,
            token=token,
            event_id=event_id,
            event_type=event_type,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
        )
        if response.status == 401:
            self.token_provider.invalidate()
            response = self._post(
                body=body,
                token=self.token_provider.token(force_refresh=True),
                event_id=event_id,
                event_type=event_type,
                tenant_id=tenant_id,
                correlation_id=correlation_id,
            )
        self._validate_result(
            response,
            event_id=event_id,
            tenant_id=tenant_id,
        )

    def _post(
        self,
        *,
        body: bytes,
        token: str,
        event_id: str,
        event_type: str,
        tenant_id: str,
        correlation_id: str,
    ) -> HTTPResult:
        timestamp = str(int(self.clock()))
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Idempotency-Key": event_id,
            "User-Agent": "codestra-vicidial-ami-events/1.0",
            "X-Codestra-Event-Id": event_id,
            "X-Codestra-Event-Type": event_type,
            "X-Codestra-Source": PRODUCER_CLIENT_ID,
            "X-Codestra-Tenant-Id": tenant_id,
            "X-Codestra-Timestamp": timestamp,
            "X-Codestra-Signature": sign_event(
                self.config.webhook_secret,
                timestamp=timestamp,
                event_id=event_id,
                body=body,
            ),
            "X-Correlation-Id": correlation_id,
        }
        return self.transport.request(
            method="POST",
            url=self.config.event_url,
            headers=headers,
            body=body,
            timeout_seconds=self.config.request_timeout_seconds,
        )

    @staticmethod
    def _validate_result(
        response: HTTPResult,
        *,
        event_id: str,
        tenant_id: str,
    ) -> None:
        if response.status in _RETRYABLE_HTTP:
            raise RetryableDeliveryError(
                f"Middleware returned retryable HTTP {response.status}"
            )
        if response.status not in {200, 202}:
            raise PermanentDeliveryError(
                f"Middleware rejected event with HTTP {response.status}"
            )
        try:
            value = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PermanentDeliveryError(
                "Middleware response is not valid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise PermanentDeliveryError(
                "Middleware response must be an object"
            )
        expected_status = "duplicate" if response.status == 200 else "accepted"
        if (
            value.get("event_id") != event_id
            or value.get("tenant_id") != tenant_id
            or value.get("status") != expected_status
            or value.get("duplicate") is not (response.status == 200)
        ):
            raise PermanentDeliveryError(
                "Middleware acceptance response identity does not match"
            )


class EventDeliveryWorker:
    def __init__(
        self,
        *,
        store: LifecycleStore,
        publisher: MiddlewareEventPublisher,
        worker_id: str | None = None,
        maximum_attempts: int = 10,
        maximum_pending_events: int = 10000,
        minimum_retry_seconds: float = 1.0,
        maximum_retry_seconds: float = 60.0,
        idle_sleep_seconds: float = 0.25,
    ) -> None:
        self.store = store
        self.publisher = publisher
        self.worker_id = worker_id or (
            "ami-delivery-" + uuid.uuid4().hex
        )
        self.maximum_attempts = maximum_attempts
        self.maximum_pending_events = maximum_pending_events
        self.minimum_retry_seconds = minimum_retry_seconds
        self.maximum_retry_seconds = maximum_retry_seconds
        self.idle_sleep_seconds = idle_sleep_seconds

    def retry_delay(self, attempt_count: int) -> float:
        exponent = max(0, min(attempt_count - 1, 16))
        return min(
            self.maximum_retry_seconds,
            self.minimum_retry_seconds * (2**exponent),
        )

    def run_once(self) -> bool:
        pending = self.store.pending_count()
        if pending > self.maximum_pending_events:
            raise RuntimeError(
                "AMI lifecycle event backlog exceeded its hard limit"
            )
        claimed = self.store.claim_due(worker_id=self.worker_id)
        if claimed is None:
            return False
        event_id = claimed["event_id"]
        attempt_count = int(claimed["attempt_count"])
        try:
            self.publisher.publish(claimed["envelope"])
        except PermanentDeliveryError as exc:
            self.store.mark_dead_letter(
                event_id,
                worker_id=self.worker_id,
                error=exc,
            )
        except (RetryableDeliveryError, AmbiguousDeliveryError) as exc:
            if attempt_count >= self.maximum_attempts:
                self.store.mark_dead_letter(
                    event_id,
                    worker_id=self.worker_id,
                    error=exc,
                )
            else:
                self.store.mark_retry(
                    event_id,
                    worker_id=self.worker_id,
                    error=exc,
                    delay_seconds=self.retry_delay(attempt_count),
                )
        except Exception as exc:
            if attempt_count >= self.maximum_attempts:
                self.store.mark_dead_letter(
                    event_id,
                    worker_id=self.worker_id,
                    error=exc,
                )
            else:
                self.store.mark_retry(
                    event_id,
                    worker_id=self.worker_id,
                    error=exc,
                    delay_seconds=self.retry_delay(attempt_count),
                )
        else:
            self.store.mark_delivered(
                event_id,
                worker_id=self.worker_id,
            )
        return True

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            if not self.run_once():
                stop.wait(self.idle_sleep_seconds)


def _positive_float(source: Mapping[str, str], name: str, default: str) -> float:
    raw = source.get(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise GatewayConfigurationError(f"{name} must be numeric") from exc
    if value <= 0:
        raise GatewayConfigurationError(f"{name} must be positive")
    return value


def _positive_int(source: Mapping[str, str], name: str, default: str) -> int:
    raw = source.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise GatewayConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise GatewayConfigurationError(f"{name} must be positive")
    return value


def delivery_config_from_environment(
    source: Mapping[str, str] | None = None,
) -> tuple[EventDeliveryConfig, URLlibMTLSTransport]:
    values = os.environ if source is None else source
    middleware_base_url = clean_https_origin(
        values.get("MIDDLEWARE_BASE_URL", ""),
        label="MIDDLEWARE_BASE_URL",
    )
    keycloak_token_url = clean_https_url(
        values.get("KEYCLOAK_TOKEN_URL", ""),
        label="KEYCLOAK_TOKEN_URL",
    )
    client_secret = read_private_text(
        values.get("MIDDLEWARE_CLIENT_SECRET_FILE", ""),
        minimum_bytes=32,
    )
    webhook_secret = read_private_text(
        values.get("MIDDLEWARE_WEBHOOK_SECRET_FILE", ""),
        minimum_bytes=32,
    ).encode("utf-8")
    ca_file = Path(values.get("MIDDLEWARE_CA_FILE", ""))
    client_cert = (
        Path(values["MIDDLEWARE_CLIENT_CERT_FILE"])
        if values.get("MIDDLEWARE_CLIENT_CERT_FILE")
        else None
    )
    client_key = (
        Path(values["MIDDLEWARE_CLIENT_KEY_FILE"])
        if values.get("MIDDLEWARE_CLIENT_KEY_FILE")
        else None
    )
    config = EventDeliveryConfig(
        middleware_base_url=middleware_base_url,
        keycloak_token_url=keycloak_token_url,
        client_id=values.get("MIDDLEWARE_CLIENT_ID", PRODUCER_CLIENT_ID).strip(),
        client_secret=client_secret,
        webhook_secret=webhook_secret,
        request_timeout_seconds=_positive_float(
            values,
            "MIDDLEWARE_REQUEST_TIMEOUT_SECONDS",
            "10",
        ),
        maximum_attempts=_positive_int(
            values,
            "MIDDLEWARE_EVENT_MAX_ATTEMPTS",
            "10",
        ),
        maximum_pending_events=_positive_int(
            values,
            "MIDDLEWARE_EVENT_BACKLOG_LIMIT",
            "10000",
        ),
        minimum_retry_seconds=_positive_float(
            values,
            "MIDDLEWARE_EVENT_MIN_RETRY_SECONDS",
            "1",
        ),
        maximum_retry_seconds=_positive_float(
            values,
            "MIDDLEWARE_EVENT_MAX_RETRY_SECONDS",
            "60",
        ),
    )
    config.validate()
    transport = URLlibMTLSTransport(
        ca_file=ca_file,
        client_cert_file=client_cert,
        client_key_file=client_key,
        server_hostname=(
            values.get("MIDDLEWARE_TLS_SERVER_NAME", "").strip() or None
        ),
    )
    return config, transport
