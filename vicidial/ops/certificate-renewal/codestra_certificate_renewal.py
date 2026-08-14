#!/usr/bin/env python3
"""Fail-closed renewal of a Codestra private leaf certificate.

The CA private key is deliberately out of scope.  A separately reviewed local
enrollment helper receives a CSR path and an output certificate path.  It must
return zero only after the approved production CA has issued the certificate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def run(*args: str, input_data: bytes | None = None) -> bytes:
    return subprocess.run(
        args, input=input_data, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ).stdout


def certificate_metadata(path: Path) -> tuple[datetime, str, str]:
    decoded = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]
    expiry = datetime.strptime(decoded["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=timezone.utc
    )
    serial = str(decoded["serialNumber"])
    der = ssl.PEM_cert_to_DER_cert(path.read_text())
    return expiry, serial, hashlib.sha256(der).hexdigest()


def public_key(path: Path, *, certificate: bool) -> bytes:
    args = ["openssl", "x509" if certificate else "pkey", "-in", str(path)]
    if certificate:
        args.extend(["-pubkey", "-noout"])
    else:
        args.append("-pubout")
    return run(*args)


def required_sans(values: list[object]) -> set[tuple[str, str]]:
    """Convert configured OpenSSL-style SANs to ssl decoder tuples."""
    parsed: set[tuple[str, str]] = set()
    for raw_value in values:
        value = str(raw_value)
        kind, separator, name = value.partition(":")
        if not separator or kind not in {"DNS", "IP Address", "URI"} or not name:
            raise RuntimeError(f"unsupported required SAN: {value}")
        parsed.add((kind, name))
    return parsed


def validate_leaf(cert: Path, key: Path, ca: Path, identity: dict[str, object]) -> None:
    run("openssl", "verify", "-purpose", str(identity["purpose"]), "-CAfile", str(ca), str(cert))
    if public_key(cert, certificate=True) != public_key(key, certificate=False):
        raise RuntimeError("certificate/private-key mismatch")
    decoded = ssl._ssl._test_decode_cert(str(cert))  # type: ignore[attr-defined]
    actual_sans = {(str(kind), str(value)) for kind, value in decoded.get("subjectAltName", ())}
    expected_sans = required_sans(list(identity["required_san_text"]))
    missing_sans = sorted(expected_sans - actual_sans)
    if missing_sans:
        raise RuntimeError(f"certificate SAN validation failed: {missing_sans}")
    text = run("openssl", "x509", "-in", str(cert), "-noout", "-text").decode()
    required = [str(identity["eku"]), "Digital Signature"]
    missing = [value for value in required if value not in text]
    if missing:
        raise RuntimeError(f"certificate identity validation failed: {missing}")
    run("openssl", "x509", "-in", str(cert), "-checkend", "86400", "-noout")


def install_leaf(
    new_cert: Path,
    new_key: Path,
    cert: Path,
    key: Path,
    identity: dict[str, object],
) -> None:
    """Install a validated pair and restore both originals on any failure."""
    backup_cert = cert.with_suffix(cert.suffix + ".previous")
    backup_key = key.with_suffix(key.suffix + ".previous")
    shutil.copy2(cert, backup_cert)
    shutil.copy2(key, backup_key)
    try:
        os.replace(new_cert, cert)
        os.replace(new_key, key)
        subprocess.run(["systemctl", "reload-or-restart", str(identity["service"])], check=True)
        subprocess.run(list(identity["post_health_command"]), check=True)
    except Exception:
        os.replace(backup_cert, cert)
        os.replace(backup_key, key)
        subprocess.run(["systemctl", "reload-or-restart", str(identity["service"])], check=False)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", required=True, type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--fixture-no-install", action="store_true")
    args = parser.parse_args()
    identity = json.loads(args.identity.read_text())
    cert = Path(identity["certificate"])
    key = Path(identity["private_key"])
    ca = Path(identity["ca_certificate"])
    expiry, serial, fingerprint = certificate_metadata(cert)
    remaining = int((expiry - datetime.now(timezone.utc)).total_seconds() // 86400)
    print(f"expiry={expiry.isoformat()} days_remaining={remaining} serial={serial} fingerprint_sha256={fingerprint}")
    for threshold in (30, 14, 7, 3, 1):
        if remaining == threshold:
            print(f"alert=certificate_expiry threshold_days={threshold}")
    if remaining > int(identity.get("renew_before_days", 30)) and not args.force:
        print("renewal=noop")
        return 0

    parent = cert.parent
    with tempfile.TemporaryDirectory(prefix=".renew-", dir=parent) as temporary:
        work = Path(temporary)
        new_key, csr, new_cert = work / "leaf.key", work / "leaf.csr", work / "leaf.crt"
        os.chmod(work, 0o700)
        run("openssl", "genpkey", "-algorithm", "ED25519", "-out", str(new_key))
        os.chmod(new_key, 0o600)
        run(
            "openssl", "req", "-new", "-key", str(new_key), "-out", str(csr),
            "-subj", str(identity["subject"]), "-config", str(identity["openssl_config"]),
            "-reqexts", "leaf_extensions",
        )
        helper = Path(identity["enrollment_helper"])
        run(str(helper), "--csr", str(csr), "--certificate-out", str(new_cert), "--profile", str(identity["profile"]))
        os.chmod(new_cert, 0o600)
        validate_leaf(new_cert, new_key, ca, identity)
        if args.fixture_no_install:
            print("renewal=fixture_validated")
            return 0

        install_leaf(new_cert, new_key, cert, key, identity)
        print("renewal=installed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"alert=certificate_renewal_failure error_type={type(exc).__name__}")
        raise
