from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from demo.run_scenario import ApiResponse, load_scenario, run_scenario


REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIO_PATH = REPO_ROOT / "demo" / "scenarios" / "rainstorm-p5-120s.json"


class InProcessApiClient:
    def __init__(self, client: TestClient, api_key: str) -> None:
        self.client = client
        self.headers = {"X-API-Key": api_key}

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> ApiResponse:
        response = self.client.request(method, path, headers=self.headers, json=payload)
        return ApiResponse(
            status=response.status_code,
            payload=response.json(),
            headers=dict(response.headers),
        )


def test_competition_demo_runs_full_record_only_flow(
    client: TestClient,
    settings: Settings,
) -> None:
    scenario = load_scenario(SCENARIO_PATH)
    report = run_scenario(
        scenario,
        InProcessApiClient(client, settings.api_key),
        time_scale=0,
        progress=None,
    )

    assert report["status"] == "passed"
    assert report["vehicle_profile"] == scenario["vehicle_profile"]
    assert report["vehicle_profile"]["xmart_os_assumption"] == "3.6.1"
    assert report["vehicle_profile"]["market"] == "UNVERIFIED"
    assert report["record_only"] is True
    assert report["vehicle_command_transmitted"] is False
    assert report["preflight"] == {
        "request": {"method": "GET", "path": "/healthz"},
        "http_status": 200,
        "response": {
            "status": "ok",
            "database": "ok",
            "actuator_mode": "record-only",
        },
        "assertion": "passed",
    }
    assert [step["action"] for step in report["steps"]] == [
        "telemetry",
        "telemetry",
        "telemetry",
        "telemetry",
        "authorize",
        "command",
        "events",
        "telemetry",
        "latest",
    ]
    assert [
        step["response"]["result"]["decision"]
        for step in report["steps"]
        if step["action"] == "telemetry"
    ] == ["STAY", "WATCH", "PREPARE", "MIGRATE_NOW", "NO_GO"]

    authorization = next(
        step["response"] for step in report["steps"] if step["action"] == "authorize"
    )
    assert "authorization_token" not in authorization
    assert len(authorization["authorization_token_sha256"]) == hashlib.sha256().digest_size * 2

    command = next(
        step["response"] for step in report["steps"] if step["action"] == "command"
    )
    assert command["status"] == "RECORDED_NOT_SENT"
    assert command["actuator_mode"] == "record-only"


def test_competition_demo_rejects_non_record_only_backend(
    settings: Settings,
) -> None:
    disabled_settings = replace(
        settings,
        database_path=settings.database_path.with_name("disabled.db"),
        actuator_mode="disabled",
    )
    scenario = load_scenario(SCENARIO_PATH)

    with TestClient(create_app(disabled_settings)) as disabled_client:
        with pytest.raises(
            AssertionError,
            match="health.actuator_mode: expected 'record-only', got 'disabled'",
        ):
            run_scenario(
                scenario,
                InProcessApiClient(disabled_client, disabled_settings.api_key),
                time_scale=0,
                progress=None,
            )


def test_competition_demo_manifest_covers_exactly_two_minutes() -> None:
    scenario = load_scenario(SCENARIO_PATH)
    assert scenario["duration_seconds"] == 120
    assert [step["at_seconds"] for step in scenario["steps"]] == [
        0,
        20,
        45,
        70,
        85,
        90,
        105,
        115,
        120,
    ]
