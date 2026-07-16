import {
  DECISIONS,
  DEFAULT_INPUTS,
  evaluateDecision,
  formatMinutes,
} from "./decision-engine.js";

const SCENARIOS = Object.freeze({
  normal: {
    ...DEFAULT_INPUTS,
    rainfallMmH: 35,
    waterLevelCm: 4,
    secondaryWaterLevelCm: 4,
    riseRateCmMin: 0.2,
    sensorConfidence: 0.94,
  },
  rising: {
    ...DEFAULT_INPUTS,
    rainfallMmH: 96,
    waterLevelCm: 14,
    secondaryWaterLevelCm: 13.5,
    riseRateCmMin: 1,
    sensorConfidence: 0.91,
  },
  conflict: {
    ...DEFAULT_INPUTS,
    rainfallMmH: 78,
    waterLevelCm: 9,
    secondaryWaterLevelCm: 18,
    riseRateCmMin: 0.7,
    sensorConfidence: 0.65,
  },
  blocked: {
    ...DEFAULT_INPUTS,
    rainfallMmH: 91,
    waterLevelCm: 13,
    secondaryWaterLevelCm: 13.5,
    riseRateCmMin: 0.9,
    routeBlocked: true,
  },
  occupant: {
    ...DEFAULT_INPUTS,
    rainfallMmH: 90,
    waterLevelCm: 14,
    secondaryWaterLevelCm: 14,
    riseRateCmMin: 1,
    occupantsClear: false,
  },
  movingFault: {
    ...DEFAULT_INPUTS,
    rainfallMmH: 98,
    waterLevelCm: 15,
    secondaryWaterLevelCm: 15,
    riseRateCmMin: 1.1,
    ownerAuthorized: true,
    networkOnline: false,
    motionState: "MOVING",
  },
});

const ids = [
  "scenario", "run-button", "step-button", "reset-button", "rainfall", "water-level",
  "secondary-water", "rise-rate", "sensor-confidence", "route-dry", "route-open",
  "occupants-clear", "charging-disconnected", "vehicle-healthy", "positioning-online",
  "network-online", "operator-online", "owner-authorized", "rain-output", "water-output",
  "water-2-output", "rise-output", "confidence-output", "decision-value", "permission-value",
  "risk-value", "confidence-value", "latest-start-value", "threshold-value", "decision-reason",
  "evidence-body", "event-id", "snapshot-hash", "action-permission", "event-time", "vehicle",
  "water", "water-line", "water-label", "scene-status-dot", "scene-status-text", "scene-desc",
  "play-migration-button", "animation-step", "animation-speed", "animation-progress",
  "animation-percent", "migration-route",
];

const el = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));
let eventCounter = 0;
let activeScenario = { ...SCENARIOS.normal };
let currentResult = null;
let animationFrameId = null;
let animationRunId = 0;
let isAnimating = false;
let vehicleAtHighPoint = false;

function setCheckbox(element, checked) {
  element.checked = Boolean(checked);
}

function applyScenario(key) {
  cancelMigrationAnimation({ resetVehicle: true });
  activeScenario = { ...(SCENARIOS[key] ?? SCENARIOS.normal) };
  el.rainfall.value = activeScenario.rainfallMmH;
  el["water-level"].value = activeScenario.waterLevelCm;
  el["secondary-water"].value = activeScenario.secondaryWaterLevelCm;
  el["rise-rate"].value = activeScenario.riseRateCmMin;
  el["sensor-confidence"].value = activeScenario.sensorConfidence;
  setCheckbox(el["route-dry"], activeScenario.routeDry);
  setCheckbox(el["route-open"], !activeScenario.routeBlocked);
  setCheckbox(el["occupants-clear"], activeScenario.occupantsClear);
  setCheckbox(el["charging-disconnected"], activeScenario.chargingDisconnected);
  setCheckbox(el["vehicle-healthy"], activeScenario.vehicleHealthy);
  setCheckbox(el["positioning-online"], activeScenario.positioningOnline);
  setCheckbox(el["network-online"], activeScenario.networkOnline);
  setCheckbox(el["operator-online"], activeScenario.emergencyOperatorOnline);
  setCheckbox(el["owner-authorized"], activeScenario.ownerAuthorized);
  updateOutputs();
  runDecision();
}

