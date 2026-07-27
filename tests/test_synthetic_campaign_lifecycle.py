from uuid import uuid4

from campaign_provisioning import (
    CampaignDesignRequest,
    CampaignDesignService,
    LifecycleStore,
    SQLiteDesignRepository,
    run_synthetic_lifecycle,
)


class FakeAdapter:
    def __init__(self):
        self.campaigns = {}
        self.provision_keys = set()
        self.lead_keys = set()
        self.agents = {}
        self.leads = {}

    def provision_campaign(self, key, command):
        desired = command["campaign"]
        replay = key in self.provision_keys
        self.provision_keys.add(key)
        if not replay:
            self.campaigns[desired["actual_id"]] = {
                "campaign_id": desired["actual_id"],
                "campaign_name": desired["name"],
                "active": "N",
                "dial_method": desired["dial_method"],
                "lists": [
                    {**item, "active": "N"} for item in desired["lists"]
                ],
                "groups": [
                    {**item, "active": "N"} for item in desired["groups"]
                ],
                "scripts": [
                    {**item, "active": "N"} for item in desired["scripts"]
                ],
                "dispositions": list(desired["dispositions"]),
            }
        return {
            "status": "complete_disabled",
            "actual": self.campaigns[desired["actual_id"]],
            "resources": {
                "campaigns": 1, "lists": 1, "groups": 3,
                "scripts": 1, "dispositions": 2,
            },
            "idempotent_replay": replay,
        }

    def campaign_actual(self, campaign_id):
        return self.campaigns[campaign_id]

    def sync_agent(self, key, command):
        self.agents.setdefault(
            command["agent"]["user_id"], {**command["agent"], "active": "N"}
        )
        return {"status": "disabled", "actual": self.agents[command["agent"]["user_id"]]}

    def publish_lead(self, key, command):
        replay = key in self.lead_keys
        self.lead_keys.add(key)
        self.leads.setdefault(
            command["lead"]["external_id"], {**command["lead"], "lead_id": 1}
        )
        return {
            "status": "published",
            "actual": self.leads[command["lead"]["external_id"]],
            "idempotent_replay": replay,
        }

    def sync_transfer_policy(self, _key, command):
        return {
            "campaign_id": command["policy"]["campaign_id"],
            "allowed_targets": sorted(command["policy"]["allowed_targets"]),
        }

    def reconcile(self):
        return {
            "status": "complete",
            "inventory": {
                "campaigns": len(self.campaigns),
                "agents": len(self.agents),
                "leads": len(self.leads),
            },
        }

    def disable_campaign(self, _key, command):
        actual = self.campaigns[command["campaign"]["actual_id"]]
        actual["active"] = "N"
        return {"status": "disabled", "actual": actual}


def request():
    return CampaignDesignRequest(
        integration_uuid=str(uuid4()),
        odoo_campaign_id=910001,
        business_unit="TEST",
        purpose="E2E",
        direction="outbound",
        environment="staging",
        default_language="en",
        time_zone="America/Santo_Domingo",
        calling_hours="STAGING-NO-DIAL",
        consent_policy="SYNTHETIC-NO-CONTACT",
        dnc_policy="GLOBAL-DNC-V1",
        recording_policy="STAGING-NO-RECORDING",
        lead_source_policy="SYNTHETIC-ONLY",
        callback_policy="DISABLED",
        appointment_policy="DISABLED",
        disposition_family="TEST-E2E-V1",
        script_template="TEST-E2E-AGENT-V1",
        n8n_template="TEST-E2E-INACTIVE-V1",
        reporting_category="SYNTHETIC-E2E",
        owner_user_id=9101,
        supervisor_user_id=9102,
        activation_policy="MANUAL-NOT-AUTHORIZED",
        agent_roles=("AGENT",),
        transfer_roles=("CLOSERS", "INBOUND"),
    )


def test_complete_synthetic_lifecycle_is_disabled_idempotent_and_reversible(tmp_path):
    result = run_synthetic_lifecycle(
        CampaignDesignService(
            SQLiteDesignRepository(str(tmp_path / "design.sqlite3"))
        ),
        LifecycleStore(str(tmp_path / "lifecycle.sqlite3")),
        FakeAdapter(),
        request(),
        "synthetic-e2e-test-correlation",
    )

    assert result["status"] == "PASS"
    assert result["campaign"]["active"] is False
    assert result["list"] == {"list_id": 91000, "active": False}
    assert result["design_event_count"] == 1
    assert result["n8n"]["active"] is False
    assert result["agent"]["active"] is False
    assert result["lead"]["customer_number_used"] is False
    assert result["idempotent_campaign_replay"] is True
    assert result["idempotent_lead_replay"] is True
    assert result["reconciliation"] == "no_unexplained_drift"
    assert result["rollback"] == "disabled"
    assert result["production_dialing"] is False
    assert result["external_communication"] is False
    assert result["audit_counts"]["outbox_event"] == 1
    assert len(result["evidence_sha256"]) == 64
