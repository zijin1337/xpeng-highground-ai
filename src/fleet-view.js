import { planFleet } from "./fleet-planner.js";
import { loadFleetScenario } from "./fleet-scenario.js";
import {
  advanceFleetGeneration,
  fleetEvidenceState,
  fleetResponseCanCommit,
} from "./fleet-view-state.js";

const STATUS_LABELS = Object.freeze({
  NOT_REQUIRED: "无需调度",
  PREPARE_ONLY: "准备观察",
  VERIFY_ONLY: "等待复核",
  SCHEDULED_SHADOW: "影子调度",
  NO_CAPACITY: "容量不足",
  WINDOW_CLOSED: "窗口关闭",
  SITE_UNAVAILABLE: "场端不可用",
  DENIED: "安全禁行",
});

const STATUS_TONES = Object.freeze({
  NOT_REQUIRED: "safe",
  PREPARE_ONLY: "warning",
  VERIFY_ONLY: "warning",
  SCHEDULED_SHADOW: "safe",
  NO_CAPACITY: "danger",
  WINDOW_CLOSED: "danger",
  SITE_UNAVAILABLE: "danger",
  DENIED: "danger",
});

const ids = [
  "view-fleet-tab",
  "view-single-tab",
  "fleet-shadow-view",
  "single-car-view",
  "fleet-source-label",
  "fleet-stage-label",
  "fleet-vehicle-count",
  "fleet-scheduled-count",
  "fleet-verify-count",
  "fleet-denied-count",
  "fleet-capacity-count",
  "fleet-map",
  "fleet-queue-body",
  "fleet-timeline",
  "fleet-evidence-body",
  "fleet-filter-clear",
  "fleet-next-button",
  "fleet-reset-button",
  "fleet-api-key-input",
  "fleet-api-connect-button",
  "fleet-api-status",
  "fleet-run-id",
  "fleet-input-hash",
  "fleet-plan-hash",
];

const el = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));
for (const [id, element] of Object.entries(el)) {
  if (!element) throw new Error(`Fleet view element is missing: ${id}`);
}

const vehicleMarkers = [...document.querySelectorAll(".fleet-vehicle-marker[data-vehicle-id]")];
const routePaths = [...document.querySelectorAll(".fleet-routes [data-route-vehicle]")];
const waterZone = document.querySelector(".fleet-water-zone");

let scenario = null;
let currentStageIndex = 0;
let displayedStageIndex = 0;
let currentPlan = null;
let selectedVehicleId = null;
let apiMode = false;
let apiKey = "";
let fleetRequestGeneration = 0;
let connectionGeneration = 0;
let currentSubmissionSnapshotId = null;
let lastSubmissionMilliseconds = 0;
let lastApiPlan = null;
let lastApiEvidence = null;
let lastApiStageIndex = null;

