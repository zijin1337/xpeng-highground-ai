export const STALE_EVENT_MESSAGE = "事件已失效，请重新提交新遥测";

const API_OPERATION_LABELS = Object.freeze({
  telemetry: "遥测",
  authorization: "授权",
  command: "命令",
});

export function classifyApiFailure(operation, status, detail = "") {
  const label = API_OPERATION_LABELS[operation] ?? "API";
  const normalizedDetail = String(detail).trim().slice(0, 120);
  const eventMissing = status === 404
    && (operation === "authorization" || operation === "command");
  if (status === 409 || eventMissing) {
    return {
      kind: "stale-event",
      status,
      invalidatesEvent: true,
      invalidatesSession: false,
      message: `${label}${eventMissing ? "事件不存在" : "冲突"} · ${STALE_EVENT_MESSAGE}`,
    };
  }

  const invalidApiKey = status === 401 && (
    operation === "telemetry"
    || operation === "authorization"
    || (operation === "command" && normalizedDetail.toLowerCase().includes("x-api-key"))
  );
  if (invalidApiKey) {
    return {
      kind: "authentication-error",
      status,
      invalidatesEvent: true,
      invalidatesSession: true,
      message: `${label}鉴权失败 · X-API-Key 已失效，请重新连接`,
    };
  }

  return {
    kind: "http-error",
    status,
    invalidatesEvent: false,
    invalidatesSession: false,
    message: `${label} HTTP ${status}${normalizedDetail ? `: ${normalizedDetail}` : ""}`,
  };
}

export function nextRequestGeneration(currentGeneration) {
  return currentGeneration + 1;
}

export function connectionResponseCanCommit(
  requestGeneration,
  latestGeneration,
  requestedApiKey,
  currentApiKey,
) {
  return requestGeneration === latestGeneration
    && Boolean(requestedApiKey)
    && requestedApiKey === String(currentApiKey ?? "").trim();
}

export function commandRequestCanContinue(
  requestGeneration,
  latestGeneration,
  expectedEventId,
  currentEventId,
  ownerAuthorized,
) {
  return requestGeneration === latestGeneration
    && Boolean(expectedEventId)
    && expectedEventId === currentEventId
    && Boolean(ownerAuthorized);
}

export function isRecordOnlyCommandEvidence(command) {
  return Boolean(
    command
    && typeof command.command_id === "string"
    && command.command_id.length > 0
    && command.status === "RECORDED_NOT_SENT"
    && command.actuator_mode === "record-only"
  );
}

export function recordedCommandPermissionText(command) {
  return isRecordOnlyCommandEvidence(command)
    ? "状态：命令已留痕 · 未向车辆发送"
    : null;
}

export function telemetryResponseState(requestGeneration, latestGeneration, responseOk) {
  const current = requestGeneration === latestGeneration;
  return {
    current,
    commitEvent: current && Boolean(responseOk),
  };
}
