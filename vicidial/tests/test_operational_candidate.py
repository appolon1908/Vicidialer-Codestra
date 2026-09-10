"""Regression coverage for the read-only production profile's systemd gate."""
from copy import deepcopy

import pytest

from test_r3_phase2 import SHA, _operational_module, verified_release


def candidate():
    return {
        "status": "ready", "deployment_profile": "production-candidate",
        "backend": "mysql", "release_sha": SHA, "release_verified": True,
        "writes_enabled": False, "internal_test_calling": False,
        "production_dialing": False, "customer_traffic_authorized": False,
        "reconciliation_policy_verified": False,
        "checks": {name: True for name in (
            "release", "hmac", "source_policy", "state_store",
            "repository", "kill_switches",
        )},
    }


def validate(ready, profile="production-candidate"):
    _operational_module().validate_documents(
        health={"status": "live"}, ready=ready, version=verified_release(),
        expected_sha=SHA, deployment_profile=profile,
    )


def test_readonly_production_candidate_is_accepted_without_mutation():
    ready = candidate()
    before = deepcopy(ready)
    validate(ready)
    assert ready == before


@pytest.mark.parametrize("environment", ["staging-read-only", "development", None, True])
def test_candidate_requires_the_production_build_environment(environment):
    version = verified_release()
    version["environment"] = environment
    with pytest.raises(ValueError, match="release environment mismatch"):
        _operational_module().validate_documents(
            health={"status": "live"}, ready=candidate(), version=version,
            expected_sha=SHA, deployment_profile="production-candidate",
        )


@pytest.mark.parametrize("field,value", [
    ("status", "not_ready"), ("deployment_profile", None),
    ("deployment_profile", "production-external-one-call"),
    ("backend", "memory"), ("release_sha", "b" * 40),
    ("release_verified", False), ("checks", []), ("checks", {}),
])
def test_candidate_cannot_mask_wrong_or_unhealthy_runtime(field, value):
    ready = candidate()
    ready[field] = value
    with pytest.raises(ValueError):
        validate(ready)


@pytest.mark.parametrize("field", [
    "writes_enabled", "internal_test_calling", "production_dialing",
    "customer_traffic_authorized", "reconciliation_policy_verified",
])
@pytest.mark.parametrize("value", [True, "false", 0, None])
def test_candidate_effects_require_explicit_false(field, value):
    ready = candidate()
    ready[field] = value
    with pytest.raises(ValueError):
        validate(ready)


@pytest.mark.parametrize("check", list(candidate()["checks"]))
@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_candidate_requires_every_dependency_check(check, value):
    ready = candidate()
    ready["checks"][check] = value
    with pytest.raises(ValueError):
        validate(ready)


def test_new_failing_dependency_cannot_be_ignored():
    ready = candidate()
    ready["checks"]["additional_dependency"] = False
    with pytest.raises(ValueError):
        validate(ready)


def test_inactive_one_call_policy_is_not_accepted_as_candidate_or_call_ready():
    ready = candidate()
    ready.update(status="not_ready", deployment_profile="production-external-one-call",
                 backend="memory")
    ready["checks"]["external_call_policy"] = False
    for profile in ("production-candidate", "production-external-one-call"):
        with pytest.raises(ValueError):
            validate(ready, profile)


def test_legacy_document_must_report_its_actual_profile():
    ready = {"status": "ready", "writes_enabled": False, "release_verified": True}
    with pytest.raises(ValueError, match="deployment profile mismatch"):
        validate(ready, "staging-read-only")
