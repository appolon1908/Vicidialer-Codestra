"""No-network coverage of the canonical Middleware event delivery boundary."""
import json

import pytest

from codestra_vicidial import middleware_events as events
from codestra_vicidial.ami_gateway import GatewayConfigurationError


@pytest.mark.parametrize("endpoint", [
    "https://middleware.invalid:bad", "https://middleware.invalid:65536",
    "https://middleware.invalid:0", "https://middleware.invalid:",
    "https://[invalid]", "https://middle ware.invalid", "https://middle\nware.invalid",
    "https://middleware.invalid\\other", "https://user@middleware.invalid",
])
@pytest.mark.parametrize("cleaner,suffix", [
    (events.clean_https_origin, ""), (events.clean_https_url, "/token"),
])
def test_invalid_endpoint_is_rejected_as_configuration_error(endpoint, cleaner, suffix):
    with pytest.raises(GatewayConfigurationError):
        cleaner(endpoint + suffix, label="TEST_ENDPOINT")


@pytest.mark.parametrize("origin", [
    "https://middleware.invalid", "https://10.40.0.1:8443", "https://[fd00::1]:8443",
])
def test_valid_private_endpoint_forms_are_preserved(origin):
    assert events.clean_https_origin(origin + "/", label="origin") == origin
    assert events.clean_https_url(origin + "/token", label="token") == origin + "/token"


def test_default_environment_path_loads_protected_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(events.os, "environ", {})
    for name in ("MIDDLEWARE_CLIENT_SECRET_FILE", "MIDDLEWARE_WEBHOOK_SECRET_FILE"):
        path = tmp_path / name
        path.write_text("synthetic-test-material-only-" * 2)
        path.chmod(0o600)
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("MIDDLEWARE_BASE_URL", "https://middleware.invalid")
    monkeypatch.setenv("KEYCLOAK_TOKEN_URL", "https://identity.invalid/token")
    monkeypatch.setenv("MIDDLEWARE_CA_FILE", str(tmp_path / "ca.pem"))
    config, transport = events.delivery_config_from_environment()
    assert config.event_url == "https://middleware.invalid/api/v1/vicidial/events"
    assert transport.ca_file == tmp_path / "ca.pem"


def config():
    return events.EventDeliveryConfig(
        middleware_base_url="https://middleware.invalid",
        keycloak_token_url="https://identity.invalid/token",
        client_id=events.PRODUCER_CLIENT_ID,
        client_secret="synthetic-test-material-only-" * 2,
        webhook_secret=b"synthetic-test-material-only-" * 2,
    )


def envelope():
    return {
        "event_id": "event-test-001", "idempotency_key": "event-test-001",
        "event_type": "codestra.vicidial.call.lifecycle.ended.v1",
        "tenant_id": "tenant-test", "correlation_id": "correlation-test",
        "source": events.PRODUCER_CLIENT_ID,
    }


def result(http_status, **body):
    return events.HTTPResult(status=http_status, headers={}, body=json.dumps(body).encode())


class Transport:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def request(self, **request):
        self.requests.append(request)
        return next(self.responses)


def test_refresh_replays_same_signed_event_without_changing_idempotency():
    transport = Transport([
        result(200, token_type="Bearer", access_token="synthetic-one", expires_in=60),
        result(401),
        result(200, token_type="Bearer", access_token="synthetic-two", expires_in=60),
        result(200, event_id="event-test-001", tenant_id="tenant-test",
               status="duplicate", duplicate=True),
    ])
    provider = events.ClientCredentialsTokenProvider(config=config(), transport=transport, clock=lambda: 1000)
    publisher = events.MiddlewareEventPublisher(config=config(), transport=transport,
                                                token_provider=provider, clock=lambda: 1000)
    publisher.publish(envelope())
    assert len(transport.requests) == 4
    first, retry = transport.requests[1], transport.requests[3]
    assert first["body"] == retry["body"] == events.canonical_event_body(envelope())
    for header in ("Idempotency-Key", "X-Codestra-Event-Id", "X-Codestra-Signature"):
        assert first["headers"][header] == retry["headers"][header]
    assert first["headers"]["Authorization"] != retry["headers"]["Authorization"]


@pytest.mark.parametrize("changes", [
    {"tenant_id": "other-tenant"}, {"event_id": "other-event"},
    {"duplicate": 0}, {"status": "duplicate"},
])
def test_cross_identity_or_malformed_acceptance_is_not_success(changes):
    body = dict(event_id="event-test-001", tenant_id="tenant-test", status="accepted", duplicate=False)
    body.update(changes)
    with pytest.raises(events.PermanentDeliveryError):
        events.MiddlewareEventPublisher._validate_result(
            result(202, **body), event_id="event-test-001", tenant_id="tenant-test",
        )


@pytest.mark.parametrize("body", [
    b'{"event_id":"other","event_id":"event-test-001","tenant_id":"tenant-test","status":"accepted","duplicate":false}',
    b'{"event_id":"event-test-001","tenant_id":"tenant-test","status":"accepted","duplicate":false,"extra":NaN}',
    b'[]', b'null', b'{', b'\xff',
])
def test_ambiguous_acknowledgment_is_not_delivery_evidence(body):
    with pytest.raises(events.PermanentDeliveryError):
        events.MiddlewareEventPublisher._validate_result(
            events.HTTPResult(status=202, headers={}, body=body),
            event_id="event-test-001", tenant_id="tenant-test",
        )


@pytest.mark.parametrize("token", ["", " synthetic", "synthetic ", "two words", "line\nbreak", "\u00e9"])
def test_malformed_token_is_never_cached_or_sent_to_middleware(token):
    transport = Transport([
        result(200, token_type="Bearer", access_token=token, expires_in=60),
        result(200, token_type="Bearer", access_token="synthetic-valid", expires_in=60),
    ])
    provider = events.ClientCredentialsTokenProvider(config=config(), transport=transport, clock=lambda: 1000)
    publisher = events.MiddlewareEventPublisher(config=config(), transport=transport,
                                                token_provider=provider, clock=lambda: 1000)
    with pytest.raises(events.PermanentDeliveryError):
        publisher.publish(envelope())
    assert len(transport.requests) == 1
    assert transport.requests[0]["url"] == config().keycloak_token_url
    assert provider.token() == "synthetic-valid"
    assert len(transport.requests) == 2


def test_duplicate_token_members_are_rejected_without_response_disclosure():
    body = b'{"token_type":"Bearer","access_token":"synthetic-private","access_token":"synthetic-other","expires_in":60}'
    transport = Transport([events.HTTPResult(status=200, headers={}, body=body)])
    provider = events.ClientCredentialsTokenProvider(config=config(), transport=transport)
    with pytest.raises(events.PermanentDeliveryError) as failure:
        provider.token()
    assert "synthetic-private" not in str(failure.value)
    assert "synthetic-other" not in str(failure.value)
    assert failure.value.__suppress_context__ is True