function currentInputs() {
  return {
    ...activeScenario,
    rainfallMmH: Number(el.rainfall.value),
    waterLevelCm: Number(el["water-level"].value),
    secondaryWaterLevelCm: Number(el["secondary-water"].value),
    riseRateCmMin: Number(el["rise-rate"].value),
    sensorConfidence: Number(el["sensor-confidence"].value),
    routeDry: el["route-dry"].checked,
    routeBlocked: !el["route-open"].checked,
    occupantsClear: el["occupants-clear"].checked,
    chargingDisconnected: el["charging-disconnected"].checked,
    vehicleHealthy: el["vehicle-healthy"].checked,
    positioningOnline: el["positioning-online"].checked,
    networkOnline: el["network-online"].checked,
    emergencyOperatorOnline: el["operator-online"].checked,
    ownerAuthorized: el["owner-authorized"].checked,
  };
}

function updateOutputs() {
  el["rain-output"].value = `${Number(el.rainfall.value).toFixed(0)} mm/h`;
  el["water-output"].value = `${Number(el["water-level"].value).toFixed(1)} cm`;
  el["water-2-output"].value = `${Number(el["secondary-water"].value).toFixed(1)} cm`;
  el["rise-output"].value = `${Number(el["rise-rate"].value).toFixed(1)} cm/min`;
  el["confidence-output"].value = `${Math.round(Number(el["sensor-confidence"].value) * 100)}%`;
}

function permissionText(result) {
  if (result.permission === "GRANTED") return "权限：单次授权已通过";
  if (result.permission === "AWAITING_OWNER") return "权限：等待车主单次授权";
  if (result.permission === "DENIED") return "权限：禁止下发移动指令";
  return "权限：无需移动";
}

function toneColor(tone) {
  return {
    safe: "#54e4b8",
    watch: "#ffc85a",
    warning: "#ffc85a",
    danger: "#ff705c",
  }[tone] ?? "#28d9e7";
}

function updateScene(result) {
  const waterLevel = Math.min(30, Math.max(0, result.inputs.waterLevelCm));
  const waterY = 327 - waterLevel * 5.5;
  const waterHeight = 360 - waterY;
  el.water.setAttribute("y", String(waterY));
  el.water.setAttribute("height", String(waterHeight));
  el["water-line"].setAttribute(
    "d",
    `M0 ${waterY} C80 ${waterY - 9} 145 ${waterY + 9} 220 ${waterY} S360 ${waterY - 9} 440 ${waterY} S550 ${waterY + 9} 630 ${waterY}`,
  );
  el["water-label"].textContent = `当前积水 ${waterLevel.toFixed(1)} cm`;

  if (vehicleAtHighPoint && result.authorizedToMove) {
    el.vehicle.setAttribute("transform", "translate(760 135) scale(.8)");
  } else if (result.decision === DECISIONS.EMERGENCY_STOP) {
    el.vehicle.setAttribute("transform", "translate(390 214) scale(.92)");
  } else {
    el.vehicle.setAttribute("transform", "translate(155 235)");
  }

  const color = toneColor(result.tone);
  el["scene-status-dot"].setAttribute("fill", color);
  el["scene-status-text"].textContent = result.authorizedToMove ? "已授权：驶向高位点" : result.label;
  el["scene-desc"].textContent = `当前水位 ${waterLevel.toFixed(1)} 厘米，系统决策为${result.label}，动作权限为${result.permission}。`;
}

function appendEvidenceRow(label, ok, detail) {
  const row = document.createElement("tr");
  const labelCell = document.createElement("td");
  const resultCell = document.createElement("td");
  const detailCell = document.createElement("td");
  const resultBadge = document.createElement("span");

  labelCell.textContent = label;
  resultBadge.className = `check-result ${ok ? "pass" : "fail"}`;
  resultBadge.textContent = ok ? "通过" : "未通过";
  resultCell.append(resultBadge);
  detailCell.textContent = detail;
  row.append(labelCell, resultCell, detailCell);
  el["evidence-body"].append(row);
}

