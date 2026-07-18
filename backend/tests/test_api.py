from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest

from backend.app import database as database_module
from backend.app.database import Database
from backend.app.decision_engine import evaluate_decision
from backend.app.models import TelemetryIn

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
    assert latest.headers["cache-control"] == "private, no-store"
    assert latest.json()["event_id"] == body["event_id"]


def test_message_id_makes_ingestion_idempotent(client: TestClient, headers: dict[str, str]):
    payload = make_payload("msg_idempotent_001")
    first = client.post("/api/v1/telemetry", json=payload, headers=headers)
    second = client.post("/api/v1/telemetry", json=payload, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert first.json()["event_id"] == second.json()["event_id"]
    assert first.json()["input_sha256"] == second.json()["input_sha256"]


def test_message_id_conflict_does_not_overwrite_original_event(
    client: TestClient,
    headers: dict[str, str],
):
    original = make_payload("msg_conflict_001")
    first = client.post("/api/v1/telemetry", json=original, headers=headers)
    assert first.status_code == 201

    conflicting = deepcopy(original)
    conflicting["environment"]["water_level_cm"] = 99
    conflict = client.post("/api/v1/telemetry", json=conflicting, headers=headers)

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == (
        "message_id already exists with a different telemetry payload"
    )

    stored = client.get(
        f"/api/v1/events/{first.json()['event_id']}",
        headers=headers,
    )
    assert stored.status_code == 200
    assert stored.json()["input_sha256"] == first.json()["input_sha256"]
    assert stored.json()["telemetry"]["environment"]["water_level_cm"] == 4

    history = client.get(
        "/api/v1/events",
        params={"site_id": "garage-test-01", "vehicle_id": "vehicle-test-01"},
        headers=headers,
    )
    assert [event["event_id"] for event in history.json()] == [first.json()["event_id"]]


def test_retry_without_captured_at_accepts_legacy_stored_hash(
    client: TestClient,
    headers: dict[str, str],
):
    payload = make_payload("msg_legacy_hash_retry")
    first = client.post("/api/v1/telemetry", json=payload, headers=headers)
    assert first.status_code == 201

    database = client.app.state.database
    with closing(database.connect()) as connection:
        stored = connection.execute(
            "SELECT payload_json FROM telemetry WHERE message_id = ?",
            (payload["message_id"],),
        ).fetchone()
        legacy_canonical_json = json.dumps(
            TelemetryIn.model_validate_json(stored["payload_json"]).model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        legacy_hash = hashlib.sha256(legacy_canonical_json.encode("utf-8")).hexdigest()
        connection.execute(
            """
            UPDATE telemetry
            SET input_sha256 = ?, captured_at_provided = NULL
            WHERE message_id = ?
            """,
            (legacy_hash, payload["message_id"]),
        )
        connection.commit()

    for _ in range(2):
        retry = client.post("/api/v1/telemetry", json=payload, headers=headers)
        assert retry.status_code == 200
        assert retry.json()["duplicate"] is True
        assert retry.json()["event_id"] == first.json()["event_id"]
        assert retry.json()["input_sha256"] == legacy_hash

    with closing(database.connect()) as connection:
        marker = connection.execute(
            "SELECT captured_at_provided FROM telemetry WHERE message_id = ?",
            (payload["message_id"],),
        ).fetchone()["captured_at_provided"]
    assert marker == 0


def test_removing_explicit_captured_at_is_a_message_id_conflict(
    client: TestClient,
    headers: dict[str, str],
):
    payload = make_payload("msg_explicit_capture_conflict")
    payload["captured_at"] = datetime.now(timezone.utc).isoformat()
    assert client.post("/api/v1/telemetry", json=payload, headers=headers).status_code == 201

    retry_without_capture = deepcopy(payload)
    retry_without_capture.pop("captured_at")
    conflict = client.post(
        "/api/v1/telemetry",
        json=retry_without_capture,
        headers=headers,
    )
    assert conflict.status_code == 409


def test_initialize_migrates_capture_presence_marker(tmp_path):
    database_path = tmp_path / "legacy-schema.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE telemetry (
                message_id TEXT PRIMARY KEY,
                site_id TEXT NOT NULL,
                vehicle_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                input_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )

    database = Database(database_path)
    database.initialize()

    with closing(database.connect()) as connection:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(telemetry)").fetchall()
        }
    assert "captured_at_provided" in columns


