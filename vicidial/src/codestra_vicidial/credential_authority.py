"""Fail-closed client for the approved local secret authority."""
from __future__ import annotations

import json
import socket
from datetime import UTC, datetime
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

    def issue_ephemeral(
        self, *, username: str, extension: str, expires_at: str,
        idempotency_key: str,
    ) -> IssuedCredential:
        """Issue a short-lived PJSIP credential through the local authority.

        The authority, rather than this adapter, owns the credential material.
        This method deliberately uses a separate operation name so a
        long-lived VICIdial bootstrap credential cannot be substituted.
        """
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CredentialAuthorityUnavailable(
                "ephemeral credential expiry is invalid"
            ) from exc
        if expiry.tzinfo is None or expiry <= datetime.now(UTC):
            raise CredentialAuthorityUnavailable(
                "ephemeral credential expiry is not in the future"
            )
        if not self.path.is_socket():
            raise CredentialAuthorityUnavailable("approved secret authority unavailable")
        request = json.dumps({
            "operation": "webrtc-pjsip-ephemeral",
            "username": username,
            "extension": extension,
            "expires_at": expires_at,
            "idempotency_key": idempotency_key,
        }, separators=(",", ":")).encode() + b"\\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(3)
                client.connect(str(self.path))
                client.sendall(request)
                response = client.makefile("rb").readline(4097)
            document = json.loads(response)
            value, reference = document["credential"], document["reference"]
            if (
                not isinstance(value, str)
                or len(value) < 24
                or not isinstance(reference, str)
                or not reference
            ):
                raise ValueError
            return IssuedCredential(value=value, reference=reference)
        except Exception as exc:
            raise CredentialAuthorityUnavailable(
                "approved secret authority unavailable"
            ) from exc
