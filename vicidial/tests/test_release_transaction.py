from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("release_transaction", SCRIPTS / "release_transaction.py")
tx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tx)

OLD, NEW = "a" * 40, "b" * 40


class FakeAdapter:
    def __init__(self, base):
        self.base = base
        self.cwd = base / "releases" / OLD
        self.env = {"CODESTRA_ADAPTER_DEPLOYMENT": "staging-read-only", "CODESTRA_ADAPTER_BACKEND": "memory"}
        self.restarts = []
        self.probes = []
        self.fail_candidate = False
        self.fail_previous = False
        self.busy = False
        self.ready_override = {}

    def unit_bytes(self):
        return b"synthetic reviewed adapter-only unit"

    def process(self):
        return self.cwd, self.env

    def quiescent(self):
        if self.busy:
            raise tx.TransitionError("Asterisk is not verified idle")

    def probe_python(self, release):
        self.probes.append(release)

    def restart(self):
        target = (self.base / "current").resolve()
        self.restarts.append(target.name)
        if (target.name == NEW and self.fail_candidate) or (target.name == OLD and self.fail_previous):
            raise tx.TransitionError("synthetic restart failure")
        self.cwd = target

    def documents(self):
        return {
            "health": {"status": "live"},
            "ready": {
                "status": "ready", "deployment_profile": "staging-read-only", "backend": "memory",
                "writes_enabled": False, "internal_test_calling": False,
                "production_dialing": False, "customer_traffic_authorized": False,
                "release_verified": True, "release_sha": self.cwd.name, **self.ready_override,
            },
            "version": {"verified": True, "git_sha": self.cwd.name},
        }


def write_document(path, document):
    path.write_bytes(tx.canonical(document))
    path.chmod(0o600)
    return tx.sha256(path.read_bytes())


def prepared_release(base, source):
    root = base / "releases" / source
    root.mkdir(parents=True)
    for name in tx.build.__globals__["INCLUDED"]:
        (root / name).mkdir()
    (root / ".venv/bin").mkdir()
    (root / ".venv/bin/python").write_bytes(b"synthetic immutable interpreter; never executed")
    (root / ".venv/bin/uvicorn").write_text("#!" + str(root / ".venv/bin/python") + "\n")
    (root / "src/codestra_vicidial").mkdir()
    (root / "src/codestra_vicidial/__init__.py").write_text('__version__ = "synthetic"\n')
    (root / "SOURCE.COMMIT").write_text(source + "\n")
    (root / "BUILD_INFO.json").write_text(json.dumps({"git_sha": source}))
    (root / "pyproject.toml").write_text("[project]\n")
    (root / "requirements.lock").write_text("synthetic==1\n")
    manifest = tx.build(root, source, "2026-09-10T00:00:00Z")
    raw = tx.canonical(manifest)
    (root / "INSTALLED_MANIFEST.json").write_bytes(raw)
    return tx.sha256(raw)


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    base, control, state = (tmp_path / name for name in ("adapter", "authority", "transaction"))
    base.mkdir()
    control.mkdir()
    old_digest = prepared_release(base, OLD)
    new_digest = prepared_release(base, NEW)
    (base / "current").symlink_to(base / "releases" / OLD)
    environment = tmp_path / "adapter.env"
    environment.write_text("CODESTRA_ADAPTER_DEPLOYMENT=staging-read-only\n")
    environment.chmod(0o600)
    release_evidence = {
        "source_sha": NEW, "installed_manifest_sha256": new_digest,
        "artifact_sha256": "c" * 64,
        "signature_verified": True, "provenance_verified": True,
        "protected_ancestry_verified": True, "offline_dependencies_verified": True,
        "release_local_venv_verified": True, "state_compatibility_verified": True,
    }
    backup_evidence = {
        "previous_sha": OLD, "expires_at": "2099-01-01T00:00:00Z",
        "encrypted_backup_verified": True, "off_host_copy_verified": True,
        "isolated_restore_verified": True, "configuration_restore_verified": True,
        "state_restore_verified": True,
    }
    system = FakeAdapter(base)
    plan = {
        "schema_version": 1, "authorization_reference": "CHG-RELEASE-TEST-20260910",
        "expires_at": "2099-01-01T00:00:00Z", "source_sha": NEW, "previous_sha": OLD,
        "manifest_sha256": new_digest, "previous_manifest_sha256": old_digest,
        "unit_sha256": tx.sha256(system.unit_bytes()),
        "environment_sha256": tx.sha256(environment.read_bytes()),
        "release_evidence_sha256": write_document(control / "verified-release.json", release_evidence),
        "backup_evidence_sha256": write_document(control / "verified-backup.json", backup_evidence),
    }
    write_document(control / "plan.json", plan)
    transaction = tx.Transaction(base, control, state, environment, system, owner=os.getuid())
    monkeypatch.setattr(tx.time, "sleep", lambda _seconds: None)
    return transaction, system, plan


