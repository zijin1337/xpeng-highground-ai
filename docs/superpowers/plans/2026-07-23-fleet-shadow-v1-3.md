# Fleet Shadow v1.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a six-vehicle, six-stage fleet shadow exercise with deterministic scheduling, SQLite evidence, authenticated FastAPI read/write APIs, and a default fleet Web dashboard that never authorizes or controls a vehicle.

**Architecture:** Add a pure Python fleet planner above the existing single-vehicle `evaluate_decision` function, then persist immutable runs and per-vehicle plans in the existing SQLite database. Mirror the same deterministic rules in a browser-only JavaScript planner using one shared JSON scenario; the Web layer labels browser output as simulated and only displays run IDs and SHA-256 evidence returned by FastAPI.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite, pytest, Node.js 20+, native ES modules, Node test runner, HTML/CSS, Android Gradle regression checks.

---

## Scope And Invariants

- This plan implements only phase A: the repository-verifiable fleet evaluation and demo loop.
- Phase B gateway engineering and phase C Android fleet UI require separate specifications and plans.
- `source_mode` accepts only `SIMULATED` and `SHADOW`; there is no `LIVE_CONTROL` value.
- The fleet path never accepts owner authorization, never writes `authorizations` or `commands`, and never calls the actuator.
- Every fleet vehicle result has `authorized_to_move = false`; only `SCHEDULED_SHADOW` has `action_permission = SHADOW_ONLY`.
- Browser output has no server run ID and no server SHA-256. Those fields appear only after a successful API response.
- Existing telemetry, authorization, record-only command, Web console, and Android contracts remain intact.
- Keep the existing untracked `.superpowers/` companion directory out of every commit.

## File Map

Create these focused modules and tests:

- `backend/app/fleet_models.py`: Pydantic input/output contracts and fleet enums.
- `backend/app/fleet_planner.py`: canonical JSON, hashes, single-car reuse, ranking, capacity, batch, and refusal logic.
- `backend/tests/fleet_fixtures.py`: deterministic builders and shared scenario loader used by Python tests.
- `backend/tests/test_fleet_models.py`: schema, scope, and forbidden-field tests.
- `backend/tests/test_fleet_planner.py`: pure planner behavior and hash tests.
- `backend/tests/test_fleet_database.py`: schema, idempotency, retrieval, and rollback tests.
- `backend/tests/test_fleet_api.py`: API status, freshness, authentication, and actuator-isolation tests.
- `backend/tests/test_fleet_scenario.py`: Python assertions against all six shared stages.
- `backend/tests/test_fleet_demo.py`: API-backed evidence runner test.
- `src/fleet-planner.js`: browser implementation of the approved deterministic planner.
- `src/fleet-scenario.js`: load and validate the shared scenario JSON for the browser.
- `src/fleet-view-state.js`: request generations, evidence-source state, and stale-state transitions.
- `src/fleet-view.js`: view switching, replay, rendering, filtering, and fleet API coordination.
- `tests/fleet-planner.test.mjs`: JavaScript scenario contract tests.
- `tests/fleet-planner-cli.mjs`: JSON projection bridge for direct Python/JavaScript parity checks.
- `tests/fleet-view-state.test.mjs`: race, stale, and evidence-label tests.
- `demo/scenarios/fleet-rainstorm-v1.json`: six immutable snapshots and expected projections.
- `demo/run_fleet_scenario.py`: repeatable API-backed evidence generator.

Modify these existing files without moving their current responsibilities:

- `backend/app/database.py`: fleet tables and fleet run persistence methods.
- `backend/app/main.py`: three fleet routes, freshness checks, static scenario mount, and version bump.
- `backend/tests/test_web_contract.py`: retain every single-car ID and assert the new fleet DOM contract.
- `index.html`: segmented view switch plus the fleet dashboard markup.
- `styles.css`: fleet layout, states, responsive ordering, and accessibility treatments.
- `src/app.js`: expose the existing API connection state to the fleet module without adding planner logic.
- `benchmarks/run_benchmark.py`: add fleet correctness and local API latency reporting.
- `backend/tests/test_benchmark.py`: assert the fleet benchmark section.
- `README.md`, `docs/DEMO.md`, `docs/BENCHMARK.md`: document operation, evidence, and limitations.
- `package.json`: bump the repository version to `1.3.0`.
- `assets/highground-demo.png`: replace the first-view screenshot after desktop visual QA passes.

## Task 1: Fleet Contracts And Test Builders

**Files:**
- Create: `backend/app/fleet_models.py`
- Create: `backend/tests/fleet_fixtures.py`
- Create: `backend/tests/test_fleet_models.py`

- [ ] **Step 1: Create the isolated Python test environment and record the baseline**

Run from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
npm test
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Expected: the current 21 Node tests pass, the existing Python suite passes, and no fleet files exist yet. If `.venv` already exists, omit only the `py -3.12 -m venv .venv` command.

- [ ] **Step 2: Write failing schema tests**

Create `backend/tests/test_fleet_models.py` with concrete validation cases:

```python
from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.fleet_models import FleetSnapshot, SourceMode
from backend.tests.fleet_fixtures import make_fleet_snapshot


def test_valid_snapshot_has_only_shadow_source_modes() -> None:
    snapshot = FleetSnapshot.model_validate(make_fleet_snapshot())
    assert snapshot.source_mode is SourceMode.SIMULATED
    assert len(snapshot.vehicles) == 2

    shadow_body = make_fleet_snapshot()
    shadow_body["source_mode"] = "SHADOW"
    assert FleetSnapshot.model_validate(shadow_body).source_mode is SourceMode.SHADOW


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda body: body.update(source_mode="LIVE_CONTROL"), "source_mode"),
        (lambda body: body["vehicles"].append(deepcopy(body["vehicles"][0])), "vehicle_id"),
        (lambda body: body["vehicles"][0]["telemetry"].update(site_id="other-site"), "site_id"),
        (lambda body: body.update(owner_authorized=True), "Extra inputs are not permitted"),
    ],
)
def test_snapshot_rejects_unsafe_or_ambiguous_input(mutation, message: str) -> None:
    body = make_fleet_snapshot()
    mutation(body)
    with pytest.raises(ValidationError, match=message):
        FleetSnapshot.model_validate(body)


def test_snapshot_requires_one_to_fifty_vehicles() -> None:
    empty = make_fleet_snapshot()
    empty["vehicles"] = []
    with pytest.raises(ValidationError, match="at least 1"):
        FleetSnapshot.model_validate(empty)

    oversized = make_fleet_snapshot()
    oversized["vehicles"] = [
        deepcopy(oversized["vehicles"][0]) for _ in range(51)
    ]
    for index, vehicle in enumerate(oversized["vehicles"]):
        vehicle["telemetry"]["vehicle_id"] = f"vehicle-{index:02d}"
        vehicle["telemetry"]["message_id"] = f"message-{index:02d}"
    with pytest.raises(ValidationError, match="at most 50"):
        FleetSnapshot.model_validate(oversized)
```

- [ ] **Step 3: Run the schema tests and confirm the missing-module failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_models.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'backend.app.fleet_models'`.

- [ ] **Step 4: Implement the Pydantic contracts**

Create `backend/app/fleet_models.py`. Use `ConfigDict(extra="forbid")` on every new input model, reuse `Identifier`, `TelemetryIn`, and `DecisionOutput`, and define these exact public names:

```python
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
        mismatched = [
            item.telemetry.vehicle_id
            for item in self.vehicles
            if item.telemetry.site_id != self.site_id
        ]
        if mismatched:
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
```

- [ ] **Step 5: Add deterministic fleet test builders**

Create `backend/tests/fleet_fixtures.py` with a fixed UTC instant and builders that return JSON-compatible dictionaries. Keep every vehicle parked, unoccupied, disconnected from charging, healthy, online, and on a dry/open route unless a test explicitly overrides it.

```python
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


FIXED_NOW = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[2]
FLEET_SCENARIO_PATH = REPO_ROOT / "demo" / "scenarios" / "fleet-rainstorm-v1.json"


def make_fleet_vehicle(
    vehicle_id: str,
    *,
    water_level_cm: float = 4,
    secondary_water_level_cm: float = 4,
    rise_rate_cm_min: float = 0.2,
    rainfall_mm_h: float = 35,
    sensor_confidence: float = 0.94,
    route_dry: bool = True,
    route_blocked: bool = False,
) -> dict[str, object]:
    return {
        "telemetry": {
            "message_id": f"msg-{vehicle_id}",
            "site_id": "garage-fleet-01",
            "vehicle_id": vehicle_id,
            "source_id": "fleet-fixture",
            "captured_at": FIXED_NOW.isoformat(),
            "environment": {
                "rainfall_mm_h": rainfall_mm_h,
                "water_level_cm": water_level_cm,
                "secondary_water_level_cm": secondary_water_level_cm,
                "rise_rate_cm_min": rise_rate_cm_min,
                "sensor_confidence": sensor_confidence,
            },
            "vehicle": {
                "occupants_clear": True,
                "charging_disconnected": True,
                "vehicle_healthy": True,
                "positioning_online": True,
                "network_online": True,
                "emergency_operator_online": True,
                "water_contact_triggered": False,
                "motion_state": "PARKED",
            },
            "site": {"route_dry": route_dry, "route_blocked": route_blocked},
        },
        "danger_water_level_cm": None,
        "route_distance_m": None,
    }


