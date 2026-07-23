from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime

from .config import DecisionPolicy
from .decision_engine import evaluate_decision
from .fleet_models import (
    PLANNER_VERSION,
    ActionPermission,
    AllocationStatus,
    FleetPlan,
    FleetSnapshot,
    FleetSummary,
    FleetVehicleInput,
    FleetVehiclePlan,
)
from .models import DecisionCode, DecisionOutput


REASONS = {
    AllocationStatus.SCHEDULED_SHADOW: "影子计划已分配批次与安全点；无车辆执行权限。",
    AllocationStatus.NO_CAPACITY: "高位安全点容量不足；保持原位并转人工协调。",
    AllocationStatus.WINDOW_CLOSED: "排队后二次计算显示最晚安全启动窗口已关闭；禁止迟发迁移。",
    AllocationStatus.SITE_UNAVAILABLE: "场端网关离线或观测过期；禁止形成迁移计划。",
}

DIRECT_STATUS = {
    DecisionCode.STAY: AllocationStatus.NOT_REQUIRED,
    DecisionCode.WATCH: AllocationStatus.NOT_REQUIRED,
    DecisionCode.PREPARE: AllocationStatus.PREPARE_ONLY,
    DecisionCode.VERIFY_ONLY: AllocationStatus.VERIFY_ONLY,
    DecisionCode.NO_GO: AllocationStatus.DENIED,
    DecisionCode.EMERGENCY_STOP: AllocationStatus.DENIED,
}

DENIED_STATUSES = {
    AllocationStatus.NO_CAPACITY,
    AllocationStatus.WINDOW_CLOSED,
    AllocationStatus.SITE_UNAVAILABLE,
    AllocationStatus.DENIED,
}


