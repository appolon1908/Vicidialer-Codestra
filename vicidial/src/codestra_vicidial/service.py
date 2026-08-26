from __future__ import annotations

import uuid
from dataclasses import dataclass

from .models import (
    AgentCommand,
    CallbackCommand,
    CampaignCommand,
    DisableAgentCommand,
    LeadCommand,
    TransferPolicyCommand,
)
from .repository import CollisionError, Repository, normalize_allowed_campaigns
from .state import StateStore


class ReadbackMismatch(RuntimeError):
    def __init__(self, results: list[dict]):
        super().__init__(
            "provisioning read-back mismatch; reconciliation required")
        self.results = results


@dataclass(frozen=True)
class FeatureFlags:
    writes: bool = False
    provisioning: bool = False
    agent_sync: bool = False
    lead_publication: bool = False
    allowed_lead_list_ids: frozenset[int] = frozenset()
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
                field for field in fields
                if str(actual.get(field)) != str(desired.get(field))
            )
        return {
            "type": resource_type, "id": resource_id,
            "match": not mismatches, "mismatches": sorted(set(mismatches)),
            "desired_hash": StateStore.digest(desired),
            "actual_hash": (
                StateStore.digest(actual) if actual is not None else None),
        }

    def _campaign_readback(
            self, command: CampaignCommand) -> tuple[dict, list[dict]]:
        spec = command.campaign
        actual = self.repository.campaign_resources(spec)
        campaign = actual.get("campaign")
        results: list[dict] = []
        campaign_desired = {
            "campaign_id": spec.actual_id, "campaign_name": spec.name,
            "active": "N", "dial_method": spec.dial_method,
            "local_call_time": spec.call_time,
            "campaign_recording": spec.recording_policy,
            "scheduled_callbacks": "Y" if spec.callback_enabled else "N",
        }
        results.append(self._comparison(
            "campaign", spec.actual_id, campaign_desired, campaign,
            tuple(campaign_desired)))
        list_rows = campaign.get("lists", []) if campaign else []
        list_by_id = {int(row["list_id"]): row for row in list_rows}
        for item in spec.lists:
            row = list_by_id.get(item.list_id)
            desired = {
                "list_id": item.list_id, "list_name": item.code,
                "campaign_id": spec.actual_id, "active": "N",
            }
            results.append(self._comparison(
                "list", item.list_id, desired, row, tuple(desired)))
        group_rows = actual.get("groups", [])
        group_by_key = {
            (row.get("kind"), row.get("code")): row for row in group_rows
        }
        for item in spec.groups:
            row = group_by_key.get((item.kind, item.code))
            desired = {"code": item.code, "name": item.name}
            fields = ["code", "name"]
            extra = []
            if item.kind == "user":
                if row is not None:
                    try:
                        campaigns = normalize_allowed_campaigns(
                            row.get("allowed_campaigns"))
                        if spec.actual_id not in campaigns:
                            extra.append("allowed_campaigns")
                    except CollisionError:
                        extra.append("allowed_campaigns")
            else:
                desired["active"] = "N"
                fields.append("active")
            results.append(self._comparison(
                item.kind + "_group", item.code, desired, row,
                tuple(fields), extra))
        script_by_id = {
            row.get("script_id"): row for row in actual.get("scripts", [])
        }
        for item in spec.scripts:
            row = script_by_id.get(item.code)
            desired = {
                "script_id": item.code, "script_name": item.name,
                "script_text": item.text, "active": "N",
            }
            extra = []
            if row is not None:
                actual_version = row.get("version")
                if actual_version is None:
                    if not str(row.get("script_comments", "")).endswith(
                            f":V{item.version}"):
                        extra.append("version")
                elif int(actual_version) != item.version:
                    extra.append("version")
            results.append(self._comparison(
                "script", item.code, desired, row, tuple(desired), extra))
        dispositions = campaign.get("dispositions", []) if campaign else []
        disposition_by_code = {row.get("status"): row for row in dispositions}
        for item in spec.dispositions:
            row = disposition_by_code.get(item.code)
            desired = {
                "status": item.code, "status_name": item.name,
                "selectable": "Y" if item.selectable else "N",
                "human_answered": "Y" if item.human_answered else "N",
                "category": item.category,
            }
            results.append(self._comparison(
                "disposition", item.code, desired, row, tuple(desired)))
        policy = self.state.campaign_policy(spec.actual_id)
        results.append(self._comparison(
            "transfer_policy", spec.actual_id,
            {"transfer_targets": sorted(spec.transfer_targets)}, policy,
            ("transfer_targets",)))
        results.append(self._comparison(
            "recording_policy_reference", spec.actual_id,
            {"recording_policy_reference": spec.recording_policy_reference},
            policy, ("recording_policy_reference",)))
        results.append(self._comparison(
            "callback_policy", spec.actual_id,
            {"callback_enabled": spec.callback_enabled}, policy,
            ("callback_enabled",)))
        results.append(self._comparison(
            "reporting_mapping", spec.actual_id,
            {"reporting_mapping": spec.reporting_mapping}, policy,
            ("reporting_mapping",)))
        mapping = self.state.mapping("campaign", spec.canonical_id)
        mapping_actual = (
            {"canonical_id": spec.canonical_id,
             "actual_id": mapping["actual_id"],
             "desired_hash": mapping["payload_hash"]}
            if mapping else None
        )
        desired_payload = spec.model_dump(mode="json")
        desired_payload["adopt_existing_sha256"] = None
        desired_hash = self.state.digest(desired_payload)
        results.append(self._comparison(
            "canonical_mapping", spec.canonical_id,
            {"canonical_id": spec.canonical_id, "actual_id": spec.actual_id,
             "desired_hash": desired_hash},
            mapping_actual, ("canonical_id", "actual_id", "desired_hash")))
        return actual, results

    def validate_campaign(self, command: CampaignCommand) -> dict:
        preflight = self.repository.validate_campaign(command.campaign)
        return {"valid": True, "collision": preflight["collision"],
                "actual_id": command.campaign.actual_id,
                "canonical_id": command.campaign.canonical_id}

    def provision_campaign(self, key: str, command: CampaignCommand) -> dict:
        self._require(self.flags.provisioning, "campaign provisioning")

        def apply() -> dict:
            desired_payload = command.campaign.model_dump(mode="json")
            desired_payload["adopt_existing_sha256"] = None
            desired_hash = self.state.digest(desired_payload)
            mapping = self.state.mapping(
                "campaign", command.campaign.canonical_id)
            if mapping and (
                    mapping["actual_id"] != command.campaign.actual_id
                    or mapping["payload_hash"] != desired_hash):
                raise CollisionError("canonical campaign mapping collision")
            actual_mapping = self.state.mapping_for_actual(
                "campaign", command.campaign.actual_id)
            if actual_mapping and actual_mapping["external_id"] != (
                    command.campaign.canonical_id):
                raise CollisionError("actual campaign ownership collision")
            snapshot_id = str(uuid.uuid4())
            before = self.repository.campaign_resources(command.campaign)
            self.state.snapshot(
                snapshot_id, "campaign.provision-disabled",
                command.campaign.canonical_id,
                {
                    "resources": before,
                    "campaign_policy": self.state.campaign_policy(
                        command.campaign.actual_id),
                    "desired_hash": desired_hash,
                },
            )
            resources = self.repository.provision_campaign_disabled(command.campaign)
            self.state.put_mapping(
                "campaign", command.campaign.canonical_id, command.campaign.actual_id,
                desired_hash)
            self.state.put_campaign_desired(
                command.campaign.canonical_id, command.campaign.actual_id,
                desired_payload)
            for group in command.campaign.groups:
                self.state.put_group_ownership(
                    group.code, command.campaign.actual_id,
                    command.campaign.business_unit, group.kind)
            self.state.put_transfer_policy(
                command.campaign.actual_id, command.campaign.transfer_targets)
            self.state.put_campaign_policy(
                command.campaign.actual_id,
                command.campaign.transfer_targets,
                command.campaign.recording_policy_reference,
                command.campaign.reporting_mapping,
                command.campaign.callback_enabled,
            )
            actual, readback = self._campaign_readback(command)
            if not all(item["match"] for item in readback):
                self.repository.disable_campaign_resources(command.campaign)
                redacted = {
                    "snapshot_id": snapshot_id,
                    "resources": [
                        {
                            "type": item["type"], "id": item["id"],
                            "match": item["match"],
                            "mismatches": item["mismatches"],
                            "desired_hash": item["desired_hash"],
                            "actual_hash": item["actual_hash"],
                        }
                        for item in readback
                    ],
                }
                self.state.record_evidence(
                    "campaign.provision-disabled",
                    command.campaign.canonical_id,
                    "reconciliation_required", redacted)
                raise ReadbackMismatch(readback)
            self.state.record_evidence(
                "campaign.provision-disabled",
                command.campaign.canonical_id,
                "complete_disabled",
                {"snapshot_id": snapshot_id, "resources": readback})
            return {
                "status": "complete_disabled", "snapshot_id": snapshot_id,
                "resources": resources, "actual": actual["campaign"],
                "resource_readback": actual, "readback": readback,
            }

        return self._execute("campaign.provision-disabled", key, command,
                             command.campaign.canonical_id, apply)

    def disable_campaign(self, key: str, command: CampaignCommand) -> dict:
        self._require(self.flags.provisioning, "campaign provisioning")

        def apply() -> dict:
            spec = command.campaign
            mapping = self.state.mapping("campaign", spec.canonical_id)
            if mapping is None:
                raise CollisionError("campaign ownership mapping missing")
            if mapping["actual_id"] != spec.actual_id:
                raise CollisionError("campaign ownership mapping mismatch")
            desired_payload = spec.model_dump(mode="json")
            desired_payload["adopt_existing_sha256"] = None
            if mapping["payload_hash"] != self.state.digest(desired_payload):
                raise CollisionError("campaign desired mapping changed")
            owner = self.repository.campaign_owner(spec.actual_id)
            if owner is None:
                raise CollisionError("campaign ownership marker missing")
            if owner != spec.canonical_id:
                raise CollisionError("campaign ownership marker mismatch")
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
            desired = command.agent.model_dump(mode="json") | {"active": False}
            desired_hash = self.state.digest(desired)
            mapping = self.state.mapping("agent", command.agent.user_id)
            if mapping and mapping["actual_id"] != command.agent.user_id:
                raise CollisionError("agent ownership mapping collision")
            actual = self.repository.sync_agent_disabled(command.agent)
            mismatches = []
            for field in ("user_id", "full_name", "user_group"):
                if actual.get(field) != desired[field]:
                    mismatches.append(field)
            if actual.get("active") not in (False, "N"):
                mismatches.append("active")
            if not set(desired["campaigns"]).issubset(set(actual.get("campaigns", []))):
                mismatches.append("campaigns")
            if set(actual.get("inbound_groups", [])) != set(desired["inbound_groups"]):
                mismatches.append("inbound_groups")
            if mismatches:
                raise RuntimeError("agent read-back mismatch: " + ",".join(mismatches))
            self.state.put_agent_mapping(
                command.agent.user_id, command.agent.user_id, desired_hash
            )
            self.state.put_agent_desired(command.agent.user_id, desired)
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

        if command.lead.list_id not in self.flags.allowed_lead_list_ids:
            raise PermissionError("lead list is not authorized")

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
            owner = self.repository.campaign_owner(command.policy.campaign_id)
            if owner is None:
                raise ValueError("campaign ownership marker missing")
            business_unit = "TEST" if owner == "TEST_SYN" else owner.split("-", 1)[0]
            for target in command.policy.allowed_targets:
                group = self.repository.transfer_target(target)
                state_owner = self.state.group_owner(target)
                if group is None:
                    raise ValueError("transfer target missing")
                if state_owner is None:
                    raise ValueError("transfer target ownership missing")
                if (state_owner["campaign_id"] != command.policy.campaign_id
                        or state_owner["business_unit"] != business_unit
                        or state_owner["kind"] not in {"inbound", "closer"}):
                    raise ValueError("transfer target ownership mismatch")
            self.state.put_transfer_policy(
                command.policy.campaign_id, command.policy.allowed_targets)
            return {"campaign_id": command.policy.campaign_id,
                    "allowed_targets": sorted(command.policy.allowed_targets)}

        return self._execute("transfer-policy.sync", key, command,
                             command.policy.campaign_id, apply)

    def reconciliation(self) -> dict:
        desired_rows = self.state.campaign_desired()
        if not desired_rows:
            return {
                "status": "blocked", "inventory": self.repository.inventory(),
                "resources": [], "reason": "desired state missing",
            }
        resources: list[dict] = []
        blocked = False
        for stored in desired_rows:
            try:
                command = CampaignCommand.model_validate({
                    "context": {
                        "correlation_id": "reconciliation-readback",
                        "actor": "adapter-reconciler",
                        "reason": "compare persisted desired and actual state",
                        "requested_at": "2026-01-01T00:00:00Z",
                    },
                    "campaign": stored["desired"],
                })
                _actual, comparisons = self._campaign_readback(command)
                for comparison in comparisons:
                    comparison["status"] = (
                        "match" if comparison["match"] else "mismatch")
                    comparison["mismatch_fields"] = comparison.pop("mismatches")
                    comparison["repair_classification"] = (
                        "none" if comparison["match"]
                        else "approval_required")
                resources.extend(comparisons)
            except Exception as exc:
                blocked = True
                resources.append({
                    "type": "campaign", "id": stored["canonical_id"],
                    "match": False, "status": "blocked",
                    "mismatch_fields": [type(exc).__name__],
                    "desired_hash": stored["desired_hash"],
                    "actual_hash": None, "repair_classification": "blocked",
                })
        mismatched = [row for row in resources if not row["match"]]
        status = (
            "blocked" if blocked
            else "drift_detected" if mismatched
            else "complete"
        )
        return {
            "status": status, "inventory": self.repository.inventory(),
            "resources": resources,
        }

    def start_reconciliation(self, idempotency_key: str) -> dict:
        request_hash = self.state.digest({"operation": "reconciliation.run"})
        prior = self.state.prior(
            "reconciliation.run", idempotency_key, request_hash
        )
        if prior is not None:
            return prior | {"idempotent_replay": True}
        run_id = str(uuid.uuid4())
        result = self.reconciliation()
        self.state.put_reconciliation_run(run_id, result)
        response = {"run_id": run_id, **result}
        self.state.record(
            "reconciliation.run", idempotency_key, request_hash, response
        )
        return response | {"idempotent_replay": False}

    def reconciliation_run(self, run_id: str) -> dict | None:
        return self.state.reconciliation_run(run_id)
