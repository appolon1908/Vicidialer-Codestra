#!/usr/bin/env python3.11
"""Activate or roll back an independently verified, prepared adapter release.

The CLI accepts an action only. A root-owned, digest-bound plan supplies all
authority; this tool never downloads, builds, installs, migrates, or enables calls.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import HTTPRedirectHandler, build_opener

from generate_installed_manifest import build
from verify_release_tuple import verify as verify_tuple

UNIT = "codestra-vicidial-restricted-adapter.service"
BASE = Path("/opt/codestra/vicidial-restricted-adapter")
CONTROL = Path("/etc/codestra/vicidial-release")
STATE = Path("/var/lib/codestra-vicidial-release")
ENVIRONMENT = Path("/etc/codestra/vicidial-restricted-adapter.env")
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
DISABLED = (
    "ENABLE_VICIDIAL_WRITES", "ENABLE_PROVISIONING", "ENABLE_AGENT_PROVISIONING",
    "ENABLE_AGENT_SYNC", "ENABLE_LEAD_PUBLICATION", "ENABLE_LIVE_CALL_CONTROL",
    "ENABLE_WARM_TRANSFER", "ENABLE_IVR_ROUTING", "ENABLE_IVR_CALLBACKS",
    "ENABLE_PRODUCTION_DIALING", "ENABLE_INTERNAL_TEST_CALLING",
    "ENABLE_SMS_DELIVERY", "ENABLE_EMAIL_DELIVERY", "ENABLE_EXTERNAL_DELIVERY",
    "ENABLE_AI_ACTIONS",
)
PLAN_FIELDS = {
    "schema_version", "authorization_reference", "expires_at", "source_sha",
    "previous_sha", "manifest_sha256", "previous_manifest_sha256",
    "unit_sha256", "environment_sha256", "release_evidence_sha256",
    "backup_evidence_sha256",
}


class TransitionError(RuntimeError):
    """A bounded deployment prerequisite or read-back failed."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def future(value: str) -> bool:
    try:
        deadline = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return deadline.tzinfo is not None and deadline > datetime.now(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return False


def validate_plan(plan: dict, *, expired_ok: bool = False) -> None:
    if not isinstance(plan, dict) or set(plan) != PLAN_FIELDS or type(plan["schema_version"]) is not int or plan["schema_version"] != 1:
        raise TransitionError("release plan schema is invalid")
    if not isinstance(plan["authorization_reference"], str) or not re.fullmatch(
        r"CHG-[A-Z0-9-]{10,80}", plan["authorization_reference"]
    ):
        raise TransitionError("release authorization reference is invalid")
    for name in ("source_sha", "previous_sha"):
        if not isinstance(plan[name], str) or not SHA.fullmatch(plan[name]):
            raise TransitionError("release source identity is invalid")
    for name in PLAN_FIELDS - {
        "schema_version", "authorization_reference", "expires_at", "source_sha", "previous_sha"
    }:
        if not isinstance(plan[name], str) or not DIGEST.fullmatch(plan[name]):
            raise TransitionError("release evidence digest is invalid")
    if plan["source_sha"] == plan["previous_sha"]:
        raise TransitionError("candidate and predecessor must differ")
    try:
        expiry = datetime.fromisoformat(plan["expires_at"].replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise TransitionError("release authorization expiry is invalid") from exc
    if expiry.tzinfo is None:
        raise TransitionError("release authorization expiry requires a timezone")
    if not expired_ok and not future(plan["expires_at"]):
        raise TransitionError("release authorization has expired")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise TransitionError("adapter health redirect is forbidden")


class SystemAdapter:
    def run(self, *command: str, timeout: int = 30, **kwargs) -> str:
        try:
            result = subprocess.run(
                command, check=True, capture_output=True,
                text=True, timeout=timeout, **kwargs,
            )
            return result.stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise TransitionError("bounded adapter command failed") from exc

    def unit_bytes(self) -> bytes:
        return self.run("/usr/bin/systemctl", "cat", UNIT).encode()

    def restart(self) -> None:
        self.run("/usr/bin/systemctl", "restart", UNIT, timeout=60)

    def process(self) -> tuple[Path, dict[str, str]]:
        value = self.run("/usr/bin/systemctl", "show", UNIT, "--property=MainPID", "--value").strip()
        if not value.isdigit() or int(value) <= 1:
            raise TransitionError("adapter process is not running")
        process = Path("/proc") / value
        account = pwd.getpwnam("codestra-adapter")
        if process.stat().st_uid != account.pw_uid or process.stat().st_gid != account.pw_gid:
            raise TransitionError("adapter process owner mismatch")
        environment = {}
        for item in (process / "environ").read_bytes().split(b"\0"):
            if b"=" in item:
                key, content = item.split(b"=", 1)
                environment[key.decode()] = content.decode()
        cwd = (process / "cwd").resolve(strict=True)
        if (process / "exe").resolve(strict=True) != (cwd / ".venv/bin/python").resolve(strict=True):
            raise TransitionError("effective interpreter is not bound to the process release")
        return cwd, environment

    def quiescent(self) -> None:
        output = self.run("/usr/sbin/asterisk", "-rx", "core show channels count")
        for field in ("channels", "calls"):
            values = re.findall(r"^(\d+) active " + field + r"\s*$", output, re.MULTILINE)
            if values != ["0"]:
                raise TransitionError("Asterisk is not verified idle")

    def documents(self) -> dict:
        opener = build_opener(NoRedirect)
        documents = {}
        for endpoint in ("health", "ready", "version"):
            with opener.open("http://127.0.0.1:8097/" + endpoint, timeout=3) as response:
                if response.status != 200:
                    raise TransitionError("adapter HTTP contract failed")
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise TransitionError("adapter HTTP document is too large")
            documents[endpoint] = json.loads(raw)
        return documents

    def probe_python(self, release: Path) -> None:
        account = pwd.getpwnam("codestra-adapter")
        script = (
            "import json,sys,pathlib,codestra_vicidial;"
            "print(json.dumps({'prefix':str(pathlib.Path(sys.prefix).resolve()),"
            "'module':str(pathlib.Path(codestra_vicidial.__file__).resolve()),"
            "'python':list(sys.version_info[:2])}))"
        )
        document = json.loads(self.run(
            str(release / ".venv/bin/python"), "-B", "-c", script,
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(release / "src"),
                 "PYTHONDONTWRITEBYTECODE": "1"},
            cwd=release, user=account.pw_uid, group=account.pw_gid, extra_groups=[],
        ))
        if document != {
            "prefix": str(release / ".venv"),
            "module": str(release / "src/codestra_vicidial/__init__.py"),
            "python": [3, 11],
        }:
            raise TransitionError("release-local interpreter or module binding mismatch")


class Transaction:
    def __init__(self, base=BASE, control=CONTROL, state=STATE, environment=ENVIRONMENT,
                 system=None, owner=0):
        self.base, self.control, self.state = Path(base), Path(control), Path(state)
        self.environment = Path(environment)
        self.system = system or SystemAdapter()
        self.owner = owner

    def trusted(self, path: Path, *, directory=False) -> bytes:
        info = path.lstat()
        expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not expected or info.st_uid != self.owner or info.st_mode & 0o022:
            raise TransitionError("release authority ownership or mode is unsafe")
        return b"" if directory else path.read_bytes()

    def document(self, name: str, expected_digest: str | None = None) -> dict:
        raw = self.trusted(self.control / name)
        if expected_digest is not None and sha256(raw) != expected_digest:
            raise TransitionError("release evidence digest mismatch")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise TransitionError("release evidence is not an object")
        return result

    def plan(self) -> dict:
        self.trusted(self.control.parent, directory=True)
        self.trusted(self.control, directory=True)
        plan = self.document("plan.json")
        validate_plan(plan)
        return plan

    def configuration(self, plan: dict) -> None:
        if sha256(self.system.unit_bytes()) != plan["unit_sha256"]:
            raise TransitionError("adapter unit configuration changed")
        if sha256(self.trusted(self.environment)) != plan["environment_sha256"]:
            raise TransitionError("adapter environment configuration changed")

    def evidence(self, plan: dict) -> None:
        release = self.document("verified-release.json", plan["release_evidence_sha256"])
        expected = {
            "source_sha": plan["source_sha"], "installed_manifest_sha256": plan["manifest_sha256"],
            "signature_verified": True, "provenance_verified": True,
            "protected_ancestry_verified": True, "offline_dependencies_verified": True,
            "release_local_venv_verified": True, "state_compatibility_verified": True,
        }
        if any((release.get(key) is not True) if value is True else (release.get(key) != value)
               for key, value in expected.items()):
            raise TransitionError("independent prepared-release verification is incomplete")
        if not isinstance(release.get("artifact_sha256"), str) or not DIGEST.fullmatch(release["artifact_sha256"]):
            raise TransitionError("verified source artifact digest is missing")
        backup = self.document("verified-backup.json", plan["backup_evidence_sha256"])
        if backup.get("previous_sha") != plan["previous_sha"] or not future(backup.get("expires_at")):
            raise TransitionError("predecessor recovery evidence is stale or mismatched")
        if any(backup.get(key) is not True for key in (
            "encrypted_backup_verified", "off_host_copy_verified", "isolated_restore_verified",
            "configuration_restore_verified", "state_restore_verified",
        )):
            raise TransitionError("predecessor recovery evidence is incomplete")

    def release(self, source: str, expected_manifest: str) -> Path:
        self.trusted(self.base.parent, directory=True)
        self.trusted(self.base, directory=True)
        releases = self.base / "releases"
        self.trusted(releases, directory=True)
        root = releases / source
        self.trusted(root, directory=True)
        for path in root.rglob("*"):
            if path.is_symlink():
                if path.lstat().st_uid != self.owner:
                    raise TransitionError("prepared release symlink ownership is unsafe")
                if not path.resolve(strict=True).is_relative_to(root):
                    raise TransitionError("prepared release contains an external symlink")
            else:
                self.trusted(path, directory=path.is_dir())
        manifest = root / "INSTALLED_MANIFEST.json"
        raw = self.trusted(manifest)
        if sha256(raw) != expected_manifest:
            raise TransitionError("prepared release manifest is not approved")
        document = json.loads(raw)
        if raw != canonical(build(root, source, document.get("generated_at", ""))):
            raise TransitionError("prepared release bytes differ from the approved manifest")
        verify_tuple(root, manifest)
        python = root / ".venv/bin/python"
        if not python.resolve(strict=True).is_relative_to(root):
            raise TransitionError("interpreter bytes are outside the prepared release")
        for entry in (root / ".venv/bin").iterdir():
            if entry.is_file():
                with entry.open("rb") as stream:
                    first = stream.readline(4096)
                if first.startswith(b"#!") and b"python" in first.lower():
                    interpreter = Path(first[2:].strip().decode())
                    if interpreter.parent != python.parent or not interpreter.resolve().is_relative_to(root):
                        raise TransitionError("console script retains another release interpreter")
        self.system.probe_python(root)
        return root

    def current(self, *, allow_missing: bool = False) -> Path:
        path = self.base / "current"
        if not path.is_symlink():
            raise TransitionError("current release is not a symlink")
        target = path.resolve(strict=not allow_missing)
        if target.parent != self.base / "releases" or not SHA.fullmatch(target.name):
            raise TransitionError("current release is outside the approved release directory")
        return target

    def runtime(self, expected: Path) -> None:
        cwd, env = self.system.process()
        if cwd != expected or self.current() != expected:
            raise TransitionError("current symlink and effective process release disagree")
        if env.get("CODESTRA_ADAPTER_DEPLOYMENT") != "staging-read-only":
            raise TransitionError("release transition requires the read-only staging profile")
        if env.get("CODESTRA_ADAPTER_BACKEND") != "memory":
            raise TransitionError("release transition requires the isolated staging backend")
        if any(env.get(flag, "false") != "false" for flag in DISABLED):
            raise TransitionError("calling or write capabilities are enabled")
        documents = self.system.documents()
        health, ready, version = (documents[name] for name in ("health", "ready", "version"))
        if health.get("status") != "live" or ready.get("status") != "ready":
            raise TransitionError("adapter health or readiness failed")
        if ready.get("deployment_profile") != "staging-read-only" or ready.get("backend") != "memory":
            raise TransitionError("adapter readiness profile mismatch")
        for name in ("writes_enabled", "internal_test_calling", "production_dialing", "customer_traffic_authorized"):
            if ready.get(name) is not False:
                raise TransitionError("adapter readiness capability is not disabled")
        if ready.get("release_verified") is not True or ready.get("release_sha") != expected.name:
            raise TransitionError("adapter readiness release mismatch")
        if version.get("verified") is not True or version.get("git_sha") != expected.name:
            raise TransitionError("adapter version release mismatch")

    def observe(self, expected: Path) -> None:
        for attempt in range(10):
            try:
                self.runtime(expected)
                return
            except (OSError, ValueError, TransitionError):
                if attempt == 9:
                    raise
                time.sleep(1)

    def save(self, record: dict) -> None:
        with tempfile.NamedTemporaryFile(dir=self.state, prefix=".journal-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(canonical(record))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, self.state / "journal.json")
            self.sync_directory(self.state)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def sync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def switch(self, target: Path) -> None:
        temporary = self.base / (".current-" + uuid.uuid4().hex)
        try:
            temporary.symlink_to(target)
            os.replace(temporary, self.base / "current")
            self.sync_directory(self.base)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def locked(self):
        self.state.mkdir(mode=0o700, parents=False, exist_ok=True)
        self.trusted(self.state, directory=True)
        descriptor = os.open(self.state / "transaction.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if info.st_uid != self.owner or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode):
                raise TransitionError("transaction lock ownership or mode is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(descriptor)

    def journal(self) -> dict | None:
        path = self.state / "journal.json"
        return json.loads(self.trusted(path)) if path.exists() else None

    def preflight(self, plan: dict) -> tuple[Path, Path]:
        self.configuration(plan)
        self.evidence(plan)
        previous = self.release(plan["previous_sha"], plan["previous_manifest_sha256"])
        candidate = self.release(plan["source_sha"], plan["manifest_sha256"])
        self.runtime(previous)
        self.system.quiescent()
        return previous, candidate

    def restore(self, record: dict) -> None:
        plan = record["plan"]
        validate_plan(plan, expired_ok=True)
        self.configuration(plan)
        previous = self.release(plan["previous_sha"], plan["previous_manifest_sha256"])
        # Recovery must not depend on the failed candidate still being present.
        if self.current(allow_missing=True).name not in {plan["source_sha"], plan["previous_sha"]}:
            raise TransitionError("another deployment changed current; recovery stopped")
        self.system.quiescent()
        record["phase"] = "rolling_back"
        self.save(record)
        self.switch(previous)
        self.system.restart()
        self.observe(previous)
        record["phase"] = "rolled_back"
        self.save(record)

    def execute(self, action: str) -> dict:
        with self.locked():
            record = self.journal()
            if action == "rollback":
                if record is None or record.get("phase") not in {
                    "switching", "switched", "committed", "rolling_back", "recovery_failed", "rolled_back"
                }:
                    raise TransitionError("no recorded deployment to roll back")
                if record["phase"] == "rolled_back":
                    validate_plan(record["plan"], expired_ok=True)
                    self.configuration(record["plan"])
                    self.release(record["plan"]["previous_sha"], record["plan"]["previous_manifest_sha256"])
                    self.observe(self.base / "releases" / record["plan"]["previous_sha"])
                    return {"status": "rolled_back", "source_sha": record["plan"]["previous_sha"]}
                self.restore(record)
                return {"status": "rolled_back", "source_sha": record["plan"]["previous_sha"]}
            plan = self.plan()
            if record and record.get("phase") not in {"committed", "rolled_back"}:
                raise TransitionError("unfinished deployment requires explicit rollback")
            if record and record.get("phase") == "committed" and record.get("plan") == plan:
                self.configuration(plan)
                self.release(plan["source_sha"], plan["manifest_sha256"])
                self.observe(self.base / "releases" / plan["source_sha"])
                return {"status": "already_committed", "source_sha": plan["source_sha"]}
            previous, candidate = self.preflight(plan)
            if action == "preflight":
                return {"status": "prepared", "source_sha": candidate.name, "previous_sha": previous.name}
            if action != "activate":
                raise TransitionError("unsupported release action")
            record = {"schema_version": 1, "plan": plan, "phase": "switching",
                      "started_at": datetime.now(timezone.utc).isoformat()}
            self.save(record)
            try:
                self.switch(candidate)
                record["phase"] = "switched"
                self.save(record)
                self.configuration(plan)
                self.system.restart()
                self.observe(candidate)
                record["phase"] = "committed"
                self.save(record)
            except Exception as exc:
                try:
                    self.restore(record)
                except Exception:  # noqa: BLE001 - retain recovery intent for every failure
                    record["phase"] = "recovery_failed"
                    self.save(record)
                    raise TransitionError("deployment failed; predecessor recovery requires intervention") from exc
                raise TransitionError("deployment failed; predecessor restored and verified") from exc
            return {"status": "committed", "source_sha": candidate.name, "previous_sha": previous.name}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "activate", "rollback"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("root deployment authority is required")
    try:
        result = Transaction().execute(args.action)
    except Exception as exc:  # noqa: BLE001 - redact failures at the privileged CLI boundary
        # Do not print subprocess output, environment values, private policy or paths.
        message = str(exc) if isinstance(exc, TransitionError) else "release authority validation failed"
        print(json.dumps({"status": "failed", "reason": message}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
