#!/usr/bin/env python3
"""Fail closed when the staging adapter runtime is unsafe."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from codestra_vicidial.reconciliation import ReconciliationDenied, ReconciliationPolicy

ALWAYS_FALSE_FLAGS = (
    "ENABLE_AGENT_SYNC",
    "ENABLE_LEAD_PUBLICATION",
    "ENABLE_IVR_CALLBACKS",
    "ENABLE_WARM_TRANSFER",
    "ENABLE_PRODUCTION_DIALING",
)

for name in ALWAYS_FALSE_FLAGS:
    if os.environ.get(name, "").lower() != "false":
        raise SystemExit("{} must remain false".format(name))

deployment = os.environ.get("CODESTRA_ADAPTER_DEPLOYMENT", "staging-read-only")
if deployment == "staging-read-only":
    if os.environ.get("CODESTRA_ADAPTER_BACKEND") != "memory":
        raise SystemExit("staging deployment requires memory backend")
    for name in ("ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING"):
        if os.environ.get(name, "").lower() != "false":
            raise SystemExit("{} must be false in staging deployment".format(name))
elif deployment == "production-reconciliation":
    if os.environ.get("CODESTRA_ADAPTER_BACKEND") != "mysql":
        raise SystemExit("production reconciliation requires loopback mysql backend")
    for name in ("ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING"):
        if os.environ.get(name, "").lower() != "true":
            raise SystemExit("{} must be true for approved reconciliation".format(name))
    if os.environ.get("OWNER_FULL_AUTONOMY") != "YES":
        raise SystemExit("owner reconciliation authorization missing")
    try:
        ReconciliationPolicy.from_file(Path(os.environ["CODESTRA_RECONCILIATION_POLICY_FILE"]))
    except (KeyError, ReconciliationDenied, ValueError) as exc:
        raise SystemExit("request-bound reconciliation policy invalid: {}".format(exc)) from exc
else:
    raise SystemExit("unsupported adapter deployment profile")
if os.environ.get("AUTHORIZED_APPLICATION_SERVER_IP") != "65.109.65.169":
    raise SystemExit("authorized application identity mismatch")
if os.environ.get("AUTHORIZED_PRIVATE_SOURCE_IP") != "10.40.0.1":
    raise SystemExit("authorized private source mismatch")
if os.environ.get("MIDDLEWARE_BASE_URL") != "https://api.codestra.agency":
    raise SystemExit("middleware URL mismatch")
if os.environ.get("KEYCLOAK_ISSUER_URL") != "https://auth.codestra.co/realms/codestra":
    raise SystemExit("Keycloak issuer mismatch")

secret = Path(os.environ["CODESTRA_ADAPTER_HMAC_FILE"])
if not secret.is_file() or secret.stat().st_size < 32:
    raise SystemExit("adapter HMAC credential missing or too short")
if not str(secret).startswith("/run/credentials/") and secret.stat().st_mode & 0o077:
    raise SystemExit("adapter HMAC credential permissions are too broad")
