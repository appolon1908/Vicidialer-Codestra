#!/usr/bin/env python3
"""Fail closed when the adapter runtime contract is unsafe."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from codestra_vicidial.reconciliation import ReconciliationDenied, ReconciliationPolicy
from codestra_vicidial.internal_calling import InternalCallError, InternalCallPolicy
from codestra_vicidial.release import load_release_info

ALWAYS_FALSE_FLAGS = (
    "ENABLE_AGENT_SYNC",
    "ENABLE_LEAD_PUBLICATION",
    "ENABLE_LIVE_CALL_CONTROL",
    "ENABLE_WARM_TRANSFER",
    "ENABLE_IVR_ROUTING",
    "ENABLE_IVR_CALLBACKS",
    "ENABLE_PRODUCTION_DIALING",
    "ENABLE_SMS_DELIVERY",
    "ENABLE_EMAIL_DELIVERY",
    "ENABLE_EXTERNAL_DELIVERY",
    "ENABLE_AI_ACTIONS",
)
DEPRECATED_FLAGS = (
    "ENABLE_CALLBACK_WRITES",
    "ENABLE_TRANSFER_POLICY_SYNC",
    "ENABLE_PSTN_DIALING",
)


def require_false(name: str) -> None:
    if os.environ.get(name, "false").lower() != "false":
        raise SystemExit("{} must remain false".format(name))


for name in ALWAYS_FALSE_FLAGS:
    require_false(name)
for name in DEPRECATED_FLAGS:
    if name in os.environ:
        raise SystemExit("deprecated feature flag {} is not supported".format(name))

deployment = os.environ.get("CODESTRA_ADAPTER_DEPLOYMENT", "staging-read-only")
release_info = None
if (
    os.environ.get("CODESTRA_RELEASE_METADATA_REQUIRED", "false").lower()
    == "true"
):
    try:
        release_info = load_release_info(required=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise SystemExit(f"release metadata invalid: {exc}") from exc
    if release_info.get("verified") is not True:
        raise SystemExit("release metadata is not verified")

backend = os.environ.get("CODESTRA_ADAPTER_BACKEND", "memory")
if deployment == "staging-read-only":
    if backend != "memory":
        raise SystemExit("staging deployment requires memory backend")
    for name in ("ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING"):
        require_false(name)
elif deployment == "production-candidate":
    if backend != "mysql":
        raise SystemExit("production candidate requires loopback mysql backend")
    for name in ("ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING"):
        require_false(name)
elif deployment == "production-reconciliation":
    if backend != "mysql":
        raise SystemExit("production reconciliation requires loopback mysql backend")
    for name in ("ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING"):
        if os.environ.get(name, "").lower() != "true":
            raise SystemExit("{} must be true for approved reconciliation".format(name))
    if os.environ.get("OWNER_FULL_AUTONOMY") != "YES":
        raise SystemExit("owner reconciliation authorization missing")
    if release_info is None or not isinstance(release_info.get("git_sha"), str):
        raise SystemExit("verified release metadata required for reconciliation")
    try:
        ReconciliationPolicy.from_file(
            Path(os.environ["CODESTRA_RECONCILIATION_POLICY_FILE"]),
            expected_release_sha=release_info["git_sha"],
        )
    except (KeyError, ReconciliationDenied, ValueError) as exc:
        raise SystemExit("request-bound reconciliation policy invalid: {}".format(exc)) from exc
elif deployment == "production-internal-test":
    if backend != "mysql":
        raise SystemExit("production internal test requires loopback mysql backend")
    for name in ("ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING"):
        require_false(name)
    if os.environ.get("ENABLE_INTERNAL_TEST_CALLING", "").lower() != "true":
        raise SystemExit("bounded internal calling must be explicitly enabled")
    if release_info is None or not isinstance(release_info.get("git_sha"), str):
        raise SystemExit("verified release metadata required for internal calling")
    try:
        InternalCallPolicy.from_file(
            Path(os.environ["CODESTRA_INTERNAL_CALL_POLICY_FILE"]),
            release_info["git_sha"],
        )
    except (KeyError, OSError, InternalCallError, ValueError) as exc:
        raise SystemExit("bounded internal call policy invalid: {}".format(exc)) from exc
    lifecycle = Path(os.environ.get(
        "CODESTRA_AMI_LIFECYCLE_STATE",
        os.environ.get("AMI_EVENT_STATE_DB", "/var/lib/codestra-vicidial/ami-events.sqlite3"),
    ))
    if not lifecycle.is_absolute() or not lifecycle.is_file():
        raise SystemExit("authoritative AMI lifecycle database unavailable")
    credential = Path(os.environ["CODESTRA_AMI_CREDENTIAL_FILE"])
    if not credential.is_file() or credential.stat().st_mode & 0o077:
        raise SystemExit("AMI credential is unavailable or permissions are unsafe")
else:
    raise SystemExit("unsupported adapter deployment profile")

if backend == "mysql":
    secret_path = Path(os.environ.get("CODESTRA_VICIDIAL_DB_FILE", "/run/secrets/vicidial-db.json"))
    try:
        credentials = json.loads(secret_path.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise SystemExit("VICIdial database credential document is invalid") from exc
    if credentials.get("host", "127.0.0.1") not in {"127.0.0.1", "localhost"}:
        raise SystemExit("VICIdial database must be loopback-only")
    if credentials.get("database", "asterisk") != "asterisk":
        raise SystemExit("VICIdial database name must be asterisk")

if os.environ.get("AUTHORIZED_APPLICATION_SERVER_IP") != "65.109.65.169":
    raise SystemExit("authorized application identity mismatch")
if os.environ.get("AUTHORIZED_PRIVATE_SOURCE_IP") != "10.40.0.1":
    raise SystemExit("authorized private source mismatch")
if os.environ.get("MIDDLEWARE_BASE_URL") != "https://api.codestra.co":
    raise SystemExit("middleware URL mismatch")
if os.environ.get("KEYCLOAK_ISSUER_URL") != "https://auth.codestra.co/realms/codestra":
    raise SystemExit("Keycloak issuer mismatch")

for name, default in (
    ("CODESTRA_ADAPTER_RATE_LIMIT_REQUESTS", "120"),
    ("CODESTRA_ADAPTER_RATE_LIMIT_WINDOW_SECONDS", "60"),
):
    try:
        value = int(os.environ.get(name, default))
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc
    if value <= 0:
        raise SystemExit(f"{name} must be greater than zero")

secret = Path(os.environ["CODESTRA_ADAPTER_HMAC_FILE"])
if not secret.is_file() or secret.stat().st_size < 32:
    raise SystemExit("adapter HMAC credential missing or too short")
if not str(secret).startswith("/run/credentials/") and secret.stat().st_mode & 0o077:
    raise SystemExit("adapter HMAC credential permissions are too broad")
