from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path


FIXED_NOW = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[2]
FLEET_SCENARIO_PATH = REPO_ROOT / "demo" / "scenarios" / "fleet-rainstorm-v1.json"


def make_fleet_vehicle(
    vehicle_id: str,
    *,
    site_id: str = "garage-fleet-01",
    message_id: str | None = None,
    captured_at: datetime = FIXED_NOW,
    water_level_cm: float = 4,
    secondary_water_level_cm: float = 4,
    rise_rate_cm_min: float = 0.2,
    rainfall_mm_h: float = 35,
    sensor_confidence: float = 0.94,
    route_dry: bool = True,
    route_blocked: bool = False,
    danger_water_level_cm: float | None = None,
    route_distance_m: float | None = None,
) -> dict[str, object]:
    return {
        "telemetry": {
            "message_id": message_id or f"msg-{vehicle_id}",
            "site_id": site_id,
            "vehicle_id": vehicle_id,
            "source_id": "fleet-fixture",
            "captured_at": captured_at.isoformat(),
            "environment": {
                "rainfall_mm_h": rainfall_mm_h,
                "water_level_cm": water_level_cm,
                "secondary_water_level_cm": secondary_water_level_cm,
                "rise_rate_cm_min": rise_rate_cm_min,
                "sensor_confidence": sensor_confidence,
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
                "route_dry": route_dry,
                "route_blocked": route_blocked,
            },
        },
        "danger_water_level_cm": danger_water_level_cm,
        "route_distance_m": route_distance_m,
    }


def make_fleet_snapshot(
    *,
    snapshot_id: str = "snapshot-fixture-01",
    site_id: str = "garage-fleet-01",
    captured_at: datetime = FIXED_NOW,
    observed_at: datetime = FIXED_NOW,
    source_mode: str = "SIMULATED",
    gateway_online: bool = True,
    batch_size: int = 1,
    batch_interval_min: float = 0.7,
    safe_points: list[dict[str, object]] | None = None,
    vehicles: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    points = safe_points or [
        {
            "safe_point_id": "high-a",
            "label": "高位 A",
            "priority": 1,
            "capacity": 1,
            "available": True,
        },
        {
            "safe_point_id": "high-b",
            "label": "高位 B",
            "priority": 2,
            "capacity": 1,
            "available": True,
        },
    ]
    fleet_vehicles = vehicles or [
        make_fleet_vehicle("vehicle-a", site_id=site_id, captured_at=captured_at),
        make_fleet_vehicle("vehicle-b", site_id=site_id, captured_at=captured_at),
    ]
    return {
        "snapshot_id": snapshot_id,
        "site_id": site_id,
        "captured_at": captured_at.isoformat(),
        "source_mode": source_mode,
        "site": {
            "observed_at": observed_at.isoformat(),
            "gateway_online": gateway_online,
            "batch_size": batch_size,
            "batch_interval_min": batch_interval_min,
            "safe_points": deepcopy(points),
        },
        "vehicles": deepcopy(fleet_vehicles),
    }


def load_fleet_scenario() -> dict[str, object]:
    return json.loads(FLEET_SCENARIO_PATH.read_text(encoding="utf-8"))
