"""Governed telephony domain values shared by adapters and audit tooling."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentState(StrEnum):
    LOGGED_OUT = "LOGGED_OUT"
    LOGIN_PENDING = "LOGIN_PENDING"
    READY = "READY"
    RINGING = "RINGING"
    INCALL = "INCALL"
    PAUSED = "PAUSED"
    WRAP_UP = "WRAP_UP"
    MANUAL_DIAL = "MANUAL_DIAL"
    CALLBACK = "CALLBACK"
    TRANSFER = "TRANSFER"
    ERROR = "ERROR"
    FORCED_LOGOUT = "FORCED_LOGOUT"


class CampaignType(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND_MANUAL = "OUTBOUND_MANUAL"
    OUTBOUND_PREVIEW = "OUTBOUND_PREVIEW"
    OUTBOUND_PROGRESSIVE = "OUTBOUND_PROGRESSIVE"
    OUTBOUND_PREDICTIVE = "OUTBOUND_PREDICTIVE"
    BLENDED = "BLENDED"
    CALLBACK = "CALLBACK"
    INTERNAL_TEST = "INTERNAL_TEST"


class CallbackState(StrEnum):
    REQUESTED = "REQUESTED"
    SCHEDULED = "SCHEDULED"
    DUE = "DUE"
    ASSIGNED = "ASSIGNED"
    CALLING = "CALLING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


class TransferType(StrEnum):
    WARM_TRANSFER = "WARM_TRANSFER"
    COLD_TRANSFER = "COLD_TRANSFER"
    QUEUE_TRANSFER = "QUEUE_TRANSFER"
    SUPERVISOR_TRANSFER = "SUPERVISOR_TRANSFER"
    EXTERNAL_APPROVED_TRANSFER = "EXTERNAL_APPROVED_TRANSFER"


class RecordingPolicy(StrEnum):
    ALWAYS = "ALWAYS"
    ON_ANSWER = "ON_ANSWER"
    ON_AGENT_CONNECT = "ON_AGENT_CONNECT"
    MANUAL_APPROVED = "MANUAL_APPROVED"
    DISABLED = "DISABLED"


class AgentStateChange(StrictModel):
    agent_id: str
    tenant_id: str
    workspace_id: str
    campaign_id: str
    previous_state: AgentState
    new_state: AgentState
    reason: str
    timestamp: datetime
    session_id: str
    source: str

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include timezone")
        return value.astimezone(timezone.utc)


class TelephonyEvent(StrictModel):
    event_id: UUID
    event_type: str = Field(pattern=r"^(call|recording|callback|agent|campaign)\.[a-z_]+$")
    schema_version: str = "1.0"
    tenant_id: str
    workspace_id: str
    campaign_id: str | None = None
    call_id: str | None = None
    agent_id: str | None = None
    lead_id: str | None = None
    timestamp: datetime
    correlation_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=8, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include timezone")
        return value.astimezone(timezone.utc)


class ExtensionRecord(StrictModel):
    extension: int = Field(ge=6100, le=6999)
    tenant: str
    workspace: str
    department: str
    campaign: str | None = None
    user: str | None = None
    device: str
    status: str
    purpose: str
    created_at: datetime
    retired_at: datetime | None = None

    @field_validator("extension")
    @classmethod
    def enforce_allocated_ranges(cls, value: int) -> int:
        if 6600 <= value <= 6899:
            raise ValueError("extension is outside an allocated Codestra range")
        return value

