from __future__ import annotations

from dataclasses import dataclass

from .models import (
    AgentCommand, CallbackCommand, CampaignCommand, DisableAgentCommand,
    LeadCommand, TransferPolicyCommand,
)
from .repository import Repository
from .state import StateStore


@dataclass(frozen=True)
class FeatureFlags:
    writes: bool = False
    provisioning: bool = False
    agent_sync: bool = False
    lead_publication: bool = False
    callback_writes: bool = False
    transfer_policy_sync: bool = False
    production_dialing: bool = False


class AdapterService:
    def __init__(self, repository: Repository, state: StateStore, flags: FeatureFlags):
        self.repository = repository
        self.state = state
        self.flags = flags

    def _execute(self, operation: str, key: str, command, target: str, fn) -> dict:
        body = command.model_dump(mode="json")
        digest = self.state.digest(body)
        prior = self.state.prior(operation, key, digest)
        if prior is not None:
            return prior | {"idempotent_replay": True}
        context = command.context
        try:
            result = fn()
            self.state.record(operation, key, digest, result)
            self.state.audit(context.correlation_id, context.actor, operation, target,
                             "success", result)
            return result | {"idempotent_replay": False}
        except Exception as exc:
            self.state.audit(context.correlation_id, context.actor, operation, target,
                             "rejected", {"error_type": type(exc).__name__})
            raise

    def _require(self, enabled: bool, name: str) -> None:
        if not self.flags.writes:
            raise PermissionError("adapter kill switch is active")
        if not enabled:
            raise PermissionError(f"{name} is disabled")

    def validate_campaign(self, command: CampaignCommand) -> dict:
        actual = self.repository.campaign(command.campaign.actual_id)
        compatible = actual is None or (
            actual.get("campaign_name") == command.campaign.name
            and actual.get("active") == "N"
            and actual.get("dial_method") == command.campaign.dial_method
        )
        return {"valid": compatible, "collision": actual is not None,
                "actual_id": command.campaign.actual_id,
                "canonical_id": command.campaign.canonical_id}

    def provision_campaign(self, key: str, command: CampaignCommand) -> dict:
        self._require(self.flags.provisioning, "campaign provisioning")

        def apply() -> dict:
            resources = self.repository.provision_campaign_disabled(command.campaign)
            actual = self.repository.campaign(command.campaign.actual_id)
            if not actual or actual.get("active") != "N":
                raise RuntimeError("campaign read-back is not disabled")
            if any(item.get("active") != "N" for item in actual.get("lists", [])):
                raise RuntimeError("list read-back is not disabled")
            self.state.put_mapping(
                "campaign", command.campaign.canonical_id, command.campaign.actual_id,
                self.state.digest(command.campaign.model_dump(mode="json")))
            self.state.put_transfer_policy(
                command.campaign.actual_id, command.campaign.transfer_targets)
            return {"status": "complete_disabled", "resources": resources, "actual": actual}

        return self._execute("campaign.provision-disabled", key, command,
                             command.campaign.canonical_id, apply)

    def disable_campaign(self, key: str, command: CampaignCommand) -> dict:
        self._require(self.flags.provisioning, "campaign provisioning")

        def apply() -> dict:
            self.repository.disable_campaign(command.campaign.actual_id)
            actual = self.repository.campaign(command.campaign.actual_id)
            if not actual or actual.get("active") != "N":
                raise RuntimeError("campaign disable read-back failed")
            return {"status": "disabled", "actual": actual}

        return self._execute("campaign.disable", key, command,
                             command.campaign.canonical_id, apply)

    def sync_agent(self, key: str, command: AgentCommand) -> dict:
        self._require(self.flags.agent_sync, "agent sync")

        def apply() -> dict:
            actual = self.repository.sync_agent_disabled(command.agent)
            if actual.get("active") not in (False, "N"):
                raise RuntimeError("agent read-back is not disabled")
            return {"status": "disabled", "actual": actual}

        return self._execute("agent.sync", key, command, command.agent.user_id, apply)

    def disable_agent(self, key: str, command: DisableAgentCommand) -> dict:
        self._require(self.flags.agent_sync, "agent sync")

        def apply() -> dict:
            actual = self.repository.disable_agent(command.user_id)
            if actual.get("active") not in (False, "N"):
                raise RuntimeError("agent disable read-back failed")
            return {"status": "disabled", "actual": actual}

        return self._execute("agent.disable", key, command, command.user_id, apply)

    def publish_lead(self, key: str, command: LeadCommand) -> dict:
        self._require(self.flags.lead_publication, "lead publication")

        def apply() -> dict:
            actual = self.repository.publish_lead(command.lead)
            if not actual.get("lead_id"):
                raise RuntimeError("lead read-back failed")
            self.state.put_mapping("lead", command.lead.external_id,
                                   str(actual["lead_id"]), self.state.digest(actual))
            return {"status": "published", "actual": actual}

        return self._execute("lead.publish", key, command, command.lead.external_id, apply)

    def create_callback(self, key: str, command: CallbackCommand) -> dict:
        self._require(self.flags.callback_writes, "callback writes")

        def apply() -> dict:
            actual = self.repository.create_callback(command.callback)
            if not actual.get("callback_id"):
                raise RuntimeError("callback read-back failed")
            self.state.put_mapping("callback", command.callback.external_id,
                                   str(actual["callback_id"]), self.state.digest(actual))
            return {"status": "created", "actual": actual}

        return self._execute("callback.create", key, command,
                             command.callback.external_id, apply)

    def sync_transfer_policy(self, key: str, command: TransferPolicyCommand) -> dict:
        self._require(self.flags.transfer_policy_sync, "transfer policy sync")

        def apply() -> dict:
            actual = self.repository.campaign(command.policy.campaign_id)
            if not actual:
                raise ValueError("campaign missing")
            known_groups = {
                group["code"] for group in getattr(self.repository, "groups", {}).values()
                if group["kind"] in {"inbound", "closer"}
            }
            if known_groups and set(command.policy.allowed_targets) - known_groups:
                raise ValueError("transfer target missing")
            self.state.put_transfer_policy(
                command.policy.campaign_id, command.policy.allowed_targets)
            return {"campaign_id": command.policy.campaign_id,
                    "allowed_targets": sorted(command.policy.allowed_targets)}

        return self._execute("transfer-policy.sync", key, command,
                             command.policy.campaign_id, apply)

    def reconciliation(self) -> dict:
        return {"status": "complete", "inventory": self.repository.inventory()}
