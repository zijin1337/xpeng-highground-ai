from __future__ import annotations

import math

from .config import DecisionPolicy
from .models import (
    DecisionCode,
    DecisionOutput,
    MotionState,
    Permission,
    RiskLevel,
    SafetyGate,
    TelemetryIn,
    TimingResult,
)


LABELS = {
    DecisionCode.STAY: "原地守望",
    DecisionCode.WATCH: "增强监测",
    DecisionCode.PREPARE: "建议准备迁移",
    DecisionCode.MIGRATE_NOW: "建议立即迁移",
    DecisionCode.VERIFY_ONLY: "只提醒，等待复核",
    DecisionCode.NO_GO: "No-Go：禁止迁移",
    DecisionCode.EMERGENCY_STOP: "异常停车并转人工",
}


def _timing(policy: DecisionPolicy, telemetry: TelemetryIn) -> tuple[TimingResult, float]:
    environment = telemetry.environment
    remaining_cm = policy.danger_water_level_cm - environment.water_level_cm
    if remaining_cm <= 0:
        time_to_threshold = 0.0
    elif environment.rise_rate_cm_min == 0:
        time_to_threshold = math.inf
    else:
        time_to_threshold = remaining_cm / environment.rise_rate_cm_min

    meters_per_minute = policy.max_speed_kmh * 1000 / 60
    route_time = policy.route_distance_m / meters_per_minute
    queue_batches = math.ceil(policy.queue_ahead / policy.batch_size)
    queue_time = queue_batches * policy.batch_interval_min
    latest_start = time_to_threshold - route_time - queue_time - policy.safety_buffer_min

    return (
        TimingResult(
            remaining_cm=remaining_cm,
            time_to_threshold_min=None if math.isinf(time_to_threshold) else time_to_threshold,
            route_time_min=route_time,
            queue_time_min=queue_time,
            latest_safe_start_min=None if math.isinf(latest_start) else latest_start,
        ),
        latest_start,
    )


def _gates(telemetry: TelemetryIn) -> list[SafetyGate]:
    vehicle = telemetry.vehicle
    site = telemetry.site
    return [
        SafetyGate(id="route-dry", label="路线全程干燥", passed=site.route_dry, detail="未发现水触点" if site.route_dry else "路线见水或触发禁行阈值"),
        SafetyGate(id="route-open", label="路线与出口可用", passed=not site.route_blocked, detail="路线与出口在线" if not site.route_blocked else "闸机、坡道或出口不可用"),
        SafetyGate(id="occupants", label="车内无人或宠物", passed=vehicle.occupants_clear, detail="乘员检测通过" if vehicle.occupants_clear else "检测到乘员或宠物"),
        SafetyGate(id="charging", label="充电枪已拔除", passed=vehicle.charging_disconnected, detail="充电互锁通过" if vehicle.charging_disconnected else "充电连接仍在"),
        SafetyGate(id="vehicle", label="车辆关键系统健康", passed=vehicle.vehicle_healthy, detail="关键系统自检通过" if vehicle.vehicle_healthy else "存在关键车辆故障"),
        SafetyGate(id="positioning", label="定位与场端在线", passed=vehicle.positioning_online, detail="定位与场端心跳正常" if vehicle.positioning_online else "定位或场端失联"),
        SafetyGate(id="network", label="主备通信可用", passed=vehicle.network_online, detail="主备链路在线" if vehicle.network_online else "通信链路不可用"),
        SafetyGate(id="operator", label="远程安全员在线", passed=vehicle.emergency_operator_online, detail="急停与人工接管可用" if vehicle.emergency_operator_online else "无法保证人工接管"),
        SafetyGate(id="water-contact", label="未触发水触禁行", passed=not vehicle.water_contact_triggered, detail="未触发水触" if not vehicle.water_contact_triggered else "车辆水触传感器已触发"),
    ]


