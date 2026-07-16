/**
 * XPENG HighGround 概念决策引擎
 *
 * 这是可解释、可测试的概念验证代码，不连接真实车辆，也不构成量产安全功能。
 * 所有车辆动作必须经过独立安全壳、车主单次授权和封闭场地验证。
 */

export const DECISIONS = Object.freeze({
  STAY: "STAY",
  WATCH: "WATCH",
  PREPARE: "PREPARE",
  MIGRATE_NOW: "MIGRATE_NOW",
  VERIFY_ONLY: "VERIFY_ONLY",
  NO_GO: "NO_GO",
  EMERGENCY_STOP: "EMERGENCY_STOP",
  SYSTEM_HOLD: "SYSTEM_HOLD",
});

export const DECISION_META = Object.freeze({
  [DECISIONS.STAY]: { label: "原地守望", tone: "safe", permission: "NONE" },
  [DECISIONS.WATCH]: { label: "增强监测", tone: "watch", permission: "NONE" },
  [DECISIONS.PREPARE]: { label: "建议准备迁移", tone: "warning", permission: "AWAITING_OWNER" },
  [DECISIONS.MIGRATE_NOW]: { label: "建议立即迁移", tone: "danger", permission: "AWAITING_OWNER" },
  [DECISIONS.VERIFY_ONLY]: { label: "只提醒，等待复核", tone: "warning", permission: "DENIED" },
  [DECISIONS.NO_GO]: { label: "No-Go：禁止迁移", tone: "danger", permission: "DENIED" },
  [DECISIONS.EMERGENCY_STOP]: { label: "异常停车并转人工", tone: "danger", permission: "DENIED" },
  [DECISIONS.SYSTEM_HOLD]: { label: "输入异常，系统保持", tone: "warning", permission: "DENIED" },
});

export const DEFAULT_INPUTS = Object.freeze({
  rainfallMmH: 35,
  waterLevelCm: 4,
  secondaryWaterLevelCm: 4,
  riseRateCmMin: 0.2,
  dangerWaterLevelCm: 22,
  rainWatchThresholdMmH: 50,
  minSensorConfidence: 0.72,
  sensorConfidence: 0.94,
  maxSensorDisagreementCm: 5,
  routeDistanceM: 260,
  maxSpeedKmh: 5,
  queueAhead: 2,
  batchSize: 3,
  batchIntervalMin: 0.7,
  safetyBufferMin: 3,
  prepareHorizonMin: 20,
  migrateHorizonMin: 7,
  routeDry: true,
  routeBlocked: false,
  occupantsClear: true,
  chargingDisconnected: true,
  vehicleHealthy: true,
  positioningOnline: true,
  networkOnline: true,
  emergencyOperatorOnline: true,
  ownerAuthorized: false,
  waterContactTriggered: false,
  motionState: "PARKED",
});

const NUMBER_KEYS = [
  "rainfallMmH",
  "waterLevelCm",
  "secondaryWaterLevelCm",
  "riseRateCmMin",
  "dangerWaterLevelCm",
  "rainWatchThresholdMmH",
  "minSensorConfidence",
  "sensorConfidence",
  "maxSensorDisagreementCm",
  "routeDistanceM",
  "maxSpeedKmh",
  "queueAhead",
  "batchSize",
  "batchIntervalMin",
  "safetyBufferMin",
  "prepareHorizonMin",
  "migrateHorizonMin",
];

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : Number.NaN;
}

export function normalizeInputs(raw = {}) {
  const normalized = { ...DEFAULT_INPUTS, ...raw };
  for (const key of NUMBER_KEYS) normalized[key] = finiteNumber(normalized[key]);
  normalized.queueAhead = Math.max(0, Math.floor(normalized.queueAhead));
  normalized.batchSize = Math.max(1, Math.floor(normalized.batchSize));
  return normalized;
}

export function calculateTiming(inputs) {
  const rise = Math.max(0, inputs.riseRateCmMin);
  const remainingCm = inputs.dangerWaterLevelCm - inputs.waterLevelCm;
  const timeToThresholdMin = remainingCm <= 0
    ? 0
    : rise === 0
      ? Number.POSITIVE_INFINITY
      : remainingCm / rise;

  const metersPerMinute = inputs.maxSpeedKmh * 1000 / 60;
  const routeTimeMin = metersPerMinute > 0
    ? inputs.routeDistanceM / metersPerMinute
    : Number.POSITIVE_INFINITY;
  const queueBatches = Math.ceil(inputs.queueAhead / inputs.batchSize);
  const queueTimeMin = queueBatches * inputs.batchIntervalMin;
  const latestSafeStartMin = timeToThresholdMin
    - routeTimeMin
    - queueTimeMin
    - inputs.safetyBufferMin;

  return {
    remainingCm,
    timeToThresholdMin,
    routeTimeMin,
    queueTimeMin,
    latestSafeStartMin,
  };
}

