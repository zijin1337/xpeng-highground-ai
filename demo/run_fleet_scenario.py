from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter_ns


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.tests.fleet_fixtures import load_fleet_scenario


DEFAULT_OUTPUT = REPO_ROOT / "demo" / "artifacts" / "latest-fleet-evidence.json"
FLEET_DEMO_API_KEY = "fleet-demo-local-only-api-key"
HEADERS = {"X-API-Key": FLEET_DEMO_API_KEY}


def _project_plan(plan: dict[str, object]) -> dict[str, object]:
    return {
        "summary": plan["summary"],
        "vehicles": [
            {
                "vehicle_id": vehicle["vehicle_id"],
                "decision": vehicle["base_decision"]["decision"],
                "allocation_status": vehicle["allocation_status"],
                "rank": vehicle["rank"],
                "batch_index": vehicle["batch_index"],
                "queue_ahead": vehicle["queue_ahead"],
                "safe_point_id": vehicle["safe_point_id"],
                "action_permission": vehicle["action_permission"],
                "authorized_to_move": vehicle["authorized_to_move"],
            }
            for vehicle in plan["vehicles"]
        ],
    }


def _fresh_snapshot(
    stage: dict[str, object],
    captured_at: datetime,
) -> dict[str, object]:
    snapshot = deepcopy(stage["snapshot"])
    timestamp = captured_at.isoformat()
    snapshot["captured_at"] = timestamp
    snapshot["site"]["observed_at"] = timestamp
    for vehicle in snapshot["vehicles"]:
        vehicle["telemetry"]["captured_at"] = timestamp
    return snapshot


def _database_counts(database_path: Path) -> dict[str, int]:
    tables = ("fleet_runs", "fleet_vehicle_plans", "authorizations", "commands")
    connection = sqlite3.connect(database_path)
    try:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }
    finally:
        connection.close()


def run_fleet_scenario(*, output_path: Path | None = None) -> dict[str, object]:
    scenario = load_fleet_scenario()
    base_time = datetime.now(timezone.utc)

    with tempfile.TemporaryDirectory(prefix="highground-fleet-demo-") as temp_dir:
        database_path = Path(temp_dir) / "fleet-evidence.db"
        settings = Settings(
            database_path=database_path,
            api_key=FLEET_DEMO_API_KEY,
            environment="fleet-demo",
            actuator_mode="record-only",
            authorization_ttl_seconds=120,
            event_max_age_seconds=300,
            capture_max_age_seconds=300,
            capture_future_tolerance_seconds=30,
            allowed_origins=("http://testserver",),
        )

        stage_reports: list[dict[str, object]] = []
        with TestClient(create_app(settings)) as client:
            for index, stage in enumerate(scenario["stages"]):
                snapshot = _fresh_snapshot(
                    stage,
                    base_time + timedelta(seconds=index),
                )
                started = perf_counter_ns()
                response = client.post(
                    "/api/v1/fleet/shadow-runs",
                    json=snapshot,
                    headers=HEADERS,
                )
                elapsed_ms = (perf_counter_ns() - started) / 1_000_000
                assert response.status_code == 201, (
                    f"{stage['stage_id']} POST returned {response.status_code}: "
                    f"{response.text}"
                )
                plan = response.json()
                assert _project_plan(plan) == stage["expect"]
                assert all(
                    vehicle["authorized_to_move"] is False
                    for vehicle in plan["vehicles"]
                )

                detail = client.get(
                    f"/api/v1/fleet/shadow-runs/{plan['run_id']}",
                    headers=HEADERS,
                )
                assert detail.status_code == 200
                assert detail.json() == plan

                stage_reports.append(
                    {
                        "stage_id": stage["stage_id"],
                        "label": stage["label"],
                        "http_status": response.status_code,
                        "run_id": plan["run_id"],
                        "input_sha256": plan["input_sha256"],
                        "plan_sha256": plan["plan_sha256"],
                        "source_mode": plan["source_mode"],
                        "summary": plan["summary"],
                        "vehicles": _project_plan(plan)["vehicles"],
                        "elapsed_ms": round(elapsed_ms, 3),
                        "assertions_passed": True,
                    }
                )

            latest = client.get(
                "/api/v1/fleet/latest",
                params={"site_id": scenario["stages"][-1]["snapshot"]["site_id"]},
                headers=HEADERS,
            )
            assert latest.status_code == 200
            assert latest.json()["run_id"] == stage_reports[-1]["run_id"]

        counts = _database_counts(database_path)
        assert counts == {
            "fleet_runs": 6,
            "fleet_vehicle_plans": 36,
            "authorizations": 0,
            "commands": 0,
        }

    report: dict[str, object] = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "planner_version": scenario["planner_version"],
        "stage_count": len(stage_reports),
        "assertions_passed": True,
        "vehicle_command_transmitted": False,
        "actuator_mode": "record-only",
        "data_claim": "repository simulated scenario; no P5, parking site, or sensor validation",
        "database_counts": counts,
        "latest_run_id": stage_reports[-1]["run_id"],
        "stages": stage_reports,
    }
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return report


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run all six fleet shadow stages through FastAPI and SQLite."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    report = run_fleet_scenario(output_path=args.output)
    print(f"Fleet evidence written to {args.output}")
    print(
        json.dumps(
            {
                "stage_count": report["stage_count"],
                "assertions_passed": report["assertions_passed"],
                "vehicle_command_transmitted": report["vehicle_command_transmitted"],
                "latest_run_id": report["latest_run_id"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["assertions_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
