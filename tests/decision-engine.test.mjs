import test from "node:test";
import assert from "node:assert/strict";

import {
  DECISIONS,
  DEFAULT_INPUTS,
  calculateTiming,
  evaluateDecision,
  normalizeInputs,
} from "../src/decision-engine.js";

test("低风险且窗口充足时保持原位守望", () => {
  const result = evaluateDecision(DEFAULT_INPUTS);
  assert.equal(result.decision, DECISIONS.STAY);
  assert.equal(result.permission, "NONE");
  assert.equal(result.authorizedToMove, false);
});

test("快速上涨且进入最晚窗口时建议立即迁移，但无授权不移动", () => {
  const result = evaluateDecision({
    ...DEFAULT_INPUTS,
    rainfallMmH: 96,
    waterLevelCm: 14,
    secondaryWaterLevelCm: 13.5,
    riseRateCmMin: 1,
  });
  assert.equal(result.decision, DECISIONS.MIGRATE_NOW);
  assert.equal(result.permission, "AWAITING_OWNER");
  assert.equal(result.authorizedToMove, false);
});

test("全部安全闸通过且车主单次授权后才授予移动权限", () => {
  const result = evaluateDecision({
    ...DEFAULT_INPUTS,
    waterLevelCm: 14,
    secondaryWaterLevelCm: 14,
    riseRateCmMin: 1,
    ownerAuthorized: true,
  });
  assert.equal(result.decision, DECISIONS.MIGRATE_NOW);
  assert.equal(result.permission, "GRANTED");
  assert.equal(result.authorizedToMove, true);
});

test("多源水位冲突时只提醒并等待复核", () => {
  const result = evaluateDecision({
    ...DEFAULT_INPUTS,
    waterLevelCm: 8,
    secondaryWaterLevelCm: 17,
  });
  assert.equal(result.decision, DECISIONS.VERIFY_ONLY);
  assert.equal(result.permission, "DENIED");
});

test("传感器置信度过低时不下发移动权限", () => {
  const result = evaluateDecision({ ...DEFAULT_INPUTS, sensorConfidence: 0.55 });
  assert.equal(result.decision, DECISIONS.VERIFY_ONLY);
});

test("路线见水时 No-Go 优先于时间窗口", () => {
  const result = evaluateDecision({
    ...DEFAULT_INPUTS,
    waterLevelCm: 14,
    secondaryWaterLevelCm: 14,
    riseRateCmMin: 1,
    routeDry: false,
    ownerAuthorized: true,
  });
  assert.equal(result.decision, DECISIONS.NO_GO);
  assert.equal(result.authorizedToMove, false);
});

test("车内有人时即使授权也禁止迁移", () => {
  const result = evaluateDecision({
    ...DEFAULT_INPUTS,
    waterLevelCm: 14,
    secondaryWaterLevelCm: 14,
    riseRateCmMin: 1,
    occupantsClear: false,
    ownerAuthorized: true,
  });
  assert.equal(result.decision, DECISIONS.NO_GO);
});

test("移动中通信异常触发最小风险停车", () => {
  const result = evaluateDecision({
    ...DEFAULT_INPUTS,
    motionState: "MOVING",
    networkOnline: false,
  });
  assert.equal(result.decision, DECISIONS.EMERGENCY_STOP);
  assert.equal(result.permission, "DENIED");
});

test("最晚安全启动窗口已关闭时禁止迟发迁移", () => {
  const result = evaluateDecision({
    ...DEFAULT_INPUTS,
    waterLevelCm: 18,
    secondaryWaterLevelCm: 18,
    riseRateCmMin: 1,
    ownerAuthorized: true,
  });
  assert.equal(result.decision, DECISIONS.NO_GO);
  assert.equal(result.riskLevel, "CRITICAL");
  assert.equal(result.authorizedToMove, false);
});

test("无上涨时到阈值时间为无穷，窗口保持充足", () => {
  const inputs = normalizeInputs({ ...DEFAULT_INPUTS, riseRateCmMin: 0 });
  const timing = calculateTiming(inputs);
  assert.equal(timing.timeToThresholdMin, Number.POSITIVE_INFINITY);
  assert.equal(timing.latestSafeStartMin, Number.POSITIVE_INFINITY);
});

test("非法车速进入系统保持状态", () => {
  const result = evaluateDecision({ ...DEFAULT_INPUTS, maxSpeedKmh: 8 });
  assert.equal(result.decision, DECISIONS.SYSTEM_HOLD);
  assert.ok(result.invalidReasons.length > 0);
});
