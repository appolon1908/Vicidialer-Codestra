"""Mission 4 (WebRTC device-limit enforcement): generate the PJSIP
AOR/endpoint/auth configuration that enforces one active WebRTC contact
per agent endpoint at the Asterisk level, independent of this adapter's
own StateStore-level session tracking.

This module produces config *content* only. It does not connect to, or
assume anything about, a live Asterisk instance's realtime backend
(static pjsip.conf includes vs. res_config_odbc/pgsql realtime rows) --
that delivery mechanism has never been verified against the actual
Server B deployment (see repository.py's bind_phone docstring, and
Vicidialer-Codestra#53). Generating correct config content is the part
of Mission 4 this codebase can do without live access; applying it is a
separate, explicitly flagged follow-up.

The generated stanza's identity is deterministic and idempotent: the
same (user_id, extension, credential_reference) always produces the
same content, so re-provisioning after a revoke is naturally consistent
rather than accumulating drift.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PjsipEndpointConfig:
    """One agent's desired PJSIP AOR/endpoint/auth state.

    ``active`` distinguishes a live registration slot (max_contacts=1,
    remove_existing=yes) from a revoked/torn-down one (endpoint disabled,
    no contacts permitted) -- both are real, renderable config, not an
    absence of config, so a revoked agent's stale endpoint definition
    cannot be silently reused by anyone who still holds the old secret.
    """

    user_id: str
    extension: str
    credential_reference: str
    active: bool

    def aor_name(self) -> str:
        return f"webrtc-{self.extension}"

    def endpoint_name(self) -> str:
        return f"webrtc-{self.extension}"

    def auth_name(self) -> str:
        return f"webrtc-auth-{self.extension}"

    def render(self) -> str:
        """Render the PJSIP config stanza for this endpoint.

        When ``active`` is False, ``max_contacts`` is set to 0 rather
        than the section being omitted -- an omitted section could be
        read by some tooling as "unmanaged" (falls back to a template
        default) instead of "explicitly disabled". max_contacts=0 always
        rejects every registration attempt for this AOR.
        """
        max_contacts = 1 if self.active else 0
        endpoint_disabled_line = "" if self.active else "disallow=all\n"
        return (
            f"[{self.aor_name()}]\n"
            f"type=aor\n"
            f"max_contacts={max_contacts}\n"
            f"remove_existing=yes\n"
            f"qualify_frequency=30\n"
            f"\n"
            f"[{self.auth_name()}]\n"
            f"type=auth\n"
            f"auth_type=userpass\n"
            f"username={self.user_id}\n"
            f"password={self.credential_reference}\n"
            f"\n"
            f"[{self.endpoint_name()}]\n"
            f"type=endpoint\n"
            f"transport=transport-wss\n"
            f"aors={self.aor_name()}\n"
            f"auth={self.auth_name()}\n"
            f"webrtc=yes\n"
            f"dtls_auto_generate_cert=yes\n"
            f"{endpoint_disabled_line}"
        )

    def content_hash(self) -> str:
        import hashlib

        return hashlib.sha256(self.render().encode("utf-8")).hexdigest()


def revoked(user_id: str, extension: str) -> PjsipEndpointConfig:
    """A teardown artifact: same identity, credential_reference cleared,
    max_contacts=0, endpoint disallowed. Distinct from "never provisioned"
    -- this is an explicit, renderable revocation record."""
    return PjsipEndpointConfig(
        user_id=user_id, extension=extension,
        credential_reference="", active=False,
    )
