from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ActuatorResult:
    status: str
    mode: str
    message: str
    details: dict[str, object]


class VehicleActuator(Protocol):
    def migrate_to_high_point(self, *, event_id: str, vehicle_id: str) -> ActuatorResult: ...


class RecordOnlyActuator:
    """Records a validated command but deliberately sends nothing to a vehicle."""

    def migrate_to_high_point(self, *, event_id: str, vehicle_id: str) -> ActuatorResult:
        return ActuatorResult(
            status="RECORDED_NOT_SENT",
            mode="record-only",
            message="命令已通过安全校验并留痕，但未发送到任何真实车辆。",
            details={
                "event_id": event_id,
                "vehicle_id": vehicle_id,
                "transmitted": False,
                "required_next_step": "接入经制造商授权并通过功能安全验证的车辆控制适配器",
            },
        )


class DisabledActuator:
    def migrate_to_high_point(self, *, event_id: str, vehicle_id: str) -> ActuatorResult:
        raise RuntimeError("Vehicle actuation is disabled")


def build_actuator(mode: str) -> VehicleActuator:
    if mode == "record-only":
        return RecordOnlyActuator()
    return DisabledActuator()