function check(id, label, ok, detail, severity = "critical") {
  return { id, label, ok: Boolean(ok), detail, severity };
}

export function evaluateSafetyGates(inputs) {
  const gates = [
    check("route-dry", "路线全程干燥", inputs.routeDry, inputs.routeDry ? "未发现水触点" : "路线见水或触发禁行阈值"),
    check("route-open", "路线与出口可用", !inputs.routeBlocked, inputs.routeBlocked ? "闸机、坡道或出口不可用" : "路线与出口在线"),
    check("occupants", "车内无人或宠物", inputs.occupantsClear, inputs.occupantsClear ? "乘员检测通过" : "检测到乘员或宠物"),
    check("charging", "充电枪已拔除", inputs.chargingDisconnected, inputs.chargingDisconnected ? "充电互锁通过" : "充电连接仍在"),
    check("vehicle", "车辆关键系统健康", inputs.vehicleHealthy, inputs.vehicleHealthy ? "关键系统自检通过" : "存在关键车辆故障"),
    check("positioning", "定位与场端在线", inputs.positioningOnline, inputs.positioningOnline ? "定位与场端心跳正常" : "定位或场端失联"),
    check("network", "主备通信可用", inputs.networkOnline, inputs.networkOnline ? "主备链路在线" : "通信链路不可用"),
    check("operator", "远程安全员在线", inputs.emergencyOperatorOnline, inputs.emergencyOperatorOnline ? "急停与人工接管可用" : "无法保证人工接管"),
    check("water-contact", "未触发水触禁行", !inputs.waterContactTriggered, inputs.waterContactTriggered ? "车辆水触传感器已触发" : "未触发水触", "critical"),
  ];
  return {
    gates,
    allPassed: gates.every((gate) => gate.ok),
    failed: gates.filter((gate) => !gate.ok),
  };
}

function invalidInputReasons(inputs) {
  const reasons = [];
  for (const key of NUMBER_KEYS) {
    if (!Number.isFinite(inputs[key])) reasons.push(`${key} 不是有效数字`);
  }
  if (inputs.rainfallMmH < 0) reasons.push("降雨强度不能为负数");
  if (inputs.waterLevelCm < 0 || inputs.secondaryWaterLevelCm < 0) reasons.push("水位不能为负数");
  if (inputs.dangerWaterLevelCm <= 0) reasons.push("禁行水位必须大于 0");
  if (inputs.maxSpeedKmh <= 0 || inputs.maxSpeedKmh > 5) reasons.push("概念场景车速必须在 0–5 km/h 内");
  if (inputs.sensorConfidence < 0 || inputs.sensorConfidence > 1) reasons.push("传感器置信度必须在 0–1 内");
  return reasons;
}

function riskLevel(inputs, timing, safety) {
  if (inputs.waterContactTriggered || inputs.waterLevelCm >= inputs.dangerWaterLevelCm || timing.latestSafeStartMin <= 0) return "CRITICAL";
  if (!safety.allPassed || timing.latestSafeStartMin <= inputs.migrateHorizonMin) return "HIGH";
  if (timing.latestSafeStartMin <= inputs.prepareHorizonMin || inputs.rainfallMmH >= inputs.rainWatchThresholdMmH) return "MEDIUM";
  return "LOW";
}

function makeResult(inputs, timing, safety, decision, reason, extra = {}) {
  const meta = DECISION_META[decision];
  const authorizedToMove = decision === DECISIONS.MIGRATE_NOW
    && safety.allPassed
    && inputs.ownerAuthorized;
  return {
    decision,
    label: meta.label,
    tone: meta.tone,
    permission: authorizedToMove ? "GRANTED" : meta.permission,
    authorizedToMove,
    reason,
    riskLevel: riskLevel(inputs, timing, safety),
    inputs,
    timing,
    safety,
    ...extra,
  };
}

