import { DEFAULT_INPUTS, evaluateDecision } from "./decision-engine.js";

export const FLEET_PLANNER_VERSION = "fleet-shadow-v1";

const ALLOCATION_STATUS = Object.freeze({
  NOT_REQUIRED: "NOT_REQUIRED",
  PREPARE_ONLY: "PREPARE_ONLY",
  VERIFY_ONLY: "VERIFY_ONLY",
  SCHEDULED_SHADOW: "SCHEDULED_SHADOW",
  NO_CAPACITY: "NO_CAPACITY",
  WINDOW_CLOSED: "WINDOW_CLOSED",
  SITE_UNAVAILABLE: "SITE_UNAVAILABLE",
  DENIED: "DENIED",
});

const DIRECT_STATUS = Object.freeze({
  STAY: ALLOCATION_STATUS.NOT_REQUIRED,
  WATCH: ALLOCATION_STATUS.NOT_REQUIRED,
  PREPARE: ALLOCATION_STATUS.PREPARE_ONLY,
  VERIFY_ONLY: ALLOCATION_STATUS.VERIFY_ONLY,
  NO_GO: ALLOCATION_STATUS.DENIED,
  EMERGENCY_STOP: ALLOCATION_STATUS.DENIED,
  SYSTEM_HOLD: ALLOCATION_STATUS.DENIED,
});

const DENIED_STATUSES = new Set([
  ALLOCATION_STATUS.NO_CAPACITY,
  ALLOCATION_STATUS.WINDOW_CLOSED,
  ALLOCATION_STATUS.SITE_UNAVAILABLE,
  ALLOCATION_STATUS.DENIED,
]);

const STATUS_REASONS = Object.freeze({
  [ALLOCATION_STATUS.SCHEDULED_SHADOW]: "影子计划已分配批次与安全点；无车辆执行权限。",
  [ALLOCATION_STATUS.NO_CAPACITY]: "高位安全点容量不足；保持原位并转人工协调。",
  [ALLOCATION_STATUS.WINDOW_CLOSED]: "排队后二次计算显示最晚安全启动窗口已关闭；禁止迟发迁移。",
  [ALLOCATION_STATUS.SITE_UNAVAILABLE]: "场端网关离线或观测过期；禁止形成迁移计划。",
});

function assertNumber(value, name, { min, max, integer = false }) {
  if (!Number.isFinite(value) || value < min || value > max || (integer && !Number.isInteger(value))) {
    throw new Error(`${name} is outside the fleet contract`);
  }
}

function assertTimestamp(value, name) {
  if (typeof value !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) || !Number.isFinite(Date.parse(value))) {
    throw new Error(`${name} must be a timezone-aware timestamp`);
  }
}

function validateSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") {
    throw new Error("snapshot must be an object");
  }
  if (!new Set(["SIMULATED", "SHADOW"]).has(snapshot.source_mode)) {
    throw new Error("source_mode must be SIMULATED or SHADOW");
  }
  if (!Array.isArray(snapshot.vehicles) || snapshot.vehicles.length < 1 || snapshot.vehicles.length > 50) {
    throw new Error("vehicles must contain between 1 and 50 items");
  }
  if (!snapshot.site || typeof snapshot.site !== "object") {
    throw new Error("site is required");
  }

  assertTimestamp(snapshot.captured_at, "captured_at");
  assertTimestamp(snapshot.site.observed_at, "site.observed_at");
  assertNumber(snapshot.site.batch_size, "site.batch_size", { min: 1, max: 50, integer: true });
  assertNumber(snapshot.site.batch_interval_min, "site.batch_interval_min", { min: 0, max: 60 });

  const safePoints = snapshot.site.safe_points;
  if (!Array.isArray(safePoints) || safePoints.length < 1 || safePoints.length > 50) {
    throw new Error("site.safe_points must contain between 1 and 50 items");
  }
  const safePointIds = new Set();
  for (const point of safePoints) {
    if (!point || typeof point.safe_point_id !== "string" || point.safe_point_id.length === 0) {
      throw new Error("safe_point_id is required");
    }
    if (safePointIds.has(point.safe_point_id)) {
      throw new Error("safe_point_id values must be unique");
    }
    safePointIds.add(point.safe_point_id);
    assertNumber(point.priority, "safe_point.priority", { min: 0, max: 1000, integer: true });
    assertNumber(point.capacity, "safe_point.capacity", { min: 1, max: 50, integer: true });
  }

  const vehicleIds = new Set();
  for (const vehicle of snapshot.vehicles) {
    const telemetry = vehicle?.telemetry;
    const vehicleId = telemetry?.vehicle_id;
    if (typeof vehicleId !== "string" || vehicleId.length === 0) {
      throw new Error("vehicle_id is required");
    }
    if (vehicleIds.has(vehicleId)) {
      throw new Error("vehicle_id values must be unique within a fleet snapshot");
    }
    vehicleIds.add(vehicleId);
    if (telemetry.site_id !== snapshot.site_id) {
      throw new Error("every telemetry.site_id must equal snapshot site_id");
    }
    assertTimestamp(telemetry.captured_at, `vehicles[${vehicleId}].telemetry.captured_at`);
  }
}