function createElement(tag, { className = "", text = "" } = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

function statusNode(status) {
  const container = createElement("span", {
    className: "fleet-status",
    text: STATUS_LABELS[status] ?? status,
  });
  container.dataset.tone = STATUS_TONES[status] ?? "warning";
  const symbol = createElement("span", { className: "fleet-status-symbol" });
  symbol.setAttribute("aria-hidden", "true");
  container.prepend(symbol);
  return container;
}

function formatWindow(value) {
  if (value === null || value === undefined) return "充足";
  if (!Number.isFinite(Number(value))) return "--";
  return `${Math.max(0, Number(value)).toFixed(1)} min`;
}

function formatBatch(vehicle) {
  if (vehicle.batch_index === null) return "—";
  return `第 ${vehicle.batch_index} 批`;
}

function formatSafePoint(vehicle) {
  return vehicle.safe_point_id ?? "未分配";
}

function setFleetApiStatus(state, text) {
  const container = el["fleet-api-status"].closest(".fleet-api-state");
  container.dataset.state = state;
  el["fleet-api-status"].textContent = text;
}

function setFleetConnectionControls({ pending = false } = {}) {
  const panel = el["fleet-api-key-input"].closest(".fleet-api-panel");
  panel.setAttribute("aria-busy", String(pending));
  el["fleet-api-key-input"].disabled = pending || apiMode;
  el["fleet-api-connect-button"].disabled = pending;
  el["fleet-api-connect-button"].textContent = pending
    ? "正在连接…"
    : apiMode
      ? "断开车队证据连接"
      : "连接 SQLite 证据";
}

function abbreviatedHash(hash) {
  return `${hash.slice(0, 12)}…${hash.slice(-8)}`;
}

function setAuditValue(element, value, { abbreviate = false } = {}) {
  if (!value) {
    element.textContent = "—";
    element.removeAttribute("title");
    return;
  }
  element.textContent = abbreviate ? abbreviatedHash(value) : value;
  if (abbreviate) element.title = value;
  else element.removeAttribute("title");
}

function renderEvidenceSource(state) {
  el["fleet-source-label"].textContent = state.label;
  el["fleet-source-label"].dataset.stale = String(state.stale);
  setAuditValue(el["fleet-run-id"], state.runId);
  setAuditValue(el["fleet-input-hash"], state.inputHash, { abbreviate: true });
  setAuditValue(el["fleet-plan-hash"], state.planHash, { abbreviate: true });
}

function renderSummary(plan) {
  el["fleet-vehicle-count"].textContent = plan.summary.vehicle_count;
  el["fleet-scheduled-count"].textContent = plan.summary.scheduled_count;
  el["fleet-verify-count"].textContent = plan.summary.verify_count;
  el["fleet-denied-count"].textContent = plan.summary.denied_count;
  el["fleet-capacity-count"].textContent = plan.summary.remaining_capacity;
}

function renderMap(plan, stageIndex) {
  const byId = new Map(plan.vehicles.map((vehicle) => [vehicle.vehicle_id, vehicle]));
  for (const marker of vehicleMarkers) {
    const vehicleId = marker.dataset.vehicleId;
    const vehicle = byId.get(vehicleId);
    const status = vehicle?.allocation_status ?? "NOT_REQUIRED";
    const selected = selectedVehicleId === vehicleId;
    marker.dataset.status = status;
    marker.setAttribute("aria-selected", String(selected));
    marker.setAttribute("aria-label", `${vehicleId} · ${STATUS_LABELS[status] ?? status}`);
    marker.classList.toggle("is-dimmed", Boolean(selectedVehicleId) && !selected);
  }
  for (const route of routePaths) {
    const vehicleId = route.dataset.routeVehicle;
    const vehicle = byId.get(vehicleId);
    const status = vehicle?.allocation_status ?? "NOT_REQUIRED";
    route.dataset.status = status;
    route.dataset.active = String(selectedVehicleId
      ? selectedVehicleId === vehicleId
      : status === "SCHEDULED_SHADOW");
  }

  const stage = scenario?.stages?.[stageIndex];
  if (stage && waterZone) {
    const levels = stage.snapshot.vehicles.map(
      (vehicle) => vehicle.telemetry.environment.water_level_cm,
    );
    const averageLevel = levels.reduce((total, level) => total + level, 0) / levels.length;
    const translate = Math.max(0, Math.min(34, (14 - averageLevel) * 3));
    waterZone.style.transform = `translateY(${translate}px)`;
    waterZone.style.opacity = String(Math.max(.42, Math.min(1, averageLevel / 14)));
    el["fleet-map"].dataset.stageId = stage.stage_id;
  }
}

function renderQueue(plan) {
  const rows = plan.vehicles.map((vehicle) => {
    const row = document.createElement("tr");
    row.dataset.vehicleId = vehicle.vehicle_id;
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-label", `筛选 ${vehicle.vehicle_id} 的逐车证据`);
    row.setAttribute("aria-selected", String(selectedVehicleId === vehicle.vehicle_id));
    row.classList.toggle(
      "is-dimmed",
      Boolean(selectedVehicleId) && selectedVehicleId !== vehicle.vehicle_id,
    );

    row.insertCell().textContent = vehicle.rank ?? "—";

    const vehicleCell = row.insertCell();
    vehicleCell.append(createElement("strong", { text: vehicle.vehicle_id }));
    vehicleCell.append(createElement("small", {
      text: `窗口 ${formatWindow(vehicle.base_decision.timing.latest_safe_start_min)}`,
    }));

    const allocationCell = row.insertCell();
    allocationCell.append(createElement("strong", { text: formatBatch(vehicle) }));
    allocationCell.append(createElement("small", { text: formatSafePoint(vehicle) }));

    row.insertCell().append(statusNode(vehicle.allocation_status));
    return row;
  });
  el["fleet-queue-body"].replaceChildren(...rows);
}

function permissionText(permission) {
  if (permission === "SHADOW_ONLY") return "SHADOW_ONLY · 不执行";
  if (permission === "DENIED") return "DENIED · 保持原位";
  return "NONE · 无动作";
}

function renderVehicleEvidence(plan) {
  const visible = selectedVehicleId
    ? plan.vehicles.filter((vehicle) => vehicle.vehicle_id === selectedVehicleId)
    : plan.vehicles;
  const rows = visible.map((vehicle) => {
    const row = document.createElement("tr");
    row.insertCell().append(createElement("strong", { text: vehicle.vehicle_id }));
    row.insertCell().textContent = `${vehicle.base_decision.label} · ${vehicle.base_decision.decision}`;
    row.insertCell().append(statusNode(vehicle.allocation_status));
    row.insertCell().textContent = formatWindow(
      vehicle.base_decision.timing.latest_safe_start_min,
    );
    row.insertCell().textContent = vehicle.reason;
    row.insertCell().append(createElement("code", {
      text: permissionText(vehicle.action_permission),
    }));
    return row;
  });
  el["fleet-evidence-body"].replaceChildren(...rows);
  el["fleet-filter-clear"].hidden = !selectedVehicleId;
}

function renderPlan(plan, stageIndex) {
  currentPlan = plan;
  displayedStageIndex = stageIndex;
  renderSummary(plan);
  renderMap(plan, stageIndex);
  renderQueue(plan);
  renderVehicleEvidence(plan);
}

function renderStageControls() {
  if (!scenario) return;
  const stage = scenario.stages[currentStageIndex];
  el["fleet-stage-label"].textContent = `阶段 ${String(currentStageIndex + 1).padStart(2, "0")} / ${String(scenario.stages.length).padStart(2, "0")} · ${stage.label}`;
  el["fleet-stage-label"].title = stage.stage_id;
  el["fleet-next-button"].disabled = currentStageIndex >= scenario.stages.length - 1;

  const steps = scenario.stages.map((item, index) => {
    const listItem = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.stageIndex = index;
    button.setAttribute("aria-label", `切换到阶段 ${index + 1}：${item.label}`);
    if (index === currentStageIndex) button.setAttribute("aria-current", "step");
    button.append(createElement("span", { text: `0${index + 1}`.slice(-2) }));
    button.append(createElement("strong", { text: item.label }));
    listItem.append(button);
    return listItem;
  });
  el["fleet-timeline"].replaceChildren(...steps);
}

function renderBrowserStage() {
  const stage = scenario.stages[currentStageIndex];
  const plan = planFleet(stage.snapshot, { now: stage.snapshot.captured_at });
  renderPlan(plan, currentStageIndex);
  renderEvidenceSource(fleetEvidenceState("browser"));
  setFleetApiStatus("", "浏览器规划模式");
}

function markLastApiEvidenceStale() {
  if (!lastApiPlan || !lastApiEvidence || lastApiStageIndex === null) return false;
  renderPlan(lastApiPlan, lastApiStageIndex);
  renderEvidenceSource(fleetEvidenceState("api-stale", lastApiEvidence));
  return true;
}

function nextSubmissionTimestamp() {
  const milliseconds = Math.max(Date.now(), lastSubmissionMilliseconds + 1);
  lastSubmissionMilliseconds = milliseconds;
  return milliseconds;
}

function buildSubmissionSnapshot(stage) {
  const milliseconds = nextSubmissionTimestamp();
  const capturedAt = new Date(milliseconds).toISOString();
  const submissionToken = `${stage.stage_id}-${milliseconds}`;
  const snapshot = structuredClone(stage.snapshot);
  snapshot.snapshot_id = `fleet-web-${submissionToken}`;
  snapshot.captured_at = capturedAt;
  snapshot.site.observed_at = capturedAt;
  for (const vehicle of snapshot.vehicles) {
    vehicle.telemetry.message_id = `${vehicle.telemetry.message_id}-${submissionToken}`;
    vehicle.telemetry.captured_at = capturedAt;
  }
  return snapshot;
}

function firstServerDetail(payload) {
  const detail = payload?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    const path = Array.isArray(first?.loc) ? first.loc.join(".") : "snapshot";
    return `${path}: ${first?.msg ?? "invalid value"}`;
  }
  return "服务器未返回详细信息";
}