def test_latest_returns_gone_when_latest_event_is_stale(
    client: TestClient,
    headers: dict[str, str],
    monkeypatch,
):
    max_age = client.app.state.settings.event_max_age_seconds
    stale_received_at = datetime.now(timezone.utc) - timedelta(seconds=max_age + 1)
    monkeypatch.setattr(database_module, "_utc_now", lambda: stale_received_at)

    ingested = client.post(
        "/api/v1/telemetry",
        json=make_payload("msg_stale_latest_001"),
        headers=headers,
    )
    assert ingested.status_code == 201

    latest = client.get(
        "/api/v1/decisions/latest",
        params={"site_id": "garage-test-01", "vehicle_id": "vehicle-test-01"},
        headers=headers,
    )
    assert latest.status_code == 410
    assert latest.headers["cache-control"] == "private, no-store"
    assert latest.json()["detail"] == "Latest decision is stale; ingest fresh telemetry"


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


def test_command_record_failure_does_not_consume_authorization(
    client: TestClient,
    headers: dict[str, str],
):
    decision = client.post(
        "/api/v1/telemetry",
        json=rising_payload("msg_atomic_command_failure"),
        headers=headers,
    )
    event_id = decision.json()["event_id"]
    authorization = client.post(
        "/api/v1/authorizations",
        json={"event_id": event_id, "owner_id": "owner-test-01"},
        headers=headers,
    ).json()

    with closing(client.app.state.database.connect()) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_command_insert
            BEFORE INSERT ON commands
            BEGIN
                SELECT RAISE(ABORT, 'injected command write failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        client.post(
            "/api/v1/commands/migrate",
            json={
                "event_id": event_id,
                "authorization_token": authorization["authorization_token"],
            },
            headers=headers,
        )

    with closing(client.app.state.database.connect()) as connection:
        stored_authorization = connection.execute(
            "SELECT used_at FROM authorizations WHERE authorization_id = ?",
            (authorization["authorization_id"],),
        ).fetchone()
        command_count = connection.execute(
            "SELECT COUNT(*) AS count FROM commands"
        ).fetchone()["count"]
    assert stored_authorization["used_at"] is None
    assert command_count == 0


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


def test_superseded_event_cannot_be_authorized(
    client: TestClient,
    headers: dict[str, str],
):
    eligible = client.post(
        "/api/v1/telemetry",
        json=rising_payload("msg_superseded_before_auth_eligible"),
        headers=headers,
    )
    assert eligible.status_code == 201

    blocked_payload = rising_payload("msg_superseded_before_auth_blocked")
    blocked_payload["site"]["route_blocked"] = True
    blocked = client.post("/api/v1/telemetry", json=blocked_payload, headers=headers)
    assert blocked.status_code == 201
    assert blocked.json()["result"]["decision"] == "NO_GO"

    authorization = client.post(
        "/api/v1/authorizations",
        json={
            "event_id": eligible.json()["event_id"],
            "owner_id": "owner-test-01",
        },
        headers=headers,
    )
    assert authorization.status_code == 409
    assert authorization.json()["detail"] == (
        "Event has been superseded by newer vehicle telemetry"
    )


def test_same_vehicle_id_at_another_site_does_not_supersede_event(
    client: TestClient,
    headers: dict[str, str],
):
    eligible = client.post(
        "/api/v1/telemetry",
        json=rising_payload("msg_same_vehicle_original_site"),
        headers=headers,
    )
    assert eligible.status_code == 201

    another_site = make_payload("msg_same_vehicle_another_site")
    another_site["site_id"] = "garage-test-02"
    ingested_elsewhere = client.post(
        "/api/v1/telemetry",
        json=another_site,
        headers=headers,
    )
    assert ingested_elsewhere.status_code == 201

    authorization = client.post(
        "/api/v1/authorizations",
        json={
            "event_id": eligible.json()["event_id"],
            "owner_id": "owner-test-01",
        },
        headers=headers,
    )
    assert authorization.status_code == 201