export function telemetryToDecisionInputs(vehicle, site) {
  const telemetry = vehicle.telemetry;
  const environment = telemetry.environment;
  const vehicleState = telemetry.vehicle;
  const siteState = telemetry.site;
  return {
    ...DEFAULT_INPUTS,
    rainfallMmH: environment.rainfall_mm_h,
    waterLevelCm: environment.water_level_cm,
    secondaryWaterLevelCm: environment.secondary_water_level_cm,
    riseRateCmMin: environment.rise_rate_cm_min,
    sensorConfidence: environment.sensor_confidence,
    dangerWaterLevelCm: vehicle.danger_water_level_cm ?? DEFAULT_INPUTS.dangerWaterLevelCm,
    routeDistanceM: vehicle.route_distance_m ?? DEFAULT_INPUTS.routeDistanceM,
    queueAhead: 0,
    batchSize: site.batch_size,
    batchIntervalMin: site.batch_interval_min,
    routeDry: siteState.route_dry,
    routeBlocked: siteState.route_blocked,
    occupantsClear: vehicleState.occupants_clear,
    chargingDisconnected: vehicleState.charging_disconnected,
    vehicleHealthy: vehicleState.vehicle_healthy,
    positioningOnline: vehicleState.positioning_online,
    networkOnline: vehicleState.network_online,
    emergencyOperatorOnline: vehicleState.emergency_operator_online,
    waterContactTriggered: vehicleState.water_contact_triggered,
    motionState: vehicleState.motion_state,
    ownerAuthorized: false,
  };
}

function nullableFinite(value) {
  return Number.isFinite(value) ? value : null;
}

function normalizeDecision(result) {
  return {
    decision: result.decision,
    label: result.label,
    risk_level: result.riskLevel,
    permission: result.permission,
    authorized_to_move: false,
    reason: result.reason,
    sensor_disagreement_cm: result.sensorDisagreementCm,
    timing: {
      remaining_cm: result.timing.remainingCm,
      time_to_threshold_min: nullableFinite(result.timing.timeToThresholdMin),
      route_time_min: result.timing.routeTimeMin,
      queue_time_min: result.timing.queueTimeMin,
      latest_safe_start_min: nullableFinite(result.timing.latestSafeStartMin),
    },
    safety_gates: result.safety.gates.map((gate) => ({
      id: gate.id,
      label: gate.label,
      passed: gate.ok,
      detail: gate.detail,
    })),
  };
}

function actionPermission(status) {
  if (status === ALLOCATION_STATUS.SCHEDULED_SHADOW) return "SHADOW_ONLY";
  if (DENIED_STATUSES.has(status)) return "DENIED";
  return "NONE";
}

function vehiclePlan({
  vehicleId,
  baseDecision,
  status,
  rank = null,
  batchIndex = null,
  queueAhead = null,
  safePointId = null,
}) {
  return {
    vehicle_id: vehicleId,
    base_decision: normalizeDecision(baseDecision),
    allocation_status: status,
    rank,
    batch_index: batchIndex,
    queue_ahead: queueAhead,
    safe_point_id: safePointId,
    action_permission: actionPermission(status),
    authorized_to_move: false,
    reason: STATUS_REASONS[status] ?? baseDecision.reason,
  };
}

function availableSlots(snapshot) {
  const points = snapshot.site.safe_points
    .filter((point) => point.available)
    .sort((left, right) => (
      left.priority - right.priority
      || compareIdentifiers(left.safe_point_id, right.safe_point_id)
    ));
  return points.flatMap((point) => Array(point.capacity).fill(point.safe_point_id));
}