function renderEvidence(result) {
  el["evidence-body"].replaceChildren();
  const disagreement = Math.abs(result.inputs.waterLevelCm - result.inputs.secondaryWaterLevelCm);
  appendEvidenceRow(
    "多源水位一致性",
    disagreement <= result.inputs.maxSensorDisagreementCm,
    `交叉差 ${disagreement.toFixed(1)} cm；允许上限 ${result.inputs.maxSensorDisagreementCm.toFixed(1)} cm`,
  );
  appendEvidenceRow(
    "证据置信度",
    result.inputs.sensorConfidence >= result.inputs.minSensorConfidence,
    `当前 ${(result.inputs.sensorConfidence * 100).toFixed(0)}%；门槛 ${(result.inputs.minSensorConfidence * 100).toFixed(0)}%`,
  );
  appendEvidenceRow(
    "最晚安全启动窗口",
    result.timing.latestSafeStartMin > 0,
    `阈值时间 ${formatMinutes(result.timing.timeToThresholdMin)} − 路线 ${formatMinutes(result.timing.routeTimeMin)} − 排队 ${formatMinutes(result.timing.queueTimeMin)} − 缓冲 ${result.inputs.safetyBufferMin.toFixed(1)} min`,
  );
  for (const gate of result.safety.gates) appendEvidenceRow(gate.label, gate.ok, gate.detail);
  appendEvidenceRow(
    "车主单次授权",
    result.permission !== "AWAITING_OWNER",
    result.inputs.ownerAuthorized ? "本次事件授权已确认，不跨事件复用" : "未授权，不允许车辆自动移动",
  );
}

function snapshotHash(inputs) {
  const text = JSON.stringify(inputs, Object.keys(inputs).sort());
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `FNV1A-${(hash >>> 0).toString(16).padStart(8, "0").toUpperCase()}`;
}

function updatePipeline(result) {
  const stages = [...document.querySelectorAll(".pipeline li")];
  const actAllowed = result.authorizedToMove || result.decision === DECISIONS.EMERGENCY_STOP;
  stages.forEach((stage, index) => {
    const active = index < 3 || (index === 3 && actAllowed) || index === 4;
    stage.classList.toggle("is-active", active);
  });
}

function runDecision() {
  updateOutputs();
  const result = evaluateDecision(currentInputs());
  eventCounter += 1;
  document.body.dataset.tone = result.tone;
  el["decision-value"].textContent = result.label;
  el["permission-value"].textContent = permissionText(result);
  el["risk-value"].textContent = result.riskLevel;
  el["confidence-value"].textContent = `证据置信度 ${(result.inputs.sensorConfidence * 100).toFixed(0)}%`;
  el["latest-start-value"].textContent = formatMinutes(result.timing.latestSafeStartMin);
  el["threshold-value"].textContent = `距禁行水位 ${Math.max(0, result.timing.remainingCm).toFixed(1)} cm`;
  el["decision-reason"].textContent = result.reason;
  el["event-id"].textContent = `EVENT-${String(eventCounter).padStart(4, "0")}`;
  el["snapshot-hash"].textContent = snapshotHash(result.inputs);
  el["action-permission"].textContent = result.permission;
  el["event-time"].dateTime = new Date().toISOString();
  el["event-time"].textContent = new Date().toLocaleString("zh-CN", { hour12: false });
  renderEvidence(result);
  updateScene(result);
  updatePipeline(result);
  currentResult = result;
  return result;
}

function stepOneMinute() {
  cancelMigrationAnimation({ resetVehicle: true });
  const rise = Number(el["rise-rate"].value);
  el["water-level"].value = Math.min(30, Number(el["water-level"].value) + rise);
  el["secondary-water"].value = Math.min(30, Number(el["secondary-water"].value) + rise * 0.96);
  runDecision();
}

function setAnimationHud(step, speedKmh, progress) {
  const percent = Math.round(Math.min(1, Math.max(0, progress)) * 100);
  el["animation-step"].textContent = step;
  el["animation-speed"].textContent = `${speedKmh.toFixed(1)} km/h`;
  el["animation-progress"].style.width = `${percent}%`;
  el["animation-percent"].textContent = `${percent}%`;
}

function setVehiclePose(x, y, rotation = 0, scale = 1) {
  el.vehicle.setAttribute("transform", `translate(${x.toFixed(1)} ${y.toFixed(1)}) rotate(${rotation.toFixed(1)} 76 68) scale(${scale.toFixed(3)})`);
}

function cancelMigrationAnimation({ resetVehicle = false } = {}) {
  animationRunId += 1;
  if (animationFrameId !== null) cancelAnimationFrame(animationFrameId);
  animationFrameId = null;
  isAnimating = false;
  el.vehicle.classList.remove("is-animating", "is-driving");
  el["migration-route"].classList.remove("is-active");
  el["play-migration-button"].disabled = false;
  el["play-migration-button"].innerHTML = '<span aria-hidden="true">▶</span> 播放车辆迁移动画';
  if (resetVehicle) {
    vehicleAtHighPoint = false;
    setVehiclePose(155, 235);
    setAnimationHud("等待播放", 0, 0);
  }
}

