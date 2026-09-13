#!/usr/bin/env python3
"""Mission 4 delivery agent: runs ON Server B (the Asterisk/VICIdial host),
polls the Vicidialer-Codestra adapter's /v1/webrtc/pjsip-config/pending
endpoint for generated PJSIP AOR/endpoint/auth (or revoke-teardown)
artifacts that have not yet been applied, writes each to a per-user config
fragment, reloads PJSIP, and acknowledges success back to the adapter.

This is the delivery half that the adapter itself intentionally does not
implement (PR #54's generation logic has no filesystem or AMI-write access
to Server B; this script is the other end of that gap, following the same
"local script talking to a remote API" shape already established by
scripts/vicidial_sync.py in this repo -- direction reversed: that script
pushes local VICIdial events out to Middleware; this one pulls generated
config down and applies it locally).

ONE-TIME MANUAL STEP REQUIRED ON SERVER B, NOT AUTOMATED BY THIS SCRIPT:
the active pjsip.conf must include the directory this script writes to, e.g.:

    #include /etc/asterisk/codestra-webrtc/*.conf

Confirmed this session that /etc/asterisk/pjsip_custom_teardown.conf was NOT
included by the live pjsip.conf -- do not assume any particular include
path is already wired. Whoever has real pjsip.conf access must add the
include line above (or the equivalent for wherever PJSIP_CONFIG_DIR below is
actually pointed) before this script's writes have any effect, and must run
`asterisk -rx "pjsip reload"` once by hand after adding it so the new
include takes effect from a clean state.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests

from codestra_vicidial.security import RequestAuthenticator

BASE_DIR = "/opt/codestra-vicidial-pjsip-sync"
ENV_PATH = os.path.join(BASE_DIR, "config", ".env")
LOG_PATH = os.path.join(BASE_DIR, "logs", "pjsip_config_sync.log")

PENDING_PATH = "/v1/webrtc/pjsip-config/pending"
ACK_PATH = "/v1/webrtc/pjsip-config/ack"
SIGNATURE_VERSION = "v2"


def load_secret_file(path: str) -> str:
    if not path:
        return ""
    try:
        with open(path, "r") as handle:
            return handle.read().strip()
    except IOError:
        logging.warning("Secret file is not readable: %s", path)
        return ""


def load_env() -> dict:
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_PATH)
    except ImportError:
        pass
    hmac_key_file = os.getenv("PJSIP_SYNC_HMAC_KEY_FILE", "")
    return {
        "adapter_base_url": os.getenv(
            "PJSIP_SYNC_ADAPTER_BASE_URL", "https://vicidial-adapter.internal.codestra.co"
        ),
        "identity": os.getenv("PJSIP_SYNC_IDENTITY", "server-b-pjsip-sync"),
        "hmac_key_file": hmac_key_file,
        "hmac_key": load_secret_file(hmac_key_file).encode() if hmac_key_file else b"",
        "campaign_id": os.getenv("PJSIP_SYNC_CAMPAIGN_ID", "TEST_SYN"),
        "config_dir": os.getenv("PJSIP_CONFIG_DIR", "/etc/asterisk/codestra-webrtc"),
        "reload_command": os.getenv(
            "PJSIP_RELOAD_COMMAND", "asterisk -rx 'pjsip reload'"
        ),
        "interval_seconds": int(os.getenv("PJSIP_SYNC_INTERVAL_SECONDS", "15")),
        "kill_switch": os.getenv(
            "PJSIP_SYNC_KILL_SWITCH", "/etc/codestra/disable-pjsip-config-sync"
        ),
        "log_level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "request_timeout_seconds": float(os.getenv("PJSIP_SYNC_TIMEOUT_SECONDS", "10")),
    }


def sync_enabled(config: dict) -> bool:
    """Fail closed: a missing/unreadable HMAC key or the emergency
    file-based kill switch both stop delivery, same convention as
    scripts/vicidial_sync.py's external_delivery_enabled()."""
    if not config["hmac_key"]:
        logging.critical(
            "no readable PJSIP_SYNC_HMAC_KEY_FILE configured; refusing to sync"
        )
        return False
    if os.path.exists(config["kill_switch"]):
        logging.critical(
            "pjsip config sync disabled by kill switch: %s", config["kill_switch"]
        )
        return False
    return True


