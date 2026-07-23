from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

from backend.app.config import DecisionPolicy
from backend.app.fleet_models import FleetSnapshot
from backend.app.fleet_planner import canonical_fleet_snapshot_json, plan_fleet
from backend.tests.fleet_fixtures import (
    FIXED_NOW,
    make_fleet_snapshot,
    make_fleet_vehicle,
)


def build_plan(body: dict[str, object], *, run_id: str = "fleet-run-test"):
    return plan_fleet(
        FleetSnapshot.model_validate(body),
        DecisionPolicy(),
        run_id=run_id,
        created_at=FIXED_NOW,
        now=FIXED_NOW,
        site_max_age_seconds=300,
    )


def test_single_vehicle_decisions_map_to_fleet_statuses_without_authorization() -> None:
    body = make_fleet_snapshot(
        vehicles=[
            make_fleet_vehicle("vehicle-stay"),
            make_fleet_vehicle(
                "vehicle-prepare",
                water_level_cm=10,
                secondary_water_level_cm=10,
                rise_rate_cm_min=0.7,
            ),
            make_fleet_vehicle("vehicle-verify", sensor_confidence=0.55),
            make_fleet_vehicle("vehicle-denied", route_dry=False),
            make_fleet_vehicle(
                "vehicle-migrate",
                water_level_cm=14,
                secondary_water_level_cm=14,
                rise_rate_cm_min=1,
            ),
        ]
    )

    plan = build_plan(body)
    by_id = {item.vehicle_id: item for item in plan.vehicles}

    assert by_id["vehicle-stay"].allocation_status.value == "NOT_REQUIRED"
    assert by_id["vehicle-prepare"].allocation_status.value == "PREPARE_ONLY"
    assert by_id["vehicle-verify"].allocation_status.value == "VERIFY_ONLY"
    assert by_id["vehicle-denied"].allocation_status.value == "DENIED"
    assert by_id["vehicle-migrate"].allocation_status.value == "SCHEDULED_SHADOW"
    assert by_id["vehicle-migrate"].base_decision.permission.value == "AWAITING_OWNER"
    assert by_id["vehicle-migrate"].action_permission.value == "SHADOW_ONLY"
    assert all(item.authorized_to_move is False for item in plan.vehicles)


def test_migrate_candidates_rank_by_window_then_vehicle_id() -> None:
    body = make_fleet_snapshot(
        vehicles=[
            make_fleet_vehicle(
                "vehicle-b",
                water_level_cm=14,
                secondary_water_level_cm=14,
                rise_rate_cm_min=1,
            ),
            make_fleet_vehicle(
                "vehicle-a",
                water_level_cm=14,
                secondary_water_level_cm=14,
                rise_rate_cm_min=1,
            ),
        ]
    )

    plan = build_plan(body)

    assert [(item.vehicle_id, item.rank) for item in plan.vehicles] == [
        ("vehicle-a", 1),
        ("vehicle-b", 2),
    ]
    assert [item.safe_point_id for item in plan.vehicles] == ["high-a", "high-b"]
    assert [item.queue_ahead for item in plan.vehicles] == [0, 1]
    assert [item.batch_index for item in plan.vehicles] == [1, 2]


def test_safe_points_sort_by_priority_then_identifier() -> None:
    body = make_fleet_snapshot(
        safe_points=[
            {
                "safe_point_id": "high-z",
                "label": "高位 Z",
                "priority": 1,
                "capacity": 1,
                "available": True,
            },
            {
                "safe_point_id": "high-a",
                "label": "高位 A",
                "priority": 1,
                "capacity": 1,
                "available": True,
            },
        ],
        vehicles=[
            make_fleet_vehicle(
                "vehicle-a",
                water_level_cm=14,
                secondary_water_level_cm=14,
                rise_rate_cm_min=1,
            ),
            make_fleet_vehicle(
                "vehicle-b",
                water_level_cm=14,
                secondary_water_level_cm=14,
                rise_rate_cm_min=1,
            ),
        ],
    )

    plan = build_plan(body)

    assert [item.safe_point_id for item in plan.vehicles] == ["high-a", "high-z"]


