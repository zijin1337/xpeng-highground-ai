from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import DecisionOutput, Identifier, TelemetryIn


PLANNER_VERSION = "fleet-shadow-v1"


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(timezone.utc)


class SourceMode(str, Enum):
    SIMULATED = "SIMULATED"
    SHADOW = "SHADOW"


class AllocationStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PREPARE_ONLY = "PREPARE_ONLY"
    VERIFY_ONLY = "VERIFY_ONLY"
    SCHEDULED_SHADOW = "SCHEDULED_SHADOW"
    NO_CAPACITY = "NO_CAPACITY"
    WINDOW_CLOSED = "WINDOW_CLOSED"
    SITE_UNAVAILABLE = "SITE_UNAVAILABLE"
    DENIED = "DENIED"


class ActionPermission(str, Enum):
    NONE = "NONE"
    SHADOW_ONLY = "SHADOW_ONLY"
    DENIED = "DENIED"


class SafePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safe_point_id: Identifier
    label: str = Field(min_length=1, max_length=80)
    priority: int = Field(ge=0, le=1000)
    capacity: int = Field(ge=1, le=50)
    available: bool = True


class FleetSiteState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    gateway_online: bool
    batch_size: int = Field(ge=1, le=50)
    batch_interval_min: float = Field(ge=0, le=60)
    safe_points: list[SafePoint] = Field(min_length=1, max_length=50)

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_aware(cls, value: datetime) -> datetime:
        return _aware_utc(value, "observed_at")

    @model_validator(mode="after")
    def safe_point_ids_are_unique(self) -> "FleetSiteState":
        ids = [point.safe_point_id for point in self.safe_points]
        if len(ids) != len(set(ids)):
            raise ValueError("safe_point_id values must be unique")
        return self


class FleetVehicleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telemetry: TelemetryIn
    danger_water_level_cm: float | None = Field(default=None, ge=1, le=300)
    route_distance_m: float | None = Field(default=None, ge=1, le=10000)


class FleetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: Identifier
    site_id: Identifier
    captured_at: datetime
    source_mode: SourceMode
    site: FleetSiteState
    vehicles: list[FleetVehicleInput] = Field(min_length=1, max_length=50)

    @field_validator("captured_at")
    @classmethod
    def captured_at_is_aware(cls, value: datetime) -> datetime:
        return _aware_utc(value, "captured_at")

    @model_validator(mode="after")
    def vehicle_scope_is_consistent(self) -> "FleetSnapshot":
        vehicle_ids = [item.telemetry.vehicle_id for item in self.vehicles]
        if len(vehicle_ids) != len(set(vehicle_ids)):
            raise ValueError("vehicle_id values must be unique within a fleet snapshot")
        if any(item.telemetry.site_id != self.site_id for item in self.vehicles):
            raise ValueError("every telemetry.site_id must equal snapshot site_id")
        return self


class FleetVehiclePlan(BaseModel):
    vehicle_id: Identifier
    base_decision: DecisionOutput
    allocation_status: AllocationStatus
    rank: int | None = Field(default=None, ge=1, le=50)
    batch_index: int | None = Field(default=None, ge=1, le=50)
    queue_ahead: int | None = Field(default=None, ge=0, le=49)
    safe_point_id: Identifier | None = None
    action_permission: ActionPermission
    authorized_to_move: Literal[False] = False
    reason: str = Field(min_length=1, max_length=500)


class FleetSummary(BaseModel):
    vehicle_count: int = Field(ge=1, le=50)
    scheduled_count: int = Field(ge=0, le=50)
    verify_count: int = Field(ge=0, le=50)
    denied_count: int = Field(ge=0, le=50)
    remaining_capacity: int = Field(ge=0, le=2500)


class FleetPlan(BaseModel):
    run_id: Identifier
    snapshot_id: Identifier
    site_id: Identifier
    source_mode: SourceMode
    planner_version: Literal["fleet-shadow-v1"] = PLANNER_VERSION
    created_at: datetime
    duplicate: bool = False
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: FleetSummary
    vehicles: list[FleetVehiclePlan] = Field(min_length=1, max_length=50)

    @field_validator("created_at")
    @classmethod
    def created_at_is_aware(cls, value: datetime) -> datetime:
        return _aware_utc(value, "created_at")
