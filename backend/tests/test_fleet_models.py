from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.fleet_models import FleetSnapshot, SourceMode
from backend.tests.fleet_fixtures import make_fleet_snapshot


def test_valid_snapshot_accepts_only_simulated_or_shadow_modes() -> None:
    snapshot = FleetSnapshot.model_validate(make_fleet_snapshot())
    assert snapshot.source_mode is SourceMode.SIMULATED
    assert len(snapshot.vehicles) == 2

    shadow_body = make_fleet_snapshot(source_mode="SHADOW")
    assert FleetSnapshot.model_validate(shadow_body).source_mode is SourceMode.SHADOW


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda body: body.update(source_mode="LIVE_CONTROL"), "source_mode"),
        (
            lambda body: body["vehicles"].append(deepcopy(body["vehicles"][0])),
            "vehicle_id values must be unique",
        ),
        (
            lambda body: body["vehicles"][0]["telemetry"].update(site_id="other-site"),
            "telemetry.site_id",
        ),
        (lambda body: body.update(owner_authorized=True), "Extra inputs are not permitted"),
    ],
)
def test_snapshot_rejects_unsafe_or_ambiguous_input(mutation, message: str) -> None:
    body = make_fleet_snapshot()
    mutation(body)
    with pytest.raises(ValidationError, match=message):
        FleetSnapshot.model_validate(body)


def test_snapshot_requires_one_to_fifty_vehicles() -> None:
    empty = make_fleet_snapshot()
    empty["vehicles"] = []
    with pytest.raises(ValidationError, match="at least 1"):
        FleetSnapshot.model_validate(empty)

    oversized = make_fleet_snapshot()
    oversized["vehicles"] = [
        deepcopy(oversized["vehicles"][0]) for _ in range(51)
    ]
    for index, vehicle in enumerate(oversized["vehicles"]):
        vehicle["telemetry"]["vehicle_id"] = f"vehicle-{index:02d}"
        vehicle["telemetry"]["message_id"] = f"message-{index:02d}"
    with pytest.raises(ValidationError, match="at most 50"):
        FleetSnapshot.model_validate(oversized)


def test_snapshot_rejects_duplicate_safe_point_ids_and_naive_times() -> None:
    duplicate = make_fleet_snapshot()
    duplicate["site"]["safe_points"][1]["safe_point_id"] = "high-a"
    with pytest.raises(ValidationError, match="safe_point_id values must be unique"):
        FleetSnapshot.model_validate(duplicate)

    naive = make_fleet_snapshot()
    naive["captured_at"] = "2026-07-23T08:00:00"
    with pytest.raises(ValidationError, match="captured_at must include a timezone"):
        FleetSnapshot.model_validate(naive)