async function responseJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function invalidateFleetRequests() {
  fleetRequestGeneration = advanceFleetGeneration(fleetRequestGeneration);
  currentSubmissionSnapshotId = null;
}

function clearFleetAuthentication() {
  apiMode = false;
  apiKey = "";
  sessionStorage.removeItem("highground-api-key");
  el["fleet-api-key-input"].value = "";
  invalidateFleetRequests();
  setFleetConnectionControls();
}

function showSubmissionFailure(message, { clearAuthentication = false } = {}) {
  if (clearAuthentication) clearFleetAuthentication();
  markLastApiEvidenceStale();
  setFleetApiStatus("error", message);
}

async function submitFleetSnapshot(submission, requestGeneration, stageIndex) {
  const requestedSnapshotId = submission.snapshot_id;
  const requestApiKey = apiKey;
  const body = JSON.stringify(submission);

  for (let attempt = 0; attempt < 2; attempt += 1) {
    let response;
    try {
      response = await fetch("/api/v1/fleet/shadow-runs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": requestApiKey,
        },
        body,
      });
    } catch (error) {
      if (!fleetResponseCanCommit(
        requestGeneration,
        fleetRequestGeneration,
        requestedSnapshotId,
        currentSubmissionSnapshotId,
      )) return;
      if (attempt === 0 && apiMode) {
        setFleetApiStatus("", "网络波动 · 正在重试同一快照…");
        await new Promise((resolve) => setTimeout(resolve, 450));
        if (!fleetResponseCanCommit(
          requestGeneration,
          fleetRequestGeneration,
          requestedSnapshotId,
          currentSubmissionSnapshotId,
        )) return;
        continue;
      }
      showSubmissionFailure(`车队快照提交失败 · ${error.message}`);
      return;
    }

    if (!fleetResponseCanCommit(
      requestGeneration,
      fleetRequestGeneration,
      requestedSnapshotId,
      currentSubmissionSnapshotId,
    )) return;

    const payload = await responseJson(response);
    if (!response.ok) {
      const detail = firstServerDetail(payload);
      if (response.status === 401) {
        showSubmissionFailure("车队鉴权失败 · 已清除 X-API-Key", {
          clearAuthentication: true,
        });
      } else if (response.status === 409) {
        showSubmissionFailure(`车队快照冲突 · ${detail}`);
      } else if (response.status === 422) {
        showSubmissionFailure(`车队快照校验失败 · ${detail}`);
      } else if (response.status >= 500) {
        showSubmissionFailure("车队证据服务暂不可用");
      } else {
        showSubmissionFailure(`车队 API HTTP ${response.status} · ${detail}`);
      }
      return;
    }

    if (!payload || payload.snapshot_id !== requestedSnapshotId) {
      showSubmissionFailure("车队 API 返回了不匹配的快照证据");
      return;
    }
    if (!fleetResponseCanCommit(
      requestGeneration,
      fleetRequestGeneration,
      requestedSnapshotId,
      currentSubmissionSnapshotId,
    )) return;

    let evidence;
    try {
      evidence = fleetEvidenceState("api", payload);
      renderPlan(payload, stageIndex);
    } catch (error) {
      showSubmissionFailure(`车队 API 证据无效 · ${error.message}`);
      return;
    }
    lastApiPlan = payload;
    lastApiEvidence = payload;
    lastApiStageIndex = stageIndex;
    renderEvidenceSource(evidence);
    setFleetApiStatus("connected", response.status === 200
      ? "SQLite 已返回幂等快照证据"
      : "快照与逐车计划已写入 SQLite");
    return;
  }
}