def test_preflight_checks_prepared_bytes_without_switch_or_restart(deployment):
    transaction, system, _plan = deployment
    before = {path: path.read_bytes() for path in system.cwd.rglob("*") if path.is_file()}
    assert transaction.execute("preflight")["status"] == "prepared"
    assert transaction.current().name == OLD
    assert system.restarts == []
    assert transaction.journal() is None
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_activation_is_idempotent_and_rollback_preserves_predecessor(deployment):
    transaction, system, _plan = deployment
    original = {path: path.read_bytes() for path in system.cwd.rglob("*") if path.is_file()}
    assert transaction.execute("activate")["status"] == "committed"
    assert transaction.current().name == NEW
    assert transaction.execute("activate")["status"] == "already_committed"
    assert system.restarts == [NEW]
    assert transaction.execute("rollback")["status"] == "rolled_back"
    assert transaction.current().name == OLD
    assert transaction.execute("rollback")["status"] == "rolled_back"
    assert system.restarts == [NEW, OLD]
    assert all(path.read_bytes() == raw for path, raw in original.items())


def test_failed_start_restores_predecessor_but_still_reports_failure(deployment):
    transaction, system, _plan = deployment
    system.fail_candidate = True
    with pytest.raises(tx.TransitionError, match="predecessor restored and verified"):
        transaction.execute("activate")
    assert system.restarts == [NEW, OLD]
    assert transaction.current().name == OLD
    assert transaction.journal()["phase"] == "rolled_back"


def test_failed_rollback_retains_recoverable_intent(deployment):
    transaction, system, _plan = deployment
    system.fail_candidate = system.fail_previous = True
    with pytest.raises(tx.TransitionError, match="requires intervention"):
        transaction.execute("activate")
    assert transaction.journal()["phase"] == "recovery_failed"
    system.fail_previous = False
    assert transaction.execute("rollback")["status"] == "rolled_back"


