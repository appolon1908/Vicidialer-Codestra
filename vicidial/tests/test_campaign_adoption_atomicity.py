from __future__ import annotations

import hashlib
import json

import pytest

from codestra_vicidial.models import CampaignSpec
from codestra_vicidial.repository import CollisionError, MySQLRepository


def _spec(current: dict) -> CampaignSpec:
    proof = hashlib.sha256(
        json.dumps(current, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return CampaignSpec.model_validate({
        "canonical_id": "COD-SYN-OUT",
        "actual_id": "CODSYN01",
        "name": "Codestra synthetic campaign",
        "business_unit": "COD",
        "direction": "outbound",
        "active": False,
        "dial_method": "MANUAL",
        "call_time": "9am-9pm",
        "recording_policy": "ONDEMAND",
        "callback_enabled": False,
        "lists": [],
        "groups": [],
        "scripts": [],
        "dispositions": [],
        "transfer_targets": [],
        "reporting_mapping": "synthetic reporting",
        "recording_policy_reference": "synthetic recording policy",
        "adopt_existing_sha256": proof,
    })


class CommitProbe:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class AdoptionProbe(MySQLRepository):
    def __init__(self, current: dict, affected: int):
        self.current = dict(current)
        self.affected = affected
        self.executions: list[tuple[str, tuple]] = []
        self.db = CommitProbe()

    def validate_campaign(self, spec):
        return {"valid": True, "collision": True}

    def _one(self, sql: str, args: tuple = ()):
        if "FROM vicidial_campaigns WHERE campaign_id=%s" in sql:
            return dict(self.current)
        return None

    def _execute(self, sql: str, args: tuple = ()) -> int:
        self.executions.append((sql, args))
        return self.affected


def _current() -> dict:
    return {
        "campaign_id": "CODSYN01",
        "campaign_name": "Legacy synthetic campaign",
        "active": "N",
        "dial_method": "MANUAL",
        "local_call_time": "9am-9pm",
        "campaign_recording": "ONDEMAND",
        "scheduled_callbacks": "N",
        "campaign_description": "LEGACY",
        "hopper_level": 7,
        "auto_dial_level": "3",
    }


def test_campaign_adoption_predicate_binds_both_dialing_level_fields():
    current = _current()
    repository = AdoptionProbe(current, affected=1)
    result = repository.provision_campaign_disabled(_spec(current))
    assert result[0] == {
        "type": "campaign", "id": "CODSYN01", "result": "updated"
    }
    sql, args = repository.executions[0]
    assert "AND hopper_level<=>%s AND auto_dial_level<=>%s" in sql
    assert args[-2:] == (7, "3")
    assert repository.db.commits == 1


def test_campaign_adoption_rejects_concurrent_dialing_level_change():
    current = _current()
    repository = AdoptionProbe(current, affected=0)
    with pytest.raises(CollisionError, match="campaign adoption proof became stale"):
        repository.provision_campaign_disabled(_spec(current))
    assert len(repository.executions) == 1
    assert repository.db.commits == 0


def test_campaign_validation_and_mutation_hash_the_same_dialing_snapshot():
    validation = __import__("inspect").getsource(MySQLRepository.validate_campaign)
    mutation = __import__("inspect").getsource(
        MySQLRepository.provision_campaign_disabled
    )
    for source in (validation, mutation):
        assert "hopper_level,auto_dial_level" in source
        assert '"hopper_level": 0' in source
        assert '"auto_dial_level": "0"' in source
    assert "current.get(\"hopper_level\")" in mutation
    assert "current.get(\"auto_dial_level\")" in mutation
