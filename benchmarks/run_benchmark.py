from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import platform
import sqlite3
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


DEFAULT_MATRIX_PATH = Path(__file__).with_name("scenarios.json")
API_KEY = "benchmark-local-api-key"
HEADERS = {"X-API-Key": API_KEY}


class BenchmarkAssertionError(RuntimeError):
    """Raised when the API no longer satisfies the benchmark contract."""


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_matrix(path: Path = DEFAULT_MATRIX_PATH) -> dict[str, Any]:
    matrix = json.loads(path.read_text(encoding="utf-8"))
    if matrix.get("schema_version") != 1:
        raise BenchmarkAssertionError("Unsupported benchmark matrix schema")
    scenarios = matrix.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise BenchmarkAssertionError("Benchmark matrix must contain scenarios")
    scenario_ids = [scenario.get("id") for scenario in scenarios]
    if len(scenario_ids) != len(set(scenario_ids)):
        raise BenchmarkAssertionError("Benchmark scenario ids must be unique")
    return matrix


def _payload(
    matrix: dict[str, Any],
    scenario: dict[str, Any],
    phase: str,
    iteration: int,
) -> dict[str, Any]:
    payload = _deep_merge(matrix["base_telemetry"], scenario["telemetry_patch"])
    scenario_id = scenario["id"]
    payload["message_id"] = f"msg_bench_{phase}_{iteration:05d}_{scenario_id}"
    payload["vehicle_id"] = f"vehicle-bench-{scenario_id}"
    payload["captured_at"] = datetime.now(timezone.utc).isoformat()
    return payload


def _require_equal(actual: Any, expected: Any, context: str) -> None:
    if actual != expected:
        raise BenchmarkAssertionError(
            f"{context}: expected {expected!r}, received {actual!r}"
        )


def _check_decision_response(
    response: Any,
    scenario: dict[str, Any],
    *,
    expected_status: int = 201,
) -> dict[str, Any]:
    scenario_id = scenario["id"]
    _require_equal(response.status_code, expected_status, f"{scenario_id} status")
    body = response.json()
    expected = scenario["expected"]
    result = body["result"]
    for field in ("decision", "risk_level", "permission", "authorized_to_move"):
        _require_equal(result[field], expected[field], f"{scenario_id} {field}")
    failed_gate_ids = [gate["id"] for gate in result["safety_gates"] if not gate["passed"]]
    _require_equal(
        failed_gate_ids,
        expected["failed_gate_ids"],
        f"{scenario_id} failed gates",
    )
    _require_equal(body["duplicate"], False, f"{scenario_id} duplicate flag")
    return body


def _elapsed_ms(start_ns: int) -> float:
    return (perf_counter_ns() - start_ns) / 1_000_000


def _nearest_rank(samples: list[float], percentile: float) -> float:
    if not samples:
        raise BenchmarkAssertionError("Cannot calculate a percentile without samples")
    ordered = sorted(samples)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def summarize(samples: list[float]) -> dict[str, int | float]:
    return {
        "count": len(samples),
        "min_ms": round(min(samples), 3),
        "p50_ms": round(_nearest_rank(samples, 0.50), 3),
        "p95_ms": round(_nearest_rank(samples, 0.95), 3),
        "max_ms": round(max(samples), 3),
        "mean_ms": round(sum(samples) / len(samples), 3),
    }


def _exercise_scenario(
    client: TestClient,
    matrix: dict[str, Any],
    scenario: dict[str, Any],
    phase: str,
    iteration: int,
    *,
    record_timings: bool,
) -> dict[str, float]:
    timings: dict[str, float] = {}
    payload = _payload(matrix, scenario, phase, iteration)

    started = perf_counter_ns()
    response = client.post("/api/v1/telemetry", json=payload, headers=HEADERS)
    ingest_ms = _elapsed_ms(started)
    body = _check_decision_response(response, scenario)
    if record_timings:
        timings["telemetry_ingest"] = ingest_ms

    started = perf_counter_ns()
    latest = client.get(
        "/api/v1/decisions/latest",
        params={"site_id": payload["site_id"], "vehicle_id": payload["vehicle_id"]},
        headers=HEADERS,
    )
    latest_ms = _elapsed_ms(started)
    _require_equal(latest.status_code, 200, f"{scenario['id']} latest status")
    latest_body = latest.json()
    _require_equal(latest_body["event_id"], body["event_id"], f"{scenario['id']} latest event")
    _require_equal(
        latest_body["result"]["decision"],
        scenario["expected"]["decision"],
        f"{scenario['id']} latest decision",
    )
    if record_timings:
        timings["latest_decision"] = latest_ms

    if scenario["expected"]["decision"] == "MIGRATE_NOW":
        started = perf_counter_ns()
        authorization = client.post(
            "/api/v1/authorizations",
            json={"event_id": body["event_id"], "owner_id": "owner-benchmark-01"},
            headers=HEADERS,
        )
        authorization_ms = _elapsed_ms(started)
        _require_equal(authorization.status_code, 201, "migration authorization status")

        started = perf_counter_ns()
        command = client.post(
            "/api/v1/commands/migrate",
            json={
                "event_id": body["event_id"],
                "authorization_token": authorization.json()["authorization_token"],
            },
            headers=HEADERS,
        )
        command_ms = _elapsed_ms(started)
        _require_equal(command.status_code, 202, "record-only command status")
        _require_equal(command.json()["status"], "RECORDED_NOT_SENT", "command result")
        _require_equal(command.json()["actuator_mode"], "record-only", "actuator mode")
        if record_timings:
            timings["authorization"] = authorization_ms
            timings["record_only_command"] = command_ms

    return timings