/**
 * 决策顺序体现“安全壳优先”：输入有效性 → 移动中异常 → 证据一致性
 * → 物理禁行条件 → 最晚安全启动时间 → 预警守望。
 */
export function evaluateDecision(rawInputs = {}) {
  const inputs = normalizeInputs(rawInputs);
  const invalidReasons = invalidInputReasons(inputs);
  const safety = evaluateSafetyGates(inputs);
  const timing = calculateTiming(inputs);

  if (invalidReasons.length) {
    return makeResult(inputs, timing, safety, DECISIONS.SYSTEM_HOLD, invalidReasons.join("；"), { invalidReasons });
  }

  const motionFault = inputs.motionState === "MOVING" && (
    !inputs.positioningOnline
    || !inputs.networkOnline
    || !inputs.emergencyOperatorOnline
    || !inputs.vehicleHealthy
    || inputs.waterContactTriggered
  );
  if (motionFault) {
    return makeResult(inputs, timing, safety, DECISIONS.EMERGENCY_STOP, "车辆移动期间安全链路异常，立即执行最小风险停车并转人工。");
  }

  if (!safety.allPassed) {
    return makeResult(
      inputs,
      timing,
      safety,
      DECISIONS.NO_GO,
      `安全闸未全部通过：${safety.failed.map((gate) => gate.label).join("、")}。`,
      { sensorDisagreementCm: Math.abs(inputs.waterLevelCm - inputs.secondaryWaterLevelCm) },
    );
  }

  if (inputs.waterLevelCm >= inputs.dangerWaterLevelCm) {
    return makeResult(inputs, timing, safety, DECISIONS.NO_GO, "当前水位已达到禁行阈值，禁止尝试涉水迁移。", { sensorDisagreementCm: Math.abs(inputs.waterLevelCm - inputs.secondaryWaterLevelCm) });
  }

  const sensorDisagreementCm = Math.abs(inputs.waterLevelCm - inputs.secondaryWaterLevelCm);
  const evidenceUncertain = inputs.sensorConfidence < inputs.minSensorConfidence
    || sensorDisagreementCm > inputs.maxSensorDisagreementCm;
  if (evidenceUncertain) {
    return makeResult(
      inputs,
      timing,
      safety,
      DECISIONS.VERIFY_ONLY,
      `多源证据不足：置信度 ${(inputs.sensorConfidence * 100).toFixed(0)}%，水位交叉差 ${sensorDisagreementCm.toFixed(1)} cm；系统只提醒，不下发移动权限。`,
      { sensorDisagreementCm },
    );
  }

  if (timing.latestSafeStartMin <= 0) {
    return makeResult(inputs, timing, safety, DECISIONS.NO_GO, "最晚安全启动窗口已经关闭，禁止迟发迁移并转人工处置。", { sensorDisagreementCm });
  }

  if (timing.latestSafeStartMin <= inputs.migrateHorizonMin) {
    const ownerText = inputs.ownerAuthorized ? "车主单次授权已确认。" : "仍需车主单次授权。";
    return makeResult(
      inputs,
      timing,
      safety,
      DECISIONS.MIGRATE_NOW,
      `最晚安全启动窗口仅剩 ${Math.max(0, timing.latestSafeStartMin).toFixed(1)} 分钟；${ownerText}`,
      { sensorDisagreementCm },
    );
  }

  if (timing.latestSafeStartMin <= inputs.prepareHorizonMin) {
    return makeResult(inputs, timing, safety, DECISIONS.PREPARE, `最晚安全启动窗口剩余 ${timing.latestSafeStartMin.toFixed(1)} 分钟，建议预登记高位点并准备授权。`, { sensorDisagreementCm });
  }

  if (inputs.rainfallMmH >= inputs.rainWatchThresholdMmH || inputs.riseRateCmMin >= 0.5) {
    return makeResult(inputs, timing, safety, DECISIONS.WATCH, "强降雨或水位上涨较快，提升采样频率并持续计算安全窗口。", { sensorDisagreementCm });
  }

  return makeResult(inputs, timing, safety, DECISIONS.STAY, "风险仍低且安全窗口充足，保持原位并持续守望。", { sensorDisagreementCm });
}

export function formatMinutes(value) {
  if (value === Number.POSITIVE_INFINITY) return "充足";
  if (!Number.isFinite(value)) return "--";
  return `${Math.max(0, value).toFixed(1)} min`;
}
