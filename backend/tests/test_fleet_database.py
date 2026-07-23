from __future__ import annotations

import sqlite3
from contextlib import closing
from copy import deepcopy

import pytest

from backend.app.config import DecisionPolicy
from backend.app.database import Database, FleetSnapshotConflictError
from backend.app.fleet_models import FleetSnapshot
from backend.app.fleet_planner import plan_fleet
from backend.tests.fleet_fixtures import FIXED_NOW, make_fleet_snapshot


def build_plan(body: dict[str, object], *, run_id: str):
    snapshot = FleetSnapshot.model_validate(body)
    return snapshot, plan_fleet(
        snapshot,
        DecisionPolicy(),
        run_id=run_id,
        created_at=FIXED_NOW,
        now=FIXED_NOW,
        site_max_age_seconds=300,
    )


def save_fixture(database: Database, body: dict[str, object], *, run_id: str):
    snapshot, plan = build_plan(body, run_id=run_id)
    return database.save_fleet_run(snapshot, plan)


def test_fleet_run_is_saved_atomically_and_loaded_by_run_and_site(tmp_path) -> None:
    database_path = tmp_path / "fleet.db"
    database = Database(database_path)
    database.initialize()

    stored = save_fixture(
        database,
        make_fleet_snapshot(),
        run_id="fleet-run-db-01",
    )

    assert stored.duplicate is False
    assert stored.plan.duplicate is False
    assert database.get_fleet_run(stored.plan.run_id).plan == stored.plan
    assert database.get_latest_fleet_run("garage-fleet-01").plan.run_id == stored.plan.run_id

    reopened = Database(database_path)
    assert reopened.get_fleet_run(stored.plan.run_id).plan.plan_sha256 == stored.plan.plan_sha256

    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM fleet_runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM fleet_vehicle_plans").fetchone()[0] == 2
        child = connection.execute(
            "SELECT allocation_status, action_permission, authorized_to_move "
            "FROM fleet_vehicle_plans ORDER BY vehicle_id LIMIT 1"
        ).fetchone()
    assert child["allocation_status"] == "NOT_REQUIRED"
    assert child["action_permission"] == "NONE"
    assert child["authorized_to_move"] == 0


def test_snapshot_id_is_idempotent_but_conflicting_content_is_rejected(tmp_path) -> None:
    database = Database(tmp_path / "fleet.db")
    database.initialize()
    body = make_fleet_snapshot()

    first = save_fixture(database, body, run_id="fleet-run-original")
    retry = save_fixture(database, deepcopy(body), run_id="fleet-run-retry")

    assert retry.duplicate is True
    assert retry.plan.duplicate is True
    assert retry.plan.run_id == first.plan.run_id
    assert retry.plan.input_sha256 == first.plan.input_sha256

    conflict = deepcopy(body)
    conflict["vehicles"][0]["telemetry"]["environment"]["water_level_cm"] = 12
    with pytest.raises(
        FleetSnapshotConflictError,
        match="snapshot_id already exists with different fleet content",
    ):
        save_fixture(database, conflict, run_id="fleet-run-conflict")

    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM fleet_runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM fleet_vehicle_plans").fetchone()[0] == 2


def test_vehicle_insert_failure_rolls_back_the_parent_run(tmp_path) -> None:
    database = Database(tmp_path / "fleet.db")
    database.initialize()
    with closing(database.connect()) as connection:
        connection.execute(
            "CREATE TRIGGER reject_fleet_vehicle BEFORE INSERT ON fleet_vehicle_plans "
            "BEGIN SELECT RAISE(ABORT, 'injected fleet write failure'); END"
        )
        connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="injected fleet write failure"):
        save_fixture(
            database,
            make_fleet_snapshot(),
            run_id="fleet-run-rollback",
        )

    with closing(database.connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM fleet_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM fleet_vehicle_plans").fetchone()[0] == 0


def test_unknown_run_and_site_return_none_and_snapshot_lookup_is_explicit(tmp_path) -> None:
    database = Database(tmp_path / "fleet.db")
    database.initialize()

    assert database.has_fleet_snapshot_id("missing-snapshot") is False
    assert database.get_fleet_run("missing-run") is None
    assert database.get_latest_fleet_run("missing-site") is None

    save_fixture(
        database,
        make_fleet_snapshot(snapshot_id="snapshot-known"),
        run_id="fleet-run-known",
    )
    assert database.has_fleet_snapshot_id("snapshot-known") is True
