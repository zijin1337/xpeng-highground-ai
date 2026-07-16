from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class DecisionPolicy:
    danger_water_level_cm: float = 22.0
    rain_watch_threshold_mm_h: float = 50.0
    min_sensor_confidence: float = 0.72
    max_sensor_disagreement_cm: float = 5.0
    route_distance_m: float = 260.0
    max_speed_kmh: float = 5.0
    queue_ahead: int = 2
    batch_size: int = 3
    batch_interval_min: float = 0.7
    safety_buffer_min: float = 3.0
    prepare_horizon_min: float = 20.0
    migrate_horizon_min: float = 7.0

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


@dataclass(frozen=True)
class Settings:
    database_path: Path
    api_key: str
    environment: str = "development"
    actuator_mode: str = "record-only"
    authorization_ttl_seconds: int = 120
    event_max_age_seconds: int = 300
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    )
    policy: DecisionPolicy = DecisionPolicy()

    @classmethod
    def from_env(cls) -> "Settings":
        database_path = Path(
            os.getenv("HIGHGROUND_DATABASE_PATH", REPO_ROOT / "data" / "highground.db")
        ).expanduser()
        origins = tuple(
            origin.strip()
            for origin in os.getenv(
                "HIGHGROUND_ALLOWED_ORIGINS",
                "http://127.0.0.1:8000,http://localhost:8000",
            ).split(",")
            if origin.strip()
        )
        return cls(
            database_path=database_path,
            api_key=os.getenv("HIGHGROUND_API_KEY", "development-only-change-me"),
            environment=os.getenv("HIGHGROUND_ENV", "development"),
            actuator_mode=os.getenv("HIGHGROUND_ACTUATOR_MODE", "record-only"),
            authorization_ttl_seconds=int(os.getenv("HIGHGROUND_AUTH_TTL_SECONDS", "120")),
            event_max_age_seconds=int(os.getenv("HIGHGROUND_EVENT_MAX_AGE_SECONDS", "300")),
            allowed_origins=origins,
        )

    def validate(self) -> None:
        if self.environment == "production" and self.api_key == "development-only-change-me":
            raise RuntimeError("Production mode requires HIGHGROUND_API_KEY to be changed")
        if self.actuator_mode not in {"disabled", "record-only"}:
            raise RuntimeError("HIGHGROUND_ACTUATOR_MODE must be disabled or record-only")
        if self.authorization_ttl_seconds < 10:
            raise RuntimeError("Authorization TTL must be at least 10 seconds")
        if self.event_max_age_seconds < self.authorization_ttl_seconds:
            raise RuntimeError("Event maximum age must be >= authorization TTL")