def test_interruption_after_switch_recovers_from_durable_journal(deployment, monkeypatch):
    transaction, system, _plan = deployment
    actual_restart = system.restart
    monkeypatch.setattr(system, "restart", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        transaction.execute("activate")
    assert transaction.current().name == NEW
    assert transaction.journal()["phase"] == "switched"
    with pytest.raises(tx.TransitionError, match="unfinished deployment"):
        transaction.execute("activate")
    monkeypatch.setattr(system, "restart", actual_restart)
    assert transaction.execute("rollback")["status"] == "rolled_back"


def test_recovery_remains_possible_after_authorization_expires(deployment, monkeypatch):
    transaction, _system, _plan = deployment
    transaction.execute("activate")
    monkeypatch.setattr(tx, "future", lambda _value: False)
    with pytest.raises(tx.TransitionError, match="authorization has expired"):
        transaction.execute("activate")
    assert transaction.execute("rollback")["status"] == "rolled_back"


@pytest.mark.parametrize("change", ["profile", "dialing", "writes", "busy", "process", "current", "environment"])
def test_effectful_or_inconsistent_runtime_blocks_before_switch(deployment, change):
    transaction, system, _plan = deployment
    if change == "profile":
        system.env["CODESTRA_ADAPTER_DEPLOYMENT"] = "production-external-one-call"
    elif change == "dialing":
        system.env["ENABLE_PRODUCTION_DIALING"] = "true"
    elif change == "writes":
        system.env["ENABLE_VICIDIAL_WRITES"] = "true"
    elif change == "busy":
        system.busy = True
    elif change == "process":
        system.cwd = transaction.base / "releases" / NEW
    elif change == "current":
        transaction.switch(transaction.base / "releases" / NEW)
    else:
        transaction.environment.write_text("changed=true\n")
    before = transaction.current()
    with pytest.raises(tx.TransitionError):
        transaction.execute("activate")
    assert transaction.current() == before
    assert system.restarts == []
    assert transaction.journal() is None


@pytest.mark.parametrize("field,value", [
    ("production_dialing", True), ("writes_enabled", None),
    ("release_sha", NEW), ("release_verified", False), ("deployment_profile", None),
])
def test_http_readback_must_prove_disabled_matching_release(deployment, field, value):
    transaction, system, _plan = deployment
    system.ready_override[field] = value
    with pytest.raises(tx.TransitionError):
        transaction.execute("preflight")
    assert system.restarts == []


@pytest.mark.parametrize("change", ["file", "manifest", "mode", "external_symlink", "old_shebang"])
def test_unapproved_prepared_release_is_never_executed(deployment, change):
    transaction, system, plan = deployment
    root = transaction.base / "releases" / NEW
    if change == "file":
        (root / "src/codestra_vicidial/__init__.py").write_text("changed=True\n")
    elif change == "manifest":
        (root / "INSTALLED_MANIFEST.json").write_text("{}")
    elif change == "mode":
        (root / "src").chmod(0o777)
    elif change == "external_symlink":
        (root / "unapproved-link").symlink_to(transaction.environment)
    else:
        (root / ".venv/bin/uvicorn").write_text(
            "#!" + str(transaction.base / "releases" / OLD / ".venv/bin/python") + "\n"
        )
        plan["manifest_sha256"] = write_document(
            root / "INSTALLED_MANIFEST.json", tx.build(root, NEW, "2026-09-10T00:00:00Z")
        )
        release = transaction.document("verified-release.json")
        release["installed_manifest_sha256"] = plan["manifest_sha256"]
        plan["release_evidence_sha256"] = write_document(transaction.control / "verified-release.json", release)
        write_document(transaction.control / "plan.json", plan)
    with pytest.raises((tx.TransitionError, ValueError)):
        transaction.execute("activate")
    assert system.restarts == []
    assert transaction.current().name == OLD


@pytest.mark.parametrize("evidence,key", [
    ("verified-release.json", "signature_verified"),
    ("verified-release.json", "offline_dependencies_verified"),
    ("verified-release.json", "state_compatibility_verified"),
    ("verified-backup.json", "off_host_copy_verified"),
    ("verified-backup.json", "isolated_restore_verified"),
])
def test_missing_independent_evidence_blocks_even_when_report_digest_matches(deployment, evidence, key):
    transaction, system, plan = deployment
    document = transaction.document(evidence)
    document[key] = False
    field = "release_evidence_sha256" if evidence == "verified-release.json" else "backup_evidence_sha256"
    plan[field] = write_document(transaction.control / evidence, document)
    write_document(transaction.control / "plan.json", plan)
    with pytest.raises(tx.TransitionError):
        transaction.execute("activate")
    assert system.restarts == []


def test_configuration_drift_blocks_rollback_without_overwriting_other_work(deployment):
    transaction, system, _plan = deployment
    transaction.execute("activate")
    transaction.environment.write_text("operator changed configuration\n")
    with pytest.raises(tx.TransitionError, match="environment configuration changed"):
        transaction.execute("rollback")
    assert transaction.current().name == NEW
    assert system.restarts == [NEW]


def test_second_deployer_cannot_enter_while_transaction_is_locked(deployment):
    transaction, system, _plan = deployment
    with transaction.locked(), pytest.raises(BlockingIOError):
        transaction.execute("activate")
    assert system.restarts == []


def test_cli_has_no_caller_selected_path_unit_or_shell_option():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "release_transaction.py"), "--help"],
        text=True, capture_output=True, check=True,
    )
    assert "{preflight,activate,rollback}" in result.stdout
    for option in ("--unit", "--path", "--command", "--plan", "--source"):
        assert option not in result.stdout

def test_rollback_does_not_require_the_failed_candidate_directory(deployment):
    transaction, system, _plan = deployment
    transaction.execute("activate")
    shutil.rmtree(transaction.base / "releases" / NEW)
    assert transaction.execute("rollback")["status"] == "rolled_back"
    assert transaction.current().name == OLD
    assert system.restarts == [NEW, OLD]


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("expires_at", "invalid"),
    ("expires_at", "2026-09-10T00:00:00"),
])
def test_recovery_rejects_malformed_plan_even_when_expiry_may_be_old(deployment, field, value):
    _transaction, _system, plan = deployment
    plan[field] = value
    with pytest.raises(tx.TransitionError):
        tx.validate_plan(plan, expired_ok=True)

@pytest.mark.parametrize("relative", [
    "src/codestra_vicidial/__pycache__/module.pyc",
    "src/codestra_vicidial/legacy.pyc", "scripts/legacy.pyo",
    ".venv/lib/__pycache__/dependency.pyc",
])
def test_excluded_bytecode_is_rejected_before_candidate_interpreter_runs(deployment, relative):
    transaction, system, _plan = deployment
    root = transaction.base / "releases" / NEW
    cache = root / relative
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"synthetic cache marker; never executed")
    with pytest.raises(tx.TransitionError, match="unmanifested cache or bytecode"):
        transaction.execute("activate")
    assert root not in system.probes
    assert system.restarts == []
    assert transaction.current().name == OLD


