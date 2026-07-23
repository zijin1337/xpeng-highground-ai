from __future__ import annotations

import json

from demo.run_fleet_scenario import run_fleet_scenario


def test_fleet_evidence_runs_all_stages_without_vehicle_control(tmp_path) -> None:
    output = tmp_path / "fleet-evidence.json"
    report = run_fleet_scenario(output_path=output)
    rendered = output.read_text(encoding="utf-8")
    persisted = json.loads(rendered)

    assert report == persisted
    assert rendered.endswith("\n")
    assert report["schema_version"] == 1
    assert report["planner_version"] == "fleet-shadow-v1"
    assert report["stage_count"] == 6
    assert report["assertions_passed"] is True
    assert report["vehicle_command_transmitted"] is False
    assert report["database_counts"] == {
        "fleet_runs": 6,
        "fleet_vehicle_plans": 36,
        "authorizations": 0,
        "commands": 0,
    }
    assert all(stage["http_status"] == 201 for stage in report["stages"])
    assert all(len(stage["run_id"]) > 10 for stage in report["stages"])
    assert all(len(stage["input_sha256"]) == 64 for stage in report["stages"])
    assert all(len(stage["plan_sha256"]) == 64 for stage in report["stages"])
    assert "api_key" not in rendered.lower()
    assert "authorization_token" not in rendered.lower()
