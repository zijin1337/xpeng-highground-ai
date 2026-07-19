from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_path=tmp_path / "highground-test.db",
        api_key="test-api-key",
        environment="test",
        actuator_mode="record-only",
        authorization_ttl_seconds=60,
        event_max_age_seconds=120,
        capture_max_age_seconds=120,
        capture_future_tolerance_seconds=15,
        allowed_origins=("http://testserver",),
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def headers() -> dict[str, str]:
    return {"X-API-Key": "test-api-key"}


def make_payload(message_id: str = "msg_test_001") -> dict[str, object]:
    return {
        "message_id": message_id,
        "site_id": "garage-test-01",
        "vehicle_id": "vehicle-test-01",
        "source_id": "edge-test-01",
        "environment": {
            "rainfall_mm_h": 35,
            "water_level_cm": 4,
            "secondary_water_level_cm": 4,
            "rise_rate_cm_min": 0.2,
            "sensor_confidence": 0.94,
        },
        "vehicle": {
            "occupants_clear": True,
            "charging_disconnected": True,
            "vehicle_healthy": True,
            "positioning_online": True,
            "network_online": True,
            "emergency_operator_online": True,
            "water_contact_triggered": False,
            "motion_state": "PARKED",
        },
        "site": {
            "route_dry": True,
            "route_blocked": False,
        },
    }
