from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from demo.video_evidence import frame_second, load_json, validate_inputs


REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_PATH = REPO_ROOT / "demo" / "scenarios" / "rainstorm-p5-120s.json"


def test_render_timeline_maps_final_frame_to_scenario_endpoint() -> None:
    assert frame_second(0, 2880, 24, 120) == 0
    assert frame_second(2878, 2880, 24, 120) < 120
    assert frame_second(2879, 2880, 24, 120) == 120


def canonical_inputs() -> tuple[dict[str, object], dict[str, object]]:
    scenario = load_json(SCENARIO_PATH)
    run_id = "abcdef123456"
    timestamp = "2026-07-19T00:00:00Z"

    def timestamp_at(seconds: int) -> str:
        value = datetime(2026, 7, 19, tzinfo=timezone.utc) + timedelta(seconds=seconds)
        return value.isoformat().replace("+00:00", "Z")

    telemetry_ids = {
        index: (f"evt_fixture_{index}", f"msg_demo_{run_id}_{index:02d}")
        for index, step in enumerate(scenario["steps"])
        if step["action"] == "telemetry"
    }
    migration_event_id = telemetry_ids[3][0]

    def fixture_telemetry(index: int) -> dict[str, object]:
        step = scenario["steps"][index]
        vehicle = dict(scenario["base_vehicle"])
        vehicle.update(step.get("vehicle", {}))
        site = dict(scenario["base_site"])
        site.update(step.get("site", {}))
        return {
            "message_id": telemetry_ids[index][1],
            "site_id": scenario["site_id"],
            "vehicle_id": scenario["vehicle_id"],
            "source_id": scenario["source_id"],
            "captured_at": timestamp_at(step["at_seconds"]),
            "environment": deepcopy(step["environment"]),
            "vehicle": vehicle,
            "site": site,
        }

    evidence_steps = []
    for index, step in enumerate(scenario["steps"]):
        action = step["action"]
        expected = deepcopy(step["expect"])
        requests = {
            "telemetry": {"method": "POST", "path": "/api/v1/telemetry"},
            "authorize": {"method": "POST", "path": "/api/v1/authorizations"},
            "command": {"method": "POST", "path": "/api/v1/commands/migrate"},
            "events": {"method": "GET", "path": "/api/v1/events"},
            "latest": {"method": "GET", "path": "/api/v1/decisions/latest"},
        }
        if action in {"telemetry", "latest"}:
            response = {
                "event_id": (
                    telemetry_ids[index][0]
                    if action == "telemetry"
                    else telemetry_ids[7][0]
                ),
                "message_id": (
                    telemetry_ids[index][1]
                    if action == "telemetry"
                    else telemetry_ids[7][1]
                ),
                    "input_sha256": "a" * 64,
                "received_at": timestamp_at(
                    step["at_seconds"] if action == "telemetry" else 115
                ),
                    **(
                        {"telemetry": fixture_telemetry(7)}
                        if action == "latest"
                        else {}
                    ),
                    "result": {
                    field: expected[field]
                    for field in ("decision", "risk_level", "permission")
                }
            }
        elif action == "authorize":
            response = {
                "authorization_id": "auth_fixture",
                "event_id": migration_event_id,
                "authorization_token_sha256": "a" * 64,
                "expires_at": "2026-07-19T00:01:00Z",
            }
        elif action == "command":
            response = {
                "command_id": "cmd_fixture",
                "event_id": migration_event_id,
                "status": "RECORDED_NOT_SENT",
                "actuator_mode": "record-only",
            }
        elif action == "events":
            response = [
                {
                    "event_id": telemetry_ids[event_index][0],
                    "message_id": telemetry_ids[event_index][1],
                    "input_sha256": "a" * 64,
                    "received_at": timestamp_at(
                        scenario["steps"][event_index]["at_seconds"]
                    ),
                    "telemetry": fixture_telemetry(event_index),
                    "result": evidence_steps[event_index]["response"]["result"],
                }
                for event_index in (3, 2, 1, 0)
            ]
        else:
            raise AssertionError(f"Unexpected canonical action: {action}")
        evidence_steps.append(
            {
                "at_seconds": step["at_seconds"],
                "action": action,
                "request": requests[action],
                "http_status": expected["http_status"],
                "latency_ms": 1.0,
                "expected": expected,
                "assertion": "passed",
                "response": response,
            }
        )
    evidence = {
        "run_id": run_id,
        "scenario_id": scenario["scenario_id"],
        "scenario_duration_seconds": scenario["duration_seconds"],
        "vehicle_profile": deepcopy(scenario["vehicle_profile"]),
        "status": "passed",
        "record_only": True,
        "vehicle_command_transmitted": False,
        "time_scale": 1,
        "wall_duration_seconds": 120.1,
        "started_at": timestamp,
        "finished_at": "2026-07-19T00:02:00Z",
        "preflight": {
            "request": {"method": "GET", "path": "/healthz"},
            "http_status": 200,
            "response": {
                "status": "ok",
                "database": "ok",
                "actuator_mode": "record-only",
            },
            "assertion": "passed",
        },
        "steps": evidence_steps,
    }
    return scenario, evidence


