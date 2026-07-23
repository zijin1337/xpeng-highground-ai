from __future__ import annotations

import sqlite3
from contextlib import closing
from copy import deepcopy
from datetime import timedelta

import pytest

from backend.app import database as database_module
from backend.app import main as main_module
from backend.tests.fleet_fixtures import (
    FIXED_NOW,
    make_fleet_snapshot,
    make_fleet_vehicle,
)


@pytest.fixture(autouse=True)
def fixed_api_clock(monkeypatch):
    monkeypatch.setattr(main_module, "_utc_now", lambda: FIXED_NOW)


def fleet_counts(client) -> tuple[int, int]:
    with closing(client.app.state.database.connect()) as connection:
        run_count = connection.execute("SELECT COUNT(*) FROM fleet_runs").fetchone()[0]
        vehicle_count = connection.execute(
            "SELECT COUNT(*) FROM fleet_vehicle_plans"
        ).fetchone()[0]
    return run_count, vehicle_count


def test_fleet_shadow_requires_api_key(client) -> None:
    response = client.post(
        "/api/v1/fleet/shadow-runs",
        json=make_fleet_snapshot(),
    )
    assert response.status_code == 401
    assert fleet_counts(client) == (0, 0)


def test_fleet_shadow_lifecycle_and_idempotency(client, headers) -> None:
    body = make_fleet_snapshot(source_mode="SHADOW")

    first = client.post("/api/v1/fleet/shadow-runs", json=body, headers=headers)
    retry = client.post("/api/v1/fleet/shadow-runs", json=body, headers=headers)
    detail = client.get(
        f"/api/v1/fleet/shadow-runs/{first.json()['run_id']}",
        headers=headers,
    )
    latest = client.get(
        "/api/v1/fleet/latest",
        params={"site_id": body["site_id"]},
        headers=headers,
    )

    assert first.status_code == 201
    assert first.json()["source_mode"] == "SHADOW"
    assert first.json()["planner_version"] == "fleet-shadow-v1"
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    assert retry.json()["run_id"] == first.json()["run_id"]
    assert detail.status_code == 200
    assert detail.json()["run_id"] == first.json()["run_id"]
    assert latest.status_code == 200
    assert latest.headers["cache-control"] == "private, no-store"
    assert latest.json()["run_id"] == first.json()["run_id"]
    assert fleet_counts(client) == (1, 2)


def test_snapshot_conflict_returns_409_without_overwrite(client, headers) -> None:
    body = make_fleet_snapshot()
    first = client.post("/api/v1/fleet/shadow-runs", json=body, headers=headers)
    assert first.status_code == 201

    conflict = deepcopy(body)
    conflict["vehicles"][0]["telemetry"]["environment"]["water_level_cm"] = 12
    response = client.post(
        "/api/v1/fleet/shadow-runs",
        json=conflict,
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "snapshot_id already exists with different fleet content"
    )
    assert fleet_counts(client) == (1, 2)


def test_missing_run_and_site_return_404_with_no_store(client, headers) -> None:
    detail = client.get(
        "/api/v1/fleet/shadow-runs/fleet-missing",
        headers=headers,
    )
    latest = client.get(
        "/api/v1/fleet/latest",
        params={"site_id": "missing-site"},
        headers=headers,
    )

    assert detail.status_code == 404
    assert detail.json()["detail"] == "Fleet shadow run not found"
    assert latest.status_code == 404
    assert latest.headers["cache-control"] == "private, no-store"
    assert latest.json()["detail"] == "No fleet shadow run found"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.update(source_mode="LIVE_CONTROL"),
        lambda body: body["site"].update(batch_size=0),
        lambda body: body["vehicles"].append(deepcopy(body["vehicles"][0])),
        lambda body: body["vehicles"][0]["telemetry"].update(site_id="other-site"),
        lambda body: body.update(owner_authorized=True),
    ],
)
def test_malformed_batch_returns_422_atomically(client, headers, mutation) -> None:
    body = make_fleet_snapshot()
    mutation(body)

    response = client.post(
        "/api/v1/fleet/shadow-runs",
        json=body,
        headers=headers,
    )

    assert response.status_code == 422
    assert fleet_counts(client) == (0, 0)


@pytest.mark.parametrize(
    "path",
    ["snapshot", "vehicle"],
)
def test_stale_or_future_capture_is_rejected_without_persistence(
    client,
    headers,
    path: str,
) -> None:
    settings = client.app.state.settings
    for offset_seconds in (
        -(settings.capture_max_age_seconds + 1),
        settings.capture_future_tolerance_seconds + 1,
    ):
        body = make_fleet_snapshot(
            snapshot_id=f"snapshot-{path}-{offset_seconds}",
        )
        captured_at = FIXED_NOW + timedelta(seconds=offset_seconds)
        if path == "snapshot":
            body["captured_at"] = captured_at.isoformat()
        else:
            body["vehicles"][0]["telemetry"]["captured_at"] = captured_at.isoformat()

        response = client.post(
            "/api/v1/fleet/shadow-runs",
            json=body,
            headers=headers,
        )
        assert response.status_code == 422

    assert fleet_counts(client) == (0, 0)


