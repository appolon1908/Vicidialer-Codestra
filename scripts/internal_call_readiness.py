#!/usr/bin/env python3
"""Read-only readiness probe for the one internal echo call (appolon/6901 -> 6000).

Run as root on Server B, once per phase of docs/INTERNAL_ECHO_CALL_RUNBOOK.md:

    --phase preflight  T-1, adapter on its steady profile, appolon inactive;
                       validates the staged --window-env-file and the saved
                       --steady-env-file that close-out will restore
    --phase window     after the window env is installed and appolon is
                       activated, immediately before the call is sent
    --phase closed     after the steady env is restored and appolon deactivated

Add ``--middleware-env-file PATH`` on the Middleware host (or with a copy of
its env file) to check the Middleware side of the same call.

The probe never writes: SQLite is opened ``mode=ro``, MariaDB is queried with
SELECT only, Asterisk and Apache are asked only ``show``/``-S``, and the adapter
is only sent HTTP GETs on loopback. It never prints secret values. Its only
output is one ``CHECK`` line per gate and a final ``VERDICT=``.

Stdlib only; Python 3.9+.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ADAPTER_ENV = Path("/etc/codestra/vicidial-restricted-adapter.env")
ADAPTER_UNIT = "codestra-vicidial-restricted-adapter.service"
ADAPTER_URL = "http://127.0.0.1:8097"
ADAPTER_WRITABLE = Path("/var/lib/codestra-vicidial-restricted-adapter")
ADAPTER_USER = "codestra-adapter"
PROFILE = "production-internal-test"
CONTEXT = "codestra-internal-test"

# Must stay identical to internal_call_transport.digest() over the approved
# Answer/Echo/Hangup route, which is what verify_route() compares at call time.
APPROVED_ROUTE = [
    {"Context": CONTEXT, "Extension": "6000", "Priority": p, "Application": a, "AppData": ""}
    for p, a in (("1", "Answer"), ("2", "Echo"), ("3", "Hangup"))
]

# production-internal-test refuses to start unless every one of these is false.
UNRELATED_GATES = (
    "ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING", "ENABLE_AGENT_PROVISIONING",
    "ENABLE_AGENT_SYNC", "ENABLE_LEAD_PUBLICATION", "ENABLE_LIVE_CALL_CONTROL",
    "ENABLE_WARM_TRANSFER", "ENABLE_IVR_ROUTING", "ENABLE_IVR_CALLBACKS",
    "ENABLE_PRODUCTION_DIALING", "ENABLE_SMS_DELIVERY", "ENABLE_EMAIL_DELIVERY",
    "ENABLE_EXTERNAL_DELIVERY", "ENABLE_AI_ACTIONS",
)
LIFECYCLE_COLUMNS = {
    "calls": {
        "call_key", "linkedid", "extension", "call_id", "correlation_id", "binding_json",
        "asterisk_uniqueid", "state", "answered", "terminal", "created_at",
        "answered_at", "ended_at", "updated_at",
    },
    "channels": {"call_key", "uniqueid", "channel", "active"},
    "event_outbox": {"event_id", "call_key", "sequence", "event_type", "envelope_json"},
}
# Middleware worker keys (app/vicidial_internal_call_adapter.py): presence is
# checked, values other than the URL/host are never printed.
MIDDLEWARE_REQUIRED = (
    "VICIDIAL_INTERNAL_CALL_BASE_URL",
    "VICIDIAL_INTERNAL_CALL_EXPECTED_HOST",
    "VICIDIAL_INTERNAL_CALL_SERVICE_IDENTITY",
    "VICIDIAL_INTERNAL_CALL_HMAC_FILE",
    "VICIDIAL_INTERNAL_CALL_CA_FILE",
    "VICIDIAL_INTERNAL_CALL_MTLS_CERT_FILE",
    "VICIDIAL_INTERNAL_CALL_MTLS_KEY_FILE",
    "CODESTRA_INTERNAL_CALL_POLICY_FILE",
)
INGRESS_VHOST = "10.40.0.2:8443"
INGRESS_PROXY = (r'^\s*ProxyPass\s+"/v1/calls/internal/"\s+'
                 r'"http://127\.0\.0\.1:8097/v1/calls/internal/"')
_SAMPLE_OPERATION = "0b7e5c1e-6f0a-5d8e-9a53-2f4a6c1d9e70"
# (path, method that must be the only one allowed, or None for "denied").
INGRESS_EXPECTATIONS = (
    ("/v1/calls/internal/originate", "POST"),
    (f"/v1/calls/internal/{_SAMPLE_OPERATION}/hangup", "POST"),
    (f"/v1/calls/internal/{_SAMPLE_OPERATION}", "GET"),
    ("/v1/calls/internal/", None),
    ("/v1/calls/internal/short", None),
    (f"/v1/calls/internal/{_SAMPLE_OPERATION}/replay", None),
    ("/v1/calls/internal/originate/extra", None),
)

RESULTS: list[tuple[str, str]] = []


def check(name: str, status: str, detail: str = "") -> None:
    RESULTS.append((name, status))
    print(f"CHECK {name}={status}" + (f" {detail}" if detail else ""))


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def principal_fingerprint(values: dict) -> str:
    return digest([values.get("tenant_id"), values.get("subject"), values.get("employee_id")])


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return values


def run(argv: list[str], timeout: int = 10, merge_stderr: bool = False) -> tuple[int, str]:
    if shutil.which(argv[0]) is None:
        return 127, ""
    try:
        done = subprocess.run(
            argv, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE,
            text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""
    return done.returncode, done.stdout


def get_json(path: str) -> tuple[int | None, dict | None]:
    try:
        with urllib.request.urlopen(ADAPTER_URL + path, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.load(exc)
        except ValueError:
            return exc.code, None
    except (OSError, ValueError):
        return None, None


def protected_mode_ok(info: os.stat_result, service_uid: int | None, service_gid: int | None) -> bool:
    # Mirrors internal_call_transport.load_protected_json as seen by the service user.
    service_owned = (
        service_uid is not None and info.st_uid == service_uid
        and info.st_mode & 0o400 and not info.st_mode & 0o377
    )
    root_group = (
        info.st_uid == 0 and service_gid is not None and info.st_gid == service_gid
        and info.st_mode & 0o040 and not info.st_mode & 0o037
    )
    return stat.S_ISREG(info.st_mode) and bool(service_owned or root_group)


def service_ids() -> tuple[int | None, int | None]:
    try:
        import pwd
        entry = pwd.getpwnam(ADAPTER_USER)
        return entry.pw_uid, entry.pw_gid
    except (ImportError, KeyError):
        return None, None


def check_window_env(env: dict[str, str], label: str) -> None:
    """The env the adapter runs with during the call window.

    scripts/validate_runtime.py (ExecStartPre) couples profile and gate: the
    internal-test profile refuses to start unless the gate is "true", and every
    other profile refuses to start unless it is "false". So the window is
    entered and left by swapping the whole env file, never by flipping the gate.
    """
    profile = env.get("CODESTRA_ADAPTER_DEPLOYMENT", "")
    check(f"{label}_profile", "PASS" if profile == PROFILE else "FAIL",
          f"deployment={profile or 'unset'} required={PROFILE}")
    gate = env.get("ENABLE_INTERNAL_TEST_CALLING", "false")
    check(f"{label}_internal_call_gate", "PASS" if gate == "true" else "FAIL",
          f"ENABLE_INTERNAL_TEST_CALLING={gate} (validate_runtime.py requires true for {PROFILE})")
    backend = env.get("CODESTRA_ADAPTER_BACKEND", "")
    check(f"{label}_backend_mysql", "PASS" if backend == "mysql" else "FAIL", f"backend={backend or 'unset'}")
    enabled = [name for name in UNRELATED_GATES if env.get(name, "false") != "false"]
    check(f"{label}_unrelated_gates_off", "FAIL" if enabled else "PASS",
          ("enabled=" + ",".join(enabled)) if enabled else "")
    required = env.get("CODESTRA_RELEASE_METADATA_REQUIRED", "false")
    check(f"{label}_release_metadata_required", "PASS" if required == "true" else "FAIL", f"value={required}")
    for key, default in (
        ("CODESTRA_INTERNAL_CALL_STATE", "/var/lib/codestra-vicidial/internal-calls.sqlite3"),
        ("CODESTRA_ADAPTER_STATE", "/var/lib/codestra-vicidial/state.sqlite3"),
    ):
        path = Path(env.get(key, default))
        check(f"{label}_writable_{key.lower()}", "PASS" if ADAPTER_WRITABLE in path.parents else "FAIL",
              f"path={path} unit ReadWritePaths={ADAPTER_WRITABLE}")


def check_live_adapter(env: dict[str, str], phase: str) -> str | None:
    """Service, /version and /ready as they are right now; returns the release SHA."""
    code, out = run(["systemctl", "is-active", ADAPTER_UNIT])
    check("adapter_service_active", "PASS" if out.strip() == "active" else "FAIL", out.strip() or f"rc={code}")

    profile = env.get("CODESTRA_ADAPTER_DEPLOYMENT", "staging-read-only")
    gate = env.get("ENABLE_INTERNAL_TEST_CALLING", "false")
    if phase == "window":
        check_window_env(env, "live")
    else:
        # Before and after the window the adapter must be on its steady profile.
        check("live_steady_profile", "PASS" if profile != PROFILE and gate == "false" else "FAIL",
              f"deployment={profile} ENABLE_INTERNAL_TEST_CALLING={gate}")

    status, version = get_json("/version")
    sha = (version or {}).get("git_sha")
    check("adapter_version", "PASS" if status == 200 and isinstance(sha, str) and len(sha) == 40 else "FAIL",
          f"http={status} git_sha={sha} verified={(version or {}).get('verified')}")

    status, ready = get_json("/ready")
    if ready is None:
        check("adapter_ready", "FAIL", f"http={status}")
        return sha
    checks = ready.get("checks") or {}
    failing = sorted(k for k, v in checks.items() if v is not True)
    check("adapter_ready", "PASS" if status == 200 and ready.get("status") == "ready" else "FAIL",
          f"http={status} status={ready.get('status')} profile={ready.get('deployment_profile')} "
          f"failing_checks={','.join(failing) or 'none'}")
    calling = ready.get("internal_test_calling")
    if phase == "window":
        check("adapter_ready_internal_call_policy",
              "PASS" if checks.get("internal_call_policy") is True else "FAIL",
              f"value={checks.get('internal_call_policy')}")
        check("adapter_internal_test_calling_on", "PASS" if calling is True else "FAIL", f"value={calling}")
    else:
        check("adapter_internal_test_calling_off", "PASS" if calling is False else "FAIL",
              f"value={calling} profile={ready.get('deployment_profile')}")
    check("adapter_customer_traffic_off", "PASS" if ready.get("customer_traffic_authorized") is False else "FAIL")
    check("adapter_production_dialing_off", "PASS" if ready.get("production_dialing") is False else "FAIL")
    return sha


def check_policy(env: dict[str, str], release_sha: str | None) -> str | None:
    raw_path = env.get("CODESTRA_INTERNAL_CALL_POLICY_FILE")
    if not raw_path:
        check("policy_file", "FAIL", "CODESTRA_INTERNAL_CALL_POLICY_FILE unset")
        return None
    path = Path(raw_path)
    try:
        info = os.lstat(path)
    except OSError:
        check("policy_file", "FAIL", f"missing path={path}")
        return None
    uid, gid = service_ids()
    check("policy_file_protected",
          "PASS" if not stat.S_ISLNK(info.st_mode) and protected_mode_ok(info, uid, gid) else "FAIL",
          f"path={path} uid={info.st_uid} gid={info.st_gid} mode={oct(info.st_mode & 0o777)}")
    try:
        policy = json.loads(path.read_text())
    except (OSError, ValueError):
        check("policy_parse", "FAIL", "not valid JSON")
        return None
    literals = {
        "schema_version": "1.0", "username": "appolon", "extension": "6901",
        "campaign": "TEST_SYN", "destination_alias": "internal:TEST_ECHO",
        "destination_extension": "6000", "dialplan_context": CONTEXT,
        "internal_only": True, "external_dialing": False, "recording": False, "max_calls": 1,
    }
    wrong = sorted(k for k, v in literals.items() if policy.get(k) != v or type(policy.get(k)) is not type(v))
    check("policy_literals", "FAIL" if wrong else "PASS", ("mismatch=" + ",".join(wrong)) if wrong else "")
    check("policy_release_matches_adapter",
          "PASS" if release_sha and policy.get("release_sha") == release_sha else "FAIL",
          f"policy={policy.get('release_sha')} adapter={release_sha}")
    ref = policy.get("authorization_reference", "")
    check("policy_authorization_reference",
          "PASS" if isinstance(ref, str) and re.fullmatch(r"CHG-[A-Z0-9-]{10,80}", ref) else "FAIL",
          f"value={ref}")
    try:
        expires = datetime.fromisoformat(str(policy.get("expires_at")).replace("Z", "+00:00"))
        remaining = (expires - datetime.now(timezone.utc)).total_seconds() if expires.tzinfo else -1
    except ValueError:
        remaining = -1
    check("policy_not_expired", "PASS" if remaining > 0 else "FAIL",
          f"expires_at={policy.get('expires_at')} hours_left={remaining / 3600:.1f}")
    for key in ("tenant_id", "subject", "employee_id"):
        check(f"policy_{key}_present", "PASS" if policy.get(key) else "FAIL")
    # Compare with the Middleware grant's line: both sides must print the same values.
    check("policy_cross_host_fingerprint", "INFO",
          f"authorization_reference={ref} principal_sha256={principal_fingerprint(policy)}")
    route = policy.get("route_sha256")
    check("policy_route_is_approved_echo", "PASS" if route == digest(APPROVED_ROUTE) else "FAIL",
          f"policy={route} approved={digest(APPROVED_ROUTE)}")
    return route


def check_state_store(env: dict[str, str], phase: str) -> None:
    path = Path(env.get("CODESTRA_INTERNAL_CALL_STATE", "/var/lib/codestra-vicidial/internal-calls.sqlite3"))
    if not path.exists():
        check("one_call_unused", "PASS", f"no execution store yet path={path}")
        return
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            rows = db.execute("SELECT operation_id,state,hangup_state,created_at FROM internal_calls").fetchall()
    except sqlite3.Error as exc:
        check("one_call_unused", "FAIL", f"unreadable path={path} error={type(exc).__name__}")
        return
    if rows and phase == "closed":
        # Expected after the call: the grant is consumed and blocks any other call.
        check("one_call_consumed", "INFO", f"rows={len(rows)} operation={rows[0][0]} state={rows[0][1]} "
              f"hangup={rows[0][2]}")
    elif rows:
        check("one_call_unused", "FAIL",
              f"already consumed rows={len(rows)} first={rows[0][0]} state={rows[0][1]} "
              "(administrator reconciliation required before another call)")
    else:
        check("one_call_unused", "PASS", f"path={path}")


def check_ami_credentials(env: dict[str, str]) -> None:
    raw = env.get("CODESTRA_AMI_CREDENTIAL_FILE")
    if not raw:
        check("ami_credential_file", "FAIL", "CODESTRA_AMI_CREDENTIAL_FILE unset")
        return
    path = Path(raw)
    try:
        info = os.lstat(path)
        config = json.loads(path.read_text())
    except (OSError, ValueError):
        check("ami_credential_file", "FAIL", f"missing or invalid path={path}")
        return
    uid, gid = service_ids()
    check("ami_credential_protected",
          "PASS" if not stat.S_ISLNK(info.st_mode) and protected_mode_ok(info, uid, gid) else "FAIL",
          f"uid={info.st_uid} gid={info.st_gid} mode={oct(info.st_mode & 0o777)}")
    host = config.get("host") if isinstance(config, dict) else None
    check("ami_loopback_only", "PASS" if host in {"127.0.0.1", "localhost"} else "FAIL", f"host={host}")
    # validate_runtime.py requires exactly these keys and an integer port.
    keys = sorted(config) if isinstance(config, dict) else []
    port = config.get("port") if isinstance(config, dict) else None
    check("ami_credential_schema",
          "PASS" if keys == ["host", "port", "secret", "username"] and type(port) is int
          and 1 <= port <= 65535 and config.get("username") and config.get("secret") else "FAIL",
          f"keys={','.join(keys)}")


def parse_dialplan(out: str) -> list[dict] | None:
    """Rebuild the AMI ShowDialPlan rows verify_route() digests from CLI output."""
    if re.search(r"Include\s*=>|Ignore pattern|Alt\. Switch", out):
        return None
    steps = re.findall(r"^\s*(?:'6000'\s*=>\s*)?(\d+)\.\s+([A-Za-z]+)\((.*?)\)", out, re.M)
    return [
        {"Context": CONTEXT, "Extension": "6000", "Priority": p, "Application": a, "AppData": d}
        for p, a, d in steps
    ]


def check_dialplan(policy_route: str | None) -> None:
    code, out = run(["asterisk", "-rx", f"dialplan show 6000@{CONTEXT}"])
    if code != 0 or not out.strip():
        check("dialplan_route_6000", "UNKNOWN", f"asterisk rc={code}")
        return
    route = parse_dialplan(out)
    if route is None:
        check("dialplan_route_6000", "FAIL", "context has includes/switches/ignorepat")
        return
    live = digest(route)
    steps = "/".join(f"{r['Priority']}.{r['Application']}({r['AppData']})" for r in route)
    check("dialplan_route_6000", "PASS" if route == APPROVED_ROUTE else "FAIL",
          f"live={steps or 'none'} live_sha256={live}")
    if policy_route:
        check("dialplan_matches_policy", "PASS" if live == policy_route else "FAIL")


def parse_contacts(out: str) -> list[str]:
    return re.findall(r"^\s*Contact:\s+6901/\S+.*?(Avail|NonQual|Unavail|Unknown|Created)", out, re.M)


def check_pjsip() -> None:
    code, out = run(["asterisk", "-rx", "pjsip show aor 6901"])
    if code != 0 or "6901" not in out:
        check("pjsip_aor_6901", "UNKNOWN" if code else "FAIL", f"asterisk rc={code}")
        return
    max_contacts = re.search(r"max_contacts\s*:\s*(\d+)", out)
    remove_existing = re.search(r"remove_existing\s*:\s*(\S+)", out)
    check("pjsip_single_device",
          "PASS" if max_contacts and max_contacts.group(1) == "1"
          and remove_existing and remove_existing.group(1).lower() in {"true", "yes"} else "FAIL",
          f"max_contacts={max_contacts and max_contacts.group(1)} remove_existing={remove_existing and remove_existing.group(1)}")
    contacts = parse_contacts(out)
    # Informational: a device only has to be registered during the call window.
    check("pjsip_6901_registered", "INFO",
          f"contacts={len(contacts)} states={','.join(contacts) or 'none'}")


def identity_findings(fields: list[str]) -> tuple[list[str], dict[str, str]]:
    """Split the appolon row into binding problems and activation state.

    Binding (level, group, campaign ACL, phone login/dialplan) must hold in every
    phase. Activation (user, phone, campaign active) is Y only inside the window.
    """
    active, level, group, allowed, phone, campaign = fields
    phone_active, _, dialplan = (phone or "").partition(":")
    campaigns = allowed.strip()[:-1].split() if allowed.strip().endswith("-") else None
    problems = []
    if level != "8":
        problems.append(f"user_level={level}")
    if group != "WEBTEST_SYN":
        problems.append(f"user_group={group}")
    if campaigns != ["TEST_SYN"]:
        problems.append(f"allowed_campaigns={allowed.strip()}")
    if dialplan != "6901":
        problems.append(f"phone={phone or 'missing'}")
    if campaign not in {"Y", "N"}:
        problems.append("campaign_missing")
    return problems, {"user": active, "phone": phone_active, "campaign": campaign}


def check_identity(phase: str) -> None:
    query = (
        "SELECT u.active,u.user_level,u.user_group,g.allowed_campaigns,"
        "(SELECT CONCAT(p.active,':',p.dialplan_number) FROM phones p "
        " WHERE p.login=u.phone_login AND p.login='6901' AND p.extension='6901'),"
        "(SELECT active FROM vicidial_campaigns WHERE campaign_id='TEST_SYN') "
        "FROM vicidial_users u JOIN vicidial_user_groups g ON g.user_group=u.user_group "
        "WHERE u.user='appolon'"
    )
    code, out = run(["mysql", "--batch", "--skip-column-names", "asterisk", "-e", query])
    if code != 0:
        check("vicidial_identity_binding", "UNKNOWN", f"mysql rc={code} (run with local socket access)")
        return
    fields = out.rstrip("\n").split("\t") if out.strip() else []
    if len(fields) != 6:
        check("vicidial_identity_binding", "FAIL", "appolon user or group missing")
        return
    problems, activation = identity_findings(fields)
    check("vicidial_identity_binding", "FAIL" if problems else "PASS", " ".join(problems))
    state = " ".join(f"{k}_active={v}" for k, v in activation.items())
    if phase == "window":
        # internal_call_identity_ready() refuses to dial unless all three are Y.
        check("vicidial_identity_activated", "PASS" if set(activation.values()) == {"Y"} else "FAIL", state)
    elif phase == "closed":
        check("vicidial_identity_deactivated", "PASS" if activation["user"] == "N" else "FAIL", state)
    else:
        # Steady state (appolon active=N) is expected before the window.
        check("vicidial_identity_activation", "INFO", state + " (activated only inside the window)")


def check_lifecycle(env: dict[str, str], gateway_env: dict[str, str]) -> None:
    raw = (env.get("CODESTRA_AMI_LIFECYCLE_STATE") or env.get("AMI_EVENT_STATE_DB")
           or gateway_env.get("AMI_EVENT_STATE_DB") or "/var/lib/codestra-vicidial/ami-events.sqlite3")
    path = Path(raw)
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            missing = []
            for table, columns in LIFECYCLE_COLUMNS.items():
                present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                missing += [f"{table}.{c}" for c in sorted(columns - present)]
            outbox = db.execute("SELECT count(*) FROM event_outbox").fetchone()[0]
    except sqlite3.Error as exc:
        check("lifecycle_store_schema", "FAIL", f"path={path} error={type(exc).__name__}")
        return
    check("lifecycle_store_schema", "FAIL" if missing else "PASS",
          f"path={path}" + (f" missing={','.join(missing)}" if missing else "") + f" outbox_rows={outbox}")
    info = path.stat()
    uid, gid = service_ids()
    readable = info.st_mode & 0o004 or (gid is not None and info.st_gid == gid and info.st_mode & 0o040) \
        or (uid is not None and info.st_uid == uid)
    check("lifecycle_store_readable_by_adapter", "PASS" if readable else "FAIL",
          f"uid={info.st_uid} gid={info.st_gid} mode={oct(info.st_mode & 0o777)}")
    status_path = Path(gateway_env.get("AMI_EVENT_STATUS_FILE", "/run/codestra-vicidial/ami-events-status.json"))
    try:
        status = json.loads(status_path.read_text())
        summary = {k: v for k, v in status.items() if isinstance(v, (str, int, float, bool)) and "secret" not in k.lower()}
        check("ami_gateway_status", "INFO", json.dumps(summary, sort_keys=True)[:400])
    except (OSError, ValueError):
        check("ami_gateway_status", "UNKNOWN", f"no status file path={status_path}")


def strip_apache_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def ingress_problems(text: str) -> list[str]:
    """Check the directives Apache actually applies to /v1/calls/internal/.

    Middleware requires a bare HTTPS origin, so the private vhost itself must
    proxy /v1/calls/internal/ and guard each route by method, source IP and
    client certificate, denying every other path under it.
    """
    text = strip_apache_comments(text)
    problems = []
    if not re.search(INGRESS_PROXY, text, re.M):
        problems.append("proxy_missing")
    blocks = [
        (re.compile(pattern), body)
        for pattern, body in re.findall(r'<LocationMatch\s+"([^"]+)"\s*>(.*?)</LocationMatch>', text, re.S | re.I)
    ]
    for path, method in INGRESS_EXPECTATIONS:
        bodies = [body for pattern, body in blocks if pattern.search(path)]
        if len(bodies) != 1:
            problems.append(f"{path}:rules={len(bodies)}")
            continue
        body = bodies[0]
        if method is None:
            ok = re.search(r"^\s*Require\s+all\s+denied\s*$", body, re.M)
        else:
            ok = (
                re.search(rf"^\s*Require\s+method\s+{method}\s*$", body, re.M)
                and re.search(r"^\s*Require\s+ip\s+10\.40\.0\.1\s*$", body, re.M)
                and "%{SSL_CLIENT_VERIFY} == 'SUCCESS'" in body
            )
        if not ok:
            problems.append(f"{path}:guard")
    return problems


def vhost_block(path: Path, line: int, server_root: Path, depth: int = 0) -> str:
    """Text of the <VirtualHost> starting at path:line, with its Includes expanded."""
    lines = path.read_text(errors="replace").splitlines()[line - 1:]
    block = []
    for raw in lines:
        block.append(raw)
        if raw.strip().lower().startswith("</virtualhost"):
            break
    return expand_includes("\n".join(block), server_root, depth)


def expand_includes(text: str, server_root: Path, depth: int = 0) -> str:
    out = []
    for raw in text.splitlines():
        found = re.match(r'^\s*Include(?:Optional)?\s+"?([^"\s]+)"?\s*$', raw, re.I)
        if not found or depth > 3:
            out.append(raw)
            continue
        pattern = found.group(1)
        pattern = pattern if pattern.startswith("/") else str(server_root / pattern)
        for name in sorted(glob.glob(pattern)):
            try:
                out.append(expand_includes(Path(name).read_text(errors="replace"), server_root, depth + 1))
            except OSError:
                continue
    return "\n".join(out)


def check_apache_route() -> None:
    ctl = next((name for name in ("apachectl", "apache2ctl", "httpd") if shutil.which(name)), None)
    if ctl is None:
        check("ingress_internal_call_route", "UNKNOWN", "apachectl not found")
        return
    code, out = run([ctl, "-S"], merge_stderr=True)
    start = out.find(INGRESS_VHOST)
    located = re.search(r"\((/[^:()]+):(\d+)\)", out[start:]) if start >= 0 else None
    if code != 0 or located is None:
        check("ingress_internal_call_route", "UNKNOWN", f"{INGRESS_VHOST} vhost not in `{ctl} -S` rc={code}")
        return
    root = re.search(r'^ServerRoot:\s*"([^"]+)"', out, re.M)
    server_root = Path(root.group(1)) if root else Path("/etc/httpd")
    vhost_file, vhost_line = Path(located.group(1)), int(located.group(2))
    try:
        text = vhost_block(vhost_file, vhost_line, server_root)
    except OSError:
        check("ingress_internal_call_route", "UNKNOWN", f"cannot read {vhost_file}")
        return
    problems = ingress_problems(text)
    check("ingress_internal_call_route", "FAIL" if problems else "PASS",
          f"vhost={vhost_file}:{vhost_line}" + (f" problems={','.join(problems)}" if problems else ""))


def check_middleware(path: Path, grant_path: Path | None) -> None:
    try:
        env = read_env(path)
    except OSError:
        check("middleware_env_file", "FAIL", f"unreadable path={path}")
        return
    missing = [k for k in MIDDLEWARE_REQUIRED if not env.get(k)]
    check("middleware_adapter_settings", "FAIL" if missing else "PASS",
          ("missing=" + ",".join(missing)) if missing else "all worker settings present")
    base = env.get("VICIDIAL_INTERNAL_CALL_BASE_URL", "").rstrip("/")
    match = re.fullmatch(r"https://([^/:?#@]+)(?::\d+)?", base)
    check("middleware_base_url_is_origin", "PASS" if match else "FAIL",
          f"base_url={base or 'unset'} (must be https origin without path)")
    expected = env.get("VICIDIAL_INTERNAL_CALL_EXPECTED_HOST", "")
    check("middleware_expected_host", "PASS" if match and match.group(1) == expected else "FAIL",
          f"expected_host={expected or 'unset'}")
    dialing = env.get("PRODUCTION_DIALING", "DISABLED")
    check("middleware_production_dialing_disabled", "PASS" if dialing == "DISABLED" else "FAIL", f"value={dialing}")
    source_sha = env.get("APP_SOURCE_SHA") or env.get("SOURCE_SHA") or ""
    check("middleware_source_sha", "PASS" if re.fullmatch(r"[0-9a-f]{40}", source_sha) else "FAIL",
          f"value={source_sha or 'unset'}")
    for key in ("VICIDIAL_INTERNAL_CALL_HMAC_FILE", "VICIDIAL_INTERNAL_CALL_MTLS_KEY_FILE"):
        name = env.get(key)
        if not name:
            continue
        try:
            info = os.lstat(name)
            check(f"middleware_{key.lower()}", "PASS" if stat.S_ISREG(info.st_mode) and not info.st_mode & 0o077 else "FAIL",
                  f"mode={oct(info.st_mode & 0o777)}")
        except OSError:
            check(f"middleware_{key.lower()}", "UNKNOWN", "not visible from this host/namespace")

    grant_path = grant_path or (Path(env["CODESTRA_INTERNAL_CALL_POLICY_FILE"]) if env.get("CODESTRA_INTERNAL_CALL_POLICY_FILE") else None)
    if grant_path is None:
        return
    try:
        info = os.lstat(grant_path)
        grant = json.loads(Path(grant_path).read_text())
    except (OSError, ValueError):
        check("middleware_grant", "UNKNOWN", f"not visible or invalid path={grant_path}")
        return
    check("middleware_grant_protected",
          "PASS" if stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o027
          and info.st_size <= 16_384 else "FAIL",
          f"uid={info.st_uid} mode={oct(info.st_mode & 0o777)} size={info.st_size}")
    principal = grant.get("principal") or {}
    wrong = [k for k, v in {
        "destination": "internal:TEST_ECHO", "internal_only": True,
        "external_dialing": False, "max_calls": 1,
    }.items() if grant.get(k, v) != v or type(grant.get(k, v)) is not type(v)]
    if principal.get("extension") != "6901":
        wrong.append("principal.extension")
    if principal.get("campaign_id") != "TEST_SYN":
        wrong.append("principal.campaign_id")
    check("middleware_grant_literals", "FAIL" if wrong else "PASS", ("mismatch=" + ",".join(wrong)) if wrong else "")
    check("middleware_grant_source_sha", "PASS" if grant.get("source_sha") == source_sha else "FAIL",
          f"grant={grant.get('source_sha')} runtime={source_sha or 'unset'}")
    try:
        start = datetime.fromisoformat(str(grant.get("not_before")).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(grant.get("expires_at")).replace("Z", "+00:00"))
        window = (end - start).total_seconds()
        left = (end - datetime.now(timezone.utc)).total_seconds()
    except (ValueError, TypeError):
        window, left = -1, -1
    check("middleware_grant_window", "PASS" if 0 < window <= 3600 and left > 0 else "FAIL",
          f"not_before={grant.get('not_before')} expires_at={grant.get('expires_at')} minutes_left={left / 60:.0f}")
    fingerprint = principal_fingerprint({
        "tenant_id": principal.get("tenant_id"), "subject": principal.get("subject"),
        "employee_id": principal.get("employee_id"),
    })
    check("middleware_grant_cross_host_fingerprint", "INFO",
          f"authorization_reference={grant.get('authorization_reference')} principal_sha256={fingerprint}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--phase", choices=("preflight", "window", "closed"), default="preflight",
                        help="preflight: steady state + staged window env (T-1); "
                             "window: after activation, before dialing; closed: after close-out")
    parser.add_argument("--adapter-env-file", type=Path, default=ADAPTER_ENV,
                        help="the env file the adapter is running with now")
    parser.add_argument("--window-env-file", type=Path,
                        help="preflight: the staged env to install for the window (required)")
    parser.add_argument("--steady-env-file", type=Path,
                        help="saved copy of the steady env; preflight/closed compare it with the live file")
    parser.add_argument("--ami-gateway-env-file", type=Path)
    parser.add_argument("--middleware-env-file", type=Path)
    parser.add_argument("--middleware-grant-file", type=Path)
    parser.add_argument("--middleware-only", action="store_true")
    args = parser.parse_args()
    if args.phase == "preflight" and not args.middleware_only and args.window_env_file is None:
        parser.error("--phase preflight requires --window-env-file (the staged window env)")

    print(f"PROBE internal-call-readiness phase={args.phase} host={os.uname().nodename} "
          f"at={datetime.now(timezone.utc).isoformat(timespec='seconds')} mode=read-only")
    if not args.middleware_only:
        if os.geteuid() != 0:
            print("PROBE warning: not root; protected files will read as FAIL/UNKNOWN", file=sys.stderr)
        live_env = load_env(args.adapter_env_file, "adapter_env_file")
        gateway_env = load_env(args.ami_gateway_env_file, "ami_gateway_env_file") if args.ami_gateway_env_file else {}
        if args.steady_env_file and args.phase in {"preflight", "closed"}:
            check_steady_copy(args.adapter_env_file, args.steady_env_file, args.phase)
        # The env whose policy/AMI/state paths the call will use.
        call_env = live_env
        if args.phase == "preflight":
            call_env = load_env(args.window_env_file, "window_env_file")
            check_window_env(call_env, "staged")
        release_sha = check_live_adapter(live_env, args.phase)
        if args.phase != "closed":
            policy_route = check_policy(call_env, release_sha)
            check_ami_credentials(call_env)
        else:
            policy_route = None
        check_state_store(call_env, args.phase)
        check_dialplan(policy_route)
        check_pjsip()
        check_identity(args.phase)
        check_lifecycle(call_env, gateway_env)
        check_apache_route()
    if args.middleware_env_file:
        check_middleware(args.middleware_env_file, args.middleware_grant_file)

    if args.phase == "preflight":
        for manual in ("backup_restore_verified", "rollback_verified", "change_ticket_approved"):
            check(manual, "MANUAL", "operator must confirm; not machine-checkable")
    blocking = [n for n, s in RESULTS if s in {"FAIL", "UNKNOWN"}]
    verdicts = {
        "preflight": ("PREFLIGHT_BLOCKED", "PREFLIGHT_PASS_PENDING_MANUAL_CONFIRMATION"),
        "window": ("WINDOW_NOT_READY", "WINDOW_READY_TO_CALL"),
        "closed": ("CLOSEOUT_INCOMPLETE", "CLOSEOUT_VERIFIED"),
    }[args.phase]
    print("BLOCKING=" + (",".join(blocking) or "none"))
    print("VERDICT=" + verdicts[0 if blocking else 1])
    print("WRITES_PERFORMED=NONE SECRETS_PRINTED=NONE PROBE_CALLS_PLACED=0")
    return 1 if blocking else 0


def load_env(path: Path, label: str) -> dict[str, str]:
    try:
        return read_env(path)
    except OSError:
        check(label, "FAIL", f"unreadable path={path}")
        return {}


def check_steady_copy(live: Path, steady: Path, phase: str) -> None:
    """Rollback evidence: the saved steady env must equal the live file.

    Before the window this proves the copy that close-out will restore is the
    real steady configuration; after close-out it proves it was restored.
    """
    try:
        same = read_env(live) == read_env(steady)
        info = os.lstat(steady)
    except OSError:
        check("steady_env_copy", "FAIL", f"unreadable path={steady}")
        return
    protected = stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077
    check("steady_env_copy", "PASS" if same and protected else "FAIL",
          f"path={steady} matches_live={same} mode={oct(info.st_mode & 0o777)} "
          + ("(restore target)" if phase == "preflight" else "(restored)"))


if __name__ == "__main__":
    sys.exit(main())
