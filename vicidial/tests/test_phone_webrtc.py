from datetime import UTC, datetime
from pathlib import Path

import pytest

from codestra_vicidial.models import RequestContext
from codestra_vicidial.phone_webrtc import (
    SYNTHETIC_CAMPAIGN,
    SYNTHETIC_DESTINATION,
    SYNTHETIC_EMPLOYEE,
    SYNTHETIC_EXTENSION,
    SYNTHETIC_USER,
    ExtensionAdoptionCommand,
    ExtensionReservationCommand,
    PhoneAdapterService,
    WebRTCCredentialCommand,
    WebRTCRevokeCommand,
    WebRTCSessionAlreadyActive,
    _safe_phone,
)
from codestra_vicidial.repository import CollisionError, MemoryRepository
from codestra_vicidial.service import FeatureFlags
from codestra_vicidial.state import StateStore


SUBJECT = "12345678-1234-4234-8234-123456789012"


class Authority:
    def __init__(self):
        self.calls = 0

    def issue_ephemeral(self, *, username, extension, expires_at, idempotency_key):
        self.calls += 1
        return type(
            "Issued",
            (),
            {
                "value": f"credential-{self.calls:02d}-" + "x" * 24,
                "reference": f"bao://webrtc/{self.calls}",
            },
        )()


def _context() -> RequestContext:
    return RequestContext(
        correlation_id="corr-phone-001",
        actor="codestra-middleware",
        tenant_id="TEST-TENANT",
        business_unit="TEST",
        campaign_id=SYNTHETIC_CAMPAIGN,
        supervisor_subject=SUBJECT,
        expected_version=0,
        reason="synthetic phone lifecycle test",
        requested_at=datetime.now(UTC),
    )


def _repo() -> MemoryRepository:
    repository = MemoryRepository()
    repository.phones[SYNTHETIC_EXTENSION] = {
        "extension": SYNTHETIC_EXTENSION,
        "dialplan_number": SYNTHETIC_EXTENSION,
        "login": SYNTHETIC_EXTENSION,
        "status": "ACTIVE",
        "active": "N",
        "phone_type": "SIP",
        "fullname": "Synthetic echo",
        "company": "TEST",
        "protocol": "SIP",
    }
    repository.agents[SYNTHETIC_USER] = {
        "user_id": SYNTHETIC_USER,
        "full_name": "Synthetic agent",
        "user_group": "WEBTEST_SYN",
        "active": "N",
        "campaigns": [SYNTHETIC_CAMPAIGN],
        "inbound_groups": [],
    }
    return repository


def _service(tmp_path: Path) -> tuple[PhoneAdapterService, Authority]:
    authority = Authority()
    return (
        PhoneAdapterService(
            _repo(),
            StateStore(tmp_path / "state.sqlite3"),
            FeatureFlags(
                writes=True,
                agent_sync=True,
                extension_reservation=True,
                webrtc_session_issuance=True,
            ),
            authority,
        ),
        authority,
    )


def test_reserve_queries_vicidial_pool_and_never_allocates_static_6101(tmp_path):
    service, _ = _service(tmp_path)
    command = ExtensionReservationCommand(
        context=_context(),
        employee_id="COD.2026.00017",
        user_id="cod00017",
        keycloak_subject=SUBJECT,
        allocation_reference="MW-ALLOC-000017",
        pool_start=6100,
        pool_end=6112,
    )

    result = service.reserve_extension("reserve-0000000001", command)

    assert result["extension"] == "6110"
    assert result["state"] == "RESERVED"
    assert service.ledger.reservation_for_extension("6101") is None


