from __future__ import annotations

import json
import os
import socket
import time
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from .ami_gateway import AMIEventGateway, EventOutbox, TLSDeliveryConfig


def _secret(name: str) -> str:
    path = Path(os.environ[name])
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError(f"{name} must reference an existing absolute file")
    return path.read_text().strip()


def _action(**values: str) -> bytes:
    allowed = {"Login", "Events", "Ping", "Logoff"}
    action = values.get("Action", "")
    if action not in allowed:
        raise ValueError("prohibited AMI action")
    if any("\r" in value or "\n" in value for value in values.values()):
        raise ValueError("invalid AMI action value")
    return ("".join(f"{key}: {value}\r\n" for key, value in values.items())
            + "\r\n").encode()


def _deliver(gateway: AMIEventGateway, endpoint: str, context) -> None:
    for event in gateway.outbox.pending(100):
        body = json.dumps(event, sort_keys=True).encode()
        request = Request(
            endpoint, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": event["event_id"],
            },
        )
        with urlopen(request, context=context, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise OSError("event delivery rejected")
        gateway.outbox.delivered(event["event_id"])


def main() -> None:
    host = os.environ.get("CODESTRA_AMI_HOST", "127.0.0.1")
    port = int(os.environ.get("CODESTRA_AMI_PORT", "5038"))
    if host not in {"127.0.0.1", "localhost"} or port != 5038:
        raise RuntimeError("AMI must remain on loopback port 5038")
    username = _secret("CODESTRA_AMI_USERNAME_FILE")
    secret = _secret("CODESTRA_AMI_SECRET_FILE")
    outbox = EventOutbox(Path(os.environ.get(
        "CODESTRA_AMI_OUTBOX",
        "/var/lib/codestra-ami-event-gateway/events.sqlite3")))
    delivery_enabled = os.environ.get("CODESTRA_EVENT_DELIVERY_ENABLED") == "true"
    endpoint = os.environ.get("CODESTRA_EVENT_ENDPOINT", "")
    tls_context = None
    if delivery_enabled:
        tls_context = TLSDeliveryConfig(
            endpoint=endpoint,
            ca_file=Path(os.environ["CODESTRA_EVENT_CA_FILE"]),
            certificate_file=Path(os.environ["CODESTRA_EVENT_CERT_FILE"]),
            private_key_file=Path(os.environ["CODESTRA_EVENT_KEY_FILE"]),
        ).context()
    delay = 1
    while True:
        epoch = f"{int(time.time())}-{uuid.uuid4().hex}"
        gateway = AMIEventGateway(outbox, epoch)
        try:
            with socket.create_connection((host, port), timeout=10) as connection:
                connection.settimeout(30)
                connection.sendall(_action(
                    Action="Login", Username=username, Secret=secret, Events="on"))
                connection.sendall(_action(Action="Events", EventMask="on"))
                delay = 1
                while True:
                    data = connection.recv(65536)
                    if not data:
                        raise OSError("AMI disconnected")
                    gateway.ingest(data)
                    if delivery_enabled and tls_context is not None:
                        _deliver(gateway, endpoint, tls_context)
        except (OSError, ValueError):
            time.sleep(delay)
            delay = min(delay * 2, 60)


if __name__ == "__main__":
    main()
