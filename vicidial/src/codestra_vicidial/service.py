from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from .models import (
    AgentCommand,
    CallbackCommand,
    CallbackLookup,
    CampaignCommand,
    CommandLookup,
    CommandLookupResult,
    DisableAgentCommand,
    ExtensionAdoptCommand,
    ExtensionReserveCommand,
    LeadCommand,
    ProvisionAgentCommand,
    TransferPolicyCommand,
    WebrtcProvisionCommand,
    WebrtcRevokeCommand,
    WebrtcRotateCommand,
)
from .repository import (
    EXTENSION_POOL_RANGES,
    ADOPTED_RESERVED_EXTENSIONS,
    CollisionError,
    Repository,
    callback_payload_hash,
    normalize_allowed_campaigns,
)
from .state import (
    CommandRecordUnavailable, MutationCompletionUncertain, ResourceMutationInProgress, ResourceSnapshotChanged,
    StaleResourceVersion, StateStore,
)
from .credential_authority import CredentialAuthorityUnavailable
from . import pjsip_config

log = logging.getLogger(__name__)


class ReadbackMismatch(RuntimeError):
    def __init__(self, results: list[dict]):
        super().__init__(
            "provisioning read-back mismatch; reconciliation required")
        self.results = results


class WebrtcSessionActive(RuntimeError):
    """Maps to HTTP 409 WEBRTC_SESSION_ALREADY_ACTIVE at the API layer."""


@dataclass(frozen=True)
class FeatureFlags:
    writes: bool = False
    provisioning: bool = False
    agent_sync: bool = False
    agent_provisioning: bool = False
    lead_publication: bool = False
    allowed_lead_list_ids: frozenset[int] = frozenset()
    callback_writes: bool = False
    transfer_policy_sync: bool = False
    production_dialing: bool = False
    extension_provisioning: bool = False
    webrtc_provisioning: bool = False