def test_capacity_exhaustion_is_an_auditable_denial() -> None:
    body = make_fleet_snapshot(
        safe_points=[
            {
                "safe_point_id": "high-a",
                "label": "高位 A",
                "priority": 1,
                "capacity": 1,
                "available": True,
            }
        ],
        vehicles=[
            make_fleet_vehicle(
                "vehicle-a",
                water_level_cm=14,
                secondary_water_level_cm=14,
                rise_rate_cm_min=1,
            ),
            make_fleet_vehicle(
                "vehicle-b",
                water_level_cm=14,
                secondary_water_level_cm=14,
                rise_rate_cm_min=1,
            ),
        ],
    )

    plan = build_plan(body)

    assert [item.allocation_status.value for item in plan.vehicles] == [
        "SCHEDULED_SHADOW",
        "NO_CAPACITY",
    ]
    assert [item.action_permission.value for item in plan.vehicles] == [
        "SHADOW_ONLY",
        "DENIED",
    ]
    assert plan.summary.scheduled_count == 1
    assert plan.summary.denied_count == 1
    assert plan.summary.remaining_capacity == 0


def test_queue_recheck_closes_late_batch_without_backfill() -> None:
    body = make_fleet_snapshot(
        batch_size=1,
        batch_interval_min=7,
        vehicles=[
            make_fleet_vehicle(
                vehicle_id,
                water_level_cm=10,
                secondary_water_level_cm=10,
                rise_rate_cm_min=1,
            )
            for vehicle_id in ("vehicle-a", "vehicle-b", "vehicle-c")
        ],
    )

    plan = build_plan(body)
    by_id = {item.vehicle_id: item for item in plan.vehicles}

    assert by_id["vehicle-a"].allocation_status.value == "SCHEDULED_SHADOW"
    assert by_id["vehicle-b"].allocation_status.value == "WINDOW_CLOSED"
    assert by_id["vehicle-b"].queue_ahead == 1
    assert by_id["vehicle-b"].batch_index == 2
    assert by_id["vehicle-b"].safe_point_id is None
    assert by_id["vehicle-c"].allocation_status.value == "NO_CAPACITY"
    assert plan.summary.remaining_capacity == 1


def test_offline_or_stale_site_refuses_only_migration_candidates() -> None:
    vehicles = [
        make_fleet_vehicle(
            "vehicle-a",
            water_level_cm=14,
            secondary_water_level_cm=14,
            rise_rate_cm_min=1,
        ),
        make_fleet_vehicle("vehicle-b"),
    ]
    offline = make_fleet_snapshot(gateway_online=False, vehicles=vehicles)
    plan = build_plan(offline)
    by_id = {item.vehicle_id: item for item in plan.vehicles}
    assert by_id["vehicle-a"].allocation_status.value == "SITE_UNAVAILABLE"
    assert by_id["vehicle-b"].allocation_status.value == "NOT_REQUIRED"
    assert plan.summary.remaining_capacity == 2

    stale = make_fleet_snapshot(
        observed_at=FIXED_NOW - timedelta(seconds=301),
        vehicles=vehicles,
    )
    assert build_plan(stale).vehicles[0].allocation_status.value == "SITE_UNAVAILABLE"


def test_plan_and_input_hashes_are_stable_across_volatile_fields() -> None:
    body = make_fleet_snapshot()
    snapshot = FleetSnapshot.model_validate(body)
    first = build_plan(body)
    second = plan_fleet(
        FleetSnapshot.model_validate(deepcopy(body)),
        DecisionPolicy(),
        run_id="different-run-id",
        created_at=FIXED_NOW + timedelta(minutes=5),
        now=FIXED_NOW,
        site_max_age_seconds=300,
    )

    assert canonical_fleet_snapshot_json(snapshot)
    assert first.input_sha256 == second.input_sha256
    assert first.plan_sha256 == second.plan_sha256
    assert len(first.input_sha256) == len(first.plan_sha256) == 64
    assert first.run_id != second.run_id
    assert first.created_at != second.created_at

    changed = deepcopy(body)
    changed["vehicles"][0]["telemetry"]["environment"]["water_level_cm"] = 5
    assert build_plan(changed).input_sha256 != first.input_sha256