function motionPose(progress) {
  if (progress <= 0.56) {
    const local = progress / 0.56;
    return {
      x: 155 + 355 * local,
      y: 235 - 8 * Math.sin(local * Math.PI / 2),
      rotation: 0,
      scale: 1,
    };
  }
  const local = (progress - 0.56) / 0.44;
  const eased = 1 - Math.pow(1 - local, 2);
  return {
    x: 510 + 250 * eased,
    y: 227 - 92 * eased,
    rotation: -19 * Math.sin(local * Math.PI),
    scale: 1 - 0.2 * eased,
  };
}

function animationPhase(progress) {
  if (progress < 0.12) return { step: "安全闸逐项自检", speed: 0, moving: false };
  if (progress < 0.22) return { step: "车主单次授权已确认", speed: 0, moving: false };
  if (progress < 0.78) {
    const moveProgress = (progress - 0.22) / 0.56;
    const speed = Math.max(1.2, 4.8 * Math.sin(moveProgress * Math.PI));
    return { step: moveProgress < 0.58 ? "沿干燥路线低速迁移" : "进入坡道并持续监测", speed, moving: true, moveProgress };
  }
  if (progress < 0.91) return { step: "到达高位点并锁车", speed: 0, moving: false };
  return { step: "生成到达证据与事件留痕", speed: 0, moving: false };
}

function playMigrationDemo() {
  if (isAnimating) return;

  el.scenario.value = "rising";
  applyScenario("rising");
  el["owner-authorized"].checked = true;
  currentResult = runDecision();
  if (currentResult.decision !== DECISIONS.MIGRATE_NOW || !currentResult.authorizedToMove) return;

  const runId = ++animationRunId;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const duration = reducedMotion ? 350 : 6800;
  const startedAt = performance.now();
  isAnimating = true;
  vehicleAtHighPoint = false;
  setVehiclePose(155, 235);
  el.vehicle.classList.add("is-animating");
  el["migration-route"].classList.add("is-active");
  el["play-migration-button"].disabled = true;
  el["play-migration-button"].textContent = "迁移演示进行中…";
  el["scene-status-dot"].setAttribute("fill", "#54e4b8");

  const frame = (now) => {
    if (runId !== animationRunId) return;
    const progress = Math.min(1, (now - startedAt) / duration);
    const phase = animationPhase(progress);
    el.vehicle.classList.toggle("is-driving", phase.moving);
    if (phase.moving) {
      const pose = motionPose(phase.moveProgress);
      setVehiclePose(pose.x, pose.y, pose.rotation, pose.scale);
    } else if (progress >= 0.78) {
      setVehiclePose(760, 135, 0, 0.8);
    }
    setAnimationHud(phase.step, phase.speed, progress);
    el["scene-status-text"].textContent = phase.step;

    if (progress < 1) {
      animationFrameId = requestAnimationFrame(frame);
      return;
    }

    animationFrameId = null;
    isAnimating = false;
    vehicleAtHighPoint = true;
    el.vehicle.classList.remove("is-animating", "is-driving");
    el["migration-route"].classList.remove("is-active");
    setVehiclePose(760, 135, 0, 0.8);
    setAnimationHud("迁移完成 · 已停入高位安全点", 0, 1);
    el["scene-status-text"].textContent = "已到达高位安全点";
    el["play-migration-button"].disabled = false;
    el["play-migration-button"].innerHTML = '<span aria-hidden="true">↻</span> 重新播放迁移动画';
  };

  animationFrameId = requestAnimationFrame(frame);
}

el.scenario.addEventListener("change", () => applyScenario(el.scenario.value));
el["run-button"].addEventListener("click", () => {
  cancelMigrationAnimation({ resetVehicle: true });
  runDecision();
});
el["step-button"].addEventListener("click", stepOneMinute);
el["reset-button"].addEventListener("click", () => applyScenario(el.scenario.value));
el["play-migration-button"].addEventListener("click", playMigrationDemo);

for (const input of document.querySelectorAll("#control-form input")) {
  input.addEventListener("input", () => {
    cancelMigrationAnimation({ resetVehicle: true });
    updateOutputs();
    runDecision();
  });
}

applyScenario("normal");
