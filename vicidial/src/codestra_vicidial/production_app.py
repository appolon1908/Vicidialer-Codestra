from __future__ import annotations

import ipaddress
import os
import sqlite3
from pathlib import Path

from fastapi.responses import JSONResponse

from .app import app
from .reconciliation import ReconciliationPolicy
from .release import load_release_info


ALWAYS_DISABLED_FLAGS = (
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

LEGACY_FLAGS = (
    "ENABLE_CALLBACK_WRITES",
    "ENABLE_TRANSFER_POLICY_SYNC",
    "ENABLE_PSTN_DIALING",
)


def _enabled(name: str) -> bool:
    return os.environ.get(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _state_store_ready() -> bool:
    path = Path(
        os.environ.get(
            "CODESTRA_ADAPTER_STATE",
            "/var/lib/codestra-vicidial/state.sqlite3",
        )
    )
    if not path.is_absolute() or not path.exists() or not path.is_file():
        return False
    connection = None
    try:
        connection = sqlite3.connect(path, timeout=1.0)
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute("SELECT 1").fetchone()
        return bool(row and row[0] == 1)
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def _hmac_ready(profile: str) -> bool:
    path = Path(os.environ.get("CODESTRA_ADAPTER_HMAC_FILE", "/run/secrets/adapter-hmac"))
    if profile == "development-loopback" and not path.exists():
        return True
    try:
        return path.is_file() and len(path.read_bytes().strip()) >= 32
    except OSError:
        return False


def _source_policy_ready(profile: str) -> bool:
    if profile == "development-loopback":
        return True
    try:
        private_source = ipaddress.ip_address(
            os.environ.get("AUTHORIZED_PRIVATE_SOURCE_IP", "10.40.0.1")
        )
    except ValueError:
        return False
    if not private_source.is_private:
        return False
    proxy = os.environ.get("AUTHORIZED_CONTAINER_PROXY_IP")
    if proxy:
        try:
            if not ipaddress.ip_address(proxy).is_private:
                return False
        except ValueError:
            return False
    return True


def _mysql_ready() -> tuple[bool, str | None]:
    """Exercise the repository connection used by operational requests."""
    try:
        service = getattr(app.state, "adapter_service", None)
        repository = getattr(service, "repository", None)
        readiness = getattr(repository, "readiness", None)
        if not callable(readiness):
            return False, None
        ok, database = readiness()
        return bool(ok), database
    except Exception:
        return False, None


def _repository_ready(profile: str) -> tuple[bool, str]:
    backend = os.environ.get("CODESTRA_ADAPTER_BACKEND", "memory").strip().lower()
    if backend == "memory":
        return profile in {
            "development-loopback",
            "staging-read-only",
            "production-candidate",
        }, backend
    if backend == "mysql":
        ok, _database = _mysql_ready()
        return ok, backend
    return False, backend


def _release_ready(profile: str) -> tuple[bool, str | None, dict]:
    required = (
        os.environ.get("CODESTRA_RELEASE_METADATA_REQUIRED", "false").lower() == "true"
        or profile in {"production-candidate", "production-reconciliation"}
    )
    try:
        release = load_release_info(required=required)
    except Exception:
        return False, None, {}
    sha = release.get("git_sha") if isinstance(release, dict) else None
    if required and release.get("verified") is not True:
        return False, str(sha) if sha else None, release
    return True, str(sha) if sha else None, release


def _reconciliation_policy_ready(profile: str, release_sha: str | None) -> bool:
    if profile != "production-reconciliation":
        return False
    if not release_sha or os.environ.get("OWNER_FULL_AUTONOMY") != "YES":
        return False
    value = os.environ.get("CODESTRA_RECONCILIATION_POLICY_FILE")
    if not value:
        return False
    try:
        ReconciliationPolicy.from_file(
            Path(value),
            expected_release_sha=release_sha,
        )
        return True
    except Exception:
        return False


def _kill_switch_ready(profile: str, reconciliation_policy_verified: bool) -> bool:
    if any(_enabled(name) for name in ALWAYS_DISABLED_FLAGS):
        return False
    if any(name in os.environ for name in LEGACY_FLAGS):
        return False

    writes = _enabled("ENABLE_VICIDIAL_WRITES")
    provisioning = _enabled("ENABLE_PROVISIONING")
    if profile == "production-reconciliation":
        # Reconciliation is the one bounded exception: both switches must be on,
        # the immutable request-bound policy must be verified, and every live
        # customer capability above must remain off.
        return writes and provisioning and reconciliation_policy_verified
    return not writes and not provisioning


def build_readiness() -> tuple[int, dict]:
    profile = os.environ.get("CODESTRA_ADAPTER_DEPLOYMENT", "development-loopback")
    release_ok, release_sha, release = _release_ready(profile)
    repository_ok, backend = _repository_ready(profile)
    reconciliation_policy_verified = _reconciliation_policy_ready(profile, release_sha)
    checks = {
        "release": release_ok,
        "hmac": _hmac_ready(profile),
        "source_policy": _source_policy_ready(profile),
        "state_store": _state_store_ready(),
        "repository": repository_ok,
        "kill_switches": _kill_switch_ready(
            profile,
            reconciliation_policy_verified,
        ),
    }
    if profile == "production-reconciliation":
        checks["reconciliation_policy"] = reconciliation_policy_verified

    status = "ready" if all(checks.values()) else "not_ready"
    writes_enabled = (
        profile == "production-reconciliation"
        and _enabled("ENABLE_VICIDIAL_WRITES")
        and _enabled("ENABLE_PROVISIONING")
        and reconciliation_policy_verified
    )
    body = {
        "status": status,
        "deployment_profile": profile,
        "backend": backend,
        "release_sha": release_sha,
        "release_verified": release.get("verified") is True,
        "reconciliation_policy_verified": reconciliation_policy_verified,
        "checks": checks,
        "writes_enabled": writes_enabled,
        "production_dialing": False,
        "customer_traffic_authorized": False,
    }
    return (200 if status == "ready" else 503), body


app.router.routes[:] = [
    route for route in app.router.routes if getattr(route, "path", None) != "/ready"
]


@app.get("/ready", include_in_schema=False)
def production_ready():
    status, body = build_readiness()
    if status != 200:
        return JSONResponse(status_code=status, content=body)
    return body