def canonical_fleet_snapshot_json(
    snapshot: FleetSnapshot | dict[str, object],
) -> str:
    normalized = FleetSnapshot.model_validate(snapshot)
    return json.dumps(
        normalized.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fleet_input_sha256(snapshot: FleetSnapshot | dict[str, object]) -> str:
    canonical = canonical_fleet_snapshot_json(snapshot)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def vehicle_policy(
    default: DecisionPolicy,
    snapshot: FleetSnapshot,
    vehicle: FleetVehicleInput,
    *,
    queue_ahead: int,
) -> DecisionPolicy:
    return replace(
        default,
        danger_water_level_cm=(
            default.danger_water_level_cm
            if vehicle.danger_water_level_cm is None
            else vehicle.danger_water_level_cm
        ),
        route_distance_m=(
            default.route_distance_m
            if vehicle.route_distance_m is None
            else vehicle.route_distance_m
        ),
        queue_ahead=queue_ahead,
        batch_size=snapshot.site.batch_size,
        batch_interval_min=snapshot.site.batch_interval_min,
    )


def _action_permission(status: AllocationStatus) -> ActionPermission:
    if status is AllocationStatus.SCHEDULED_SHADOW:
        return ActionPermission.SHADOW_ONLY
    if status in DENIED_STATUSES:
        return ActionPermission.DENIED
    return ActionPermission.NONE


def _vehicle_plan(
    *,
    vehicle_id: str,
    base_decision: DecisionOutput,
    status: AllocationStatus,
    rank: int | None = None,
    batch_index: int | None = None,
    queue_ahead: int | None = None,
    safe_point_id: str | None = None,
) -> FleetVehiclePlan:
    return FleetVehiclePlan(
        vehicle_id=vehicle_id,
        base_decision=base_decision,
        allocation_status=status,
        rank=rank,
        batch_index=batch_index,
        queue_ahead=queue_ahead,
        safe_point_id=safe_point_id,
        action_permission=_action_permission(status),
        authorized_to_move=False,
        reason=REASONS.get(status, base_decision.reason),
    )


def _available_slots(snapshot: FleetSnapshot) -> list[str]:
    slots: list[str] = []
    for point in sorted(
        (point for point in snapshot.site.safe_points if point.available),
        key=lambda point: (point.priority, point.safe_point_id),
    ):
        slots.extend([point.safe_point_id] * point.capacity)
    return slots


def _latest_start(decision: DecisionOutput) -> float:
    value = decision.timing.latest_safe_start_min
    return float("inf") if value is None else value


def _plan_hash(plan: FleetPlan) -> str:
    payload = plan.model_dump(
        mode="json",
        exclude={"run_id", "created_at", "duplicate", "plan_sha256"},
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_fleet(
    snapshot: FleetSnapshot,
    policy: DecisionPolicy,
    *,
    run_id: str,
    created_at: datetime,
    now: datetime,
    site_max_age_seconds: int,
) -> FleetPlan:
    evaluated: list[tuple[FleetVehicleInput, DecisionOutput]] = []
    for vehicle in snapshot.vehicles:
        base_decision = evaluate_decision(
            vehicle.telemetry,
            vehicle_policy(policy, snapshot, vehicle, queue_ahead=0),
            owner_authorized=False,
        )
        evaluated.append((vehicle, base_decision))

    candidates = sorted(
        (
            item
            for item in evaluated
            if item[1].decision is DecisionCode.MIGRATE_NOW
        ),
        key=lambda item: (
            _latest_start(item[1]),
            item[0].telemetry.vehicle_id,
        ),
    )
    non_candidates = sorted(
        (
            item
            for item in evaluated
            if item[1].decision is not DecisionCode.MIGRATE_NOW
        ),
        key=lambda item: item[0].telemetry.vehicle_id,
    )

    site_age_seconds = (now - snapshot.site.observed_at).total_seconds()
    site_available = (
        snapshot.site.gateway_online
        and site_age_seconds <= site_max_age_seconds
    )
    slots = _available_slots(snapshot)
    slot_index = 0
    candidate_plans: list[FleetVehiclePlan] = []

    for rank, (vehicle, base_decision) in enumerate(candidates, start=1):
        vehicle_id = vehicle.telemetry.vehicle_id
        if not site_available:
            candidate_plans.append(
                _vehicle_plan(
                    vehicle_id=vehicle_id,
                    base_decision=base_decision,
                    status=AllocationStatus.SITE_UNAVAILABLE,
                    rank=rank,
                )
            )
            continue

        if slot_index >= len(slots):
            candidate_plans.append(
                _vehicle_plan(
                    vehicle_id=vehicle_id,
                    base_decision=base_decision,
                    status=AllocationStatus.NO_CAPACITY,
                    rank=rank,
                )
            )
            continue

        allocated_point = slots[slot_index]
        slot_index += 1
        queue_ahead = rank - 1
        batch_index = queue_ahead // snapshot.site.batch_size + 1
        reevaluated = evaluate_decision(
            vehicle.telemetry,
            vehicle_policy(
                policy,
                snapshot,
                vehicle,
                queue_ahead=queue_ahead,
            ),
            owner_authorized=False,
        )
        if reevaluated.decision is not DecisionCode.MIGRATE_NOW:
            candidate_plans.append(
                _vehicle_plan(
                    vehicle_id=vehicle_id,
                    base_decision=base_decision,
                    status=AllocationStatus.WINDOW_CLOSED,
                    rank=rank,
                    batch_index=batch_index,
                    queue_ahead=queue_ahead,
                )
            )
            continue

        candidate_plans.append(
            _vehicle_plan(
                vehicle_id=vehicle_id,
                base_decision=base_decision,
                status=AllocationStatus.SCHEDULED_SHADOW,
                rank=rank,
                batch_index=batch_index,
                queue_ahead=queue_ahead,
                safe_point_id=allocated_point,
            )
        )

    direct_plans = [
        _vehicle_plan(
            vehicle_id=vehicle.telemetry.vehicle_id,
            base_decision=base_decision,
            status=DIRECT_STATUS[base_decision.decision],
        )
        for vehicle, base_decision in non_candidates
    ]
    vehicles = [*candidate_plans, *direct_plans]
    scheduled_count = sum(
        item.allocation_status is AllocationStatus.SCHEDULED_SHADOW
        for item in vehicles
    )
    summary = FleetSummary(
        vehicle_count=len(vehicles),
        scheduled_count=scheduled_count,
        verify_count=sum(
            item.allocation_status is AllocationStatus.VERIFY_ONLY
            for item in vehicles
        ),
        denied_count=sum(
            item.allocation_status in DENIED_STATUSES
            for item in vehicles
        ),
        remaining_capacity=len(slots) - scheduled_count,
    )
    plan = FleetPlan(
        run_id=run_id,
        snapshot_id=snapshot.snapshot_id,
        site_id=snapshot.site_id,
        source_mode=snapshot.source_mode,
        planner_version=PLANNER_VERSION,
        created_at=created_at,
        duplicate=False,
        input_sha256=fleet_input_sha256(snapshot),
        plan_sha256="0" * 64,
        summary=summary,
        vehicles=vehicles,
    )
    return plan.model_copy(update={"plan_sha256": _plan_hash(plan)})
