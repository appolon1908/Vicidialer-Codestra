from __future__ import annotations

import json
import logging
import os
import signal
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from .ami_gateway import (
    AMIClient,
    AMIConnectionConfig,
    AMIProtocolError,
    AgentDirectory,
    GatewayConfigurationError,
    LifecycleStore,
    parse_bool,
    read_private_text,
    require_false,
)
from .middleware_events import (
    ClientCredentialsTokenProvider,
    EventDeliveryWorker,
    MiddlewareEventPublisher,
    URLlibMTLSTransport,
    delivery_config_from_environment,
)


log = logging.getLogger(__name__)

_EFFECT_FLAGS = (
    "ENABLE_VICIDIAL_WRITES",
    "VICIDIAL_WRITES_ENABLED",
    "ENABLE_PROVISIONING",
    "ENABLE_AGENT_SYNC",
    "ENABLE_LEAD_PUBLICATION",
    "ENABLE_CALLBACK_WRITES",
    "ENABLE_TRANSFER_POLICY_SYNC",
    "ENABLE_LIVE_CALL_CONTROL",
    "ENABLE_WARM_TRANSFER",
    "ENABLE_IVR_ROUTING",
    "ENABLE_PRODUCTION_DIALING",
    "LIVE_PSTN_DIALING",
    "CALLBACK_DISPATCH",
    "CALLBACK_DISPATCH_ENABLED",
    "ENABLE_SMS_DELIVERY",
    "LIVE_SMS_DELIVERY",
    "ENABLE_EMAIL_DELIVERY",
    "LIVE_EMAIL_DELIVERY",
    "ENABLE_AI_ACTIONS",
    "TELEPHONY_ADMIN_WRITE",
    "RECORDING_WRITE",
    "LEAD_PUBLICATION",
)


