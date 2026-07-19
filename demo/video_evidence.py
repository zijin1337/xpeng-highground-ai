from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any


ACTION_REQUESTS = {
    "telemetry": {"method": "POST", "path": "/api/v1/telemetry"},
    "authorize": {"method": "POST", "path": "/api/v1/authorizations"},
    "command": {"method": "POST", "path": "/api/v1/commands/migrate"},
    "events": {"method": "GET", "path": "/api/v1/events"},
    "latest": {"method": "GET", "path": "/api/v1/decisions/latest"},
}
DECISION_FIELDS = ("decision", "risk_level", "permission")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
RUN_ID_PATTERN = re.compile(r"[0-9a-f]{12}")
CANONICAL_SCENARIO_ID = "rainstorm-p5-120s"
CANONICAL_STEP_SEQUENCE = (
    (0, "telemetry"),
    (20, "telemetry"),
    (45, "telemetry"),
    (70, "telemetry"),
    (85, "authorize"),
    (90, "command"),
    (105, "events"),
    (115, "telemetry"),
    (120, "latest"),
)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame_second(frame_index: int, frame_count: int, fps: float, duration: float) -> float:
    if frame_index < 0 or frame_index >= frame_count:
        raise ValueError("frame_index must be within the rendered frame range")
    if frame_count <= 1:
        return 0.0
    if frame_index == frame_count - 1:
        return duration
    return frame_index / fps


def contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(contains_key(item, key) for item in value)
    return False


def require_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{label} is not a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def find_unique_action_step(
    steps: list[dict[str, Any]],
    action: str,
) -> dict[str, Any]:
    matches = [step for step in steps if step.get("action") == action]
    if len(matches) != 1:
        raise ValueError(
            f"Evidence must contain exactly one {action!r} step; found {len(matches)}"
        )
    return matches[0]


def validate_step_response(
    index: int,
    action: str,
    expected: dict[str, Any],
    response: Any,
) -> None:
    if action in {"telemetry", "latest"}:
        if not isinstance(response, dict):
            raise ValueError(f"Evidence step {index} response must be an object")
        for field in ("event_id", "message_id", "input_sha256"):
            value = response.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"Evidence step {index} response is missing {field}")
        if SHA256_PATTERN.fullmatch(response["input_sha256"]) is None:
            raise ValueError(f"Evidence step {index} response has an invalid input_sha256")
        require_timestamp(response.get("received_at"), f"Evidence step {index} received_at")
        result = response.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"Evidence step {index} decision result is missing")
        for field in DECISION_FIELDS:
            if field not in expected:
                raise ValueError(
                    f"Scenario step {index} is missing expected decision field {field!r}"
                )
            if field not in result or result[field] != expected[field]:
                raise ValueError(
                    f"Evidence step {index} response has the wrong {field}"
                )
        return

    if action == "command":
        if not isinstance(response, dict):
            raise ValueError("Command evidence response is missing")
        for field in ("command_id", "event_id"):
            value = response.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"Command evidence is missing {field}")
        for field in ("status", "actuator_mode"):
            if field not in expected:
                raise ValueError(
                    f"Scenario command step is missing expected field {field!r}"
                )
            if field not in response or response[field] != expected[field]:
                raise ValueError(f"Command evidence has the wrong {field}")
        return

    if action == "events":
        minimum_count = expected.get("minimum_count")
        if not isinstance(minimum_count, int) or isinstance(minimum_count, bool):
            raise ValueError("Scenario events step minimum_count is missing")
        if not isinstance(response, list):
            raise ValueError("Events evidence response must be an array")
        if len(response) < minimum_count:
            raise ValueError(
                "Events evidence response does not meet the expected minimum_count"
            )
        for event_index, event in enumerate(response):
            if not isinstance(event, dict):
                raise ValueError(f"Events evidence item {event_index} must be an object")
            for field in ("event_id", "message_id", "input_sha256"):
                value = event.get(field)
                if not isinstance(value, str) or not value:
                    raise ValueError(
                        f"Events evidence item {event_index} is missing {field}"
                    )
            if SHA256_PATTERN.fullmatch(event["input_sha256"]) is None:
                raise ValueError(
                    f"Events evidence item {event_index} has an invalid input_sha256"
                )
            require_timestamp(
                event.get("received_at"),
                f"Events evidence item {event_index} received_at",
            )
        return

    if action == "authorize":
        if not isinstance(response, dict):
            raise ValueError("Authorization evidence response must be an object")
        for field in ("authorization_id", "event_id"):
            value = response.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"Authorization evidence is missing {field}")
        token_hash = response.get("authorization_token_sha256")
        if (
            not isinstance(token_hash, str)
            or re.fullmatch(r"[0-9a-f]{64}", token_hash) is None
        ):
            raise ValueError(
                "Authorization evidence requires a 64-character SHA-256 token hash"
            )
        require_timestamp(response.get("expires_at"), "Authorization expires_at")