def test_static_adoption_requires_exact_proof_and_keeps_extension(tmp_path):
    service, _ = _service(tmp_path)
    proof = StateStore.digest(_safe_phone(service.repository.phone(SYNTHETIC_EXTENSION)))
    command = ExtensionAdoptionCommand(
        context=_context(),
        employee_id=SYNTHETIC_EMPLOYEE,
        user_id=SYNTHETIC_USER,
        keycloak_subject=SUBJECT,
        extension=SYNTHETIC_EXTENSION,
        existing_state_sha256=proof,
        allocation_reference="STATIC-TEST_SYN-6101",
    )

    result = service.adopt_extension("adopt-0000000001", command)

    assert result["extension"] == SYNTHETIC_EXTENSION
    assert result["mode"] == "ADOPTED"
    assert result["replayed"] is False
    assert service.ledger.reservation_for_extension(SYNTHETIC_EXTENSION)["mode"] == "ADOPTED"


def test_webrtc_issuance_is_single_device_and_ticket_is_single_use(tmp_path):
    service, authority = _service(tmp_path)
    proof = StateStore.digest(_safe_phone(service.repository.phone(SYNTHETIC_EXTENSION)))
    adopt = ExtensionAdoptionCommand(
        context=_context(),
        employee_id=SYNTHETIC_EMPLOYEE,
        user_id=SYNTHETIC_USER,
        keycloak_subject=SUBJECT,
        extension=SYNTHETIC_EXTENSION,
        existing_state_sha256=proof,
        allocation_reference="STATIC-TEST_SYN-6101",
    )
    service.adopt_extension("adopt-0000000002", adopt)

    def command(binding: str) -> WebRTCCredentialCommand:
        return WebRTCCredentialCommand(
            context=_context(),
            employee_id=SYNTHETIC_EMPLOYEE,
            user_id=SYNTHETIC_USER,
            keycloak_subject=SUBJECT,
            extension=SYNTHETIC_EXTENSION,
            browser_session_binding=binding,
            allocation_reference="STATIC-TEST_SYN-6101",
            permitted_call_scope=[SYNTHETIC_DESTINATION],
        )

    first = service.issue_webrtc(
        "webrtc-0000000001",
        command("a" * 64),
    )
    assert first["status"] == "issued"
    assert first["environment"] == "STAGING"
    assert first["permitted_call_scope"] == [SYNTHETIC_DESTINATION]
    assert authority.calls == 1
    assert service.ledger.consume_ticket(first["registration_ticket"]) is not None
    assert service.ledger.consume_ticket(first["registration_ticket"]) is None

    with pytest.raises(WebRTCSessionAlreadyActive):
        service.issue_webrtc("webrtc-0000000002", command("b" * 64))


def test_revoke_invalidates_issued_session(tmp_path):
    service, _ = _service(tmp_path)
    proof = StateStore.digest(_safe_phone(service.repository.phone(SYNTHETIC_EXTENSION)))
    adopt = ExtensionAdoptionCommand(
        context=_context(),
        employee_id=SYNTHETIC_EMPLOYEE,
        user_id=SYNTHETIC_USER,
        keycloak_subject=SUBJECT,
        extension=SYNTHETIC_EXTENSION,
        existing_state_sha256=proof,
        allocation_reference="STATIC-TEST_SYN-6101",
    )
    service.adopt_extension("adopt-0000000003", adopt)
    issued = service.issue_webrtc(
        "webrtc-0000000003",
        WebRTCCredentialCommand(
            context=_context(),
            employee_id=SYNTHETIC_EMPLOYEE,
            user_id=SYNTHETIC_USER,
            keycloak_subject=SUBJECT,
            extension=SYNTHETIC_EXTENSION,
            browser_session_binding="c" * 64,
            allocation_reference="STATIC-TEST_SYN-6101",
        ),
    )

    result = service.revoke_webrtc(
        WebRTCRevokeCommand(
            context=_context(),
            employee_id=SYNTHETIC_EMPLOYEE,
            user_id=SYNTHETIC_USER,
            keycloak_subject=SUBJECT,
            extension=SYNTHETIC_EXTENSION,
            reason="manual synthetic suspension",
        )
    )

    assert result["sessions_revoked"] == 1
    assert service.ledger.consume_ticket(issued["registration_ticket"]) is None
