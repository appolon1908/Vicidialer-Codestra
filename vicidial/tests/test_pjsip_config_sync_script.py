"""Unit tests for scripts/pjsip_config_sync.py's core logic: signing,
apply-and-reload, and ack. These validate the script's own code paths with
requests/subprocess mocked out -- there is no live Server B to actually
write files on or reload in this sandbox.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import pjsip_config_sync as sync_mod  # noqa: E402
from codestra_vicidial.security import RequestAuthenticator  # noqa: E402


def make_config(tmp_path):
    return {
        "adapter_base_url": "https://vicidial-adapter.internal.codestra.co",
        "identity": "server-b-pjsip-sync",
        "hmac_key": b"synthetic-test-key",
        "campaign_id": "TEST_SYN",
        "config_dir": str(tmp_path / "pjsip-webrtc"),
        "reload_command": "true",
        "interval_seconds": 15,
        "kill_switch": str(tmp_path / "kill-switch-not-present"),
        "request_timeout_seconds": 5,
    }


def test_sync_enabled_requires_hmac_key(tmp_path):
    config = make_config(tmp_path)
    config["hmac_key"] = b""
    assert sync_mod.sync_enabled(config) is False


def test_sync_enabled_respects_kill_switch_file(tmp_path):
    config = make_config(tmp_path)
    Path(config["kill_switch"]).write_text("stop")
    assert sync_mod.sync_enabled(config) is False


def test_sync_enabled_true_when_key_present_and_no_kill_switch(tmp_path):
    config = make_config(tmp_path)
    assert sync_mod.sync_enabled(config) is True


def test_apply_config_writes_file_and_invokes_reload(tmp_path):
    config = make_config(tmp_path)
    item = {
        "user_id": "COD1001", "extension": "6300", "active": True,
        "content": "endpoint config content\n", "content_hash": "abc123",
    }
    with patch.object(sync_mod.subprocess, "run") as mock_run:
        sync_mod.apply_config(config, item)
    written = Path(config["config_dir"]) / "COD1001.conf"
    assert written.read_text() == "endpoint config content\n"
    mock_run.assert_called_once()
    assert mock_run.call_args.args[0] == config["reload_command"]


def test_signed_headers_produces_a_signature_the_server_verifies():
    """The client (script) and server (app.py's authenticator) must derive
    the identical signature for the same inputs -- this proves the script's
    signed_headers() is wire-compatible with RequestAuthenticator.verify(),
    not just internally self-consistent."""
    key = b"shared-test-secret"
    server_auth = RequestAuthenticator("server-b-pjsip-sync", key, frozenset({"127.0.0.1"}))
    client_auth = RequestAuthenticator("server-b-pjsip-sync", key, frozenset())

    body = b'{"user_id": "COD1001", "content_hash": "abc123"}'
    headers = sync_mod.signed_headers(
        client_auth, method="POST", path="/v1/webrtc/pjsip-config/ack",
        scope="telephony:webrtc-provision", body=body, idempotency_key="ack-key-1",
    )

    # Should not raise: the server-side authenticator accepts the
    # client-constructed signature for the matching request.
    server_auth.verify(
        source="127.0.0.1",
        identity=headers["X-Service-Identity"],
        scopes={headers["X-Service-Scopes"]},
        timestamp=int(headers["X-Request-Timestamp"]),
        nonce=headers["X-Request-Nonce"],
        body=body,
        signature=headers["X-Request-Signature"],
        required_scope="telephony:webrtc-provision",
        method="POST",
        path="/v1/webrtc/pjsip-config/ack",
        idempotency_key="ack-key-1",
        signature_version=headers["X-Signature-Version"],
    )


def test_run_once_applies_and_acks_each_pending_item(tmp_path):
    config = make_config(tmp_path)
    auth = RequestAuthenticator(config["identity"], config["hmac_key"], frozenset())
    pending_item = {
        "user_id": "COD1001", "extension": "6300", "active": True,
        "content": "endpoint config\n", "content_hash": "hash-1",
    }

    fake_get_response = MagicMock()
    fake_get_response.json.return_value = {"pending": [pending_item]}
    fake_get_response.raise_for_status.return_value = None

    fake_post_response = MagicMock()
    fake_post_response.status_code = 200
    fake_post_response.raise_for_status.return_value = None

    with patch.object(sync_mod.requests, "get", return_value=fake_get_response) as mock_get, \
         patch.object(sync_mod.requests, "post", return_value=fake_post_response) as mock_post, \
         patch.object(sync_mod.subprocess, "run") as mock_run:
        applied = sync_mod.run_once(config, auth)

    assert applied == 1
    mock_get.assert_called_once()
    mock_post.assert_called_once()
    mock_run.assert_called_once()
    written = Path(config["config_dir"]) / "COD1001.conf"
    assert written.exists()


def test_run_once_is_noop_when_kill_switch_present(tmp_path):
    config = make_config(tmp_path)
    Path(config["kill_switch"]).write_text("stop")
    auth = RequestAuthenticator(config["identity"], config["hmac_key"], frozenset())
    with patch.object(sync_mod.requests, "get") as mock_get:
        applied = sync_mod.run_once(config, auth)
    assert applied == 0
    mock_get.assert_not_called()
