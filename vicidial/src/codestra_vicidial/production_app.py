from __future__ import annotations

import ipaddress
import json
import os
import sqlite3
from pathlib import Path

from fastapi.responses import JSONResponse

from .app import app
from .release import load_release_info


LIVE_FLAGS = (
    "ENABLE_VICIDIAL_WRITES",
    "ENABLE_PROVISIONING",
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
        # Keep the readiness view aligned with the base adapter's established
        # private-source default while still rejecting public source values.
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
    secret_path = Path(
        os.environ.get("CODESTRA_VICIDIAL_DB_FILE", "/run/secrets/vicidial-db.json")
    )
    try:
        credentials = json.loads(secret_path.read_text())
    except (OSError, ValueError, TypeError):
        return False, None

    host = str(credentials.get("host", "127.0.0.1"))
    if host not in {"127.0.0.1", "localhost"}:
        return False, None
    database = str(credentials.get("database", "asterisk"))
    if database != "asterisk":
        return False, database

    connection = None
    try:
        import pymysql

        connection = pymysql.connect(
            host=host,
            unix_socket=credentials.get("unix_socket"),
            user=credentials["user"],
            password=credentials["password"],
            database=database,
            port=int(credentials.get("port", 3306)),
            connect_timeout=2,
            read_timeout=2,
            write_timeout=2,
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS db_name, 1 AS ok")
            row = cursor.fetchone()
            if not row or row.get("db_name") != "asterisk" or int(row.get("ok", 0)) != 1:
                return False, database
            cursor.execute(
                "SELECT COUNT(*) AS table_count FROM information_schema.tables "
                "WHERE table_schema=%s AND table_name IN "
                "('vicidial_campaigns','vicidial_users','vicidial_list','vicidial_callbacks')",
                (database,),
            )
            row = cursor.fetchone()
            if not row or int(row.get("table_count", 0)) != 4:
                return False, database
        return True, database
    except Exception:
        return False, database
    finally:
        if connection is not None:
            connection.close()


def _repository_ready(profile: str) -> tuple[bool, str]:
    backend = os.environ.get("CODESTRA_ADAPTER_BACKEND", "memory").strip().lower()
    if backend == "memory":
        # Memory is valid only for development/shadow certification, never as
        # the production VICIdial system-of-record backend.
        return profile in {"development-loopback", "staging-read-only", "production-candidate"}, backend
    if backend == "mysql":
        ok, _database = _mysql_ready()
        return ok, backend
    return False, backend


def _release_ready(profile: str) -> tuple[bool, str | None]:
    required = (
        os.environ.get("CODESTRA_RELEASE_METADATA_REQUIRED", "false").lower() == "true"
        or profile in {"production-candidate", "production-reconciliation"}
    )
    try:
        release = load_release_info(required=required)
    except Exception:
        return False, None
    sha = release.get("git_sha") if isinstance(release, dict) else None
    if required and release.get("verified") is not True:
        return False, str(sha) if sha else None
    return True, str(sha) if sha else None


def _kill_switch_ready() -> bool:
    if any(_enabled(name) for name in LIVE_FLAGS):
        return False
    # Deprecated names are rejected even when set to false. Their presence can
    # otherwise create a false belief that an activation control is effective.
    return not any(name in os.environ for name in LEGACY_FLAGS)


def build_readiness() -> tuple[int, dict]:
    profile = os.environ.get("CODESTRA_ADAPTER_DEPLOYMENT", "development-loopback")
    release_ok, release_sha = _release_ready(profile)
    repository_ok, backend = _repository_ready(profile)
    checks = {
        "release": release_ok,
        "hmac": _hmac_ready(profile),
        "source_policy": _source_policy_ready(profile),
        "state_store": _state_store_ready(),
        "repository": repository_ok,
        "kill_switches": _kill_switch_ready(),
    }
    status = "ready" if all(checks.values()) else "not_ready"
    body = {
        "status": status,
        "deployment_profile": profile,
        "backend": backend,
        "release_sha": release_sha,
        "checks": checks,
        "writes_enabled": False,
        "production_dialing": False,
        "customer_traffic_authorized": False,
    }
    return (200 if status == "ready" else 503), body


# Replace the shallow readiness route from the base adapter with the production
# dependency-aware route. /health remains a liveness probe and must not perform
# dependency I/O.
app.router.routes[:] = [
    route for route in app.router.routes if getattr(route, "path", None) != "/ready"
]


@app.get("/ready", include_in_schema=False)
def production_ready():
    status, body = build_readiness()
    if status != 200:
        return JSONResponse(status_code=status, content=body)
    return body