class AdapterService:
    def __init__(self, repository: Repository, state: StateStore, flags: FeatureFlags,
                 credential_authority=None):
        self.repository = repository
        self.state = state
        self.flags = flags
        self.credential_authority = credential_authority

    def agent_availability(self, user_id: str, extension: str) -> dict:
        return self.repository.agent_availability(user_id, extension)

    def _execute(self, operation: str, key: str, command, target: str, fn) -> dict:
        body = command.model_dump(mode="json")
        digest = self.state.digest(body)
        prior = self.state.prior(operation, key, digest)
        if prior is not None:
            return prior | {"idempotent_replay": True}
        context = command.context
        resource_key = f"{operation.split('.', 1)[0]}:{target}"
        claimed = False
        completing = False
        try:
            self.state.claim_resource_version(
                resource_key, context.expected_version, key
            )
            claimed = True
            result = fn()
            completing = True
            result = self.state.complete_command(
                resource_key, context.expected_version, key, operation, digest,
                result, context.correlation_id, context.actor, target,
            )
            claimed = False
            return result | {"idempotent_replay": False}
        except Exception as exc:
            if completing:
                # The telephony mutation was read back, but local completion
                # failed. Preserve the durable claim to block blind retries.
                raise MutationCompletionUncertain(
                    "command completion requires reconciliation"
                ) from exc
            if isinstance(exc, StaleResourceVersion):
                # Another worker may have completed this exact command between
                # our initial replay lookup and the version claim.
                prior = self.state.prior(operation, key, digest)
                if prior is not None:
                    return prior | {"idempotent_replay": True}
            if claimed:
                self.state.release_resource_claim(
                    resource_key, context.expected_version, key
                )
            self.state.audit(context.correlation_id, context.actor, operation, target,
                             "rejected", {"error_type": type(exc).__name__})
            raise

    def lookup_command(self, request: CommandLookup) -> CommandLookupResult | None:
        query = request.root
        self._require_campaign_scope(query.command, query.command.context.campaign_id)
        request_hash = self.state.digest(query.command.model_dump(mode="json"))
        record = self.state.lookup_command(query.operation, query.idempotency_key, request_hash)
        if record is None:
            return None
        try:
            return CommandLookupResult.model_validate({
                "operation": query.operation, "idempotency_key": query.idempotency_key,
                **record,
            })
        except ValidationError as exc:
            raise CommandRecordUnavailable("durable command result unavailable") from exc

    def versioned_readback(self, resource_key: str, reader) -> dict | None:
        before = self.state.resource_observation(resource_key)
        if before[2]:
            raise ResourceMutationInProgress("resource mutation is already in progress")
        actual = reader()
        after = self.state.resource_observation(resource_key)
        if before != after or after[2]:
            raise ResourceSnapshotChanged("resource changed during read-back; retry the read")
        return actual | {"resource_version": before[0]} if actual is not None else None

    def callback_by_external_id(self, external_id: str) -> dict | None:
        mapping = self.state.mapping("callback", external_id)
        if mapping is None:
            return None
        callback_id = int(mapping["actual_id"])
        result = self.versioned_readback(
            f"callback:{external_id}",
            lambda: self._callback_readback(external_id, callback_id),
        )
        return result

    def _callback_readback(self, external_id: str, callback_id: int) -> dict:
        actual = self.repository.callback(callback_id)
        marker = str((actual or {}).get("comments", "")).split(":", 3)
        observed_external = (
            marker[1] if len(marker) == 4 and marker[0] == "CODESTRA"
            else (actual or {}).get("external_id")
        )
        if actual is None or observed_external != external_id:
            raise ReadbackMismatch([{
                "type": "callback", "id": callback_id,
                "mismatches": ["external_reference"],
            }])
        try:
            if int(actual["callback_id"]) != callback_id:
                raise ValueError("callback identity mismatch")
            callback_time = actual["callback_time"]
            if isinstance(callback_time, str):
                callback_time = datetime.fromisoformat(callback_time.replace("Z", "+00:00"))
            if callback_time.tzinfo is None:
                callback_time = callback_time.replace(tzinfo=timezone.utc)
            return CallbackLookup.model_validate({
                "external_id": external_id, "callback_id": callback_id,
                "resource_version": self.state.resource_version(f"callback:{external_id}"),
                "campaign_id": actual["campaign_id"], "lead_id": actual["lead_id"],
                "user_id": actual.get("user", actual.get("user_id")),
                "recipient": actual["recipient"], "status": actual["status"],
                "callback_time": callback_time.astimezone(timezone.utc),
            }).model_dump(mode="json")
        except (KeyError, ValueError, AttributeError, ValidationError) as exc:
            raise ReadbackMismatch([{
                "type": "callback", "id": callback_id,
                "mismatches": ["invalid_actual_state"],
            }]) from exc

    def _require_campaign_scope(
        self, command, campaign_id: str, business_unit: str | None = None,
        *, allow_unregistered: bool = False,
    ) -> None:
        context = command.context
        if context.campaign_id != campaign_id:
            raise PermissionError("request campaign claim mismatch")
        if business_unit is not None and context.business_unit != business_unit:
            raise PermissionError("request business-unit claim mismatch")
        authority = self.state.campaign_authority(campaign_id)
        if authority is None:
            if allow_unregistered:
                return
            raise PermissionError("campaign authority is not registered")
        if (
            authority["tenant_id"] != context.tenant_id
            or authority["business_unit"] != context.business_unit
            or authority["supervisor_subject"] != context.supervisor_subject
        ):
            raise PermissionError("request campaign authority mismatch")

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
                        if campaigns != [spec.actual_id]:
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
        self._require_campaign_scope(
            command, command.campaign.actual_id, command.campaign.business_unit,
            allow_unregistered=True,
        )
        resource_key = f"campaign:{command.campaign.canonical_id}"
        if self.state.resource_version(resource_key) != command.context.expected_version:
            raise StaleResourceVersion("expected version is stale")
        preflight = self.repository.validate_campaign(command.campaign)
        return {"valid": True, "collision": preflight["collision"],
                "actual_id": command.campaign.actual_id,
                "canonical_id": command.campaign.canonical_id}

    def provision_campaign(self, key: str, command: CampaignCommand) -> dict:
        self._require(self.flags.provisioning, "campaign provisioning")
        self._require_campaign_scope(
            command, command.campaign.actual_id, command.campaign.business_unit,
            allow_unregistered=True,
        )

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
            self.state.put_campaign_authority(
                command.campaign.actual_id,
                command.context.tenant_id,
                command.context.business_unit,
                command.context.supervisor_subject,
            )
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
        self._require_campaign_scope(
            command, command.campaign.actual_id, command.campaign.business_unit)

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
        self._require_campaign_scope(command, command.agent.campaigns[0])

        def apply() -> dict:
            desired = command.agent.model_dump(mode="json") | {"active": False}
            desired["adopt_existing_sha256"] = None
            desired_hash = self.state.digest(desired)
            mapping = self.state.mapping("agent", command.agent.user_id)
            existing = self.repository.agent(command.agent.user_id)
            if mapping is None and existing is not None:
                adoption_hash = command.agent.adopt_existing_sha256
                intent = self.state.agent_adoption_intent(command.agent.user_id)
                if intent is None:
                    if existing.get("active") not in (False, "N"):
                        raise CollisionError("active existing agent cannot be adopted")
                    if adoption_hash is None or adoption_hash != self.state.digest(existing):
                        raise CollisionError("existing agent requires exact adoption proof")
                    self.state.put_agent_adoption_intent(
                        command.agent.user_id, adoption_hash, desired_hash
                    )
                elif (adoption_hash != intent["original_hash"]
                      or desired_hash != intent["desired_hash"]):
                    raise CollisionError("agent adoption intent mismatch")
                controlled_match = (
                    existing.get("user_id") == desired["user_id"]
                    and existing.get("full_name") == desired["full_name"]
                    and existing.get("user_group") == desired["user_group"]
                    and existing.get("active") in (False, "N")
                    and existing.get("campaigns") == desired["campaigns"]
                    and set(existing.get("inbound_groups", []))
                    == set(desired["inbound_groups"])
                )
                if intent is not None and controlled_match:
                    self.state.finalize_agent_adoption(
                        command.agent.user_id, command.agent.user_id,
                        desired_hash, desired,
                    )
                    return {"status": "disabled", "actual": existing}
            if mapping and mapping["actual_id"] != command.agent.user_id:
                raise CollisionError("agent ownership mapping collision")
            try:
                actual = self.repository.sync_agent_disabled(command.agent)
                mismatches = []
                for field in ("user_id", "full_name", "user_group"):
                    if actual.get(field) != desired[field]:
                        mismatches.append(field)
                if actual.get("active") not in (False, "N"):
                    mismatches.append("active")
                if actual.get("campaigns") != desired["campaigns"]:
                    mismatches.append("campaigns")
                if set(actual.get("inbound_groups", [])) != set(
                    desired["inbound_groups"]
                ):
                    mismatches.append("inbound_groups")
                if mismatches:
                    raise RuntimeError(
                        "agent read-back mismatch: " + ",".join(mismatches)
                    )
                if command.agent.adopt_existing_sha256:
                    self.state.finalize_agent_adoption(
                        command.agent.user_id, command.agent.user_id,
                        desired_hash, desired,
                    )
                else:
                    self.state.put_agent_mapping(
                        command.agent.user_id, command.agent.user_id, desired_hash
                    )
                    self.state.put_agent_desired(command.agent.user_id, desired)
                return {"status": "disabled", "actual": actual}
            finally:
                release_lock = getattr(
                    self.repository, "release_agent_adoption_lock", None
                )
                if command.agent.adopt_existing_sha256 and release_lock:
                    release_lock()

        return self._execute("agent.sync", key, command, command.agent.user_id, apply)

    def provision_agent(self, key: str, command: ProvisionAgentCommand) -> dict:
        self._require(self.flags.agent_provisioning, "agent provisioning")
        self._require_campaign_scope(command, command.agent.campaigns[0])
        if self.credential_authority is None:
            raise CredentialAuthorityUnavailable("approved secret authority unavailable")

        def apply() -> dict:
            desired = command.agent.model_dump(mode="json")
            desired["adopt_existing_sha256"] = None
            desired_hash = self.state.digest(desired)
            intent = self.state.agent_adoption_intent(command.agent.user_id)
            actual = self.repository.agent(command.agent.user_id)
            if intent is None:
                if actual is not None:
                    raise CollisionError("agent identifier already exists")
                self.state.put_agent_adoption_intent(
                    command.agent.user_id, "missing", desired_hash
                )
                issued = self.credential_authority.issue(
                    username=command.agent.user_id,
                    extension=command.agent.extension,
                    idempotency_key=key,
                )
                actual = self.repository.provision_agent_disabled(command.agent, issued.value)
            elif intent["original_hash"] != "missing" or intent["desired_hash"] != desired_hash:
                raise CollisionError("agent provisioning intent mismatch")
            elif actual is None:
                issued = self.credential_authority.issue(
                    username=command.agent.user_id,
                    extension=command.agent.extension,
                    idempotency_key=key,
                )
                actual = self.repository.provision_agent_disabled(command.agent, issued.value)
            mismatches = [field for field in ("user_id", "full_name", "user_group")
                          if actual.get(field) != desired[field]]
            if actual.get("active") not in (False, "N"):
                mismatches.append("active")
            if int(actual.get("user_level", 0)) != command.user_level:
                mismatches.append("user_level")
            if actual.get("extension") != command.agent.extension:
                mismatches.append("extension")
            if actual.get("campaigns") != desired["campaigns"]:
                mismatches.append("campaigns")
            if set(actual.get("inbound_groups", [])) != set(desired["inbound_groups"]):
                mismatches.append("inbound_groups")
            if mismatches:
                self.repository.disable_agent(command.agent.user_id)
                raise ReadbackMismatch([{"type": "agent", "id": command.agent.user_id,
                                         "mismatches": sorted(set(mismatches))}])
            self.state.finalize_agent_adoption(
                command.agent.user_id, command.agent.user_id, desired_hash, desired
            )
            sanitized = {k: v for k, v in actual.items() if k not in {"pass", "password", "credential"}}
            return {"status": "complete_disabled", "actual": sanitized,
                    "recovered_after_commit": intent is not None}

        return self._execute("agent.provision-disabled", key, command,
                             command.agent.user_id, apply)

    def disable_agent(self, key: str, command: DisableAgentCommand) -> dict:
        self._require(self.flags.agent_sync, "agent sync")
        desired = self.state.agent_desired(command.user_id)
        campaigns = (desired or {}).get("desired", {}).get("campaigns", [])
        if len(campaigns) != 1:
            raise PermissionError("agent campaign binding is not durable and exact")
        self._require_campaign_scope(command, campaigns[0])

        def apply() -> dict:
            # Atomic and immediate (Mission 6): the phone row is
            # deactivated by repository.disable_agent itself; here we also
            # invalidate any WebRTC session/credential in the same
            # request, before read-back, so a disabled agent can never be
            # left with a live registration ticket. Mission 4 extends this
            # to also tear down the generated PJSIP config artifact, so a
            # disabled agent's endpoint is independently unusable at the
            # telephony layer too, not only via the revoked app session.
            existing_config = self.state.webrtc_pjsip_config(command.user_id)
            self.state.clear_webrtc_session(command.user_id)
            actual = self.repository.disable_agent(command.user_id)
            if actual.get("active") not in (False, "N"):
                raise RuntimeError("agent disable read-back failed")
            if existing_config is not None and existing_config["active"]:
                teardown = pjsip_config.revoked(command.user_id, existing_config["extension"])
                self.state.put_webrtc_pjsip_config(
                    command.user_id, existing_config["extension"], active=False,
                    content=teardown.render(), content_hash=teardown.content_hash(),
                )
                log.info(
                    "webrtc.credential.revoked",
                    extra={"user_id": command.user_id, "extension": existing_config["extension"],
                           "contact_count": 0, "reason": "agent_disabled",
                           "pjsip_content_hash": teardown.content_hash()},
                )
            return {"status": "disabled", "actual": actual}

        return self._execute("agent.disable", key, command, command.user_id, apply)

    def reserve_extension(self, key: str, command: ExtensionReserveCommand) -> dict:
        """Mission 6 reserve_extension: allocate the next free number from
        one named pool. Never returns an ADOPTED_RESERVED_EXTENSIONS
        member (e.g. 6101), even though some fall inside a pool's numeric
        range - those are bound exclusively through adopt_extension."""
        self._require(self.flags.extension_provisioning, "extension provisioning")
        if self.credential_authority is None:
            raise CredentialAuthorityUnavailable("approved secret authority unavailable")
        reservation = command.reservation
        self._require_campaign_scope(command, command.context.campaign_id)

        def apply() -> dict:
            low, high = EXTENSION_POOL_RANGES[reservation.pool]
            taken = self.repository.taken_extensions() | ADOPTED_RESERVED_EXTENSIONS
            extension = next(
                (str(n) for n in range(low, high + 1) if str(n) not in taken), None,
            )
            if extension is None:
                raise CollisionError(f"no free extension in pool {reservation.pool}")
            issued = self.credential_authority.issue(
                username=reservation.user_id, extension=extension, idempotency_key=key,
            )
            actual = self.repository.bind_phone(reservation.user_id, extension, issued.value)
            if actual.get("extension") != extension or actual.get("active") not in (True, "Y"):
                raise RuntimeError("extension reservation read-back failed")
            sanitized = {k: v for k, v in actual.items()
                        if k not in {"pass", "password", "credential"}}
            return {"status": "reserved", "pool": reservation.pool, "actual": sanitized}

        return self._execute(
            "extension.reserve", key, command, reservation.user_id, apply)

    def adopt_extension(self, key: str, command: ExtensionAdoptCommand) -> dict:
        """Mission 6 adopt_extension: bind an already-existing extension
        (e.g. 6101/cod00016/TEST_SYN) WITHOUT ever consuming a pool slot.

        Deliberately distinct code path from reserve_extension, not a
        shared function with a flag: if the phone row already exists, this
        only links ownership (repository.link_existing_phone) and never
        rewrites its credential, because a live hardware/softphone may
        already be registered with that secret. Only a genuinely new
        extension (no existing phones row) gets a freshly issued one, via
        bind_phone - the same path reserve_extension uses.
        """
        self._require(self.flags.extension_provisioning, "extension provisioning")
        if self.credential_authority is None:
            raise CredentialAuthorityUnavailable("approved secret authority unavailable")
        adoption = command.adoption
        self._require_campaign_scope(command, command.context.campaign_id)

        def apply() -> dict:
            desired = adoption.model_dump(mode="json")
            desired["adopt_existing_sha256"] = None
            desired_hash = self.state.digest(desired)
            mapping = self.state.mapping("extension", adoption.extension)
            if mapping is not None and mapping["actual_id"] != adoption.user_id:
                raise CollisionError("extension already adopted by a different user")
            already_present = adoption.extension in self.repository.taken_extensions()
            if already_present:
                actual = self.repository.link_existing_phone(
                    adoption.user_id, adoption.extension)
            else:
                issued = self.credential_authority.issue(
                    username=adoption.user_id, extension=adoption.extension,
                    idempotency_key=key,
                )
                actual = self.repository.bind_phone(
                    adoption.user_id, adoption.extension, issued.value)
            if actual.get("extension") != adoption.extension:
                raise RuntimeError("extension adoption read-back failed")
            self.state.put_mapping(
                "extension", adoption.extension, adoption.user_id, desired_hash)
            sanitized = {k: v for k, v in actual.items()
                        if k not in {"pass", "password", "credential"}}
            return {"status": "adopted", "already_present": already_present, "actual": sanitized}

        return self._execute("extension.adopt", key, command, adoption.user_id, apply)

    def provision_webrtc(self, key: str, command: WebrtcProvisionCommand) -> dict:
        """Mission 6/7: issue a short-lived, single-use WebRTC registration
        credential - never the durable phones.pass SIP secret. Enforces at
        most one active session per user; a concurrent second attempt
        raises WebrtcSessionActive, which the API layer maps to
        409 WEBRTC_SESSION_ALREADY_ACTIVE."""
        self._require(self.flags.webrtc_provisioning, "webrtc provisioning")
        if self.credential_authority is None:
            raise CredentialAuthorityUnavailable("approved secret authority unavailable")
        user_id = command.webrtc.user_id
        self._require_campaign_scope(command, command.context.campaign_id)

        def apply() -> dict:
            phone = self.repository.phone(user_id)
            if phone is None:
                raise ValueError("no phone assignment exists for this user")
            existing_session = self.state.webrtc_session(user_id)
            if existing_session is not None and not existing_session["single_use_consumed"]:
                if existing_session["expires_at"] > datetime.now(timezone.utc).isoformat():
                    log.warning(
                        "webrtc.registration.rejected",
                        extra={"user_id": user_id, "extension": phone["extension"],
                               "contact_count": 1, "reason": "session_already_active"},
                    )
                    raise WebrtcSessionActive(
                        "a WebRTC session is already active for this user")
            issued = self.credential_authority.issue(
                username=user_id, extension=phone["extension"], idempotency_key=key,
                operation="webrtc-registration",
            )
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            self.state.put_webrtc_session(
                user_id, phone["extension"], issued.reference, expires_at=expires_at)
            # Mission 4: generate the Asterisk-level max_contacts=1 PJSIP
            # config artifact alongside the app-level session, so the
            # 409 fast-path is backed by independent telephony-layer
            # enforcement rather than being the only guard.
            desired_endpoint = pjsip_config.PjsipEndpointConfig(
                user_id=user_id, extension=phone["extension"],
                credential_reference=issued.reference, active=True,
            )
            self.state.put_webrtc_pjsip_config(
                user_id, phone["extension"], active=True,
                content=desired_endpoint.render(),
                content_hash=desired_endpoint.content_hash(),
            )
            log.info(
                "webrtc.registration.accepted",
                extra={"user_id": user_id, "extension": phone["extension"],
                       "contact_count": 1,
                       "pjsip_content_hash": desired_endpoint.content_hash()},
            )
            return {
                "status": "provisioned", "extension": phone["extension"],
                "credential": issued.value, "credential_reference": issued.reference,
                "expires_at": expires_at, "max_active_sessions": 1,
            }

        return self._execute("webrtc.provision", key, command, user_id, apply)

    def rotate_webrtc_secret(self, key: str, command: WebrtcRotateCommand) -> dict:
        """Mission 6: replace the active session's credential; the prior
        one is invalidated by simply no longer being tracked (single row
        per user_id in webrtc_sessions)."""
        self._require(self.flags.webrtc_provisioning, "webrtc provisioning")
        if self.credential_authority is None:
            raise CredentialAuthorityUnavailable("approved secret authority unavailable")
        user_id = command.user_id
        self._require_campaign_scope(command, command.context.campaign_id)

        def apply() -> dict:
            phone = self.repository.phone(user_id)
            if phone is None:
                raise ValueError("no phone assignment exists for this user")
            issued = self.credential_authority.issue(
                username=user_id, extension=phone["extension"], idempotency_key=key,
                operation="webrtc-registration",
            )
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            self.state.put_webrtc_session(
                user_id, phone["extension"], issued.reference, expires_at=expires_at)
            rotated_endpoint = pjsip_config.PjsipEndpointConfig(
                user_id=user_id, extension=phone["extension"],
                credential_reference=issued.reference, active=True,
            )
            self.state.put_webrtc_pjsip_config(
                user_id, phone["extension"], active=True,
                content=rotated_endpoint.render(),
                content_hash=rotated_endpoint.content_hash(),
            )
            log.info(
                "webrtc.credential.rotated",
                extra={"user_id": user_id, "extension": phone["extension"],
                       "pjsip_content_hash": rotated_endpoint.content_hash()},
            )
            return {
                "status": "rotated", "extension": phone["extension"],
                "credential": issued.value, "credential_reference": issued.reference,
                "expires_at": expires_at,
            }

        return self._execute("webrtc.rotate", key, command, user_id, apply)

    def revoke_webrtc(self, key: str, command: WebrtcRevokeCommand) -> dict:
        """Mission 6: one atomic operation - invalidate the session ticket,
        the tracked credential reference, and (via clear_phone_credential)
        the phone row's live SIP secret, so a revoked WebRTC channel can
        never silently keep working through a stale credential."""
        self._require(self.flags.webrtc_provisioning, "webrtc provisioning")
        user_id = command.user_id
        self._require_campaign_scope(command, command.context.campaign_id)

        def apply() -> dict:
            existing_config = self.state.webrtc_pjsip_config(user_id)
            self.state.clear_webrtc_session(user_id)
            actual = self.repository.clear_phone_credential(user_id)
            if existing_config is not None:
                teardown = pjsip_config.revoked(user_id, existing_config["extension"])
                self.state.put_webrtc_pjsip_config(
                    user_id, existing_config["extension"], active=False,
                    content=teardown.render(), content_hash=teardown.content_hash(),
                )
                log.info(
                    "webrtc.credential.revoked",
                    extra={"user_id": user_id, "extension": existing_config["extension"],
                           "contact_count": 0,
                           "pjsip_content_hash": teardown.content_hash()},
                )
            return {"status": "revoked", "actual": actual}

        return self._execute("webrtc.revoke", key, command, user_id, apply)

    def publish_lead(self, key: str, command: LeadCommand) -> dict:
        self._require(self.flags.lead_publication, "lead publication")
        self._require_campaign_scope(command, command.context.campaign_id)

        if command.lead.list_id not in self.flags.allowed_lead_list_ids:
            raise PermissionError("lead list is not authorized")
        campaign = self.repository.campaign(command.context.campaign_id)
        campaign_list_ids = {
            int(row["list_id"]) for row in (campaign or {}).get("lists", [])
        }
        if command.lead.list_id not in campaign_list_ids:
            raise PermissionError("lead list does not belong to campaign")

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
        self._require_campaign_scope(command, command.callback.campaign_id)

        spec = command.callback
        def authorize() -> None:
            if spec.recipient != "USERONLY":
                raise PermissionError("callbacks must remain assigned to one agent")
            owner = self.repository.campaign_owner(spec.campaign_id)
            if owner is None or self.state.mapping("campaign", owner) is None:
                raise PermissionError("callback campaign ownership is not durable")
            policy = self.state.campaign_policy(spec.campaign_id)
            if not policy or not policy.get("callback_enabled"):
                raise PermissionError("callback policy is disabled or missing")
            lead = self.repository.lead(spec.lead_id)
            campaign = self.repository.campaign(spec.campaign_id)
            campaign_list_ids = {
                int(row["list_id"])
                for row in (campaign or {}).get("lists", [])
            }
            if (
                lead is None
                or int(lead.get("list_id", -1)) not in campaign_list_ids
            ):
                raise PermissionError("callback lead does not belong to campaign")
            agent = self.repository.agent(spec.user_id)
            desired_agent = self.state.agent_desired(spec.user_id)
            if (
                agent is None
                or desired_agent is None
                or agent.get("campaigns") != [spec.campaign_id]
                or desired_agent["desired"].get("campaigns") != [spec.campaign_id]
            ):
                raise PermissionError("callback agent campaign binding is not exact")

        def apply() -> dict:
            authorize()
            actual = self.repository.create_callback(spec)
            callback_id = actual.get("callback_id")
            observed = self.repository.callback(int(callback_id)) if callback_id else None
            marker = str((observed or {}).get("comments", ""))
            expected_time = spec.callback_time.isoformat().replace("+00:00", "Z")
            observed_time = (observed or {}).get("callback_time")
            if hasattr(observed_time, "replace") and not isinstance(
                observed_time, str
            ):
                observed_time = (
                    observed_time.replace(tzinfo=timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            expected = {
                "lead_id": spec.lead_id,
                "campaign_id": spec.campaign_id,
                "user": spec.user_id,
                "recipient": "USERONLY",
                "status": "ACTIVE",
                "callback_time": expected_time,
            }
            aliases = {"user": "user_id"}
            mismatches = []
            for field, value in expected.items():
                observed_value = (
                    observed_time
                    if field == "callback_time"
                    else (observed or {}).get(
                        field, (observed or {}).get(aliases.get(field, ""))
                    )
                )
                if str(observed_value) != str(value):
                    mismatches.append(field)
            if marker.startswith("CODESTRA:"):
                parts = marker.split(":", 3)
                if (
                    len(parts) < 4
                    or parts[1] != spec.external_id
                    or parts[2] != callback_payload_hash(spec)
                    or parts[3] != spec.comments
                ):
                    mismatches.append("external_reference")
            elif marker != spec.comments or (observed or {}).get("external_id") != spec.external_id:
                mismatches.append("external_reference")
            if not callback_id or mismatches:
                raise ReadbackMismatch([{
                    "type": "callback",
                    "id": callback_id,
                    "mismatches": sorted(set(mismatches or ["missing"])),
                }])
            authorize()
            self.state.put_mapping("callback", command.callback.external_id,
                                   str(callback_id), self.state.digest(observed))
            return {"status": "created", "actual": observed}

        return self._execute("callback.create", key, command,
                             command.callback.external_id, apply)

    def sync_transfer_policy(self, key: str, command: TransferPolicyCommand) -> dict:
        self._require(self.flags.transfer_policy_sync, "transfer policy sync")
        self._require_campaign_scope(command, command.policy.campaign_id)

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
                authority = self.state.campaign_authority(stored["actual_id"])
                if authority is None:
                    raise PermissionError("campaign authority is not registered")
                command = CampaignCommand.model_validate({
                    "context": {
                        "correlation_id": "reconciliation-readback",
                        "actor": "adapter-reconciler",
                        "tenant_id": authority["tenant_id"],
                        "business_unit": authority["business_unit"],
                        "campaign_id": stored["actual_id"],
                        "supervisor_subject": authority["supervisor_subject"],
                        "expected_version": self.state.resource_version(
                            f"campaign:{stored['canonical_id']}"
                        ),
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
        response = {"run_id": run_id, **result}
        concurrent_prior = self.state.persist_reconciliation_run(
            run_id, result, idempotency_key, request_hash
        )
        if concurrent_prior is not None:
            return concurrent_prior | {"idempotent_replay": True}
        return response | {"idempotent_replay": False}

    def reconciliation_run(self, run_id: str) -> dict | None:
        return self.state.reconciliation_run(run_id)