def test_authorization_cannot_be_consumed_after_event_is_superseded(
    client: TestClient,
    headers: dict[str, str],
):
    eligible = client.post(
        "/api/v1/telemetry",
        json=rising_payload("msg_superseded_after_auth_eligible"),
        headers=headers,
    )
    assert eligible.status_code == 201
    event_id = eligible.json()["event_id"]

    authorization = client.post(
        "/api/v1/authorizations",
        json={"event_id": event_id, "owner_id": "owner-test-01"},
        headers=headers,
    )
    assert authorization.status_code == 201
    authorization_body = authorization.json()

    blocked_payload = rising_payload("msg_superseded_after_auth_blocked")
    blocked_payload["site"]["route_blocked"] = True
    blocked = client.post("/api/v1/telemetry", json=blocked_payload, headers=headers)
    assert blocked.status_code == 201
    assert blocked.json()["result"]["decision"] == "NO_GO"

    command = client.post(
        "/api/v1/commands/migrate",
        json={
            "event_id": event_id,
            "authorization_token": authorization_body["authorization_token"],
        },
        headers=headers,
    )
    assert command.status_code == 409
    assert command.json()["detail"] == (
        "Event has been superseded by newer vehicle telemetry"
    )

    with closing(client.app.state.database.connect()) as connection:
        row = connection.execute(
            "SELECT used_at FROM authorizations WHERE authorization_id = ?",
            (authorization_body["authorization_id"],),
        ).fetchone()
    assert row["used_at"] is None


def test_event_superseded_during_command_preparation_is_not_recorded(
    client: TestClient,
    headers: dict[str, str],
    monkeypatch,
):
    eligible = client.post(
        "/api/v1/telemetry",
        json=rising_payload("msg_superseded_during_command_eligible"),
        headers=headers,
    ).json()
    authorization = client.post(
        "/api/v1/authorizations",
        json={"event_id": eligible["event_id"], "owner_id": "owner-test-01"},
        headers=headers,
    ).json()

    actuator = client.app.state.actuator
    original_migrate = actuator.migrate_to_high_point

    def supersede_before_recording(*, event_id: str, vehicle_id: str):
        blocked_payload = rising_payload("msg_superseded_during_command_blocked")
        blocked_payload["site"]["route_blocked"] = True
        telemetry = TelemetryIn.model_validate(blocked_payload)
        result = evaluate_decision(telemetry, client.app.state.settings.policy)
        client.app.state.database.save_telemetry_and_decision(telemetry, result)
        return original_migrate(event_id=event_id, vehicle_id=vehicle_id)

    monkeypatch.setattr(actuator, "migrate_to_high_point", supersede_before_recording)

    command = client.post(
        "/api/v1/commands/migrate",
        json={
            "event_id": eligible["event_id"],
            "authorization_token": authorization["authorization_token"],
        },
        headers=headers,
    )
    assert command.status_code == 409
    assert command.json()["detail"] == (
        "Event has been superseded by newer vehicle telemetry"
    )

    with closing(client.app.state.database.connect()) as connection:
        stored_authorization = connection.execute(
            "SELECT used_at FROM authorizations WHERE authorization_id = ?",
            (authorization["authorization_id"],),
        ).fetchone()
        command_count = connection.execute(
            "SELECT COUNT(*) AS count FROM commands"
        ).fetchone()["count"]
    assert stored_authorization["used_at"] is None
    assert command_count == 0


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


def test_database_connections_are_closed_after_requests(
    client: TestClient,
    headers: dict[str, str],
    monkeypatch,
):
    database = client.app.state.database
    original_connect = database.connect
    opened_connections = []

    def tracked_connect():
        connection = original_connect()
        opened_connections.append(connection)
        return connection

    monkeypatch.setattr(database, "connect", tracked_connect)

    assert client.get("/healthz").status_code == 200
    assert client.post(
        "/api/v1/telemetry",
        json=make_payload("msg_connection_close_001"),
        headers=headers,
    ).status_code == 201
    assert client.get(
        "/api/v1/decisions/latest",
        params={"site_id": "garage-test-01", "vehicle_id": "vehicle-test-01"},
        headers=headers,
    ).status_code == 200

    assert opened_connections
    for connection in opened_connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