def test_video_validation_rejects_missing_event_identifier() -> None:
    scenario, evidence = canonical_inputs()
    del evidence["steps"][0]["response"]["event_id"]

    with pytest.raises(ValueError, match="missing event_id"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_broken_authorization_chain() -> None:
    scenario, evidence = canonical_inputs()
    authorization = next(
        step for step in evidence["steps"] if step["action"] == "authorize"
    )
    authorization["response"]["event_id"] = "evt_unknown"

    with pytest.raises(ValueError, match="Authorization event_id"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_unknown_event_list_entry() -> None:
    scenario, evidence = canonical_inputs()
    events = next(step for step in evidence["steps"] if step["action"] == "events")
    events["response"][0]["event_id"] = "evt_unknown"

    with pytest.raises(ValueError, match="unknown event_id"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_event_hash_mismatch() -> None:
    scenario, evidence = canonical_inputs()
    events = next(step for step in evidence["steps"] if step["action"] == "events")
    events["response"][0]["input_sha256"] = "b" * 64

    with pytest.raises(ValueError, match="input_sha256"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_latest_hash_mismatch() -> None:
    scenario, evidence = canonical_inputs()
    latest = next(step for step in evidence["steps"] if step["action"] == "latest")
    latest["response"]["input_sha256"] = "b" * 64

    with pytest.raises(ValueError, match="Latest evidence input_sha256"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_timestamp_duration_mismatch() -> None:
    scenario, evidence = canonical_inputs()
    evidence["finished_at"] = "2026-07-19T00:30:00Z"

    with pytest.raises(ValueError, match="timestamps do not match"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_received_at_outside_run() -> None:
    scenario, evidence = canonical_inputs()
    evidence["steps"][0]["response"]["received_at"] = "2026-07-18T23:59:00Z"

    with pytest.raises(ValueError, match="outside the run"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_scenario_telemetry_mismatch() -> None:
    scenario, evidence = canonical_inputs()
    scenario["steps"][0]["environment"]["water_level_cm"] = 21

    with pytest.raises(ValueError, match="scenario field environment"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_persisted_decision_mismatch() -> None:
    scenario, evidence = canonical_inputs()
    evidence["steps"][1]["response"]["result"] = {
        "decision": "STAY",
        "risk_level": "LOW",
        "permission": "NONE",
    }
    evidence["steps"][1]["expected"] = {
        "http_status": 201,
        "decision": "STAY",
        "risk_level": "LOW",
        "permission": "NONE",
    }
    scenario["steps"][1]["expect"] = deepcopy(evidence["steps"][1]["expected"])

    with pytest.raises(ValueError, match="Persisted event result"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_changed_canonical_timeline() -> None:
    scenario, evidence = canonical_inputs()
    scenario["steps"][4], scenario["steps"][5] = (
        scenario["steps"][5],
        scenario["steps"][4],
    )
    evidence["steps"][4], evidence["steps"][5] = (
        evidence["steps"][5],
        evidence["steps"][4],
    )

    with pytest.raises(ValueError, match="Canonical scenario timeline"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_noncanonical_scenario_id() -> None:
    scenario, evidence = canonical_inputs()
    scenario["scenario_id"] = "replacement-120s"
    evidence["scenario_id"] = "replacement-120s"

    with pytest.raises(ValueError, match="rainstorm-p5-120s"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_duplicate_command_step() -> None:
    scenario, evidence = canonical_inputs()
    scenario["steps"][4] = deepcopy(scenario["steps"][5])
    evidence["steps"][4] = deepcopy(evidence["steps"][5])

    with pytest.raises(ValueError, match="Canonical scenario timeline"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_vehicle_profile_mismatch() -> None:
    scenario, evidence = canonical_inputs()
    evidence["vehicle_profile"]["model"] = "UNVERIFIED"

    with pytest.raises(ValueError, match="vehicle profiles"):
        validate_inputs(scenario, evidence)


def test_video_validation_requires_record_only_health_preflight() -> None:
    scenario, evidence = canonical_inputs()
    evidence["preflight"]["response"]["actuator_mode"] = "disabled"

    with pytest.raises(ValueError, match="preflight actuator mode"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_nested_plaintext_authorization_token() -> None:
    scenario, evidence = canonical_inputs()
    evidence["steps"][0]["response"] = {
        "nested": [{"authorization_token": "must-not-be-published"}]
    }

    with pytest.raises(ValueError, match="unredacted authorization token"):
        validate_inputs(scenario, evidence)


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [("method", "GET"), ("path", "/api/v1/events")],
)
def test_video_validation_rejects_tampered_request(
    field: str,
    tampered_value: str,
) -> None:
    scenario, evidence = canonical_inputs()
    evidence["steps"][0]["request"][field] = tampered_value

    with pytest.raises(ValueError, match="wrong request"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_tampered_http_status() -> None:
    scenario, evidence = canonical_inputs()
    evidence["steps"][0]["http_status"] = 200

    with pytest.raises(ValueError, match="wrong HTTP status"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_tampered_expected_values() -> None:
    scenario, evidence = canonical_inputs()
    evidence["steps"][0]["expected"]["decision"] = "NO_GO"

    with pytest.raises(ValueError, match="expectation does not match"):
        validate_inputs(scenario, evidence)


@pytest.mark.parametrize("field", ["decision", "risk_level", "permission"])
def test_video_validation_rejects_tampered_decision_result(field: str) -> None:
    scenario, evidence = canonical_inputs()
    evidence["steps"][0]["response"]["result"][field] = "TAMPERED"

    with pytest.raises(ValueError, match=rf"wrong {field}"):
        validate_inputs(scenario, evidence)


def test_video_validation_rejects_insufficient_event_evidence() -> None:
    scenario, evidence = canonical_inputs()
    events_step = next(
        step for step in evidence["steps"] if step["action"] == "events"
    )
    events_step["response"] = [{}, {}, {}]

    with pytest.raises(ValueError, match="minimum_count"):
        validate_inputs(scenario, evidence)


def test_video_validation_requires_redacted_authorization_token_hash() -> None:
    scenario, evidence = canonical_inputs()
    authorization_step = next(
        step for step in evidence["steps"] if step["action"] == "authorize"
    )
    authorization_step["response"]["authorization_token_sha256"] = "not-a-hash"

    with pytest.raises(ValueError, match="64-character SHA-256 token hash"):
        validate_inputs(scenario, evidence)