def test_controller_does_not_create_excluded_bytecode_in_its_own_release(tmp_path):
    for name in ("release_transaction.py", "generate_installed_manifest.py", "verify_release_tuple.py"):
        shutil.copyfile(SCRIPTS / name, tmp_path / name)
    subprocess.run(
        [sys.executable, str(tmp_path / "release_transaction.py"), "--help"],
        text=True, capture_output=True, check=True,
    )
    assert not list(tmp_path.rglob("__pycache__"))


@pytest.mark.parametrize("states", [("yes",), ("",), ("unknown",), ("no", "yes")])
def test_unloaded_unit_configuration_blocks_before_switch(deployment, monkeypatch, states):
    transaction, system, _plan = deployment
    boundary = tx.SystemAdapter()
    loaded_states = iter(states)
    unit = system.unit_bytes().decode()

    def systemctl(*command, **_kwargs):
        assert command[:2] in {
            ("/usr/bin/systemctl", "show"), ("/usr/bin/systemctl", "cat")
        }
        assert tx.UNIT in command
        return next(loaded_states) if command[1] == "show" else unit

    monkeypatch.setattr(boundary, "run", systemctl)
    monkeypatch.setattr(system, "unit_bytes", boundary.unit_bytes)
    with pytest.raises(tx.TransitionError, match="not loaded by systemd"):
        transaction.execute("activate")
    assert system.restarts == []
    assert transaction.current().name == OLD
    assert transaction.journal() is None


def test_loaded_unit_configuration_allows_readonly_preflight(deployment, monkeypatch):
    transaction, system, _plan = deployment
    boundary = tx.SystemAdapter()
    unit = system.unit_bytes().decode()
    monkeypatch.setattr(boundary, "run", lambda *args, **_kwargs: "no" if args[1] == "show" else unit)
    monkeypatch.setattr(system, "unit_bytes", boundary.unit_bytes)
    assert transaction.execute("preflight")["status"] == "prepared"
    assert system.restarts == []


def test_completed_audit_records_survive_the_next_transaction(deployment):
    transaction, _system, plan = deployment
    transaction.execute("activate")
    transaction.execute("rollback")
    directory = transaction.state / "audit"
    before = {path: path.read_bytes() for path in directory.glob("*.json")}
    records = [json.loads(raw) for raw in before.values()]
    assert {"committed", "rolled_back"} <= {record["phase"] for record in records}
    original_id = records[0]["transaction_id"]
    assert all(record["transaction_id"] == original_id for record in records)
    assert all(path.stat().st_mode & 0o077 == 0 for path in before)
    assert directory.stat().st_mode & 0o077 == 0

    plan["authorization_reference"] = "CHG-RELEASE-SECOND-20260910"
    write_document(transaction.control / "plan.json", plan)
    transaction.execute("activate")
    assert transaction.journal()["transaction_id"] != original_id
    assert all(path.read_bytes() == raw for path, raw in before.items())
    assert len(list(directory.glob("*.json"))) > len(before)


def test_audit_failure_before_intent_blocks_pointer_and_restart(deployment, monkeypatch):
    transaction, system, _plan = deployment

    def unavailable(_record):
        raise OSError("synthetic audit storage failure")

    monkeypatch.setattr(transaction, "audit", unavailable)
    with pytest.raises(OSError, match="audit storage failure"):
        transaction.execute("activate")
    assert transaction.current().name == OLD
    assert system.restarts == []
    assert transaction.journal() is None


def test_completion_audit_failure_rolls_back_without_claiming_commit(deployment, monkeypatch):
    transaction, system, _plan = deployment
    audit = transaction.audit

    def fail_completion(record):
        if record["phase"] == "committed":
            raise OSError("synthetic completion audit failure")
        audit(record)

    monkeypatch.setattr(transaction, "audit", fail_completion)
    with pytest.raises(tx.TransitionError, match="predecessor restored and verified"):
        transaction.execute("activate")
    assert transaction.current().name == OLD
    assert system.restarts == [NEW, OLD]
    records = [json.loads(path.read_bytes()) for path in (transaction.state / "audit").glob("*.json")]
    assert "committed" not in {record["phase"] for record in records}
    assert transaction.journal()["phase"] == "rolled_back"