function compareIdentifiers(left, right) {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function compareCandidates(left, right) {
  const leftStart = left.baseDecision.timing.latestSafeStartMin;
  const rightStart = right.baseDecision.timing.latestSafeStartMin;
  if (leftStart < rightStart) return -1;
  if (leftStart > rightStart) return 1;
  return compareIdentifiers(left.vehicle.telemetry.vehicle_id, right.vehicle.telemetry.vehicle_id);
}

export function planFleet(snapshot, options = {}) {
  validateSnapshot(snapshot);
  const now = options.now ?? snapshot.captured_at;
  const siteMaxAgeSeconds = options.siteMaxAgeSeconds ?? 300;
  assertTimestamp(now, "options.now");
  assertNumber(siteMaxAgeSeconds, "options.siteMaxAgeSeconds", { min: 0, max: Number.MAX_SAFE_INTEGER });

  const evaluated = snapshot.vehicles.map((vehicle) => {
    const decisionInputs = telemetryToDecisionInputs(vehicle, snapshot.site);
    return {
      vehicle,
      decisionInputs,
      baseDecision: evaluateDecision(decisionInputs),
    };
  });
  const candidates = evaluated
    .filter((item) => item.baseDecision.decision === "MIGRATE_NOW")
    .sort(compareCandidates);
  const nonCandidates = evaluated
    .filter((item) => item.baseDecision.decision !== "MIGRATE_NOW")
    .sort((left, right) => (
      compareIdentifiers(left.vehicle.telemetry.vehicle_id, right.vehicle.telemetry.vehicle_id)
    ));

  const siteAgeSeconds = (Date.parse(now) - Date.parse(snapshot.site.observed_at)) / 1000;
  const siteAvailable = snapshot.site.gateway_online && siteAgeSeconds <= siteMaxAgeSeconds;
  const slots = availableSlots(snapshot);
  let slotIndex = 0;

  const candidatePlans = candidates.map((item, index) => {
    const rank = index + 1;
    const vehicleId = item.vehicle.telemetry.vehicle_id;
    if (!siteAvailable) {
      return vehiclePlan({
        vehicleId,
        baseDecision: item.baseDecision,
        status: ALLOCATION_STATUS.SITE_UNAVAILABLE,
        rank,
      });
    }
    if (slotIndex >= slots.length) {
      return vehiclePlan({
        vehicleId,
        baseDecision: item.baseDecision,
        status: ALLOCATION_STATUS.NO_CAPACITY,
        rank,
      });
    }

    const allocatedPoint = slots[slotIndex];
    slotIndex += 1;
    const queueAhead = rank - 1;
    const batchIndex = Math.floor(queueAhead / snapshot.site.batch_size) + 1;
    const reevaluated = evaluateDecision({
      ...item.decisionInputs,
      queueAhead,
      ownerAuthorized: false,
    });
    if (reevaluated.decision !== "MIGRATE_NOW") {
      return vehiclePlan({
        vehicleId,
        baseDecision: item.baseDecision,
        status: ALLOCATION_STATUS.WINDOW_CLOSED,
        rank,
        batchIndex,
        queueAhead,
      });
    }
    return vehiclePlan({
      vehicleId,
      baseDecision: item.baseDecision,
      status: ALLOCATION_STATUS.SCHEDULED_SHADOW,
      rank,
      batchIndex,
      queueAhead,
      safePointId: allocatedPoint,
    });
  });

  const directPlans = nonCandidates.map((item) => vehiclePlan({
    vehicleId: item.vehicle.telemetry.vehicle_id,
    baseDecision: item.baseDecision,
    status: DIRECT_STATUS[item.baseDecision.decision] ?? ALLOCATION_STATUS.DENIED,
  }));
  const vehicles = [...candidatePlans, ...directPlans];
  const scheduledCount = vehicles.filter(
    (vehicle) => vehicle.allocation_status === ALLOCATION_STATUS.SCHEDULED_SHADOW,
  ).length;

  return {
    snapshot_id: snapshot.snapshot_id,
    site_id: snapshot.site_id,
    source_mode: snapshot.source_mode,
    planner_version: FLEET_PLANNER_VERSION,
    summary: {
      vehicle_count: vehicles.length,
      scheduled_count: scheduledCount,
      verify_count: vehicles.filter(
        (vehicle) => vehicle.allocation_status === ALLOCATION_STATUS.VERIFY_ONLY,
      ).length,
      denied_count: vehicles.filter(
        (vehicle) => DENIED_STATUSES.has(vehicle.allocation_status),
      ).length,
      remaining_capacity: slots.length - scheduledCount,
    },
    vehicles,
  };
}

export function projectFleetPlan(plan) {
  return {
    summary: {
      vehicle_count: plan.summary.vehicle_count,
      scheduled_count: plan.summary.scheduled_count,
      verify_count: plan.summary.verify_count,
      denied_count: plan.summary.denied_count,
      remaining_capacity: plan.summary.remaining_capacity,
    },
    vehicles: plan.vehicles.map((vehicle) => ({
      vehicle_id: vehicle.vehicle_id,
      decision: vehicle.base_decision.decision,
      allocation_status: vehicle.allocation_status,
      rank: vehicle.rank,
      batch_index: vehicle.batch_index,
      queue_ahead: vehicle.queue_ahead,
      safe_point_id: vehicle.safe_point_id,
      action_permission: vehicle.action_permission,
      authorized_to_move: vehicle.authorized_to_move,
    })),
  };
}
