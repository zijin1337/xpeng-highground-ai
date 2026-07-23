from __future__ import annotations

from benchmarks.run_benchmark import load_matrix, run_benchmark


def test_benchmark_matrix_covers_decisions_and_produces_latency_report():
    matrix = load_matrix()
    decisions = {scenario["expected"]["decision"] for scenario in matrix["scenarios"]}
    assert decisions == {
        "STAY",
        "WATCH",
        "PREPARE",
        "MIGRATE_NOW",
        "VERIFY_ONLY",
        "NO_GO",
        "EMERGENCY_STOP",
    }

    report = run_benchmark(iterations=2, warmups=0)

    assert report["correctness"]["passed"] is True
    assert report["correctness"]["vehicle_command_transmitted"] is False
    assert len(report["correctness"]["cases"]) == len(matrix["scenarios"])

    latency = report["latency"]
    scenario_count = len(matrix["scenarios"])
    assert latency["telemetry_ingest_all_scenarios"]["count"] == scenario_count * 2
    assert latency["latest_decision"]["count"] == scenario_count * 2
    assert latency["migration_authorization"]["count"] == 2
    assert latency["record_only_command"]["count"] == 2
    for summary in latency["telemetry_ingest_by_scenario"].values():
        assert summary["count"] == 2
        assert summary["min_ms"] <= summary["p50_ms"] <= summary["p95_ms"]
        assert summary["p95_ms"] <= summary["max_ms"]

    fleet = report["fleet_shadow"]
    assert fleet["correctness"] == {
        "passed": True,
        "stage_count": 6,
        "vehicle_count_per_stage": 6,
        "vehicle_command_transmitted": False,
    }
    fleet_latency = fleet["latency"]["fleet_shadow_run"]
    assert fleet_latency["count"] == 2
    assert (
        fleet_latency["min_ms"]
        <= fleet_latency["p50_ms"]
        <= fleet_latency["p95_ms"]
        <= fleet_latency["max_ms"]
    )