function submitCurrentStage() {
  if (!apiMode || !scenario) return;
  fleetRequestGeneration = advanceFleetGeneration(fleetRequestGeneration);
  const requestGeneration = fleetRequestGeneration;
  const stageIndex = currentStageIndex;
  const submission = buildSubmissionSnapshot(scenario.stages[stageIndex]);
  currentSubmissionSnapshotId = submission.snapshot_id;
  markLastApiEvidenceStale();
  setFleetApiStatus("connected", "API 已连接 · 正在写入车队快照…");
  void submitFleetSnapshot(submission, requestGeneration, stageIndex);
}

function selectStage(index) {
  if (!scenario || index < 0 || index >= scenario.stages.length) return;
  currentStageIndex = index;
  selectedVehicleId = null;
  renderStageControls();
  if (apiMode) {
    if (!markLastApiEvidenceStale()) renderBrowserStage();
    submitCurrentStage();
  } else {
    renderBrowserStage();
  }
}

function toggleVehicleSelection(vehicleId) {
  selectedVehicleId = selectedVehicleId === vehicleId ? null : vehicleId;
  if (currentPlan) renderPlan(currentPlan, displayedStageIndex);
}

function switchToBrowserMode() {
  apiMode = false;
  apiKey = "";
  invalidateFleetRequests();
  setFleetConnectionControls();
  renderBrowserStage();
}

