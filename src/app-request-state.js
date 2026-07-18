export const STALE_EVENT_MESSAGE = "事件已失效，请重新提交新遥测";

const API_OPERATION_LABELS = Object.freeze({
  telemetry: "遥测",
  authorization: "授权",
  command: "命令",
});

export function classifyApiFailure(operation, status, detail = "") {
  const label = API_OPERATION_LABELS[operation] ?? "API";
  const eventMissing = status === 404
    && (operation === "authorization" || operation === "command");
  if (status === 409 || eventMissing) {
    return {
      kind: "stale-event",
      status,
      invalidatesEvent: true,
      message: `${label}${eventMissing ? "事件不存在" : "冲突"} · ${STALE_EVENT_MESSAGE}`,
    };
  }

  const normalizedDetail = String(detail).trim().slice(0, 120);
  return {
    kind: "http-error",
    status,
    invalidatesEvent: false,
    message: `${label} HTTP ${status}${normalizedDetail ? `: ${normalizedDetail}` : ""}`,
  };
}

export function nextRequestGeneration(currentGeneration) {
  return currentGeneration + 1;
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

export function telemetryResponseState(requestGeneration, latestGeneration, responseOk) {
  const current = requestGeneration === latestGeneration;
  return {
    current,
    commitEvent: current && Boolean(responseOk),
  };
}