def setup_logging(level: str) -> None:
    log_dir = os.path.dirname(LOG_PATH)
    if log_dir and not os.path.isdir(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
    )


def signed_headers(
    auth: RequestAuthenticator, *, method: str, path: str, scope: str,
    body: bytes, idempotency_key: str = "",
) -> dict:
    timestamp = int(time.time())
    nonce = uuid.uuid4().hex
    signature = auth.sign(
        timestamp, nonce, body, method=method, path=path,
        scopes={scope}, idempotency_key=idempotency_key,
        signature_version=SIGNATURE_VERSION,
    )
    headers = {
        "X-Service-Identity": auth.identity,
        "X-Service-Scopes": scope,
        "X-Request-Timestamp": str(timestamp),
        "X-Request-Nonce": nonce,
        "X-Request-Signature": signature,
        "X-Signature-Version": SIGNATURE_VERSION,
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def fetch_pending(config: dict, auth: RequestAuthenticator) -> list[dict]:
    url = config["adapter_base_url"].rstrip("/") + PENDING_PATH
    headers = signed_headers(
        auth, method="GET", path=PENDING_PATH, scope="telephony:read", body=b"",
    )
    response = requests.get(url, headers=headers, timeout=config["request_timeout_seconds"])
    response.raise_for_status()
    return response.json().get("pending", [])


def apply_config(config: dict, item: dict) -> None:
    """Write one user's generated artifact and reload PJSIP. Idempotent:
    identical content_hash produces an identical file, and a reload is safe
    to run repeatedly."""
    config_dir = Path(config["config_dir"])
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / f"{item['user_id']}.conf"
    target.write_text(item["content"], encoding="utf-8")
    logging.info(
        "pjsip.config.written",
        extra={
            "user_id": item["user_id"], "extension": item["extension"],
            "active": item["active"], "content_hash": item["content_hash"],
            "path": str(target),
        },
    )
    subprocess.run(config["reload_command"], shell=True, check=True, timeout=30)


def ack_applied(config: dict, auth: RequestAuthenticator, item: dict) -> None:
    payload = {
        "context": {"campaign_id": config["campaign_id"]},
        "user_id": item["user_id"],
        "content_hash": item["content_hash"],
    }
    body = json.dumps(payload).encode()
    idempotency_key = f"pjsip-ack-{item['user_id']}-{item['content_hash']}"[:200]
    headers = signed_headers(
        auth, method="POST", path=ACK_PATH, scope="telephony:webrtc-provision",
        body=body, idempotency_key=idempotency_key,
    )
    headers["Content-Type"] = "application/json"
    url = config["adapter_base_url"].rstrip("/") + ACK_PATH
    response = requests.post(url, data=body, headers=headers, timeout=config["request_timeout_seconds"])
    if response.status_code == 403:
        logging.warning(
            "ack rejected as forbidden (webrtc_provisioning likely disabled at the "
            "adapter): %s", response.text,
        )
        return
    response.raise_for_status()


def run_once(config: dict, auth: RequestAuthenticator) -> int:
    if not sync_enabled(config):
        return 0
    applied_count = 0
    for item in fetch_pending(config, auth):
        try:
            apply_config(config, item)
            ack_applied(config, auth, item)
            applied_count += 1
        except Exception:
            logging.exception(
                "failed to apply/ack pjsip config for user_id=%s", item.get("user_id")
            )
    return applied_count


def main() -> None:
    config = load_env()
    setup_logging(config["log_level"])
    auth = RequestAuthenticator(
        config["identity"], config["hmac_key"], frozenset(),
    )
    once = "--once" in sys.argv
    while True:
        try:
            applied = run_once(config, auth)
            if applied:
                logging.info("pjsip config sync applied %d artifact(s)", applied)
        except Exception:
            logging.exception("pjsip config sync iteration failed")
        if once:
            break
        time.sleep(config["interval_seconds"])


if __name__ == "__main__":
    main()
