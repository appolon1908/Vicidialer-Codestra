import importlib.util
import subprocess
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "ops"
    / "certificate-renewal"
    / "codestra_certificate_renewal.py"
)
SPEC = importlib.util.spec_from_file_location("codestra_certificate_renewal", SCRIPT)
assert SPEC and SPEC.loader
RENEWAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENEWAL)


def test_renewal_units_are_fail_closed() -> None:
    root = Path(__file__).parents[1] / "ops" / "certificate-renewal"
    service = (root / "codestra-vicidial-certificate-renewal.service").read_text()
    timer = (root / "codestra-vicidial-certificate-renewal.timer").read_text()
    script = (root / "codestra_certificate_renewal.py").read_text()
    assert "OnCalendar=*-*-* 00,12:00:00" in timer
    assert "RandomizedDelaySec=30m" in timer
    assert "Persistent=true" in timer
    assert "os.replace(new_cert, cert)" in script
    assert "os.replace(backup_cert, cert)" in script
    assert "validate_leaf(new_cert, new_key, ca, identity)" in script
    assert "alert=certificate_renewal_failure" in script
    assert "(30, 14, 7, 3, 1)" in script
    assert "PrivateTmp=yes" in service
    assert "ProtectSystem=strict" in service
    assert "/etc/codestra/secrets/middleware-event-mtls" in service


def test_production_identity_is_exact() -> None:
    text = (
        Path(__file__).parents[1]
        / "ops"
        / "certificate-renewal"
        / "observer-server.json.example"
    ).read_text()
    assert "DNS:vici-agent" in text
    assert "IP Address:10.40.0.2" in text
    assert "spiffe://codestra.internal/agent/vici" in text
    assert '"purpose": "sslserver"' in text


def test_required_sans_are_exact_entries() -> None:
    expected = RENEWAL.required_sans(
        ["DNS:vici-agent", "IP Address:10.40.0.2", "URI:spiffe://codestra.internal/agent/vici"]
    )
    actual = {
        ("DNS", "vici-agent.example"),
        ("IP Address", "10.40.0.20"),
        ("URI", "spiffe://codestra.internal/agent/vici-extra"),
    }
    assert expected - actual == expected


def test_install_rolls_back_pair_when_key_replace_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cert, key = tmp_path / "live.crt", tmp_path / "live.key"
    new_cert, new_key = tmp_path / "new.crt", tmp_path / "new.key"
    cert.write_text("old-cert")
    key.write_text("old-key")
    new_cert.write_text("new-cert")
    new_key.write_text("new-key")
    real_replace = RENEWAL.os.replace

    def fail_new_key(source: Path, destination: Path) -> None:
        if Path(source) == new_key:
            raise OSError("simulated key replacement failure")
        real_replace(source, destination)

    monkeypatch.setattr(RENEWAL.os, "replace", fail_new_key)
    monkeypatch.setattr(
        RENEWAL.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0),
    )
    identity = {"service": "fixture.service", "post_health_command": ["true"]}
    with pytest.raises(OSError, match="simulated key replacement failure"):
        RENEWAL.install_leaf(new_cert, new_key, cert, key, identity)
    assert cert.read_text() == "old-cert"
    assert key.read_text() == "old-key"