def _risk(
    telemetry: TelemetryIn,
    policy: DecisionPolicy,
    latest_start: float,
    gates: list[SafetyGate],
) -> RiskLevel:
    if (
        telemetry.vehicle.water_contact_triggered
        or telemetry.environment.water_level_cm >= policy.danger_water_level_cm
        or latest_start <= 0
    ):
        return RiskLevel.CRITICAL
    if not all(gate.passed for gate in gates) or latest_start <= policy.migrate_horizon_min:
        return RiskLevel.HIGH
    if (
        latest_start <= policy.prepare_horizon_min
        or telemetry.environment.rainfall_mm_h >= policy.rain_watch_threshold_mm_h
    ):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def evaluate_decision(
    telemetry: TelemetryIn,
    policy: DecisionPolicy | None = None,
    *,
    owner_authorized: bool = False,
) -> DecisionOutput:
    policy = policy or DecisionPolicy()
    timing, latest_start = _timing(policy, telemetry)
    gates = _gates(telemetry)
    failed_gates = [gate for gate in gates if not gate.passed]
    environment = telemetry.environment
    vehicle = telemetry.vehicle
    disagreement = abs(environment.water_level_cm - environment.secondary_water_level_cm)

    motion_fault = vehicle.motion_state == MotionState.MOVING and (
        not vehicle.positioning_online
        or not vehicle.network_online
        or not vehicle.emergency_operator_online
        or not vehicle.vehicle_healthy
        or vehicle.water_contact_triggered
    )

    if motion_fault:
        decision = DecisionCode.EMERGENCY_STOP
        permission = Permission.DENIED
        reason = "车辆移动期间安全链路异常，立即执行最小风险停车并转人工。"
    elif failed_gates:
        decision = DecisionCode.NO_GO
        permission = Permission.DENIED
        reason = "安全闸未全部通过：" + "、".join(gate.label for gate in failed_gates) + "。"
    elif environment.water_level_cm >= policy.danger_water_level_cm:
        decision = DecisionCode.NO_GO
        permission = Permission.DENIED
        reason = "当前水位已达到禁行阈值，禁止尝试涉水迁移。"
    elif (
        environment.sensor_confidence < policy.min_sensor_confidence
        or disagreement > policy.max_sensor_disagreement_cm
    ):
        decision = DecisionCode.VERIFY_ONLY
        permission = Permission.DENIED
        reason = (
            f"多源证据不足：置信度 {environment.sensor_confidence * 100:.0f}%，"
            f"水位交叉差 {disagreement:.1f} cm；系统只提醒，不下发移动权限。"
        )
    elif latest_start <= 0:
        decision = DecisionCode.NO_GO
        permission = Permission.DENIED
        reason = "最晚安全启动窗口已经关闭，禁止迟发迁移并转人工处置。"
    elif latest_start <= policy.migrate_horizon_min:
        decision = DecisionCode.MIGRATE_NOW
        permission = Permission.GRANTED if owner_authorized else Permission.AWAITING_OWNER
        reason = (
            f"最晚安全启动窗口仅剩 {latest_start:.1f} 分钟；"
            + ("车主单次授权已确认。" if owner_authorized else "仍需车主单次授权。")
        )
    elif latest_start <= policy.prepare_horizon_min:
        decision = DecisionCode.PREPARE
        permission = Permission.AWAITING_OWNER
        reason = f"最晚安全启动窗口剩余 {latest_start:.1f} 分钟，建议预登记高位点并准备授权。"
    elif (
        environment.rainfall_mm_h >= policy.rain_watch_threshold_mm_h
        or environment.rise_rate_cm_min >= 0.5
    ):
        decision = DecisionCode.WATCH
        permission = Permission.NONE
        reason = "强降雨或水位上涨较快，提升采样频率并持续计算安全窗口。"
    else:
        decision = DecisionCode.STAY
        permission = Permission.NONE
        reason = "风险仍低且安全窗口充足，保持原位并持续守望。"

    return DecisionOutput(
        decision=decision,
        label=LABELS[decision],
        risk_level=_risk(telemetry, policy, latest_start, gates),
        permission=permission,
        authorized_to_move=decision == DecisionCode.MIGRATE_NOW and owner_authorized,
        reason=reason,
        sensor_disagreement_cm=disagreement,
        timing=timing,
        safety_gates=gates,
    )
