from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

StrictText = Annotated[str, Field(min_length=1, max_length=255)]
CanonicalCampaign = Annotated[
    str,
    Field(
        pattern=(
            r"^(?:TEST_SYN|(?:MOY|COD|SCP|MBL|RLP|FTP|TRX|CAL|TEST)-"
            r"[A-Z0-9]+(?:-[A-Z0-9]+)*-(?:OUT|IN))$"
        ),
        max_length=40,
    ),
]
ActualCampaign = Annotated[str, Field(pattern=r"^[A-Z0-9_]{2,8}$")]
ResourceCode = Annotated[str, Field(pattern=r"^[A-Z0-9_]{2,20}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RequestContext(StrictModel):
    correlation_id: Annotated[str, Field(min_length=8, max_length=128)]
    actor: Annotated[str, Field(min_length=3, max_length=128)]
    tenant_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{2,128}$")]
    business_unit: Literal["MOY", "COD", "SCP", "MBL", "RLP", "FTP", "TRX", "CAL", "TEST"]
    campaign_id: ActualCampaign
    supervisor_subject: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9._:@-]{3,128}$")
    ]
    expected_version: Annotated[int, Field(ge=0)]
    reason: Annotated[str, Field(min_length=8, max_length=500)]
    requested_at: datetime


class ListSpec(StrictModel):
    list_id: Annotated[int, Field(ge=11000, le=91999)]
    code: Annotated[str, Field(pattern=r"^[A-Z0-9_]{2,30}$")]
    active: Literal[False] = False


class GroupSpec(StrictModel):
    code: ResourceCode
    name: Annotated[str, Field(min_length=2, max_length=40)]
    kind: Literal["user", "inbound", "closer"]
    active: Literal[False] = False


class ScriptSpec(StrictModel):
    code: ResourceCode
    name: Annotated[str, Field(min_length=2, max_length=50)]
    text: Annotated[str, Field(min_length=1, max_length=65535)]
    version: Annotated[int, Field(ge=1, le=999)]
    active: Literal[False] = False


class DispositionSpec(StrictModel):
    code: Annotated[str, Field(pattern=r"^[A-Z0-9_]{1,6}$")]
    name: Annotated[str, Field(min_length=2, max_length=30)]
    selectable: Literal[True, False] = True
    human_answered: Literal[True, False] = False
    category: Annotated[str, Field(pattern=r"^[A-Z0-9_ -]{2,20}$")] = "UNDEFINED"


class CampaignSpec(StrictModel):
    canonical_id: CanonicalCampaign
    actual_id: ActualCampaign
    name: Annotated[str, Field(min_length=2, max_length=40)]
    business_unit: Literal["MOY", "COD", "SCP", "MBL", "RLP", "FTP", "TRX", "CAL", "TEST"]
    direction: Literal["inbound", "outbound", "blended"]
    active: Literal[False] = False
    dial_method: Literal["MANUAL", "INBOUND_MAN"] = "MANUAL"
    call_time: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{2,10}$")] = "9am-9pm"
    recording_policy: Literal["NEVER", "ONDEMAND"] = "ONDEMAND"
    callback_enabled: Literal[True, False] = False
    default_list_id: Annotated[int, Field(ge=11000, le=91999)] | None = None
    lists: list[ListSpec] = Field(default_factory=list, max_length=50)
    groups: list[GroupSpec] = Field(default_factory=list, max_length=50)
    scripts: list[ScriptSpec] = Field(default_factory=list, max_length=20)
    dispositions: list[DispositionSpec] = Field(default_factory=list, max_length=50)
    transfer_targets: list[ResourceCode] = Field(default_factory=list, max_length=20)
    reporting_mapping: StrictText
    recording_policy_reference: StrictText
    adopt_existing_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None

    @model_validator(mode="after")
    def validate_ownership(self) -> "CampaignSpec":
        if self.canonical_id != "TEST_SYN" and not self.canonical_id.startswith(self.business_unit + "-"):
            raise ValueError("campaign/business-unit mismatch")
        if self.canonical_id == "TEST_SYN" and self.business_unit != "TEST":
            raise ValueError("TEST_SYN requires TEST business unit")
        ids = [item.list_id for item in self.lists]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate list_id")
        ranges = {
            "MOY": (11000, 11999), "COD": (21000, 21999), "SCP": (31000, 31999),
            "MBL": (41000, 41999), "RLP": (51000, 51999), "FTP": (61000, 61999),
            "TRX": (71000, 71999), "CAL": (81000, 81999), "TEST": (91000, 91999),
        }
        low, high = ranges[self.business_unit]
        if any(not low <= item <= high for item in ids):
            raise ValueError("list_id outside business-unit range")
        if self.default_list_id is not None and self.default_list_id not in ids:
            raise ValueError("default_list_id must identify a manifest list")
        for collection in (self.groups, self.scripts, self.dispositions):
            codes = [item.code for item in collection]
            if len(codes) != len(set(codes)):
                raise ValueError("duplicate resource code")
        return self


