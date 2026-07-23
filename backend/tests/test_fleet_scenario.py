from __future__ import annotations

import json
import subprocess

from backend.app.config import DecisionPolicy
from backend.app.fleet_models import FleetSnapshot
from backend.app.fleet_planner import plan_fleet
from backend.tests.fleet_fixtures import (
    FIXED_NOW,
    FLEET_SCENARIO_PATH,
    REPO_ROOT,
    load_fleet_scenario,
)


def project(plan) -> dict[str, object]:
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


def python_projections() -> dict[str, dict[str, object]]:
    scenario = load_fleet_scenario()
    projections: dict[str, dict[str, object]] = {}
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
        projections[stage["stage_id"]] = project(plan)
    return projections


def test_all_six_python_stages_match_the_shared_contract() -> None:
    scenario = load_fleet_scenario()
    assert scenario["schema_version"] == 1
    assert scenario["planner_version"] == "fleet-shadow-v1"
    assert scenario["default_stage_index"] == 3
    assert len(scenario["stages"]) == 6

    projections = python_projections()
    for stage in scenario["stages"]:
        assert projections[stage["stage_id"]] == stage["expect"]


def test_javascript_and_python_scenario_projections_are_identical() -> None:
    result = subprocess.run(
        [
            "node",
            str(REPO_ROOT / "tests" / "fleet-planner-cli.mjs"),
            str(FLEET_SCENARIO_PATH),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    javascript = {
        item["stage_id"]: item["projection"]
        for item in json.loads(result.stdout)
    }
    assert javascript == python_projections()
