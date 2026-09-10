from __future__ import annotations

import pytest

from codestra_vicidial.models import CampaignCommand
from codestra_vicidial.repository import MemoryRepository
from codestra_vicidial.service import AdapterService
from codestra_vicidial.state import MutationCompletionUncertain, StateStore
from test_adapter import campaign_payload, service_for


@pytest.mark.parametrize("failed_table", ["requests", "resource_versions", "audit"])
def test_completion_failure_rolls_back_all_local_success_state(tmp_path, failed_table):
    service = service_for(MemoryRepository(), tmp_path)
    state = service.state
    # Each trigger injects an error at a different point in the final commit.
    state.db.execute(
        f"CREATE TRIGGER fail_completion BEFORE INSERT ON {failed_table} "
        "BEGIN SELECT RAISE(ABORT, 'synthetic completion failure'); END"
    )
    state.db.commit()
    with pytest.raises(MutationCompletionUncertain):
        service.provision_campaign("atomic-command", CampaignCommand.model_validate(campaign_payload()))
    reopened = StateStore(tmp_path / "state.sqlite3")
    assert reopened.resource_version("campaign:COD-TEST-OUT") == 0
    assert reopened.db.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
    assert reopened.db.execute("SELECT count(*) FROM audit WHERE outcome='success'").fetchone()[0] == 0
    assert reopened.db.execute("SELECT count(*) FROM resource_mutations").fetchone()[0] == 1
    # Telephony read-back succeeded; the retained claim prevents a blind repeat.
    assert service.repository.campaigns["CODT0001"]["active"] == "N"
    reopened.db.close()
    state.db.close()


def test_completed_command_is_replayable_after_restart(tmp_path):
    service = service_for(MemoryRepository(), tmp_path)
    command = CampaignCommand.model_validate(campaign_payload())
    result = service.provision_campaign("durable-command", command)
    service.state.db.close()
    state = StateStore(tmp_path / "state.sqlite3")
    restarted = AdapterService(service.repository, state, service.flags)
    replay = restarted.provision_campaign("durable-command", command)
    assert replay == result | {"idempotent_replay": True}
    assert state.resource_version("campaign:COD-TEST-OUT") == 1
    assert state.db.execute("SELECT count(*) FROM resource_mutations").fetchone()[0] == 0
    assert state.db.execute("SELECT count(*) FROM audit WHERE outcome='success'").fetchone()[0] == 1
    state.db.close()


def test_completion_requires_the_exact_claim(tmp_path):
    state = StateStore(tmp_path / "state.sqlite3")
    state.claim_resource_version("campaign:COD-TEST-OUT", 0, "owner-command")
    with pytest.raises(RuntimeError, match="claim was lost"):
        state.complete_command("campaign:COD-TEST-OUT", 0, "other-command", "campaign.disable",
                               "digest", {}, "correlation", "actor", "COD-TEST-OUT")
    assert state.resource_version("campaign:COD-TEST-OUT") == 0
    assert state.db.execute("SELECT count(*) FROM resource_mutations").fetchone()[0] == 1
    state.db.close()


def test_retry_racing_with_completion_recovers_recorded_response(tmp_path, monkeypatch):
    service = service_for(MemoryRepository(), tmp_path)
    command = CampaignCommand.model_validate(campaign_payload())
    other_state = StateStore(tmp_path / "state.sqlite3")
    other_worker = AdapterService(service.repository, other_state, service.flags)
    claim = service.state.claim_resource_version

    def complete_before_claim(*args):
        other_worker.provision_campaign("racing-command", command)
        claim(*args)

    monkeypatch.setattr(service.state, "claim_resource_version", complete_before_claim)
    result = service.provision_campaign("racing-command", command)
    assert result["idempotent_replay"] is True
    assert result["resource_version"] == 1
    assert service.state.db.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
    other_state.db.close()
    service.state.db.close()