def make_fleet_snapshot() -> dict[str, object]:
    return {
        "snapshot_id": "snapshot-fixture-01",
        "site_id": "garage-fleet-01",
        "captured_at": FIXED_NOW.isoformat(),
        "source_mode": "SIMULATED",
        "site": {
            "observed_at": FIXED_NOW.isoformat(),
            "gateway_online": True,
            "batch_size": 1,
            "batch_interval_min": 0.7,
            "safe_points": [
                {
                    "safe_point_id": "high-a",
                    "label": "高位 A",
                    "priority": 1,
                    "capacity": 1,
                    "available": True,
                },
                {
                    "safe_point_id": "high-b",
                    "label": "高位 B",
                    "priority": 2,
                    "capacity": 1,
                    "available": True,
                },
            ],
        },
        "vehicles": [make_fleet_vehicle("vehicle-a"), make_fleet_vehicle("vehicle-b")],
    }


def load_fleet_scenario() -> dict[str, object]:
    return json.loads(FLEET_SCENARIO_PATH.read_text(encoding="utf-8"))


def cloned_snapshot() -> dict[str, object]:
    return deepcopy(make_fleet_snapshot())
```

- [ ] **Step 6: Run contract tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_models.py -q
```

Expected: all fleet model tests pass.

Commit:

```powershell
git add backend/app/fleet_models.py backend/tests/fleet_fixtures.py backend/tests/test_fleet_models.py
git commit -m "feat: define fleet shadow contracts"
```

## Task 2: Deterministic Python Fleet Planner

**Files:**
- Create: `backend/app/fleet_planner.py`
- Create: `backend/tests/test_fleet_planner.py`

- [ ] **Step 1: Write failing planner tests for mapping, ordering, capacity, and safety**

Create `backend/tests/test_fleet_planner.py`. Use fixed `run_id`, `created_at`, and `now` values so only the intended input changes between assertions.

```python
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

from backend.app.config import DecisionPolicy
from backend.app.fleet_models import FleetSnapshot
from backend.app.fleet_planner import canonical_fleet_snapshot_json, plan_fleet
from backend.tests.fleet_fixtures import FIXED_NOW, make_fleet_snapshot, make_fleet_vehicle


def build_plan(body: dict[str, object]):
    return plan_fleet(
        FleetSnapshot.model_validate(body),
        DecisionPolicy(),
        run_id="fleet-run-test",
        created_at=FIXED_NOW,
        now=FIXED_NOW,
        site_max_age_seconds=300,
    )


def test_migrate_candidates_rank_by_window_then_vehicle_id() -> None:
    body = make_fleet_snapshot()
    body["vehicles"] = [
        make_fleet_vehicle("vehicle-b", water_level_cm=14, rise_rate_cm_min=1),
        make_fleet_vehicle("vehicle-a", water_level_cm=14, rise_rate_cm_min=1),
    ]
    plan = build_plan(body)
    assert [(item.vehicle_id, item.rank) for item in plan.vehicles] == [
        ("vehicle-a", 1),
        ("vehicle-b", 2),
    ]
    assert [item.safe_point_id for item in plan.vehicles] == ["high-a", "high-b"]
    assert all(item.authorized_to_move is False for item in plan.vehicles)


def test_capacity_exhaustion_is_an_auditable_denial() -> None:
    body = make_fleet_snapshot()
    body["site"]["safe_points"] = [body["site"]["safe_points"][0]]
    body["vehicles"] = [
        make_fleet_vehicle("vehicle-a", water_level_cm=14, rise_rate_cm_min=1),
        make_fleet_vehicle("vehicle-b", water_level_cm=14, rise_rate_cm_min=1),
    ]
    plan = build_plan(body)
    assert [item.allocation_status.value for item in plan.vehicles] == [
        "SCHEDULED_SHADOW",
        "NO_CAPACITY",
    ]
    assert [item.action_permission.value for item in plan.vehicles] == [
        "SHADOW_ONLY",
        "DENIED",
    ]


def test_queue_recheck_closes_late_batch_without_backfill() -> None:
    body = make_fleet_snapshot()
    body["site"]["batch_interval_min"] = 7
    body["vehicles"] = [
        make_fleet_vehicle("vehicle-a", water_level_cm=10, rise_rate_cm_min=1),
        make_fleet_vehicle("vehicle-b", water_level_cm=10, rise_rate_cm_min=1),
        make_fleet_vehicle("vehicle-c", water_level_cm=10, rise_rate_cm_min=1),
    ]
    plan = build_plan(body)
    by_id = {item.vehicle_id: item for item in plan.vehicles}
    assert by_id["vehicle-a"].allocation_status.value == "SCHEDULED_SHADOW"
    assert by_id["vehicle-b"].allocation_status.value == "WINDOW_CLOSED"
    assert by_id["vehicle-c"].allocation_status.value == "NO_CAPACITY"
    assert by_id["vehicle-b"].safe_point_id is None
    assert plan.summary.remaining_capacity == 1


def test_offline_or_stale_site_refuses_only_migration_candidates() -> None:
    body = make_fleet_snapshot()
    body["site"]["gateway_online"] = False
    body["vehicles"] = [
        make_fleet_vehicle("vehicle-a", water_level_cm=14, rise_rate_cm_min=1),
        make_fleet_vehicle("vehicle-b"),
    ]
    plan = build_plan(body)
    assert [item.allocation_status.value for item in plan.vehicles] == [
        "SITE_UNAVAILABLE",
        "NOT_REQUIRED",
    ]

    stale = deepcopy(body)
    stale["site"]["gateway_online"] = True
    stale["site"]["observed_at"] = (FIXED_NOW - timedelta(seconds=301)).isoformat()
    assert build_plan(stale).vehicles[0].allocation_status.value == "SITE_UNAVAILABLE"


def test_plan_and_input_hashes_are_stable_and_sha256_sized() -> None:
    body = make_fleet_snapshot()
    first = build_plan(body)
    second = plan_fleet(
        FleetSnapshot.model_validate(deepcopy(body)),
        DecisionPolicy(),
        run_id="different-run-id",
        created_at=FIXED_NOW + timedelta(minutes=5),
        now=FIXED_NOW,
        site_max_age_seconds=300,
    )
    assert canonical_fleet_snapshot_json(first_snapshot := FleetSnapshot.model_validate(body))
    assert first.input_sha256 == second.input_sha256
    assert first.plan_sha256 == second.plan_sha256
    assert len(first.input_sha256) == len(first.plan_sha256) == 64
    assert first_snapshot.snapshot_id == first.snapshot_id


def test_safe_points_sort_by_priority_then_identifier() -> None:
    body = make_fleet_snapshot()
    body["site"]["safe_points"] = [
        {**body["site"]["safe_points"][1], "priority": 1, "safe_point_id": "high-z"},
        {**body["site"]["safe_points"][0], "priority": 1, "safe_point_id": "high-a"},
    ]
    body["vehicles"] = [
        make_fleet_vehicle("vehicle-a", water_level_cm=14, rise_rate_cm_min=1),
        make_fleet_vehicle("vehicle-b", water_level_cm=14, rise_rate_cm_min=1),
    ]
    plan = build_plan(body)
    assert [item.safe_point_id for item in plan.vehicles] == ["high-a", "high-z"]
```

- [ ] **Step 2: Run planner tests and verify the missing-module failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_planner.py -q
```

Expected: collection fails because `backend.app.fleet_planner` does not exist.

- [ ] **Step 3: Implement canonical hashing and per-vehicle policy construction**

Create `backend/app/fleet_planner.py` with these exact public functions:

```python
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime

from .config import DecisionPolicy
from .decision_engine import evaluate_decision
from .fleet_models import FleetPlan, FleetSnapshot, FleetVehiclePlan