def _validate_event_chain(evidence_steps: list[dict[str, Any]]) -> None:
    telemetry_events: list[tuple[int, str, str, str]] = []
    telemetry_by_event: dict[str, tuple[str, str]] = {}
    telemetry_message_ids: set[str] = set()
    migration_event_ids: list[str] = []
    authorization_steps: list[dict[str, Any]] = []
    command_steps: list[dict[str, Any]] = []
    latest_steps: list[dict[str, Any]] = []
    event_list_steps: list[dict[str, Any]] = []

    for step_index, step in enumerate(evidence_steps):
        action = step["action"]
        response = step.get("response")
        if action in {"telemetry", "latest"} and isinstance(response, dict):
            event_id = response["event_id"]
            message_id = response["message_id"]
            input_sha256 = response["input_sha256"]
            if action == "telemetry":
                if event_id in telemetry_by_event or message_id in telemetry_message_ids:
                    raise ValueError("Telemetry evidence contains duplicate event identifiers")
                telemetry_events.append(
                    (step_index, event_id, message_id, input_sha256)
                )
                telemetry_by_event[event_id] = (message_id, input_sha256)
                telemetry_message_ids.add(message_id)
                result = response.get("result")
                if isinstance(result, dict) and result.get("decision") == "MIGRATE_NOW":
                    migration_event_ids.append(event_id)
            else:
                latest_steps.append(step)
        elif action == "authorize":
            authorization_steps.append(step)
        elif action == "command":
            command_steps.append(step)
        elif action == "events":
            event_list_steps.append(step)

    if not telemetry_events:
        raise ValueError("Evidence must contain telemetry event identifiers")
    if len(migration_event_ids) != 1:
        raise ValueError("Evidence must contain exactly one MIGRATE_NOW event identifier")
    migration_event_id = migration_event_ids[0]

    if len(authorization_steps) != 1:
        raise ValueError("Evidence must contain exactly one authorization step")
    authorization_response = authorization_steps[0]["response"]
    if authorization_response["event_id"] != migration_event_id:
        raise ValueError("Authorization event_id does not match the MIGRATE_NOW event")

    if len(command_steps) != 1:
        raise ValueError("Evidence must contain exactly one command step")
    command_response = command_steps[0]["response"]
    if command_response["event_id"] != authorization_response["event_id"]:
        raise ValueError("Command event_id does not match the authorized event")

    if len(event_list_steps) != 1:
        raise ValueError("Evidence must contain exactly one events query")
    listed_events = event_list_steps[0]["response"]
    events_step_index = evidence_steps.index(event_list_steps[0])
    expected_listed_event_ids = {
        event_id
        for step_index, event_id, _, _ in telemetry_events
        if step_index < events_step_index
    }
    listed_event_ids: set[str] = set()
    for event in listed_events:
        event_id = event["event_id"]
        if event_id in listed_event_ids:
            raise ValueError("Events evidence contains a duplicate event_id")
        listed_event_ids.add(event_id)
        if event_id not in telemetry_by_event:
            raise ValueError("Events evidence contains an unknown event_id")
        expected_message_id, expected_input_sha256 = telemetry_by_event[event_id]
        if expected_message_id != event["message_id"]:
            raise ValueError("Events evidence message_id does not match its event_id")
        if expected_input_sha256 != event["input_sha256"]:
            raise ValueError("Events evidence input_sha256 does not match its event_id")
    if migration_event_id not in listed_event_ids:
        raise ValueError("Events evidence does not contain the authorized event")
    if listed_event_ids != expected_listed_event_ids:
        raise ValueError("Events evidence does not match all prior telemetry events")

    if len(latest_steps) != 1:
        raise ValueError("Evidence must contain exactly one latest-decision query")
    latest_response = latest_steps[0]["response"]
    _, expected_event_id, expected_message_id, expected_input_sha256 = telemetry_events[-1]
    if latest_response["event_id"] != expected_event_id:
        raise ValueError("Latest evidence event_id does not match the newest telemetry")
    if latest_response["message_id"] != expected_message_id:
        raise ValueError("Latest evidence message_id does not match the newest telemetry")
    if latest_response["input_sha256"] != expected_input_sha256:
        raise ValueError("Latest evidence input_sha256 does not match the newest telemetry")


