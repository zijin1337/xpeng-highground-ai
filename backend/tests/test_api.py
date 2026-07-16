from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from .conftest import make_payload


def rising_payload(message_id: str = "msg_rising_001") -> dict[str, object]:
    payload = make_payload(message_id)
    payload["environment"] = {
        "rainfall_mm_h": 96,
        "water_level_cm": 14,
        "secondary_water_level_cm": 13.5,
        "rise_rate_cm_min": 1.0,
        "sensor_confidence": 0.91,
    }
    return payload


def test_health_is_public_and_database_is_ready(client: TestClient):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": "ok",
        "actuator_mode": "record-only",
    }


def test_telemetry_requires_api_key(client: TestClient):
    response = client.post("/api/v1/telemetry", json=make_payload())
    assert response.status_code == 401


def test_session_confirms_authenticated_runtime(client: TestClient, headers: dict[str, str]):
    response = client.get("/api/v1/session", headers=headers)
    assert response.status_code == 200
    assert response.json()["storage"] == "sqlite"
    assert response.json()["actuator_mode"] == "record-only"


def test_ingest_normal_telemetry_and_read_latest(client: TestClient, headers: dict[str, str]):
    response = client.post("/api/v1/telemetry", json=make_payload(), headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert body["duplicate"] is False
    assert body["result"]["decision"] == "STAY"
    assert body["result"]["permission"] == "NONE"
    assert len(body["input_sha256"]) == 64

    latest = client.get(
        "/api/v1/decisions/latest",
        params={"site_id": "garage-test-01", "vehicle_id": "vehicle-test-01"},
        headers=headers,
    )
    assert latest.status_code == 200
    assert latest.json()["event_id"] == body["event_id"]


def test_message_id_makes_ingestion_idempotent(client: TestClient, headers: dict[str, str]):
    payload = make_payload("msg_idempotent_001")
    first = client.post("/api/v1/telemetry", json=payload, headers=headers)
    second = client.post("/api/v1/telemetry", json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert first.json()["event_id"] == second.json()["event_id"]


def test_rising_event_authorization_and_one_time_command(
    client: TestClient,
    headers: dict[str, str],
):
    decision = client.post(
        "/api/v1/telemetry",
        json=rising_payload(),
        headers=headers,
    )
    assert decision.status_code == 201
    decision_body = decision.json()
    assert decision_body["result"]["decision"] == "MIGRATE_NOW"
    assert decision_body["result"]["permission"] == "AWAITING_OWNER"

    authorization = client.post(
        "/api/v1/authorizations",
        json={"event_id": decision_body["event_id"], "owner_id": "owner-test-01"},
        headers=headers,
    )
    assert authorization.status_code == 201
    token = authorization.json()["authorization_token"]

    command_payload = {
        "event_id": decision_body["event_id"],
        "authorization_token": token,
    }
    command = client.post(
        "/api/v1/commands/migrate",
        json=command_payload,
        headers=headers,
    )
    assert command.status_code == 202
    assert command.json()["status"] == "RECORDED_NOT_SENT"
    assert command.json()["actuator_mode"] == "record-only"

    replay = client.post(
        "/api/v1/commands/migrate",
        json=command_payload,
        headers=headers,
    )
    assert replay.status_code == 401


def test_no_go_event_cannot_be_authorized(client: TestClient, headers: dict[str, str]):
    payload = rising_payload("msg_no_go_001")
    payload = deepcopy(payload)
    payload["site"]["route_dry"] = False
    decision = client.post("/api/v1/telemetry", json=payload, headers=headers)
    assert decision.status_code == 201
    body = decision.json()
    assert body["result"]["decision"] == "NO_GO"

    authorization = client.post(
        "/api/v1/authorizations",
        json={"event_id": body["event_id"], "owner_id": "owner-test-01"},
        headers=headers,
    )
    assert authorization.status_code == 409


def test_event_history_is_persisted(client: TestClient, headers: dict[str, str]):
    for index in range(3):
        payload = make_payload(f"msg_history_{index}")
        response = client.post("/api/v1/telemetry", json=payload, headers=headers)
        assert response.status_code == 201

    history = client.get(
        "/api/v1/events",
        params={
            "site_id": "garage-test-01",
            "vehicle_id": "vehicle-test-01",
            "limit": 2,
        },
        headers=headers,
    )
    assert history.status_code == 200
    assert len(history.json()) == 2