def canonical_fleet_snapshot_json(snapshot: FleetSnapshot | dict[str, object]) -> str:
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
    vehicle,
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
```

Canonical plan hashing must serialize the final response after excluding exactly `run_id`, `created_at`, `duplicate`, and `plan_sha256`. Include `input_sha256`, the planner version, summary, and ordered vehicle plans in the plan hash. Build the validated plan first with `plan_sha256="0" * 64`, then calculate and replace the hash as follows:

```python
hash_payload = plan.model_dump(
    mode="json",
    exclude={"run_id", "created_at", "duplicate", "plan_sha256"},
)
canonical_plan = json.dumps(
    hash_payload,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
plan_sha256 = hashlib.sha256(canonical_plan.encode("utf-8")).hexdigest()
return plan.model_copy(update={"plan_sha256": plan_sha256})
```

- [ ] **Step 4: Implement the approved one-pass scheduler**

Implement `plan_fleet(snapshot: FleetSnapshot, policy: DecisionPolicy, *, run_id: str, created_at: datetime, now: datetime, site_max_age_seconds: int) -> FleetPlan` with the following rule order:

Within the function:

1. Evaluate every vehicle once with `queue_ahead=0` and `owner_authorized=False`.
2. Map `STAY` and `WATCH` to `NOT_REQUIRED`, `PREPARE` to `PREPARE_ONLY`, `VERIFY_ONLY` to `VERIFY_ONLY`, and `NO_GO` or `EMERGENCY_STOP` to `DENIED`.
3. Sort only `MIGRATE_NOW` candidates by `(latest_safe_start_min, vehicle_id)` and assign one-based ranks.
4. Expand available safe points into slots sorted by `(priority, safe_point_id)`; a capacity-two point contributes two consecutive slots.
5. If the gateway is offline or `now - site.observed_at` exceeds `site_max_age_seconds`, give every migration candidate `SITE_UNAVAILABLE` and allocate no slot.
6. Otherwise consume slots in ranked order. When no unconsumed slot remains, return `NO_CAPACITY`.
7. For a consumed slot, set `queue_ahead = rank - 1` and `batch_index = floor(queue_ahead / batch_size) + 1`, then reevaluate through `evaluate_decision` with that queue.
8. If the second evaluation is not `MIGRATE_NOW`, return `WINDOW_CLOSED`, clear `safe_point_id`, mark the consumed slot as released for `remaining_capacity`, and do not return it to the slot iterator.
9. Successful allocations return `SCHEDULED_SHADOW` and `SHADOW_ONLY`; `NO_CAPACITY`, `WINDOW_CLOSED`, `SITE_UNAVAILABLE`, and `DENIED` return `DENIED`; other statuses return `NONE`.
10. Sort the output vehicle list by candidate rank first and then by `vehicle_id`; non-candidates follow in `vehicle_id` order with `rank=None`.
11. Count only `VERIFY_ONLY` in `verify_count`; count the four refusal statuses plus `DENIED` in `denied_count`; compute `remaining_capacity` as total available capacity minus successful scheduled allocations.
12. Set every `authorized_to_move` field to `False` and never import `actuator`, authorization, command, database, HTTP, or UI modules.

Use these exact refusal reasons so Python, JavaScript, and the fixed scenario can compare stable text categories without implying vehicle execution:

```python
REASONS = {
    "SCHEDULED_SHADOW": "影子计划已分配批次与安全点；无车辆执行权限。",
    "NO_CAPACITY": "高位安全点容量不足；保持原位并转人工协调。",
    "WINDOW_CLOSED": "排队后二次计算显示最晚安全启动窗口已关闭；禁止迟发迁移。",
    "SITE_UNAVAILABLE": "场端网关离线或观测过期；禁止形成迁移计划。",
}
```

- [ ] **Step 5: Run focused planner tests and all Python decision tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_models.py backend\tests\test_fleet_planner.py backend\tests\test_decision_engine.py -q
```

Expected: all tests pass; existing single-car decision tests remain unchanged.

- [ ] **Step 6: Commit the pure planner**

```powershell
git add backend/app/fleet_planner.py backend/tests/test_fleet_planner.py
git commit -m "feat: add deterministic fleet shadow planner"
```

## Task 3: Atomic SQLite Fleet Evidence

**Files:**
- Modify: `backend/app/database.py:12-413`
- Create: `backend/tests/test_fleet_database.py`

- [ ] **Step 1: Write failing database tests**

Create `backend/tests/test_fleet_database.py` covering first save, identical retry, conflicting retry, retrieval, foreign-key rows, connection closure, and rollback. The central happy-path test must use the public methods planned below:

```python
import sqlite3
from contextlib import closing
from copy import deepcopy

import pytest

from backend.app.config import DecisionPolicy
from backend.app.database import Database, FleetSnapshotConflictError
from backend.app.fleet_models import FleetSnapshot
from backend.app.fleet_planner import plan_fleet
from backend.tests.fleet_fixtures import FIXED_NOW, make_fleet_snapshot


def save_fixture(database: Database, body: dict[str, object]):
    snapshot = FleetSnapshot.model_validate(body)
    plan = plan_fleet(
        snapshot,
        DecisionPolicy(),
        run_id="fleet-run-db-01",
        created_at=FIXED_NOW,
        now=FIXED_NOW,
        site_max_age_seconds=300,
    )
    return database.save_fleet_run(snapshot, plan)


def test_fleet_run_is_saved_atomically_and_loaded_by_run_and_site(tmp_path) -> None:
    database = Database(tmp_path / "fleet.db")
    database.initialize()
    stored = save_fixture(database, make_fleet_snapshot())

    assert stored.duplicate is False
    assert database.get_fleet_run(stored.plan.run_id).plan == stored.plan
    assert database.get_latest_fleet_run("garage-fleet-01").plan.run_id == stored.plan.run_id
    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM fleet_runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM fleet_vehicle_plans").fetchone()[0] == 2


def test_snapshot_id_is_idempotent_but_conflicting_content_is_rejected(tmp_path) -> None:
    database = Database(tmp_path / "fleet.db")
    database.initialize()
    body = make_fleet_snapshot()
    first = save_fixture(database, body)
    retry = save_fixture(database, deepcopy(body))
    assert retry.duplicate is True
    assert retry.plan.run_id == first.plan.run_id

    conflict = deepcopy(body)
    conflict["vehicles"][0]["telemetry"]["environment"]["water_level_cm"] = 12
    with pytest.raises(FleetSnapshotConflictError):
        save_fixture(database, conflict)


def test_vehicle_insert_failure_rolls_back_the_parent_run(tmp_path) -> None:
    database = Database(tmp_path / "fleet.db")
    database.initialize()
    with closing(database.connect()) as connection:
        connection.execute(
            "CREATE TRIGGER reject_fleet_vehicle BEFORE INSERT ON fleet_vehicle_plans "
            "BEGIN SELECT RAISE(ABORT, 'injected fleet write failure'); END"
        )
        connection.commit()
    with pytest.raises(sqlite3.IntegrityError, match="injected fleet write failure"):
        save_fixture(database, make_fleet_snapshot())
    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM fleet_runs").fetchone()[0] == 0
```

- [ ] **Step 2: Run the database tests and verify the missing-method failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_database.py -q
```

Expected: tests fail because `StoredFleetRun`, `FleetSnapshotConflictError`, and the fleet database methods are not defined.

- [ ] **Step 3: Add fleet tables to the existing initialization transaction**

Extend the existing `executescript` in `Database.initialize()` with this schema:

```sql
CREATE TABLE IF NOT EXISTS fleet_runs (
    run_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL UNIQUE,
    site_id TEXT NOT NULL,
    source_mode TEXT NOT NULL CHECK (source_mode IN ('SIMULATED', 'SHADOW')),
    captured_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    planner_version TEXT NOT NULL,
    input_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    plan_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fleet_vehicle_plans (
    run_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    base_decision_json TEXT NOT NULL,
    allocation_status TEXT NOT NULL,
    rank INTEGER,
    batch_index INTEGER,
    queue_ahead INTEGER,
    safe_point_id TEXT,
    action_permission TEXT NOT NULL,
    authorized_to_move INTEGER NOT NULL CHECK (authorized_to_move = 0),
    reason TEXT NOT NULL,
    PRIMARY KEY (run_id, vehicle_id),
    FOREIGN KEY (run_id) REFERENCES fleet_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_fleet_runs_site_received
    ON fleet_runs(site_id, received_at DESC);
```

Do not alter the existing four tables, indexes, WAL mode, foreign-key pragma, or connection context manager.

- [ ] **Step 4: Implement idempotent save and read methods**

Import `FleetPlan`, `FleetSnapshot`, `canonical_fleet_snapshot_json`, and `fleet_input_sha256`. Define the dataclass and exception at module scope; insert the method bodies shown after them inside `Database` with the existing class indentation:

```python
@dataclass(frozen=True)
class StoredFleetRun:
    plan: FleetPlan
    received_at: datetime
    duplicate: bool = False


class FleetSnapshotConflictError(RuntimeError):
    """Raised when one snapshot ID is reused for different canonical input."""


@staticmethod
def _row_to_fleet_run(row: sqlite3.Row, *, duplicate: bool = False) -> StoredFleetRun:
    plan = FleetPlan.model_validate_json(row["plan_json"])
    if duplicate:
        plan = plan.model_copy(update={"duplicate": True})
    return StoredFleetRun(
        plan=plan,
        received_at=datetime.fromisoformat(row["received_at"]),
        duplicate=duplicate,
    )


def has_fleet_snapshot_id(self, snapshot_id: str) -> bool:
    with self._connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM fleet_runs WHERE snapshot_id = ? LIMIT 1",
            (snapshot_id,),
        ).fetchone()
    return row is not None


def save_fleet_run(self, snapshot: FleetSnapshot, plan: FleetPlan) -> StoredFleetRun:
    input_json = canonical_fleet_snapshot_json(snapshot)
    input_sha256 = fleet_input_sha256(snapshot)
    if input_sha256 != plan.input_sha256:
        raise ValueError("fleet plan input hash does not match snapshot")
    persisted_plan = plan.model_copy(update={"duplicate": False})
    plan_json = persisted_plan.model_dump_json()
    received_at = _utc_now()

    with self._connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT plan_json, received_at, input_sha256 "
            "FROM fleet_runs WHERE snapshot_id = ?",
            (snapshot.snapshot_id,),
        ).fetchone()
        if existing:
            connection.rollback()
            if existing["input_sha256"] != input_sha256:
                raise FleetSnapshotConflictError(
                    "snapshot_id already exists with different fleet content"
                )
            return self._row_to_fleet_run(existing, duplicate=True)

        connection.execute(
            """
            INSERT INTO fleet_runs (
                run_id, snapshot_id, site_id, source_mode, captured_at,
                received_at, planner_version, input_json, input_sha256, plan_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                persisted_plan.run_id,
                snapshot.snapshot_id,
                snapshot.site_id,
                snapshot.source_mode.value,
                snapshot.captured_at.isoformat(),
                received_at.isoformat(),
                persisted_plan.planner_version,
                input_json,
                input_sha256,
                plan_json,
            ),
        )
        for vehicle in persisted_plan.vehicles:
            connection.execute(
                """
                INSERT INTO fleet_vehicle_plans (
                    run_id, vehicle_id, base_decision_json, allocation_status,
                    rank, batch_index, queue_ahead, safe_point_id,
                    action_permission, authorized_to_move, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    persisted_plan.run_id,
                    vehicle.vehicle_id,
                    vehicle.base_decision.model_dump_json(),
                    vehicle.allocation_status.value,
                    vehicle.rank,
                    vehicle.batch_index,
                    vehicle.queue_ahead,
                    vehicle.safe_point_id,
                    vehicle.action_permission.value,
                    vehicle.reason,
                ),
            )
        connection.commit()
    return StoredFleetRun(plan=persisted_plan, received_at=received_at)


def get_fleet_run(self, run_id: str) -> StoredFleetRun | None:
    with self._connection() as connection:
        row = connection.execute(
            "SELECT plan_json, received_at FROM fleet_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return self._row_to_fleet_run(row) if row else None


def get_latest_fleet_run(self, site_id: str) -> StoredFleetRun | None:
    with self._connection() as connection:
        row = connection.execute(
            "SELECT plan_json, received_at FROM fleet_runs "
            "WHERE site_id = ? ORDER BY received_at DESC, rowid DESC LIMIT 1",
            (site_id,),
        ).fetchone()
    return self._row_to_fleet_run(row) if row else None
```

Implementation requirements:

- Start `save_fleet_run` with `BEGIN IMMEDIATE`.
- Canonicalize the validated snapshot with `canonical_fleet_snapshot_json` and compare its SHA-256 to the existing row for the same `snapshot_id`.
- Return the stored original plan with `duplicate=True` for an identical retry; do not create another run or child row.
- Raise `FleetSnapshotConflictError("snapshot_id already exists with different fleet content")` for different content.
- Serialize `plan_json` with `duplicate=False`; set the response-only duplicate flag with `model_copy(update={"duplicate": True})` when loading an identical retry.
- Insert `fleet_runs` and all `fleet_vehicle_plans` before one commit. Any SQL exception must leave both tables unchanged.
- Deserialize `FleetPlan` and `DecisionOutput` through Pydantic rather than manually trusting JSON.
- Close every connection through the existing `_connection()` context manager.

- [ ] **Step 5: Run database and existing database-facing API tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_database.py backend\tests\test_api.py -q
```

Expected: all tests pass, including existing telemetry idempotency and authorization atomicity tests.

- [ ] **Step 6: Commit persistence**

```powershell
git add backend/app/database.py backend/tests/test_fleet_database.py
git commit -m "feat: persist fleet shadow evidence"
```

## Task 4: Authenticated Fleet API And Freshness Rules

**Files:**
- Modify: `backend/app/main.py:1-379`
- Create: `backend/tests/test_fleet_api.py`

- [ ] **Step 1: Write failing API contract tests**

Create `backend/tests/test_fleet_api.py`. Cover `401`, `201`, duplicate `200`, conflict `409`, missing run `404`, stale latest `410`, malformed batch `422`, stale capture `422`, site-unavailable plans, and actuator isolation. Use this actuator proof verbatim:

```python
import pytest

from backend.app import main as main_module
from backend.tests.fleet_fixtures import FIXED_NOW, make_fleet_snapshot


@pytest.fixture(autouse=True)
def fixed_api_clock(monkeypatch):
    monkeypatch.setattr(main_module, "_utc_now", lambda: FIXED_NOW)


def test_fleet_shadow_route_never_calls_actuator(client, headers, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    actuator = client.app.state.actuator

    def forbidden_call(*, event_id: str, vehicle_id: str):
        calls.append((event_id, vehicle_id))
        raise AssertionError("fleet shadow flow must not call the actuator")

    monkeypatch.setattr(actuator, "migrate_to_high_point", forbidden_call)
    response = client.post(
        "/api/v1/fleet/shadow-runs",
        json=make_fleet_snapshot(),
        headers=headers,
    )
    assert response.status_code == 201
    assert calls == []
    assert all(item["authorized_to_move"] is False for item in response.json()["vehicles"])
```

The lifecycle test must assert:

```python
first = client.post("/api/v1/fleet/shadow-runs", json=body, headers=headers)
retry = client.post("/api/v1/fleet/shadow-runs", json=body, headers=headers)
detail = client.get(f"/api/v1/fleet/shadow-runs/{first.json()['run_id']}", headers=headers)
latest = client.get(
    "/api/v1/fleet/latest",
    params={"site_id": body["site_id"]},
    headers=headers,
)
assert first.status_code == 201
assert retry.status_code == 200
assert retry.json()["duplicate"] is True
assert detail.json()["run_id"] == first.json()["run_id"]
assert latest.headers["cache-control"] == "private, no-store"
```

For atomic `422`, duplicate one vehicle ID in the JSON, POST it, and query both fleet tables through `client.app.state.database.connect()`; assert both counts are zero. Repeat with `source_mode="LIVE_CONTROL"`, an out-of-range `batch_size`, and one mismatched `telemetry.site_id`. For stale latest, monkeypatch `backend.app.database._utc_now` before the original POST so the stored `received_at` is older than `event_max_age_seconds`, restore it, then assert `410` and the no-store header.

- [ ] **Step 2: Run API tests and verify route-not-found failures**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_api.py -q
```

Expected: POST and GET calls return `404 Not Found` because the three routes are not registered.

- [ ] **Step 3: Generalize capture freshness checks without changing telemetry behavior**

Keep `_capture_time_error` behavior for existing telemetry and add a fleet helper that checks:

- snapshot `captured_at` against `capture_max_age_seconds` and `capture_future_tolerance_seconds`;
- every vehicle telemetry `captured_at` against the same limits;
- site `observed_at` only for excessive future time at request validation;
- an old site `observed_at` is accepted and converted into `SITE_UNAVAILABLE` by the planner.

Use this concrete helper body:

```python
def _fleet_capture_time_error(
    payload: FleetSnapshot,
    settings: Settings,
    *,
    now: datetime,
) -> str | None:
    captures = [
        ("captured_at", payload.captured_at),
        *[
            (
                f"vehicles[{item.telemetry.vehicle_id}].telemetry.captured_at",
                item.telemetry.captured_at,
            )
            for item in payload.vehicles
        ],
    ]
    for path, captured_at in captures:
        age_seconds = (now - captured_at).total_seconds()
        if age_seconds > settings.capture_max_age_seconds:
            return (
                f"{path} is older than the configured maximum age "
                f"({settings.capture_max_age_seconds}s)"
            )
        if age_seconds < -settings.capture_future_tolerance_seconds:
            return (
                f"{path} is ahead of server time beyond the configured tolerance "
                f"({settings.capture_future_tolerance_seconds}s)"
            )
    site_age_seconds = (now - payload.site.observed_at).total_seconds()
    if site_age_seconds < -settings.capture_future_tolerance_seconds:
        return (
            "site.observed_at is ahead of server time beyond the configured tolerance "
            f"({settings.capture_future_tolerance_seconds}s)"
        )
    return None
```

Return a message that names the failing path, such as `vehicles[vehicle-a].telemetry.captured_at is older than the configured maximum age (120s)`. Before returning `422`, call `database.has_fleet_snapshot_id(payload.snapshot_id)`; identical retries and conflicting retries with an existing ID must proceed to the database so they retain `200` and `409` semantics.

- [ ] **Step 4: Register the three fleet routes**

Inside `create_app`, import `uuid4`, `FleetPlan`, `FleetSnapshot`, `FleetSnapshotConflictError`, and `plan_fleet`, then reuse the existing `require_api_key`, `LATEST_RESPONSE_HEADERS`, settings, and database instance. Add these exact route contracts:

```python
@app.post(
    f"{API_PREFIX}/fleet/shadow-runs",
    response_model=FleetPlan,
    status_code=status.HTTP_201_CREATED,
    tags=["fleet-shadow"],
)
def create_fleet_shadow_run(
    payload: FleetSnapshot,
    response: Response,
    _: str = Security(require_api_key),
) -> FleetPlan:
    now = _utc_now()
    capture_error = _fleet_capture_time_error(payload, settings, now=now)
    if capture_error and not database.has_fleet_snapshot_id(payload.snapshot_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=capture_error,
        )
    plan = plan_fleet(
        payload,
        settings.policy,
        run_id=f"fleet_{uuid4().hex}",
        created_at=now,
        now=now,
        site_max_age_seconds=settings.capture_max_age_seconds,
    )
    try:
        stored = database.save_fleet_run(payload, plan)
    except FleetSnapshotConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if stored.duplicate:
        response.status_code = status.HTTP_200_OK
    return stored.plan


@app.get(
    f"{API_PREFIX}/fleet/shadow-runs/{{run_id}}",
    response_model=FleetPlan,
    tags=["fleet-shadow"],
)
def get_fleet_shadow_run(
    run_id: str,
    _: str = Security(require_api_key),
) -> FleetPlan:
    stored = database.get_fleet_run(run_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Fleet shadow run not found")
    return stored.plan


@app.get(
    f"{API_PREFIX}/fleet/latest",
    response_model=FleetPlan,
    tags=["fleet-shadow"],
)
def get_latest_fleet_shadow_run(
    response: Response,
    site_id: str = Query(min_length=1, max_length=80),
    _: str = Security(require_api_key),
) -> FleetPlan:
    stored = database.get_latest_fleet_run(site_id)
    if not stored:
        raise HTTPException(
            status_code=404,
            detail="No fleet shadow run found",
            headers=LATEST_RESPONSE_HEADERS,
        )
    age_seconds = (_utc_now() - stored.received_at).total_seconds()
    if age_seconds > settings.event_max_age_seconds:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Latest fleet shadow run is stale; submit a fresh snapshot",
            headers=LATEST_RESPONSE_HEADERS,
        )
    response.headers.update(LATEST_RESPONSE_HEADERS)
    return stored.plan
```

POST behavior:

- Capture `now = _utc_now()` once.
- Reject new stale/future snapshot or vehicle telemetry with `422` before planning or writes.
- Call `plan_fleet(payload, settings.policy, run_id=f"fleet_{uuid4().hex}", created_at=now, now=now, site_max_age_seconds=settings.capture_max_age_seconds)`.
- Save through `database.save_fleet_run` and translate `FleetSnapshotConflictError` to `409`.
- Return the stored original with HTTP `200` and `duplicate=true` for identical retries; otherwise return `201` and `duplicate=false`.

GET behavior:

- Run detail returns `404` with `Fleet shadow run not found` when absent.
- Latest returns `404` with `No fleet shadow run found` when absent.
- Latest compares `now` to `StoredFleetRun.received_at`; when older than `event_max_age_seconds`, return `410`, `Cache-Control: private, no-store`, and `Latest fleet shadow run is stale; submit a fresh snapshot`.
- Every successful latest response also includes `Cache-Control: private, no-store`.

Do not reference `app.state.actuator`, authorization handlers, or command handlers anywhere in the new routes.

- [ ] **Step 5: Add an atomic planner/database failure test**

Monkeypatch `backend.app.main.plan_fleet` to raise `RuntimeError("injected planner failure")`, assert the TestClient raises the error, and then query both fleet tables to prove both counts remain zero. Separately install the database trigger from Task 3, post a valid fleet snapshot, and prove the parent row also rolls back.

- [ ] **Step 6: Run focused and complete backend tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_api.py backend\tests\test_fleet_database.py -q
.\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Expected: all tests pass; the existing command safety and connection-closure tests remain green.

- [ ] **Step 7: Commit the fleet API**

```powershell
git add backend/app/main.py backend/tests/test_fleet_api.py
git commit -m "feat: expose fleet shadow run api"
```

## Task 5: Six-Stage Shared Scenario And JavaScript Parity

**Files:**
- Create: `demo/scenarios/fleet-rainstorm-v1.json`
- Create: `src/fleet-planner.js`
- Create: `src/fleet-scenario.js`
- Create: `tests/fleet-planner.test.mjs`
- Create: `tests/fleet-planner-cli.mjs`
- Create: `backend/tests/test_fleet_scenario.py`
- Modify: `backend/tests/fleet_fixtures.py`
- Modify: `backend/app/main.py:360-374`

- [ ] **Step 1: Define the complete shared scenario before implementing JavaScript**

Create `demo/scenarios/fleet-rainstorm-v1.json` with top-level values `schema_version: 1`, `planner_version: "fleet-shadow-v1"`, `default_stage_index: 3`, and a six-item `stages` array. Every stage object contains `stage_id`, `label`, one complete `snapshot`, and one complete `expect` projection. Every `snapshot` must include all site fields and six complete `FleetVehicleInput` records; no stage inherits data from another stage at runtime. Use vehicle IDs `p5-01` through `p5-06`, one site ID `garage-demo-b1`, fixed timezone-aware timestamps separated by one minute, and stable message IDs that include the stage and vehicle ID.

Use this exact behavioral matrix to choose the complete numeric telemetry and expected projections:

| Stage | Site configuration | Required expected outcomes |
|---|---|---|
| `01-daily-watch` | two available safe points, interval `0.7` | all six `STAY / NOT_REQUIRED` |
| `02-heavy-rain` | same capacity | all six `WATCH / NOT_REQUIRED`; summary `6/0/0/0/2` |
| `03-prepare-window` | same capacity | `p5-01` and `p5-02` `PREPARE / PREPARE_ONLY`; `p5-03` through `p5-06` `WATCH / NOT_REQUIRED`; summary `6/0/0/0/2` |
| `04-rapid-rise` | `high-a` and `high-b`, capacity one each | `p5-01` and `p5-02` `SCHEDULED_SHADOW`; `p5-03` `VERIFY_ONLY`; `p5-04` route-dry failure `DENIED`; `p5-05` and `p5-06` `NO_CAPACITY` |
| `05-capacity-limited` | only `high-a`, capacity one | `p5-01` scheduled; `p5-02`, `p5-05`, and `p5-06` `NO_CAPACITY`; `p5-03` verify; `p5-04` denied; summary `6/1/1/4/0` |
| `06-window-closed` | two slots, `batch_size=1`, interval `7.0` | `p5-01` scheduled; `p5-02` `WINDOW_CLOSED`; `p5-05` and `p5-06` stay `NO_CAPACITY` without backfill; `p5-03` verify; `p5-04` denied; summary `6/1/1/4/1` |

Summary shorthand in this table is `vehicle_count/scheduled_count/verify_count/denied_count/remaining_capacity`. Stage 4 is therefore `6/2/1/3/0`; stage 1 is `6/0/0/0/2`.

Stage 1 uses rainfall `35`, water `4`, and rise rate `0.2` for every vehicle. Stage 2 uses rainfall `65`, water `6`, and rise rate `0.5` for every vehicle. In stage 3, `p5-01` and `p5-02` use water `10` and rise rate `0.7`, while the remaining vehicles retain stage-2 values. For stages 4 and 5, migration candidates use `water_level_cm=14`, `secondary_water_level_cm=14`, and `rise_rate_cm_min=1`. For the equal-window candidates `p5-01`, `p5-02`, `p5-05`, and `p5-06` in stage 6, use `water_level_cm=10`, `secondary_water_level_cm=10`, and `rise_rate_cm_min=1`; the first candidate has no queue delay and the second loses seven minutes during reevaluation. In stages 4-6, use `sensor_confidence=0.55` for `p5-03` and `site.route_dry=false` for `p5-04`. Keep all vehicle safety fields otherwise passing.

Each expected vehicle projection must contain exactly:

```json
{
  "vehicle_id": "p5-01",
  "decision": "MIGRATE_NOW",
  "allocation_status": "SCHEDULED_SHADOW",
  "rank": 1,
  "batch_index": 1,
  "queue_ahead": 0,
  "safe_point_id": "high-a",
  "action_permission": "SHADOW_ONLY",
  "authorized_to_move": false
}
```

Use JSON `null` for fields that do not apply. Store expected summaries with all five `FleetSummary` fields.

- [ ] **Step 2: Add failing Python scenario assertions**

Create `backend/tests/test_fleet_scenario.py` and compare a stable projection, not volatile IDs or timestamps:

```python
from backend.app.config import DecisionPolicy
from backend.app.fleet_models import FleetSnapshot
from backend.app.fleet_planner import plan_fleet
from backend.tests.fleet_fixtures import FIXED_NOW, load_fleet_scenario


def project(plan):
    return {
        "summary": plan.summary.model_dump(mode="json"),
        "vehicles": [
            {
                "vehicle_id": item.vehicle_id,
                "decision": item.base_decision.decision.value,
                "allocation_status": item.allocation_status.value,
                "rank": item.rank,
                "batch_index": item.batch_index,
                "queue_ahead": item.queue_ahead,
                "safe_point_id": item.safe_point_id,
                "action_permission": item.action_permission.value,
                "authorized_to_move": item.authorized_to_move,
            }
            for item in plan.vehicles
        ],
    }


def test_all_six_python_stages_match_the_shared_contract() -> None:
    scenario = load_fleet_scenario()
    assert scenario["schema_version"] == 1
    assert len(scenario["stages"]) == 6
    for stage in scenario["stages"]:
        snapshot = FleetSnapshot.model_validate(stage["snapshot"])
        plan = plan_fleet(
            snapshot,
            DecisionPolicy(),
            run_id=f"fleet-{stage['stage_id']}",
            created_at=FIXED_NOW,
            now=snapshot.captured_at,
            site_max_age_seconds=300,
        )
        assert project(plan) == stage["expect"]
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_scenario.py -q
```

Expected: the test fails until every scenario expected projection agrees with the Python planner. Adjust only scenario input numbers or genuine planner defects; do not weaken expected states.

- [ ] **Step 3: Write failing Node tests against the same JSON**

Create `tests/fleet-planner.test.mjs` using `readFile` and `JSON.parse`, then assert all stages:

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { planFleet, projectFleetPlan } from "../src/fleet-planner.js";

const scenario = JSON.parse(await readFile(
  new URL("../demo/scenarios/fleet-rainstorm-v1.json", import.meta.url),
  "utf8",
));

test("JavaScript planner matches all six shared fleet stages", () => {
  assert.equal(scenario.stages.length, 6);
  for (const stage of scenario.stages) {
    const plan = planFleet(stage.snapshot, { now: stage.snapshot.captured_at });
    assert.deepEqual(projectFleetPlan(plan), stage.expect, stage.stage_id);
    assert.ok(plan.vehicles.every((vehicle) => vehicle.authorized_to_move === false));
  }
});
```

Run:

```powershell
node --test tests\fleet-planner.test.mjs
```

Expected: collection fails because `src/fleet-planner.js` does not exist.

- [ ] **Step 4: Implement the JavaScript planner by adapting the existing decision engine**

Create `src/fleet-planner.js` and import `DEFAULT_INPUTS` and `evaluateDecision` from `src/decision-engine.js`. Export the constant `FLEET_PLANNER_VERSION = "fleet-shadow-v1"` and the functions `telemetryToDecisionInputs(vehicle, site)`, `planFleet(snapshot, options)`, and `projectFleetPlan(plan)`. The adapter must have this concrete implementation shape:

```javascript
export const FLEET_PLANNER_VERSION = "fleet-shadow-v1";

export function telemetryToDecisionInputs(vehicle, site) {
  const telemetry = vehicle.telemetry;
  const environment = telemetry.environment;
  const vehicleState = telemetry.vehicle;
  const siteState = telemetry.site;
  return {
    ...DEFAULT_INPUTS,
    rainfallMmH: environment.rainfall_mm_h,
    waterLevelCm: environment.water_level_cm,
    secondaryWaterLevelCm: environment.secondary_water_level_cm,
    riseRateCmMin: environment.rise_rate_cm_min,
    sensorConfidence: environment.sensor_confidence,
    dangerWaterLevelCm: vehicle.danger_water_level_cm ?? DEFAULT_INPUTS.dangerWaterLevelCm,
    routeDistanceM: vehicle.route_distance_m ?? DEFAULT_INPUTS.routeDistanceM,
    queueAhead: 0,
    batchSize: site.batch_size,
    batchIntervalMin: site.batch_interval_min,
    routeDry: siteState.route_dry,
    routeBlocked: siteState.route_blocked,
    occupantsClear: vehicleState.occupants_clear,
    chargingDisconnected: vehicleState.charging_disconnected,
    vehicleHealthy: vehicleState.vehicle_healthy,
    positioningOnline: vehicleState.positioning_online,
    networkOnline: vehicleState.network_online,
    emergencyOperatorOnline: vehicleState.emergency_operator_online,
    waterContactTriggered: vehicleState.water_contact_triggered,
    motionState: vehicleState.motion_state,
    ownerAuthorized: false,
  };
}
```

`planFleet(snapshot, options = {})` must use the same mapping, sort, slot expansion, `rank - 1` queue, one-pass reevaluation, no-backfill rule, status permissions, output order, reasons, and summary counts specified in Task 2. The base call keeps `queueAhead: 0`; the second call replaces it with `rank - 1`. Convert existing decision-engine camel-case fields into the snake-case fleet response, including `authorized_to_move: false` regardless of the nested decision result. `options.now` defaults to `snapshot.captured_at`, and `options.siteMaxAgeSeconds` defaults to `300`. `projectFleetPlan(plan)` must return the exact stable projection consumed by the shared scenario tests, including all five summary fields and all nine per-vehicle fields.

Normalize every JavaScript `base_decision` to the API shape: `risk_level` from `riskLevel`, `authorized_to_move: false`, `sensor_disagreement_cm` from `sensorDisagreementCm`, snake-case timing keys, and `safety_gates` entries with `passed` copied from the JavaScript gate's `ok`. This lets `src/fleet-view.js` render browser and API plans through one code path without changing evidence field names.

The browser plan shape deliberately excludes `run_id`, `input_sha256`, and `plan_sha256`. It includes:

```javascript
{
  snapshot_id: snapshot.snapshot_id,
  site_id: snapshot.site_id,
  source_mode: snapshot.source_mode,
  planner_version: FLEET_PLANNER_VERSION,
  summary,
  vehicles,
}
```

Throw an `Error` before planning if the source mode is not `SIMULATED` or `SHADOW`, vehicle count is outside 1-50, vehicle IDs repeat, a telemetry site differs, or a required site number is outside the Pydantic bounds. This prevents the static UI from silently accepting data the API would reject.

- [ ] **Step 5: Add scenario loading and a parity bridge**

Create `src/fleet-scenario.js`:

```javascript
export const FLEET_SCENARIO_URL = "./demo/scenarios/fleet-rainstorm-v1.json";

export async function loadFleetScenario(fetchImpl = fetch) {
  const response = await fetchImpl(FLEET_SCENARIO_URL, { cache: "no-store" });
  if (!response.ok) throw new Error(`车队场景加载失败 · HTTP ${response.status}`);
  const scenario = await response.json();
  if (scenario.schema_version !== 1 || scenario.stages?.length !== 6) {
    throw new Error("车队场景合同无效");
  }
  return scenario;
}
```

Create `tests/fleet-planner-cli.mjs` so Python can compare actual JavaScript output directly:

```javascript
import { readFile } from "node:fs/promises";
import { planFleet, projectFleetPlan } from "../src/fleet-planner.js";

const scenarioPath = process.argv[2];
if (!scenarioPath) throw new Error("scenario path is required");
const scenario = JSON.parse(await readFile(scenarioPath, "utf8"));
const output = scenario.stages.map((stage) => ({
  stage_id: stage.stage_id,
  projection: projectFleetPlan(planFleet(stage.snapshot, { now: stage.snapshot.captured_at })),
}));
process.stdout.write(JSON.stringify(output));
```

Extend `backend/tests/test_fleet_scenario.py` with `subprocess.run` against this bridge and compare each JavaScript projection to the Python `project(plan)` output. Use `check=True`, `capture_output=True`, `text=True`, and the absolute shared scenario path. Expected: one direct equality assertion per stage.

Mount `REPO_ROOT / "demo"` at `/demo` in `backend/app/main.py` after the existing `/assets` mount so the same JSON loads from both FastAPI and GitHub Pages.

- [ ] **Step 6: Run Python, JavaScript, and parity tests**

Run:

```powershell
node --test tests\fleet-planner.test.mjs
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_scenario.py -q
npm test
```

Expected: all six stages match in Python, JavaScript, and the direct parity bridge; all existing Node tests pass.

- [ ] **Step 7: Commit the shared contract and browser planner**

```powershell
git add demo/scenarios/fleet-rainstorm-v1.json src/fleet-planner.js src/fleet-scenario.js tests/fleet-planner.test.mjs tests/fleet-planner-cli.mjs backend/tests/test_fleet_scenario.py backend/tests/fleet_fixtures.py backend/app/main.py
git commit -m "feat: add shared fleet rainstorm scenario"
```

## Task 6: Fleet Dashboard, Replay, And Request Safety

**Files:**
- Create: `src/fleet-view-state.js`
- Create: `src/fleet-view.js`
- Create: `tests/fleet-view-state.test.mjs`
- Modify: `index.html:26-249`
- Modify: `styles.css:1-764`
- Modify: `src/app.js:1-775`
- Modify: `backend/tests/test_web_contract.py:8-113`

- [ ] **Step 1: Write failing pure state tests**

Create `tests/fleet-view-state.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";

import {
  advanceFleetGeneration,
  fleetResponseCanCommit,
  fleetEvidenceState,
} from "../src/fleet-view-state.js";

test("only the latest response for the current snapshot may update evidence", () => {
  assert.equal(fleetResponseCanCommit(4, 4, "snap-4", "snap-4"), true);
  assert.equal(fleetResponseCanCommit(3, 4, "snap-4", "snap-4"), false);
  assert.equal(fleetResponseCanCommit(4, 4, "snap-3", "snap-4"), false);
  assert.equal(advanceFleetGeneration(4), 5);
});

test("browser and api evidence can never share server identifiers", () => {
  assert.deepEqual(fleetEvidenceState("browser"), {
    label: "SIMULATED · 浏览器规划 · 不写 SQLite",
    runId: null,
    inputHash: null,
    planHash: null,
    stale: false,
  });
  const api = fleetEvidenceState("api", {
    run_id: "fleet-1",
    input_sha256: "a".repeat(64),
    plan_sha256: "b".repeat(64),
  });
  assert.equal(api.label, "SIMULATED · SQLite 证据");
  assert.equal(api.runId, "fleet-1");
});

test("offline api keeps readable evidence but marks it stale", () => {
  const state = fleetEvidenceState("api-stale", {
    run_id: "fleet-1",
    input_sha256: "a".repeat(64),
    plan_sha256: "b".repeat(64),
  });
  assert.equal(state.stale, true);
  assert.match(state.label, /已过期/);
});
```

Run:

```powershell
node --test tests\fleet-view-state.test.mjs
```

Expected: collection fails because `src/fleet-view-state.js` does not exist.

- [ ] **Step 2: Implement request-generation and evidence-source state**

Create `src/fleet-view-state.js` with the three tested exports. `fleetEvidenceState` must reject an API state that lacks a nonempty `run_id` or two 64-character lowercase hexadecimal hashes. It must preserve the last valid identifiers for `api-stale` while changing the label to `SQLite 证据已过期 · 待提交新快照`.

Run the state test again and expect all three tests to pass.

- [ ] **Step 3: Extend the DOM contract before writing view code**

Add these IDs to a new `FLEET_REQUIRED_IDS` set in `backend/tests/test_web_contract.py`:

```python
FLEET_REQUIRED_IDS = {
    "view-fleet-tab",
    "view-single-tab",
    "fleet-shadow-view",
    "single-car-view",
    "fleet-source-label",
    "fleet-stage-label",
    "fleet-vehicle-count",
    "fleet-scheduled-count",
    "fleet-verify-count",
    "fleet-denied-count",
    "fleet-capacity-count",
    "fleet-map",
    "fleet-queue-body",
    "fleet-timeline",
    "fleet-evidence-body",
    "fleet-next-button",
    "fleet-reset-button",
    "fleet-api-key-input",
    "fleet-api-connect-button",
    "fleet-api-status",
    "fleet-run-id",
    "fleet-input-hash",
    "fleet-plan-hash",
}
```

Assert the combined ID list has no duplicates, every existing `REQUIRED_IDS` entry still exists, the fleet view is not marked `hidden`, and the single-car view is initially marked `hidden`. Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_web_contract.py -q
```

Expected: failure listing the missing fleet IDs.

- [ ] **Step 4: Add the segmented views and fleet dashboard markup**

Modify `index.html` so the first content inside `<main>` is a two-button tablist. Use native buttons with `role="tab"`, `aria-selected`, and `aria-controls`; default to `view-fleet-tab` selected. Wrap all current single-car sections unchanged inside `#single-car-view` and set `hidden` initially.

Add `#fleet-shadow-view` with this semantic order:

1. Compact heading and persistent source label.
2. Five summary counters.
3. A three-column operations band containing `#fleet-map`, the ranked queue table body `#fleet-queue-body`, and fleet API controls.
4. A six-step replay control with `#fleet-timeline`, reset, and next-stage buttons.
5. A horizontally scrollable per-vehicle evidence table with `#fleet-evidence-body`.
6. An audit strip with run ID, input hash, and plan hash.

The map must be a stable `viewBox="0 0 900 480"` SVG containing named water zones, six button-like vehicle markers with `data-vehicle-id`, route paths, and two high-point bays. Its accessible name must be `车队数字孪生风险图，非实车状态`. Do not add a control, authorization button, or migration command button to the fleet view.

Load `src/fleet-view.js` as a second module script after the existing `src/app.js` script.

- [ ] **Step 5: Implement fleet replay, rendering, filtering, and API coordination**

Create `src/fleet-view.js` and import `planFleet`, `loadFleetScenario`, and the state helpers. Implement these concrete behaviors:

- Load the scenario once, start at `default_stage_index` so the first view shows scheduled, verify, and denied outcomes together, and render the browser plan immediately.
- Timeline and reset choose immutable stage objects. Next stops at stage six rather than wrapping silently.
- Clicking a vehicle marker or queue row sets `aria-selected=true` and filters the evidence table to that vehicle; clicking the already selected vehicle clears the filter.
- Browser rendering calls `fleetEvidenceState("browser")`, writes em dashes to the three audit values, and never computes a fake SHA-256.
- Fleet API connect calls `/api/v1/session` with the fleet API key, stores the key in the existing `highground-api-key` session key, and updates only after the latest connection generation returns.
- In API mode, changing stage advances the fleet request generation, keeps the last API plan visible with `api-stale` evidence, and automatically posts a fresh submission copy to `/api/v1/fleet/shadow-runs`. Build that copy once per request generation: use `snapshot_id = fleet-web-<stage_id>-<UTC milliseconds>`, suffix every vehicle `message_id` with the same submission token, and set snapshot, site observation, and all telemetry capture times to the same current UTC ISO timestamp. Network retries reuse the same copy so the API idempotency contract remains observable.
- A response commits only when `fleetResponseCanCommit(requestGeneration, latestGeneration, requestedSnapshotId, currentSnapshotId)` is true.
- On success, render the returned plan and `fleetEvidenceState("api", response)`; display the full run ID and abbreviated hashes with a title containing each full hash.
- On network error, keep the last API plan readable, mark it stale, and never replace it with a local result. A user can switch explicitly back to browser mode by disconnecting.
- POST `401` clears the saved key and leaves stale evidence. POST `409` shows a snapshot conflict. `422` shows the first server detail. `5xx` shows service unavailable. No failure falls back silently.
- Render all status names as text plus a shape/icon class; color alone must not convey meaning.

Modify `src/app.js` only to dispatch a `highground:api-session` custom event after a successful or failed existing single-car connection, with `{ connected }` in `detail`. The fleet module listens for this event and reads the already stored `highground-api-key` session value after a successful connection; the API key is not copied into event payloads. No fleet planning or rendering code belongs in `src/app.js`.

- [ ] **Step 6: Add stable desktop and mobile styles**

Modify `styles.css` with these layout constraints:

- `.view-tabs` uses a compact segmented control and never changes width when selection changes.
- `.fleet-summary` is a five-column grid above 900 px and a two-column grid below 760 px; the final counter spans both mobile columns.
- `.fleet-operations` uses `minmax(0, .9fr) minmax(420px, 1.6fr) minmax(280px, .8fr)` on wide screens.
- `#fleet-map` has `width: 100%`, `aspect-ratio: 15 / 8`, and a minimum rendered height of 320 px on desktop.
- Queue rows have a fixed minimum height of 52 px and status text wraps without resizing the table columns.
- At 760 px and below, order content as summary, queue, map, timeline, evidence; tables scroll horizontally inside their own wrappers.
- Use the existing neutral surfaces, teal accent, green safe, amber warning, and red danger tokens. Do not introduce gradients, decorative orbs, negative letter spacing, or nested cards.
- Focus rings remain visible for tabs, vehicle markers, queue rows, and replay buttons.
- Under `prefers-reduced-motion`, disable route and marker transitions.

- [ ] **Step 7: Run Web contracts and Node tests**

Run:

```powershell
npm test
.\.venv\Scripts\python.exe -m pytest backend\tests\test_web_contract.py -q
```

Expected: all Node and DOM contract tests pass; the original single-car required IDs and six scenarios remain unchanged.

- [ ] **Step 8: Commit the fleet Web experience**

```powershell
git add index.html styles.css src/app.js src/fleet-view.js src/fleet-view-state.js tests/fleet-view-state.test.mjs backend/tests/test_web_contract.py
git commit -m "feat: add fleet shadow dashboard"
```

## Task 7: Evidence Runner, Benchmark, Version, And Documentation

**Files:**
- Create: `demo/run_fleet_scenario.py`
- Create: `backend/tests/test_fleet_demo.py`
- Modify: `benchmarks/run_benchmark.py`
- Modify: `backend/tests/test_benchmark.py`
- Modify: `backend/app/main.py:121-129`
- Modify: `package.json:2-4`
- Modify: `README.md`
- Modify: `docs/DEMO.md`
- Modify: `docs/BENCHMARK.md`

- [ ] **Step 1: Write a failing API-backed evidence test**

Create `backend/tests/test_fleet_demo.py`:

```python
import json

from demo.run_fleet_scenario import run_fleet_scenario


def test_fleet_evidence_runs_all_stages_without_vehicle_control(tmp_path) -> None:
    output = tmp_path / "fleet-evidence.json"
    report = run_fleet_scenario(output_path=output)
    persisted = json.loads(output.read_text(encoding="utf-8"))

    assert report == persisted
    assert report["schema_version"] == 1
    assert report["planner_version"] == "fleet-shadow-v1"
    assert report["stage_count"] == 6
    assert report["assertions_passed"] is True
    assert report["vehicle_command_transmitted"] is False
    assert all(stage["http_status"] == 201 for stage in report["stages"])
    assert all(len(stage["run_id"]) > 10 for stage in report["stages"])
    assert all(len(stage["input_sha256"]) == 64 for stage in report["stages"])
    assert all(len(stage["plan_sha256"]) == 64 for stage in report["stages"])
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_demo.py -q
```

Expected: collection fails because `demo.run_fleet_scenario` does not exist.

- [ ] **Step 2: Implement the evidence runner**

Create `demo/run_fleet_scenario.py` with a public function:

```python
def run_fleet_scenario(*, output_path: Path | None = None) -> dict[str, object]:
```

The function must:

- load the shared JSON with `load_fleet_scenario`;
- clone each stage before POST and replace the snapshot, site observation, and all vehicle capture timestamps with `base_time + stage_index seconds`, where `base_time` is captured once from `datetime.now(timezone.utc)`; keep the immutable JSON file unchanged;
- create `Settings` with a temporary SQLite path, `environment="fleet-demo"`, a private local API key, and `actuator_mode="record-only"`;
- use `TestClient(create_app(settings))` and POST all six stages to `/api/v1/fleet/shadow-runs`;
- assert each response status, summary, and projected vehicle list against the stage expectation;
- GET each run by ID and GET the latest site run after the final stage;
- query SQLite and prove `fleet_runs=6`, `fleet_vehicle_plans=36`, `authorizations=0`, and `commands=0`;
- emit run ID, full input hash, full plan hash, source mode, summary, vehicle projection, elapsed milliseconds, and an assertion result for each stage;
- include `vehicle_command_transmitted: false`, `actuator_mode: "record-only"`, and `data_claim: "repository simulated scenario; no P5, parking site, or sensor validation"` at report level;
- omit API keys and authorization tokens entirely;
- write UTF-8 indented JSON with a trailing newline when `output_path` is supplied.

Add a `main()` using `argparse` with one option, `--output`, defaulting to `demo/artifacts/latest-fleet-evidence.json`. Exit zero only when all assertions pass.

- [ ] **Step 3: Add fleet correctness and latency to the local benchmark**

In `benchmarks/run_benchmark.py`, add one untimed correctness pass over all six fleet stages and `iterations` timed POSTs of stage 4 with a fresh `snapshot_id`, message IDs, and current timestamps each iteration. Report:

```json
{
  "fleet_shadow": {
    "correctness": {
      "passed": true,
      "stage_count": 6,
      "vehicle_count_per_stage": 6,
      "vehicle_command_transmitted": false
    },
    "latency": {
      "fleet_shadow_run": {
        "count": 50,
        "min_ms": 0,
        "p50_ms": 0,
        "p95_ms": 0,
        "max_ms": 0,
        "mean_ms": 0
      }
    }
  }
}
```

The numeric zeros above describe field shape, not expected measurements; populate them through the existing `summarize()` function. Extend `backend/tests/test_benchmark.py` to assert six correctness stages, `iterations` latency samples, percentile ordering, and `vehicle_command_transmitted is False`. Do not add a millisecond pass/fail threshold.

- [ ] **Step 4: Run evidence and benchmark tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests\test_fleet_demo.py backend\tests\test_benchmark.py -q
.\.venv\Scripts\python.exe demo\run_fleet_scenario.py --output demo\artifacts\latest-fleet-evidence.json
.\.venv\Scripts\python.exe -m benchmarks.run_benchmark --iterations 5 --warmups 1
```

Expected: tests pass, the runner reports six successful stages and no commands, and the benchmark contains both the existing single-car results and the new fleet section.

- [ ] **Step 5: Update version metadata and capability documentation**

Set `package.json` and the FastAPI application version to `1.3.0`.

Update `README.md` with:

- fleet shadow as the first review evidence entry;
- a revised architecture diagram showing `FleetSnapshot -> fleet planner -> existing evaluate_decision -> FleetPlan -> SQLite/Web` and an explicit disconnect from commands;
- the three fleet API routes and their `201/200/409/404/410/422` behavior;
- browser `SIMULATED` versus API SQLite evidence labels;
- the exact evidence runner command;
- the new files in the project tree;
- unchanged claims that no real P5, site, sensor, trained ML, or live control was validated.

Update `docs/DEMO.md` with a fleet-first review flow: open the default fleet view, inspect stage 4, advance through capacity and window closure, connect the local API, rerun, and show the returned run ID and hashes. Keep the existing two-minute single-car demo as a separate section and preserve all record-only warnings.

Update `docs/BENCHMARK.md` with the fleet benchmark scope: six vehicles, deterministic correctness, local TestClient/SQLite latency, no network or hardware, no production SLO, and no comparison to official XPENG product metrics.

- [ ] **Step 6: Run documentation-linked commands and commit**

Run every command newly documented at least once, then run:

```powershell
rg -n "LIVE_CONTROL|real P5|实车验证|真实停车场验证|trained model" README.md docs demo/scenarios/fleet-rainstorm-v1.json
```

Expected: `LIVE_CONTROL` appears only in explicit rejection/non-goal explanations, and no text claims real vehicle, real site, real sensor, or trained-model validation.

Commit:

```powershell
git add demo/run_fleet_scenario.py backend/tests/test_fleet_demo.py benchmarks/run_benchmark.py backend/tests/test_benchmark.py backend/app/main.py package.json README.md docs/DEMO.md docs/BENCHMARK.md
git commit -m "docs: publish fleet shadow evidence workflow"
```

## Task 8: Full Regression And Visual Acceptance

**Files:**
- Modify after verified capture: `assets/highground-demo.png`
- Do not commit: `.qa/`, `demo/artifacts/`, `.superpowers/`, `.venv/`

- [ ] **Step 1: Run the complete automated regression matrix**

Run from the repository root:

```powershell
npm test
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe -m benchmarks.run_benchmark --iterations 50 --warmups 3
.\.venv\Scripts\python.exe demo\run_fleet_scenario.py --output demo\artifacts\latest-fleet-evidence.json
Push-Location p5-headunit
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug :app:lintDebug --no-daemon
Pop-Location
```

Expected: every Node and Python test passes, all six fleet stages pass, benchmark correctness is true, the runner records no transmitted command, Android JVM tests pass, the debug APK builds, and Android lint reports no errors.

- [ ] **Step 2: Start the API-backed Web app for visual QA**

Run:

```powershell
$env:HIGHGROUND_DATABASE_PATH = "$PWD\data\fleet-visual-qa.db"
$env:HIGHGROUND_API_KEY = "fleet-visual-qa-key"
$server = Start-Process -FilePath ".\.venv\Scripts\python.exe" -ArgumentList "-m","uvicorn","backend.app.main:app","--host","127.0.0.1","--port","8173" -PassThru -WindowStyle Hidden
Invoke-RestMethod http://127.0.0.1:8173/healthz
```

Expected health response: `status=ok`, `database=ok`, `actuator_mode=record-only`. Keep `$server` for cleanup in Step 5.

- [ ] **Step 3: Verify desktop rendering and interactions**

Use the in-app browser at `http://127.0.0.1:8173/` with a 1440x1000 viewport. Capture `.qa/fleet-desktop.png` and verify:

- the first viewport identifies HighGround AI and shows the fleet shadow dashboard, with a visible hint of timeline/evidence below;
- stage 4 contains two scheduled rows, one verify row, one route-denied row, and capacity refusals matching JSON;
- map pixels are nonblank, all six markers are visible, no text overlaps, and queue rows do not resize when selected;
- selecting `p5-03` filters evidence, and selecting it again restores all vehicles;
- next-stage transitions show capacity limitation and window closure without changing dashboard width;
- browser audit fields show em dashes and `SIMULATED · 浏览器规划 · 不写 SQLite`;
- connecting with `fleet-visual-qa-key` and submitting shows a real `fleet_` run ID and two SHA-256 values;
- switching to single-car view preserves the current console and lets its existing local decision run.

Replace `assets/highground-demo.png` only with the verified 1440x1000 fleet screenshot. Do not use a screenshot containing a failed request, stale badge, clipped row, or temporary API key.

- [ ] **Step 4: Verify narrow-screen rendering and request-race behavior**

Use 390x844 and 768x1024 viewports and capture `.qa/fleet-mobile-390.png` and `.qa/fleet-tablet-768.png`. Verify summary, queue, map, timeline, and evidence appear in that order; horizontal scrolling stays inside tables; all buttons fit; the SVG is nonblank; no labels overlap; and focus indicators remain visible.

While API mode is connected, trigger two stage changes before the first response completes. Confirm only the current snapshot's run ID and hashes render. Stop the API process temporarily and advance a stage; confirm the last plan stays visible with `SQLite 证据已过期 · 待提交新快照` and no browser plan is mixed into it.

- [ ] **Step 5: Stop QA services and remove only generated QA state**

Run:

```powershell
if ($server -and !$server.HasExited) { Stop-Process -Id $server.Id }
Remove-Item -LiteralPath "$PWD\data\fleet-visual-qa.db" -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$PWD\data\fleet-visual-qa.db-shm" -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$PWD\data\fleet-visual-qa.db-wal" -ErrorAction SilentlyContinue
git status --short
```

Expected: QA database files are gone; `.qa/`, `.venv/`, `demo/artifacts/`, and database files remain ignored; `.superpowers/` may remain untracked and must not be staged.

- [ ] **Step 6: Commit the verified screenshot and final fixes**

If visual QA required source fixes, rerun the focused Web tests and the complete regression matrix before this commit.

```powershell
git add assets/highground-demo.png index.html styles.css src/fleet-view.js src/fleet-view-state.js tests/fleet-view-state.test.mjs backend/tests/test_web_contract.py
git diff --cached --check
git commit -m "test: verify fleet shadow review experience"
```

Expected: the commit contains only the verified screenshot and any directly related visual/test fixes. If there were no source fixes and the screenshot was already committed in Task 7, omit empty paths and do not create an empty commit.

- [ ] **Step 7: Audit the final branch before handoff**

Run:

```powershell
git status --short
git log --oneline --decorate -10
git diff main...HEAD --stat
rg -n "owner_authorized|authorizations|commands/migrate|migrate_to_high_point" backend/app/fleet_models.py backend/app/fleet_planner.py src/fleet-planner.js src/fleet-view.js
```

Expected: only `.superpowers/` is untracked; fleet implementation commits are present; the diff is limited to phase A repository content; the final search finds no owner authorization, command route, or actuator call inside fleet modules. `ownerAuthorized: false` may appear only in the JavaScript adapter that explicitly disables authorization.

## Completion Evidence

The implementation is complete only when all of these are true:

- Six shared stages pass through Python, JavaScript, and direct parity comparison.
- POST, duplicate retry, conflict, detail, latest, stale, malformed, and authentication API contracts pass.
- SQLite contains atomic parent/child evidence with stable input and plan SHA-256 values.
- The default Web view visibly distinguishes browser simulation from API-backed SQLite evidence.
- Capacity exhaustion, site unavailability, route denial, and queue-window closure are preserved as auditable safe refusals.
- No fleet request accepts authorization or reaches command/actuator code.
- Existing single-car Node/Python behavior and Android build/test/lint remain green.
- Desktop, tablet, and mobile screenshots show a nonblank risk map without overlap or clipping.
- README, Demo, and Benchmark wording does not claim real P5, real parking-site, sensor, or trained-model validation.
