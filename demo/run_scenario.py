from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO = REPO_ROOT / "demo" / "scenarios" / "rainstorm-p5-120s.json"
DEFAULT_OUTPUT = REPO_ROOT / "demo" / "artifacts" / "latest-evidence.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ApiResponse:
    status: int
    payload: Any
    headers: dict[str, str]


class ApiClient(Protocol):
    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> ApiResponse: ...


class HttpApiClient:
    def __init__(self, api_url: str, api_key: str, timeout_seconds: float) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> ApiResponse:
        body = None
        headers = {
            "Accept": "application/json",
            "X-API-Key": self.api_key,
            "User-Agent": "highground-competition-demo/1.0",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.api_url}{path}",
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return ApiResponse(
                    status=response.status,
                    payload=_decode_json(response.read()),
                    headers=dict(response.headers.items()),
                )
        except urllib.error.HTTPError as error:
            return ApiResponse(
                status=error.code,
                payload=_decode_json(error.read()),
                headers=dict(error.headers.items()),
            )


def _decode_json(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def load_scenario(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        scenario = json.load(handle)
    if not isinstance(scenario, dict):
        raise ValueError("Scenario must contain one JSON object")
    steps = scenario.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("Scenario steps must be a non-empty array")
    previous_at = -1.0
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"Scenario step {index} must be an object")
        at_seconds = step.get("at_seconds")
        if not isinstance(at_seconds, (int, float)) or at_seconds < previous_at:
            raise ValueError("Scenario steps must have non-decreasing at_seconds")
        previous_at = float(at_seconds)
    duration = scenario.get("duration_seconds")
    if not isinstance(duration, (int, float)) or duration < previous_at:
        raise ValueError("duration_seconds must cover the final scenario step")
    return scenario


def _require_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _expect_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def _assert_decision(payload: object, expected: dict[str, object]) -> None:
    body = _require_mapping(payload, "response")
    result = _require_mapping(body.get("result"), "response.result")
    for field in ("decision", "risk_level", "permission"):
        if field in expected:
            _expect_equal(result.get(field), expected[field], field)


def _telemetry_payload(
    scenario: dict[str, object],
    step: dict[str, object],
    run_id: str,
    index: int,
) -> dict[str, object]:
    environment = deepcopy(_require_mapping(step.get("environment"), "step.environment"))
    vehicle = deepcopy(_require_mapping(scenario.get("base_vehicle"), "base_vehicle"))
    site = deepcopy(_require_mapping(scenario.get("base_site"), "base_site"))
    vehicle.update(_require_mapping(step.get("vehicle", {}), "step.vehicle"))
    site.update(_require_mapping(step.get("site", {}), "step.site"))
    return {
        "message_id": f"msg_demo_{run_id}_{index:02d}",
        "site_id": scenario["site_id"],
        "vehicle_id": scenario["vehicle_id"],
        "source_id": scenario["source_id"],
        "captured_at": utc_now().isoformat(),
        "environment": environment,
        "vehicle": vehicle,
        "site": site,
    }


def _redacted_response(action: str, payload: object) -> object:
    if action != "authorize" or not isinstance(payload, dict):
        return payload
    redacted = dict(payload)
    token = redacted.pop("authorization_token", None)
    if isinstance(token, str):
        redacted["authorization_token_sha256"] = hashlib.sha256(
            token.encode("utf-8")
        ).hexdigest()
    return redacted


def _step_request(
    *,
    client: ApiClient,
    scenario: dict[str, object],
    step: dict[str, object],
    run_id: str,
    index: int,
    state: dict[str, str],
) -> tuple[ApiResponse, str, str]:
    action = step.get("action")
    if action == "telemetry":
        response = client.request(
            "POST",
            "/api/v1/telemetry",
            _telemetry_payload(scenario, step, run_id, index),
        )
        if isinstance(response.payload, dict) and isinstance(response.payload.get("event_id"), str):
            state["event_id"] = response.payload["event_id"]
        return response, "POST", "/api/v1/telemetry"
    if action == "authorize":
        event_id = state.get("event_id")
        if not event_id:
            raise AssertionError("authorize step requires a preceding telemetry event")
        response = client.request(
            "POST",
            "/api/v1/authorizations",
            {"event_id": event_id, "owner_id": step.get("owner_id")},
        )
        if isinstance(response.payload, dict):
            token = response.payload.get("authorization_token")
            if isinstance(token, str):
                state["authorization_token"] = token
        return response, "POST", "/api/v1/authorizations"
    if action == "command":
        event_id = state.get("event_id")
        token = state.get("authorization_token")
        if not event_id or not token:
            raise AssertionError("command step requires event and authorization token")
        return (
            client.request(
                "POST",
                "/api/v1/commands/migrate",
                {"event_id": event_id, "authorization_token": token},
            ),
            "POST",
            "/api/v1/commands/migrate",
        )
    query = urllib.parse.urlencode(
        {"site_id": scenario["site_id"], "vehicle_id": scenario["vehicle_id"]}
    )
    if action == "events":
        return client.request("GET", f"/api/v1/events?{query}"), "GET", "/api/v1/events"
    if action == "latest":
        return (
            client.request("GET", f"/api/v1/decisions/latest?{query}"),
            "GET",
            "/api/v1/decisions/latest",
        )
    raise ValueError(f"Unknown scenario action: {action!r}")


def _assert_step(step: dict[str, object], response: ApiResponse) -> None:
    expected = _require_mapping(step.get("expect", {}), "step.expect")
    if "http_status" in expected:
        _expect_equal(response.status, expected["http_status"], "http_status")
    action = step.get("action")
    if action in {"telemetry", "latest"}:
        _assert_decision(response.payload, expected)
    elif action == "command":
        body = _require_mapping(response.payload, "response")
        for field in ("status", "actuator_mode"):
            if field in expected:
                _expect_equal(body.get(field), expected[field], field)
    elif action == "events" and "minimum_count" in expected:
        if not isinstance(response.payload, list):
            raise AssertionError("events response must be an array")
        if len(response.payload) < int(expected["minimum_count"]):
            raise AssertionError(
                f"minimum_count: expected at least {expected['minimum_count']}, "
                f"got {len(response.payload)}"
            )


def _record_only_preflight(client: ApiClient) -> dict[str, object]:
    response = client.request("GET", "/healthz")
    _expect_equal(response.status, 200, "health.http_status")
    body = _require_mapping(response.payload, "health.response")
    _expect_equal(body.get("status"), "ok", "health.status")
    _expect_equal(body.get("database"), "ok", "health.database")
    _expect_equal(body.get("actuator_mode"), "record-only", "health.actuator_mode")
    return {
        "request": {"method": "GET", "path": "/healthz"},
        "http_status": response.status,
        "response": body,
        "assertion": "passed",
    }


def _derive_execution_claims(records: list[dict[str, object]]) -> tuple[bool, bool]:
    command_records = [record for record in records if record.get("action") == "command"]
    if len(command_records) != 1:
        raise AssertionError(
            "canonical demo must contain exactly one verified command response"
        )
    command = _require_mapping(command_records[0].get("response"), "command.response")
    _expect_equal(command.get("actuator_mode"), "record-only", "command.actuator_mode")
    _expect_equal(command.get("status"), "RECORDED_NOT_SENT", "command.status")
    return command["actuator_mode"] == "record-only", command["status"] != "RECORDED_NOT_SENT"


def run_scenario(
    scenario: dict[str, object],
    client: ApiClient,
    *,
    time_scale: float = 1.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.perf_counter,
    progress: Callable[[str], None] | None = print,
) -> dict[str, object]:
    if time_scale < 0:
        raise ValueError("time_scale must be >= 0")
    preflight = _record_only_preflight(client)
    started_at = utc_now()
    started_clock = monotonic_fn()
    run_id = uuid4().hex[:12]
    state: dict[str, str] = {}
    records: list[dict[str, object]] = []
    steps = scenario["steps"]
    assert isinstance(steps, list)

    for index, raw_step in enumerate(steps):
        step = _require_mapping(raw_step, f"steps[{index}]")
        target_elapsed = float(step["at_seconds"]) * time_scale
        remaining = target_elapsed - (monotonic_fn() - started_clock)
        if remaining > 0:
            sleep_fn(remaining)
        if progress:
            progress(
                f"[{step['at_seconds']:>3}s] {step['name']} "
                f"({step['action']})"
            )
        request_started = monotonic_fn()
        response, method, path = _step_request(
            client=client,
            scenario=scenario,
            step=step,
            run_id=run_id,
            index=index,
            state=state,
        )
        latency_ms = round((monotonic_fn() - request_started) * 1000, 3)
        _assert_step(step, response)
        records.append(
            {
                "at_seconds": step["at_seconds"],
                "name": step["name"],
                "action": step["action"],
                "request": {"method": method, "path": path},
                "http_status": response.status,
                "latency_ms": latency_ms,
                "expected": step.get("expect", {}),
                "response": _redacted_response(str(step["action"]), response.payload),
                "assertion": "passed",
            }
        )

    record_only, vehicle_command_transmitted = _derive_execution_claims(records)
    finished_at = utc_now()
    wall_duration_seconds = max(0.0, monotonic_fn() - started_clock)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "scenario_id": scenario["scenario_id"],
        "title": scenario["title"],
        "vehicle_profile": deepcopy(
            _require_mapping(scenario.get("vehicle_profile"), "vehicle_profile")
        ),
        "status": "passed",
        "record_only": record_only,
        "vehicle_command_transmitted": vehicle_command_transmitted,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "scenario_duration_seconds": scenario["duration_seconds"],
        "wall_duration_seconds": round(wall_duration_seconds, 3),
        "time_scale": time_scale,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "preflight": preflight,
        "steps": records,
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run and assert the canonical HighGround competition demo over HTTP."
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument(
        "--api-url",
        default=os.getenv("HIGHGROUND_API_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("HIGHGROUND_API_KEY", "change-this-before-deploy"),
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--time-scale",
        type=float,
        default=1.0,
        help="1.0 runs the 120-second timeline; 0 runs immediately for CI.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    scenario = load_scenario(args.scenario)
    client = HttpApiClient(args.api_url, args.api_key, args.timeout)
    try:
        report = run_scenario(scenario, client, time_scale=args.time_scale)
    except (AssertionError, OSError, ValueError) as error:
        print(f"demo failed: {error}", file=sys.stderr)
        return 1
    write_report(args.output, report)
    print(f"PASS: {len(report['steps'])} steps; evidence: {args.output.resolve()}")
    print(
        "Vehicle command transmitted: "
        f"{str(report['vehicle_command_transmitted']).lower()} "
        f"(record_only={str(report['record_only']).lower()})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