function connectionCanCommit(generation, candidate) {
  return generation === connectionGeneration
    && Boolean(candidate)
    && candidate === el["fleet-api-key-input"].value.trim();
}

async function connectFleetApi() {
  if (apiMode) {
    switchToBrowserMode();
    return;
  }
  connectionGeneration = advanceFleetGeneration(connectionGeneration);
  const requestGeneration = connectionGeneration;
  const candidate = el["fleet-api-key-input"].value.trim();
  if (!candidate) {
    setFleetApiStatus("error", "请输入 X-API-Key");
    return;
  }

  setFleetConnectionControls({ pending: true });
  setFleetApiStatus("", "正在验证车队证据连接…");
  try {
    const response = await fetch("/api/v1/session", {
      headers: { "X-API-Key": candidate },
    });
    if (!connectionCanCommit(requestGeneration, candidate)) return;
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const session = await response.json();
    if (!connectionCanCommit(requestGeneration, candidate)) return;
    apiMode = true;
    apiKey = candidate;
    sessionStorage.setItem("highground-api-key", candidate);
    setFleetConnectionControls();
    const storageLabel = session.storage === "sqlite" ? "SQLite" : session.storage;
    setFleetApiStatus("connected", `API 已连接 · ${storageLabel}`);
    submitCurrentStage();
  } catch (error) {
    if (!connectionCanCommit(requestGeneration, candidate)) return;
    setFleetApiStatus("error", `车队后端连接失败 · ${error.message}`);
  } finally {
    if (requestGeneration === connectionGeneration) setFleetConnectionControls();
  }
}

function adoptSharedApiSession() {
  const sharedKey = sessionStorage.getItem("highground-api-key")?.trim();
  if (!sharedKey) return;
  connectionGeneration = advanceFleetGeneration(connectionGeneration);
  apiMode = true;
  apiKey = sharedKey;
  el["fleet-api-key-input"].value = sharedKey;
  setFleetConnectionControls();
  setFleetApiStatus("connected", "已复用单车控制台 API 会话");
  submitCurrentStage();
}