def _validate_run_timing(
    evidence_steps: list[dict[str, Any]],
    started_at: datetime,
    finished_at: datetime,
    wall_duration: float,
    time_scale: float,
) -> None:
    timestamp_duration = (finished_at - started_at).total_seconds()
    if abs(timestamp_duration - wall_duration) > 2:
        raise ValueError("Evidence timestamps do not match wall_duration_seconds")

    previous_received_at: datetime | None = None
    for index, step in enumerate(evidence_steps):
        if step["action"] != "telemetry":
            continue
        response = step["response"]
        received_at = require_timestamp(
            response.get("received_at"),
            f"Evidence step {index} received_at",
        )
        if not started_at <= received_at <= finished_at:
            raise ValueError(f"Evidence step {index} received_at is outside the run")
        if previous_received_at is not None and received_at < previous_received_at:
            raise ValueError("Telemetry received_at timestamps are not monotonic")
        expected_elapsed = float(step["at_seconds"]) * time_scale
        actual_elapsed = (received_at - started_at).total_seconds()
        if abs(actual_elapsed - expected_elapsed) > 5:
            raise ValueError(
                f"Evidence step {index} received_at does not match its timeline offset"
            )
        previous_received_at = received_at


def _validate_telemetry_payloads(
    scenario: dict[str, Any],
    evidence_steps: list[dict[str, Any]],
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    telemetry_response_by_message = {
        step["response"]["message_id"]: step["response"]
        for step in evidence_steps
        if step["action"] == "telemetry"
    }
    persisted_by_message: dict[str, dict[str, Any]] = {}
    for step in evidence_steps:
        response = step["response"]
        records: list[dict[str, Any]] = []
        if step["action"] == "events":
            records = response
        elif step["action"] == "latest":
            records = [response]
        for record in records:
            telemetry = record.get("telemetry")
            if not isinstance(telemetry, dict):
                raise ValueError("Persisted event evidence is missing telemetry")
            message_id = record["message_id"]
            if telemetry.get("message_id") != message_id:
                raise ValueError("Persisted telemetry message_id does not match its event")
            if message_id in persisted_by_message:
                raise ValueError("Persisted telemetry evidence contains a duplicate message_id")
            telemetry_response = telemetry_response_by_message.get(message_id)
            if telemetry_response is None:
                raise ValueError("Persisted event has no matching telemetry response")
            if record.get("result") != telemetry_response.get("result"):
                raise ValueError(
                    f"Persisted event result does not match telemetry response {message_id}"
                )
            captured_at = require_timestamp(
                telemetry.get("captured_at"),
                f"Persisted telemetry {message_id} captured_at",
            )
            received_at = require_timestamp(
                record.get("received_at"),
                f"Persisted telemetry {message_id} received_at",
            )
            if not started_at <= captured_at <= received_at <= finished_at:
                raise ValueError(
                    f"Persisted telemetry {message_id} timestamps are outside the run"
                )
            persisted_by_message[message_id] = telemetry

    base_vehicle = scenario.get("base_vehicle")
    base_site = scenario.get("base_site")
    if not isinstance(base_vehicle, dict) or not isinstance(base_site, dict):
        raise ValueError("Canonical scenario base telemetry is missing")

    expected_message_ids: set[str] = set()
    for index, step in enumerate(scenario["steps"]):
        if step["action"] != "telemetry":
            continue
        message_id = f"msg_demo_{run_id}_{index:02d}"
        expected_message_ids.add(message_id)
        telemetry = persisted_by_message.get(message_id)
        if telemetry is None:
            raise ValueError(f"Persisted telemetry is missing {message_id}")
        expected_vehicle = dict(base_vehicle)
        expected_vehicle.update(step.get("vehicle", {}))
        expected_site = dict(base_site)
        expected_site.update(step.get("site", {}))
        expected_fields = {
            "site_id": scenario.get("site_id"),
            "vehicle_id": scenario.get("vehicle_id"),
            "source_id": scenario.get("source_id"),
            "environment": step.get("environment"),
            "vehicle": expected_vehicle,
            "site": expected_site,
        }
        for field, expected_value in expected_fields.items():
            if telemetry.get(field) != expected_value:
                raise ValueError(
                    f"Persisted telemetry {message_id} does not match scenario field {field}"
                )
    if set(persisted_by_message) != expected_message_ids:
        raise ValueError("Persisted telemetry evidence does not match the canonical run")


def validate_inputs(scenario: dict[str, Any], evidence: dict[str, Any]) -> None:
    if scenario.get("duration_seconds") != 120:
        raise ValueError("Canonical video requires a 120-second scenario")
    if scenario.get("scenario_id") != CANONICAL_SCENARIO_ID:
        raise ValueError("Canonical video requires the rainstorm-p5-120s scenario")
    if evidence.get("scenario_id") != scenario.get("scenario_id"):
        raise ValueError("Evidence and scenario IDs do not match")
    if evidence.get("status") != "passed":
        raise ValueError("Evidence status must be passed")
    run_id = evidence.get("run_id")
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("Evidence run_id must be 12 lowercase hexadecimal characters")
    started_at = require_timestamp(evidence.get("started_at"), "Evidence started_at")
    finished_at = require_timestamp(evidence.get("finished_at"), "Evidence finished_at")
    if finished_at < started_at:
        raise ValueError("Evidence finished_at must not precede started_at")
    if evidence.get("scenario_duration_seconds") != scenario.get("duration_seconds"):
        raise ValueError("Evidence and scenario durations do not match")
    vehicle_profile = scenario.get("vehicle_profile")
    if not isinstance(vehicle_profile, dict):
        raise ValueError("Scenario vehicle profile is missing")
    if evidence.get("vehicle_profile") != vehicle_profile:
        raise ValueError("Evidence and scenario vehicle profiles do not match")
    if evidence.get("record_only") is not True:
        raise ValueError("Evidence must prove record_only=true")
    if evidence.get("vehicle_command_transmitted") is not False:
        raise ValueError("Evidence must prove vehicle_command_transmitted=false")
    time_scale = evidence.get("time_scale")
    if not isinstance(time_scale, (int, float)) or isinstance(time_scale, bool):
        raise ValueError("Competition video evidence time_scale is missing")
    if float(time_scale) != 1.0:
        raise ValueError("Competition video requires evidence captured with time_scale=1")
    raw_wall_duration = evidence.get("wall_duration_seconds")
    if not isinstance(raw_wall_duration, (int, float)) or isinstance(
        raw_wall_duration, bool
    ):
        raise ValueError("Evidence wall duration is missing")
    wall_duration = float(raw_wall_duration)
    if not 119.5 <= wall_duration <= 130:
        raise ValueError("Competition evidence must prove a complete 120-second run")
    if contains_key(evidence, "authorization_token"):
        raise ValueError("Evidence contains an unredacted authorization token")

    preflight = evidence.get("preflight")
    if not isinstance(preflight, dict):
        raise ValueError("Evidence record-only preflight is missing")
    if preflight.get("assertion") != "passed":
        raise ValueError("Evidence record-only preflight did not pass")
    if preflight.get("http_status") != 200:
        raise ValueError("Evidence record-only preflight must return HTTP 200")
    if preflight.get("request") != {"method": "GET", "path": "/healthz"}:
        raise ValueError("Evidence preflight must call GET /healthz")
    preflight_response = preflight.get("response")
    if not isinstance(preflight_response, dict):
        raise ValueError("Evidence record-only preflight response is missing")
    if preflight_response.get("status") != "ok":
        raise ValueError("Evidence preflight service status must be ok")
    if preflight_response.get("database") != "ok":
        raise ValueError("Evidence preflight database status must be ok")
    if preflight_response.get("actuator_mode") != "record-only":
        raise ValueError("Evidence preflight actuator mode must be record-only")

    scenario_steps = scenario.get("steps")
    evidence_steps = evidence.get("steps")
    if not isinstance(scenario_steps, list) or not isinstance(evidence_steps, list):
        raise ValueError("Scenario and evidence steps must be arrays")
    scenario_sequence = tuple(
        (step.get("at_seconds"), step.get("action"))
        for step in scenario_steps
        if isinstance(step, dict)
    )
    if scenario_sequence != CANONICAL_STEP_SEQUENCE:
        raise ValueError("Canonical scenario timeline or action sequence has changed")
    if len(scenario_steps) != len(evidence_steps):
        raise ValueError("Evidence must contain every canonical scenario step")
    for index, (expected, actual) in enumerate(zip(scenario_steps, evidence_steps)):
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            raise ValueError(f"Scenario and evidence step {index} must be objects")
        if actual.get("assertion") != "passed":
            raise ValueError(f"Evidence step {index} did not pass")
        if actual.get("at_seconds") != expected.get("at_seconds"):
            raise ValueError(f"Evidence step {index} has the wrong timestamp")
        if actual.get("action") != expected.get("action"):
            raise ValueError(f"Evidence step {index} has the wrong action")
        latency_ms = actual.get("latency_ms")
        if (
            not isinstance(latency_ms, (int, float))
            or isinstance(latency_ms, bool)
            or not math.isfinite(float(latency_ms))
            or float(latency_ms) < 0
        ):
            raise ValueError(f"Evidence step {index} has an invalid latency_ms")
        action = expected.get("action")
        if not isinstance(action, str) or action not in ACTION_REQUESTS:
            raise ValueError(f"Scenario step {index} has an unsupported action")
        if actual.get("request") != ACTION_REQUESTS[action]:
            raise ValueError(f"Evidence step {index} has the wrong request")
        step_expectation = expected.get("expect")
        if not isinstance(step_expectation, dict):
            raise ValueError(f"Scenario step {index} expectation is missing")
        if actual.get("expected") != step_expectation:
            raise ValueError(f"Evidence step {index} expectation does not match scenario")
        if "http_status" not in step_expectation:
            raise ValueError(f"Scenario step {index} expected HTTP status is missing")
        if actual.get("http_status") != step_expectation["http_status"]:
            raise ValueError(f"Evidence step {index} has the wrong HTTP status")
        validate_step_response(
            index,
            action,
            step_expectation,
            actual.get("response"),
        )
        if action == "telemetry":
            expected_message_id = f"msg_demo_{run_id}_{index:02d}"
            response = actual["response"]
            if response["message_id"] != expected_message_id:
                raise ValueError(
                    f"Evidence step {index} message_id does not match its run_id"
                )
    command_step = find_unique_action_step(evidence_steps, "command")
    command_response = command_step.get("response")
    if not isinstance(command_response, dict):
        raise ValueError("Command evidence response is missing")
    if command_response.get("status") != "RECORDED_NOT_SENT":
        raise ValueError("Command evidence must be RECORDED_NOT_SENT")
    if command_response.get("actuator_mode") != "record-only":
        raise ValueError("Command evidence actuator mode must be record-only")
    _validate_event_chain(evidence_steps)
    _validate_run_timing(
        evidence_steps,
        started_at,
        finished_at,
        wall_duration,
        float(time_scale),
    )
    _validate_telemetry_payloads(
        scenario,
        evidence_steps,
        run_id,
        started_at,
        finished_at,
    )
