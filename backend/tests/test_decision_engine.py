from __future__ import annotations

from backend.app.config import DecisionPolicy
from backend.app.decision_engine import evaluate_decision
from backend.app.models import TelemetryIn

from .conftest import make_payload


def test_sensor_conflict_denies_movement():
    payload = make_payload("msg_conflict")
    payload["environment"]["water_level_cm"] = 8
    payload["environment"]["secondary_water_level_cm"] = 17
    telemetry = TelemetryIn.model_validate(payload)
    result = evaluate_decision(telemetry)
    assert result.decision.value == "VERIFY_ONLY"
    assert result.permission.value == "DENIED"


def test_closed_safety_window_is_no_go():
    payload = make_payload("msg_closed_window")
    payload["environment"].update(
        {
            "rainfall_mm_h": 100,
            "water_level_cm": 18,
            "secondary_water_level_cm": 18,
            "rise_rate_cm_min": 1,
        }
    )
    telemetry = TelemetryIn.model_validate(payload)
    result = evaluate_decision(telemetry)
    assert result.decision.value == "NO_GO"
    assert result.risk_level.value == "CRITICAL"


def test_policy_is_server_controlled_not_telemetry_controlled():
    payload = make_payload("msg_policy")
    payload["environment"]["water_level_cm"] = 10
    payload["environment"]["secondary_water_level_cm"] = 10
    payload["environment"]["rise_rate_cm_min"] = 1
    telemetry = TelemetryIn.model_validate(payload)
    strict_policy = DecisionPolicy(danger_water_level_cm=16)
    result = evaluate_decision(telemetry, strict_policy)
    assert result.timing.remaining_cm == 6
    assert result.decision.value == "NO_GO"