class CampaignCommand(StrictModel):
    context: RequestContext
    campaign: CampaignSpec


class ReconciliationCampaignCommand(CampaignCommand):
    authorization_reference: Annotated[str, Field(pattern=r"^CHG-[A-Z0-9-]{10,80}$")]
    plan_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    backup_reference: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:/-]{8,255}$")]


class AgentSpec(StrictModel):
    user_id: Annotated[str, Field(pattern=r"^[A-Z]{3}[0-9]{4,12}$")]
    full_name: Annotated[str, Field(min_length=2, max_length=50)]
    user_group: ResourceCode
    campaigns: list[ActualCampaign] = Field(min_length=1, max_length=1)
    inbound_groups: list[ResourceCode] = Field(default_factory=list, max_length=30)
    active: Literal[False] = False
    adopt_existing_sha256: Annotated[
        str, Field(pattern=r"^[a-f0-9]{64}$")
    ] | None = None


class AgentCommand(StrictModel):
    context: RequestContext
    agent: AgentSpec


class ProvisionAgentSpec(AgentSpec):
    user_id: Literal["appolon"]
    user_group: Literal["WEBTEST_SYN"]
    campaigns: list[Literal["TEST_SYN"]] = Field(min_length=1, max_length=1)
    inbound_groups: list[ResourceCode] = Field(default_factory=list, max_length=0)
    extension: Annotated[str, Field(pattern=r"^[0-9]{4,8}$")]
    role: Literal["supervisor"]
    campaign: Literal["TEST_SYN"]
    internal_only: Literal[True]
    external_dialing: Literal[False]


class ProvisionAgentCommand(AgentCommand):
    agent: ProvisionAgentSpec
    user_level: Literal[8]
    authorization_reference: Annotated[str, Field(pattern=r"^CHG-[A-Z0-9-]{10,80}$")]
    plan_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    backup_reference: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:/-]{8,255}$")]


ExtensionPool = Literal[
    "transportation", "moneybee", "web_ai", "senior_products",
    "student_repayment", "supervisor_qa",
]
ExtensionNumber = Annotated[str, Field(pattern=r"^[0-9]{4}$")]


class ExtensionReserveSpec(StrictModel):
    """Allocate the next free extension from a named pool.

    The pool identifier is deliberately NOT derived from ``business_unit``/
    ``campaign_id`` here - this codebase's business-unit codes (MOY, COD,
    SCP, MBL, RLP, FTP, TRX, CAL, TEST) have no confirmed one-to-one mapping
    onto the six named campaign-type pools from the platform spec
    (Transportation, MoneyBee, Web/AI, Senior Products, Student Repayment,
    Supervisor/QA). Guessing that mapping wrong would misallocate real
    extensions across campaigns, so the caller (Middleware) must state the
    pool explicitly until a human confirms the mapping.
    """

    user_id: Annotated[str, Field(pattern=r"^[A-Z]{3}[0-9]{4,12}$")]
    pool: ExtensionPool
    webrtc_enabled: Literal[True, False] = False
    incoming_allowed: Literal[True, False] = True
    outgoing_allowed: Literal[True, False] = True


class ExtensionReserveCommand(StrictModel):
    context: RequestContext
    reservation: ExtensionReserveSpec


class ExtensionAdoptSpec(StrictModel):
    """Bind one already-existing extension to a platform user without ever
    touching pool allocation - e.g. the permanent 6101/cod00016/TEST_SYN
    staging bootstrap. Distinct type from ``ExtensionReserveSpec`` on
    purpose: an adopt request that accidentally reused reservation logic
    could silently consume a pool slot or reallocate a reserved number.
    """

    user_id: Annotated[str, Field(pattern=r"^[A-Z]{3}[0-9]{4,12}$")]
    extension: ExtensionNumber
    webrtc_enabled: Literal[True, False] = False
    incoming_allowed: Literal[True, False] = True
    outgoing_allowed: Literal[True, False] = True
    adopt_existing_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None


class ExtensionAdoptCommand(StrictModel):
    context: RequestContext
    adoption: ExtensionAdoptSpec


class WebrtcProvisionSpec(StrictModel):
    user_id: Annotated[str, Field(pattern=r"^[A-Z]{3}[0-9]{4,12}$")]