def _positive_float(
    source: Mapping[str, str],
    name: str,
    default: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = source.get(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise GatewayConfigurationError(f"{name} must be numeric") from exc
    if not minimum <= value <= maximum:
        raise GatewayConfigurationError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _bounded_port(
    source: Mapping[str, str],
    name: str,
    default: str,
) -> int:
    raw = source.get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise GatewayConfigurationError(f"{name} must be an integer") from exc
    if not 1 <= value <= 65535:
        raise GatewayConfigurationError(f"{name} is outside the valid port range")
    return value


def _absolute_path(
    source: Mapping[str, str],
    name: str,
    default: str,
) -> Path:
    value = Path(source.get(name, default))
    if not value.is_absolute():
        raise GatewayConfigurationError(f"{name} must be an absolute path")
    return value


@dataclass(frozen=True, slots=True)
class GatewayRuntimeConfig:
    ami: AMIConnectionConfig
    agent_directory_file: Path
    state_database: Path
    status_file: Path
    reconnect_min_seconds: float
    reconnect_max_seconds: float
    delivery_idle_seconds: float
    source_sha: str

    @classmethod
    def from_environment(
        cls,
        source: Mapping[str, str] | None = None,
    ) -> "GatewayRuntimeConfig":
        values = os.environ if source is None else source
        if not parse_bool(
            values.get("VICIDIAL_LIFECYCLE_EVENTS_ENABLED"),
            name="VICIDIAL_LIFECYCLE_EVENTS_ENABLED",
        ):
            raise GatewayConfigurationError(
                "VICIDIAL_LIFECYCLE_EVENTS_ENABLED must be explicitly true"
            )
        if not parse_bool(values.get("SEND_EVENTS"), name="SEND_EVENTS"):
            raise GatewayConfigurationError(
                "SEND_EVENTS must be explicitly true for the event-only gateway"
            )
        require_false(values, *_EFFECT_FLAGS)
        calls_placed = values.get("CALLS_PLACED", "0").strip()
        if calls_placed != "0":
            raise GatewayConfigurationError(
                "CALLS_PLACED must remain exactly 0 during source and "
                "no-effect certification"
            )

        secret = read_private_text(
            values.get("AMI_SECRET_FILE", ""),
            minimum_bytes=16,
        )
        username = values.get("AMI_USERNAME", "").strip()
        if not username:
            raise GatewayConfigurationError("AMI_USERNAME is required")
        ami = AMIConnectionConfig(
            host=values.get("AMI_HOST", "127.0.0.1").strip(),
            port=_bounded_port(values, "AMI_PORT", "5038"),
            username=username,
            secret=secret,
            connect_timeout_seconds=_positive_float(
                values,
                "AMI_CONNECT_TIMEOUT_SECONDS",
                "5",
                minimum=1,
                maximum=30,
            ),
            receive_timeout_seconds=_positive_float(
                values,
                "AMI_RECEIVE_TIMEOUT_SECONDS",
                "1",
                minimum=0.1,
                maximum=10,
            ),
        )
        ami.validate()

        minimum = _positive_float(
            values,
            "AMI_RECONNECT_MIN_SECONDS",
            "1",
            minimum=0.1,
            maximum=60,
        )
        maximum = _positive_float(
            values,
            "AMI_RECONNECT_MAX_SECONDS",
            "30",
            minimum=1,
            maximum=300,
        )
        if maximum < minimum:
            raise GatewayConfigurationError(
                "AMI_RECONNECT_MAX_SECONDS must not be below the minimum"
            )

        source_sha = values.get("APP_SOURCE_SHA", "").strip()
        if source_sha and (
            len(source_sha) != 40
            or any(
                char not in "0123456789abcdef"
                for char in source_sha.lower()
            )
        ):
            raise GatewayConfigurationError(
                "APP_SOURCE_SHA must be an exact 40-character hexadecimal SHA"
            )

        return cls(
            ami=ami,
            agent_directory_file=_absolute_path(
                values,
                "AMI_AGENT_DIRECTORY_FILE",
                "/etc/codestra-vicidial/ami-agent-directory.json",
            ),
            state_database=_absolute_path(
                values,
                "AMI_EVENT_STATE_DB",
                "/var/lib/codestra-vicidial/ami-events.sqlite3",
            ),
            status_file=_absolute_path(
                values,
                "AMI_EVENT_STATUS_FILE",
                "/run/codestra-vicidial/ami-events-status.json",
            ),
            reconnect_min_seconds=minimum,
            reconnect_max_seconds=maximum,
            delivery_idle_seconds=_positive_float(
                values,
                "MIDDLEWARE_EVENT_IDLE_SECONDS",
                "0.25",
                minimum=0.05,
                maximum=5,
            ),
            source_sha=source_sha or "unknown",
        )


class StatusWriter:
    """Write non-secret health state atomically for a local monitor."""

    def __init__(self, path: Path, *, source_sha: str) -> None:
        self.path = path
        self.source_sha = source_sha
        self._lock = threading.Lock()
        self._runtime_status = "starting"
        self._state: dict[str, object] = {
            "schema_version": "1.0",
            "service": "codestra-vicidial-ami-events",
            "status": "starting",
            "ami_connected": False,
            "delivery_worker_running": False,
            "pending_events": 0,
            "retrying_events": 0,
            "dead_letter_events": 0,
            "delivery_status": "idle",
            "last_ami_event_at": None,
            "last_delivery_cycle_at": None,
            "last_error_code": None,
            "source_sha": source_sha,
        }

    def update(self, **values: object) -> None:
        with self._lock:
            if "status" in values:
                self._runtime_status = str(values["status"])
            self._state.update(values)
            if self._state["dead_letter_events"]:
                delivery_status = "dead_letter"
            elif self._state["retrying_events"]:
                delivery_status = "retrying"
            elif self._state["pending_events"]:
                delivery_status = "pending"
            else:
                delivery_status = "idle"
            self._state["delivery_status"] = delivery_status
            self._state["status"] = (
                "degraded"
                if self._runtime_status == "ready"
                and delivery_status in {"retrying", "dead_letter"}
                else self._runtime_status
            )
            self._state["observed_at"] = (
                datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            )
            self._write_locked()

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        temporary = self.path.with_name(
            self.path.name
            + f".tmp.{os.getpid()}.{threading.get_ident()}"
        )
        payload = json.dumps(
            self._state,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o640,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        os.replace(temporary, self.path)


class GatewayRuntime:
    def __init__(
        self,
        *,
        config: GatewayRuntimeConfig,
        directory: AgentDirectory,
        store: LifecycleStore,
        ami_client: AMIClient,
        delivery_worker: EventDeliveryWorker,
        status: StatusWriter,
    ) -> None:
        self.config = config
        self.directory = directory
        self.store = store
        self.ami_client = ami_client
        self.delivery_worker = delivery_worker
        self.status = status
        self.stop = threading.Event()
        self._delivery_error: BaseException | None = None

    def request_stop(self, *_args: object) -> None:
        self.stop.set()

    def _delivery_loop(self) -> None:
        self.status.update(
            delivery_worker_running=True,
            **self.store.delivery_health(),
        )
        try:
            while not self.stop.is_set():
                worked = self.delivery_worker.run_once()
                self.status.update(
                    delivery_worker_running=True,
                    **self.store.delivery_health(),
                    last_delivery_cycle_at=(
                        datetime.now(timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z")
                    ),
                )
                if not worked:
                    self.stop.wait(self.config.delivery_idle_seconds)
        except BaseException as exc:
            self._delivery_error = exc
            self.status.update(
                status="failed",
                delivery_worker_running=False,
                last_error_code=exc.__class__.__name__,
                **self.store.delivery_health(),
            )
            self.stop.set()
        finally:
            self.status.update(delivery_worker_running=False)

    def run(self) -> None:
        delivery = threading.Thread(
            target=self._delivery_loop,
            name="vicidial-event-delivery",
            daemon=True,
        )
        delivery.start()
        reconnect_delay = self.config.reconnect_min_seconds
        try:
            while not self.stop.is_set():
                self.status.update(
                    status="connecting",
                    ami_connected=False,
                    **self.store.delivery_health(),
                    last_error_code=None,
                )
                authenticated = threading.Event()

                def connected() -> None:
                    authenticated.set()
                    self.status.update(
                        status="ready",
                        ami_connected=True,
                        last_error_code=None,
                        **self.store.delivery_health(),
                    )

                try:
                    for message in self.ami_client.messages(
                        self.stop,
                        on_connected=connected,
                    ):
                        self.store.process(
                            message,
                            self.directory,
                            observed_at=datetime.now(timezone.utc),
                        )
                        self.status.update(
                            status="ready",
                            ami_connected=True,
                            last_ami_event_at=(
                                datetime.now(timezone.utc)
                                .isoformat()
                                .replace("+00:00", "Z")
                            ),
                            **self.store.delivery_health(),
                        )
                        reconnect_delay = self.config.reconnect_min_seconds
                        if self.stop.is_set():
                            break
                    if not self.stop.is_set() and authenticated.is_set():
                        raise ConnectionError("AMI event stream ended")
                except (
                    AMIProtocolError,
                    ConnectionError,
                    OSError,
                    TimeoutError,
                ) as exc:
                    if self.stop.is_set():
                        break
                    log.warning(
                        "AMI event stream unavailable; reconnecting: %s",
                        exc.__class__.__name__,
                    )
                    self.status.update(
                        status="degraded",
                        ami_connected=False,
                        last_error_code=exc.__class__.__name__,
                        **self.store.delivery_health(),
                    )
                    self.stop.wait(reconnect_delay)
                    reconnect_delay = min(
                        self.config.reconnect_max_seconds,
                        reconnect_delay * 2,
                    )
        finally:
            self.stop.set()
            delivery.join(timeout=15)
            self.status.update(
                status=(
                    "stopped"
                    if self._delivery_error is None
                    else "failed"
                ),
                ami_connected=False,
                delivery_worker_running=False,
                **self.store.delivery_health(),
            )
        if delivery.is_alive():
            raise RuntimeError("event delivery worker did not stop cleanly")
        if self._delivery_error is not None:
            raise RuntimeError("event delivery worker failed") from self._delivery_error


def build_runtime(
    source: Mapping[str, str] | None = None,
) -> GatewayRuntime:
    values = os.environ if source is None else source
    config = GatewayRuntimeConfig.from_environment(values)
    directory = AgentDirectory.from_file(config.agent_directory_file)
    store = LifecycleStore(config.state_database)
    delivery_config, transport = delivery_config_from_environment(values)
    # Keycloak and Middleware may have distinct DNS identities. Preserve the
    # configured trust roots and client identity, but bind each request path to
    # its own configured hostname rather than applying the Middleware pin twice.
    token_transport = URLlibMTLSTransport(
        ca_file=transport.ca_file,
        client_cert_file=transport.client_cert_file,
        client_key_file=transport.client_key_file,
        server_hostname=urlsplit(delivery_config.keycloak_token_url).hostname,
        maximum_response_bytes=transport.maximum_response_bytes,
    )
    token_provider = ClientCredentialsTokenProvider(
        config=delivery_config,
        transport=token_transport,
    )
    publisher = MiddlewareEventPublisher(
        config=delivery_config,
        transport=transport,
        token_provider=token_provider,
    )
    worker = EventDeliveryWorker(
        store=store,
        publisher=publisher,
        maximum_attempts=delivery_config.maximum_attempts,
        maximum_pending_events=delivery_config.maximum_pending_events,
        minimum_retry_seconds=delivery_config.minimum_retry_seconds,
        maximum_retry_seconds=delivery_config.maximum_retry_seconds,
        idle_sleep_seconds=config.delivery_idle_seconds,
    )
    return GatewayRuntime(
        config=config,
        directory=directory,
        store=store,
        ami_client=AMIClient(config.ami),
        delivery_worker=worker,
        status=StatusWriter(
            config.status_file,
            source_sha=config.source_sha,
        ),
    )


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    runtime = build_runtime()
    signal.signal(signal.SIGTERM, runtime.request_stop)
    signal.signal(signal.SIGINT, runtime.request_stop)
    runtime.run()


if __name__ == "__main__":
    main()
