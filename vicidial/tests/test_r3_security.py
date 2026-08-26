from pathlib import Path

import pytest

from codestra_vicidial.security import (
    RequestAuthenticator,
)
from codestra_vicidial.state import (
    StateStore,
)


IDENTITY = "codestra-middleware"
SOURCE = "127.0.0.1"
KEY = b"r3-security-test-key-32-bytes!!!"


def authenticator(
    state: StateStore,
    *,
    request_limit: int = 120,
) -> RequestAuthenticator:
    return RequestAuthenticator(
        identity=IDENTITY,
        key=KEY,
        authorized_sources=frozenset(
            {SOURCE}
        ),
        nonce_store=state.consume_nonce,
        rate_limit_store=(
            state.consume_rate_limit
        ),
        rate_limit_requests=request_limit,
        rate_limit_window_seconds=60,
    )


def verify(
    auth: RequestAuthenticator,
    nonce: str,
    *,
    now: int = 1_800_000_000,
) -> None:
    body = b'{"synthetic":true}'

    signature = auth.sign(
        now,
        nonce,
        body,
        scopes={"telephony:read"},
    )

    auth.verify(
        source=SOURCE,
        identity=IDENTITY,
        scopes={"telephony:read"},
        timestamp=now,
        nonce=nonce,
        body=body,
        signature=signature,
        required_scope="telephony:read",
        now=now,
    )


def test_nonce_replay_survives_authenticator_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"

    first_state = StateStore(database)
    first_auth = authenticator(first_state)

    verify(
        first_auth,
        "nonce-r3-replay-0001",
    )

    second_state = StateStore(database)
    second_auth = authenticator(
        second_state
    )

    with pytest.raises(
        PermissionError,
        match="request replay",
    ):
        verify(
            second_auth,
            "nonce-r3-replay-0001",
        )


def test_future_skew_nonce_is_retained_through_latest_acceptance() -> None:
    auth = RequestAuthenticator(
        identity=IDENTITY,
        key=KEY,
        authorized_sources=frozenset({SOURCE}),
        max_clock_skew_seconds=60,
    )
    current = 1_800_000_000
    timestamp = current + 60
    nonce = "nonce-future-skew-replay-0001"
    body = b'{"synthetic":true}'
    signature = auth.sign(timestamp, nonce, body, scopes={"telephony:read"})
    arguments = {
        "source": SOURCE, "identity": IDENTITY,
        "scopes": {"telephony:read"}, "timestamp": timestamp,
        "nonce": nonce, "body": body, "signature": signature,
        "required_scope": "telephony:read",
    }
    auth.verify(**arguments, now=current)
    with pytest.raises(PermissionError, match="request replay"):
        auth.verify(**arguments, now=current + 61)


def test_rate_limit_is_shared_in_state_store(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"

    first_state = StateStore(database)
    first_auth = authenticator(
        first_state,
        request_limit=2,
    )

    verify(
        first_auth,
        "nonce-r3-rate-0001",
    )

    verify(
        first_auth,
        "nonce-r3-rate-0002",
    )

    second_state = StateStore(database)
    second_auth = authenticator(
        second_state,
        request_limit=2,
    )

    with pytest.raises(
        PermissionError,
        match="request rate limit exceeded",
    ):
        verify(
            second_auth,
            "nonce-r3-rate-0003",
        )


def test_systemd_launch_uses_python_module() -> None:
    unit = (
        Path(__file__).parents[1]
        / "ops"
        / "codestra-vicidial-restricted-adapter.service"
    ).read_text()

    assert (
        "current/.venv/bin/python -m uvicorn "
        "codestra_vicidial.app:app"
    ) in unit

    assert (
        "current/.venv/bin/uvicorn "
        "codestra_vicidial.app:app"
    ) not in unit