class WebrtcProvisionCommand(StrictModel):
    context: RequestContext
    webrtc: WebrtcProvisionSpec


class WebrtcRotateCommand(StrictModel):
    context: RequestContext
    user_id: Annotated[str, Field(pattern=r"^[A-Z]{3}[0-9]{4,12}$")]


class WebrtcRevokeCommand(StrictModel):
    context: RequestContext
    user_id: Annotated[str, Field(pattern=r"^[A-Z]{3}[0-9]{4,12}$")]


class DisableAgentCommand(StrictModel):
    context: RequestContext
    user_id: Annotated[str, Field(pattern=r"^[A-Z]{3}[0-9]{4,12}$")]


class LeadSpec(StrictModel):
    external_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,50}$")]
    list_id: Annotated[int, Field(ge=11000, le=91999)]
    phone_code: Annotated[str, Field(pattern=r"^[0-9]{1,4}$")]
    phone_number: Annotated[str, Field(pattern=r"^[0-9]{7,18}$")]
    first_name: Annotated[str, Field(max_length=30)] = ""
    last_name: Annotated[str, Field(max_length=30)] = ""
    status: Annotated[str, Field(pattern=r"^[A-Z0-9_]{1,6}$")] = "NEW"


class LeadCommand(StrictModel):
    context: RequestContext
    lead: LeadSpec


class CallbackSpec(StrictModel):
    external_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9.-]{1,50}$")]
    lead_id: Annotated[int, Field(gt=0)]
    campaign_id: ActualCampaign
    user_id: Annotated[str, Field(pattern=r"^[A-Z]{3}[0-9]{4,12}$")]
    callback_time: datetime
    recipient: Literal["USERONLY"] = "USERONLY"
    comments: Annotated[str, Field(max_length=127)] = ""

    @field_validator("callback_time")
    @classmethod
    def callback_time_must_be_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("callback_time must include a timezone offset")
        return value.astimezone(timezone.utc)


class CallbackCommand(StrictModel):
    context: RequestContext
    callback: CallbackSpec


class TransferPolicySpec(StrictModel):
    campaign_id: ActualCampaign
    allowed_targets: list[ResourceCode] = Field(max_length=30)

    @field_validator("allowed_targets")
    @classmethod
    def unique_targets(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate transfer target")
        return value


class TransferPolicyCommand(StrictModel):
    context: RequestContext
    policy: TransferPolicySpec


class CallbackLookup(StrictModel):
    external_id: str
    callback_id: Annotated[int, Field(gt=0)]
    resource_version: Annotated[int, Field(ge=0)]
    campaign_id: ActualCampaign
    lead_id: Annotated[int, Field(gt=0)]
    user_id: str
    recipient: str
    status: str
    callback_time: datetime


class CommandLookupKey(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    idempotency_key: Annotated[str, Field(min_length=8, max_length=200)]


class CampaignCommandLookup(CommandLookupKey):
    operation: Literal["campaign.provision-disabled", "campaign.disable"]
    command: CampaignCommand


class AgentSyncLookup(CommandLookupKey):
    operation: Literal["agent.sync"]
    command: AgentCommand


class AgentProvisionLookup(CommandLookupKey):
    operation: Literal["agent.provision-disabled"]
    command: ProvisionAgentCommand


class AgentDisableLookup(CommandLookupKey):
    operation: Literal["agent.disable"]
    command: DisableAgentCommand


class LeadCommandLookup(CommandLookupKey):
    operation: Literal["lead.publish"]
    command: LeadCommand


class CallbackCommandLookup(CommandLookupKey):
    operation: Literal["callback.create"]
    command: CallbackCommand


class TransferCommandLookup(CommandLookupKey):
    operation: Literal["transfer-policy.sync"]
    command: TransferPolicyCommand


class CommandLookup(RootModel[Annotated[
    CampaignCommandLookup | AgentSyncLookup | AgentProvisionLookup | AgentDisableLookup
    | LeadCommandLookup | CallbackCommandLookup | TransferCommandLookup,
    Field(discriminator="operation"),
]]):
    """Identify an exact prior command without applying it again."""


class RecordedCommandResult(BaseModel):
    """Preserve operation-specific fields and require a committed version."""

    model_config = ConfigDict(extra="allow")
    resource_version: Annotated[int, Field(ge=1, strict=True)]


class CommandLookupResult(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    status: Literal["recorded"] = "recorded"
    operation: str
    idempotency_key: str
    recorded_at: AwareDatetime
    result: RecordedCommandResult