def test_identical_retry_remains_idempotent_after_capture_ages_out(
    client,
    headers,
    monkeypatch,
) -> None:
    body = make_fleet_snapshot(snapshot_id="snapshot-aged-retry")
    first = client.post("/api/v1/fleet/shadow-runs", json=body, headers=headers)
    assert first.status_code == 201

    aged_now = FIXED_NOW + timedelta(
        seconds=client.app.state.settings.capture_max_age_seconds + 1
    )
    monkeypatch.setattr(main_module, "_utc_now", lambda: aged_now)
    retry = client.post("/api/v1/fleet/shadow-runs", json=body, headers=headers)

    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    assert retry.json()["run_id"] == first.json()["run_id"]


def test_offline_or_stale_site_returns_auditable_refusal_plan(client, headers) -> None:
    migration_vehicle = make_fleet_vehicle(
        "vehicle-a",
        water_level_cm=14,
        secondary_water_level_cm=14,
        rise_rate_cm_min=1,
    )
    offline = make_fleet_snapshot(
        snapshot_id="snapshot-offline",
        gateway_online=False,
        vehicles=[migration_vehicle],
    )
    offline_response = client.post(
        "/api/v1/fleet/shadow-runs",
        json=offline,
        headers=headers,
    )
    assert offline_response.status_code == 201
    assert offline_response.json()["vehicles"][0]["allocation_status"] == (
        "SITE_UNAVAILABLE"
    )

    stale = make_fleet_snapshot(
        snapshot_id="snapshot-stale-site",
        observed_at=FIXED_NOW
        - timedelta(seconds=client.app.state.settings.capture_max_age_seconds + 1),
        vehicles=[migration_vehicle],
    )
    stale_response = client.post(
        "/api/v1/fleet/shadow-runs",
        json=stale,
        headers=headers,
    )
    assert stale_response.status_code == 201
    assert stale_response.json()["vehicles"][0]["allocation_status"] == (
        "SITE_UNAVAILABLE"
    )


def test_latest_returns_gone_when_fleet_run_is_stale(
    client,
    headers,
    monkeypatch,
) -> None:
    stale_received_at = FIXED_NOW - timedelta(
        seconds=client.app.state.settings.event_max_age_seconds + 1
    )
    monkeypatch.setattr(database_module, "_utc_now", lambda: stale_received_at)
    created = client.post(
        "/api/v1/fleet/shadow-runs",
        json=make_fleet_snapshot(snapshot_id="snapshot-stale-latest"),
        headers=headers,
    )
    assert created.status_code == 201

    latest = client.get(
        "/api/v1/fleet/latest",
        params={"site_id": "garage-fleet-01"},
        headers=headers,
    )
    assert latest.status_code == 410
    assert latest.headers["cache-control"] == "private, no-store"
    assert latest.json()["detail"] == (
        "Latest fleet shadow run is stale; submit a fresh snapshot"
    )


def test_fleet_shadow_route_never_calls_actuator(client, headers, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    actuator = client.app.state.actuator

    def forbidden_call(*, event_id: str, vehicle_id: str):
        calls.append((event_id, vehicle_id))
        raise AssertionError("fleet shadow flow must not call the actuator")

    monkeypatch.setattr(actuator, "migrate_to_high_point", forbidden_call)
    response = client.post(
        "/api/v1/fleet/shadow-runs",
        json=make_fleet_snapshot(snapshot_id="snapshot-no-actuator"),
        headers=headers,
    )

    assert response.status_code == 201
    assert calls == []
    assert all(
        item["authorized_to_move"] is False
        for item in response.json()["vehicles"]
    )


def test_planner_failure_leaves_no_partial_rows(client, headers, monkeypatch) -> None:
    def injected_failure(*args, **kwargs):
        raise RuntimeError("injected planner failure")

    monkeypatch.setattr(main_module, "plan_fleet", injected_failure)
    with pytest.raises(RuntimeError, match="injected planner failure"):
        client.post(
            "/api/v1/fleet/shadow-runs",
            json=make_fleet_snapshot(snapshot_id="snapshot-planner-failure"),
            headers=headers,
        )
    assert fleet_counts(client) == (0, 0)


def test_database_failure_rolls_back_fleet_run(client, headers) -> None:
    with closing(client.app.state.database.connect()) as connection:
        connection.execute(
            "CREATE TRIGGER reject_fleet_api_vehicle BEFORE INSERT ON fleet_vehicle_plans "
            "BEGIN SELECT RAISE(ABORT, 'injected fleet api write failure'); END"
        )
        connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected fleet api write failure"):
        client.post(
            "/api/v1/fleet/shadow-runs",
            json=make_fleet_snapshot(snapshot_id="snapshot-db-failure"),
            headers=headers,
        )
    assert fleet_counts(client) == (0, 0)
