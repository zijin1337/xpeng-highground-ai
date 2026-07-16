from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


Identifier = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MotionState(str, Enum):
    PARKED = "PARKED"
    MOVING = "MOVING"


class DecisionCode(str, Enum):
    STAY = "STAY"
    WATCH = "WATCH"
    PREPARE = "PREPARE"
    MIGRATE_NOW = "MIGRATE_NOW"
    VERIFY_ONLY = "VERIFY_ONLY"
    NO_GO = "NO_GO"
    EMERGENCY_STOP = "EMERGENCY_STOP"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Permission(str, Enum):
    NONE = "NONE"
    AWAITING_OWNER = "AWAITING_OWNER"
    GRANTED = "GRANTED"
    DENIED = "DENIED"


class EnvironmentReading(BaseModel):
    rainfall_mm_h: float = Field(ge=0, le=500)
    water_level_cm: float = Field(ge=0, le=300)
    secondary_water_level_cm: float = Field(ge=0, le=300)
    rise_rate_cm_min: float = Field(ge=0, le=30)
    sensor_confidence: float = Field(ge=0, le=1)


class VehicleState(BaseModel):
    occupants_clear: bool
    charging_disconnected: bool
    vehicle_healthy: bool
    positioning_online: bool
    network_online: bool
    emergency_operator_online: bool
    water_contact_triggered: bool = False
    motion_state: MotionState = MotionState.PARKED


class SiteState(BaseModel):
    route_dry: bool
    route_blocked: bool


class TelemetryIn(BaseModel):
    message_id: Identifier = Field(default_factory=lambda: f"msg_{uuid4().hex}")
    site_id: Identifier
    vehicle_id: Identifier
    source_id: Identifier
    captured_at: datetime = Field(default_factory=utc_now)
    environment: EnvironmentReading
    vehicle: VehicleState
    site: SiteState

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return value.astimezone(timezone.utc)


class SafetyGate(BaseModel):
    id: str
    label: str
    passed: bool
    detail: str


class TimingResult(BaseModel):
    remaining_cm: float
    time_to_threshold_min: float | None
    route_time_min: float
    queue_time_min: float
    latest_safe_start_min: float | None


class DecisionOutput(BaseModel):
    decision: DecisionCode
    label: str
    risk_level: RiskLevel
    permission: Permission
    authorized_to_move: bool
    reason: str
    sensor_disagreement_cm: float
    timing: TimingResult
    safety_gates: list[SafetyGate]


class TelemetryDecisionResponse(BaseModel):
    event_id: str
    message_id: str
    duplicate: bool
    received_at: datetime
    input_sha256: str
    result: DecisionOutput


class EventDetail(BaseModel):
    event_id: str
    message_id: str
    received_at: datetime
    input_sha256: str
    telemetry: TelemetryIn
    result: DecisionOutput


class AuthorizationRequest(BaseModel):
    event_id: Identifier
    owner_id: Identifier


class AuthorizationResponse(BaseModel):
    authorization_id: str
    event_id: str
    authorization_token: str
    expires_at: datetime
    warning: str


class MigrationCommandRequest(BaseModel):
    event_id: Identifier
    authorization_token: str = Field(min_length=20, max_length=200)


class CommandResponse(BaseModel):
    command_id: str
    event_id: str
    status: Literal["RECORDED_NOT_SENT"]
    actuator_mode: Literal["record-only"]
    message: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]
    actuator_mode: str