function invalidateSharedApiSession() {
  if (!apiMode && !lastApiPlan) return;
  connectionGeneration = advanceFleetGeneration(connectionGeneration);
  clearFleetAuthentication();
  markLastApiEvidenceStale();
  setFleetApiStatus("error", "共享 API 会话已断开");
}

function activateView(view) {
  const fleetActive = view === "fleet";
  el["fleet-shadow-view"].hidden = !fleetActive;
  el["single-car-view"].hidden = fleetActive;
  el["view-fleet-tab"].setAttribute("aria-selected", String(fleetActive));
  el["view-single-tab"].setAttribute("aria-selected", String(!fleetActive));
  el["view-fleet-tab"].tabIndex = fleetActive ? 0 : -1;
  el["view-single-tab"].tabIndex = fleetActive ? -1 : 0;
}

function setupInteractions() {
  el["view-fleet-tab"].addEventListener("click", () => activateView("fleet"));
  el["view-single-tab"].addEventListener("click", () => activateView("single"));
  for (const tab of [el["view-fleet-tab"], el["view-single-tab"]]) {
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const showFleet = event.key === "ArrowLeft" || event.key === "Home";
      activateView(showFleet ? "fleet" : "single");
      (showFleet ? el["view-fleet-tab"] : el["view-single-tab"]).focus();
    });
  }

  el["fleet-timeline"].addEventListener("click", (event) => {
    const button = event.target.closest("button[data-stage-index]");
    if (button) selectStage(Number(button.dataset.stageIndex));
  });
  el["fleet-next-button"].addEventListener("click", () => {
    if (!scenario) return;
    selectStage(Math.min(currentStageIndex + 1, scenario.stages.length - 1));
  });
  el["fleet-reset-button"].addEventListener("click", () => selectStage(0));
  el["fleet-filter-clear"].addEventListener("click", () => toggleVehicleSelection(selectedVehicleId));
  el["fleet-api-connect-button"].addEventListener("click", connectFleetApi);

  el["fleet-queue-body"].addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-vehicle-id]");
    if (row) toggleVehicleSelection(row.dataset.vehicleId);
  });
  el["fleet-queue-body"].addEventListener("keydown", (event) => {
    const row = event.target.closest("tr[data-vehicle-id]");
    if (!row || !["Enter", " "].includes(event.key)) return;
    event.preventDefault();
    toggleVehicleSelection(row.dataset.vehicleId);
  });

  for (const marker of vehicleMarkers) {
    marker.addEventListener("click", () => toggleVehicleSelection(marker.dataset.vehicleId));
    marker.addEventListener("keydown", (event) => {
      if (!["Enter", " "].includes(event.key)) return;
      event.preventDefault();
      toggleVehicleSelection(marker.dataset.vehicleId);
    });
  }

  window.addEventListener("highground:api-session", (event) => {
    if (event.detail?.connected) adoptSharedApiSession();
    else invalidateSharedApiSession();
  });
}

async function initializeFleetView() {
  setupInteractions();
  const savedApiKey = sessionStorage.getItem("highground-api-key");
  if (savedApiKey) el["fleet-api-key-input"].value = savedApiKey;
  setFleetConnectionControls();

  try {
    scenario = await loadFleetScenario();
    if (scenario.planner_version !== "fleet-shadow-v1") {
      throw new Error("车队规划器版本不匹配");
    }
    currentStageIndex = Math.min(
      Math.max(0, scenario.default_stage_index),
      scenario.stages.length - 1,
    );
    renderStageControls();
    if (apiMode) {
      renderBrowserStage();
      submitCurrentStage();
    } else {
      renderBrowserStage();
    }
  } catch (error) {
    el["fleet-source-label"].textContent = "SIMULATED · 场景不可用";
    el["fleet-source-label"].dataset.stale = "true";
    el["fleet-stage-label"].textContent = error.message;
    setFleetApiStatus("error", "车队演练加载失败");
    el["fleet-next-button"].disabled = true;
    el["fleet-reset-button"].disabled = true;
  }
}

void initializeFleetView();