def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def run_benchmark(
    *,
    matrix_path: Path = DEFAULT_MATRIX_PATH,
    iterations: int = 50,
    warmups: int = 3,
) -> dict[str, Any]:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if warmups < 0:
        raise ValueError("warmups cannot be negative")

    matrix = load_matrix(matrix_path)
    scenarios = matrix["scenarios"]
    latency_samples: dict[str, list[float]] = {
        "telemetry_ingest": [],
        "latest_decision": [],
        "authorization": [],
        "record_only_command": [],
    }
    ingest_by_scenario: dict[str, list[float]] = {
        scenario["id"]: [] for scenario in scenarios
    }

    with tempfile.TemporaryDirectory(prefix="highground-benchmark-") as temp_dir:
        settings = Settings(
            database_path=Path(temp_dir) / "benchmark.db",
            api_key=API_KEY,
            environment="benchmark",
            actuator_mode="record-only",
            authorization_ttl_seconds=120,
            event_max_age_seconds=300,
            capture_max_age_seconds=300,
            capture_future_tolerance_seconds=30,
            allowed_origins=("http://testserver",),
        )
        _require_equal(settings.policy.to_dict(), matrix["policy"], "benchmark policy")

        with TestClient(create_app(settings)) as client:
            correctness_cases = []
            for index, scenario in enumerate(scenarios):
                _exercise_scenario(
                    client,
                    matrix,
                    scenario,
                    "correctness",
                    index,
                    record_timings=False,
                )
                correctness_cases.append(
                    {
                        "id": scenario["id"],
                        "decision": scenario["expected"]["decision"],
                        "passed": True,
                    }
                )

            for iteration in range(warmups):
                for scenario in scenarios:
                    _exercise_scenario(
                        client,
                        matrix,
                        scenario,
                        "warmup",
                        iteration,
                        record_timings=False,
                    )

            for iteration in range(iterations):
                for scenario in scenarios:
                    timings = _exercise_scenario(
                        client,
                        matrix,
                        scenario,
                        "measure",
                        iteration,
                        record_timings=True,
                    )
                    for operation, elapsed_ms in timings.items():
                        latency_samples[operation].append(elapsed_ms)
                    ingest_by_scenario[scenario["id"]].append(
                        timings["telemetry_ingest"]
                    )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Local in-process FastAPI TestClient + temporary SQLite; includes ASGI dispatch, "
            "validation, decision logic, persistence, and serialization; excludes network and TLS."
        ),
        "configuration": {
            "iterations_per_scenario": iterations,
            "warmup_iterations_per_scenario": warmups,
            "scenario_count": len(scenarios),
            "percentile_method": "nearest-rank",
            "actuator_mode": "record-only",
            "policy": matrix["policy"],
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or "not-reported",
            "sqlite": sqlite3.sqlite_version,
            "fastapi": _package_version("fastapi"),
            "starlette": _package_version("starlette"),
            "httpx2": _package_version("httpx2"),
        },
        "correctness": {
            "passed": True,
            "cases": correctness_cases,
            "vehicle_command_transmitted": False,
        },
        "latency": {
            "telemetry_ingest_all_scenarios": summarize(
                latency_samples["telemetry_ingest"]
            ),
            "telemetry_ingest_by_scenario": {
                scenario_id: summarize(samples)
                for scenario_id, samples in ingest_by_scenario.items()
            },
            "latest_decision": summarize(latency_samples["latest_decision"]),
            "migration_authorization": summarize(latency_samples["authorization"]),
            "record_only_command": summarize(latency_samples["record_only_command"]),
        },
    }


def _arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the deterministic HighGround in-process API benchmark."
    )
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX_PATH)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    report = run_benchmark(
        matrix_path=args.matrix,
        iterations=args.iterations,
        warmups=args.warmups,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Benchmark report written to {args.output}")
        print(json.dumps(report["latency"], ensure_ascii=False, indent=2))
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
