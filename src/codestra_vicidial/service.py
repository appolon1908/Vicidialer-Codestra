from __future__ import annotations

from dataclasses import dataclass
import uuid

from .models import (
    AgentCommand, CallbackCommand, CampaignCommand, DisableAgentCommand,
    LeadCommand, TransferPolicyCommand,
)
from .repository import CollisionError, Repository, normalize_allowed_campaigns
from .state import StateStore


class ReadbackMismatch(RuntimeError):
    def __init__(self, results: list[dict]):
        super().__init__("provisioning read-back mismatch; reconciliation required")
        self.results = results


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

    @staticmethod
    def _comparison(resource_type: str, resource_id: str | int, desired: dict,
                    actual: dict | None, fields: tuple[str, ...],
                    extra_mismatches: list[str] | None = None) -> dict:
        mismatches = list(extra_mismatches or [])
        if actual is None:
            mismatches.append("missing")
        else:
            mismatches.extend(
                field for field in fields if str(actual.get(field)) != str(desired.get(field))
            )
        return {
            "type": resource_type, "id": resource_id,
            "match": not mismatches, "mismatches": sorted(set(mismatches)),
            "desired_hash": StateStore.digest(desired),
            "actual_hash": StateStore.digest(actual) if actual is not None else None,
        }

    def _campaign_readback(self, command: CampaignCommand) -> tuple[dict, list[dict]]:
        spec = command.campaign
        actual = self.repository.readback_campaign(spec)
        results: list[dict] = []
        campaign_desired = {
            "campaign_id": spec.actual_id, "campaign_name": spec.name, "active": "N",
            "dial_method": spec.dial_method, "local_call_time": spec.call_time,
            "campaign_recording": spec.recording_policy,
            "scheduled_callbacks": "Y" if spec.callback_enabled else "N",
        }
        results.append(self._comparison(
            "campaign", spec.actual_id, campaign_desired, actual.get("campaign"),
            tuple(campaign_desired)))
        list_rows = actual.get("lists", [])
        for index, item in enumerate(spec.lists):
            row = list_rows[index] if index < len(list_rows) else None
            desired = {"list_id": item.list_id, "list_name": item.code,
                       "campaign_id": spec.actual_id, "active": "N"}
            results.append(self._comparison(
                "list", item.list_id, desired, row, tuple(desired)))
        group_rows = actual.get("groups", [])
        for index, item in enumerate(spec.groups):
            row = group_rows[index] if index < len(group_rows) else None
            desired = {"code": item.code, "name": item.name}
            fields = ["code", "name"]
            extra = []
            if item.kind == "user":
                if row is not None:
                    try:
                        campaigns = normalize_allowed_campaigns(row.get("allowed_campaigns"))
                        if spec.actual_id not in campaigns:
                            extra.append("allowed_campaigns")
                    except CollisionError:
                        extra.append("allowed_campaigns")
            else:
                desired["active"] = "N"
                fields.append("active")
            results.append(self._comparison(
                item.kind + "_group", item.code, desired, row, tuple(fields), extra))
        script_rows = actual.get("scripts", [])
        for index, item in enumerate(spec.scripts):
            row = script_rows[index] if index < len(script_rows) else None
            desired = {"script_id": item.code, "script_name": item.name,
                       "script_text": item.text, "active": "N"}
            extra = []
            if row is not None:
                actual_version = row.get("version")
                if actual_version is None:
                    marker = f":V{item.version}"
                    if not str(row.get("script_comments", "")).endswith(marker):
                        extra.append("version")
                elif int(actual_version) != item.version:
                    extra.append("version")
            results.append(self._comparison(
                "script", item.code, desired, row, tuple(desired), extra))
        disposition_rows = actual.get("dispositions", [])
        for index, item in enumerate(spec.dispositions):
            row = disposition_rows[index] if index < len(disposition_rows) else None
            desired = {
                "campaign_id": spec.actual_id, "status": item.code,
                "status_name": item.name, "selectable": "Y" if item.selectable else "N",
                "human_answered": "Y" if item.human_answered else "N",
                "category": item.category,
            }
            results.append(self._comparison(
                "disposition", item.code, desired, row, tuple(desired)))
        policy = self.state.campaign_policy(spec.actual_id)
        policy_desired = {
            "transfer_targets": sorted(spec.transfer_targets),
            "recording_policy_reference": spec.recording_policy_reference,
            "reporting_mapping": spec.reporting_mapping,
            "callback_enabled": spec.callback_enabled,
        }
        results.append(self._comparison(
            "transfer_policy", spec.actual_id,
            {"transfer_targets": policy_desired["transfer_targets"]}, policy,
            ("transfer_targets",)))
        results.append(self._comparison(
            "recording_policy_reference", spec.actual_id,
            {"recording_policy_reference": spec.recording_policy_reference}, policy,
            ("recording_policy_reference",)))
        results.append(self._comparison(
            "callback_policy", spec.actual_id,
            {"callback_enabled": spec.callback_enabled}, policy, ("callback_enabled",)))
        results.append(self._comparison(
            "reporting_mapping", spec.actual_id,
            {"reporting_mapping": spec.reporting_mapping}, policy, ("reporting_mapping",)))
        return actual, results

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
            snapshot_id = str(uuid.uuid4())
            before = self.repository.readback_campaign(command.campaign)
            self.state.snapshot(
                snapshot_id, "campaign.provision-disabled",
                command.campaign.canonical_id,
                {
                    "resources": before,
                    "campaign_policy": self.state.campaign_policy(
                        command.campaign.actual_id),
                    "desired_hash": self.state.digest(
                        command.campaign.model_dump(mode="json")),
                },
            )
            resources = self.repository.provision_campaign_disabled(command.campaign)
            self.state.put_mapping(
                "campaign", command.campaign.canonical_id, command.campaign.actual_id,
                self.state.digest(command.campaign.model_dump(mode="json")))
            self.state.put_transfer_policy(
                command.campaign.actual_id, command.campaign.transfer_targets)
            self.state.put_campaign_policy(
                command.campaign.actual_id, command.campaign.transfer_targets,
                command.campaign.recording_policy_reference,
                command.campaign.reporting_mapping, command.campaign.callback_enabled)
            actual, readback = self._campaign_readback(command)
            if not all(item["match"] for item in readback):
                self.repository.disable_campaign_resources(command.campaign)
                redacted = {
                    "snapshot_id": snapshot_id,
                    "resources": [
                        {"type": item["type"], "id": item["id"],
                         "match": item["match"], "mismatches": item["mismatches"],
                         "desired_hash": item["desired_hash"],
                         "actual_hash": item["actual_hash"]}
                        for item in readback
                    ],
                }
                self.state.record_evidence(
                    "campaign.provision-disabled", command.campaign.canonical_id,
                    "reconciliation_required", redacted)
                raise ReadbackMismatch(readback)
            self.state.record_evidence(
                "campaign.provision-disabled", command.campaign.canonical_id,
                "complete_disabled",
                {"snapshot_id": snapshot_id, "resources": readback})
            return {
                "status": "complete_disabled", "snapshot_id": snapshot_id,
                "resources": resources, "readback": readback,
                "actual": self.repository.campaign(command.campaign.actual_id),
            }

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
