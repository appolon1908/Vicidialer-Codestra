"""Fail-closed client for the approved local secret authority."""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path


class CredentialAuthorityUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class IssuedCredential:
    value: str
    reference: str


class UnixCredentialAuthority:
    def __init__(self, path: Path):
        self.path = path

    def issue(self, *, username: str, extension: str, idempotency_key: str) -> IssuedCredential:
        if not self.path.is_socket():
            raise CredentialAuthorityUnavailable("approved secret authority unavailable")
        request = json.dumps({
            "operation": "vicidial-bootstrap",
            "username": username,
            "extension": extension,
            "idempotency_key": idempotency_key,
        }, separators=(",", ":")).encode() + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(3)
                client.connect(str(self.path))
                client.sendall(request)
                response = client.makefile("rb").readline(4097)
            document = json.loads(response)
            value, reference = document["credential"], document["reference"]
            if not isinstance(value, str) or len(value) < 24 or not isinstance(reference, str):
                raise ValueError
            return IssuedCredential(value=value, reference=reference)
        except Exception as exc:
            raise CredentialAuthorityUnavailable("approved secret authority unavailable") from exc
