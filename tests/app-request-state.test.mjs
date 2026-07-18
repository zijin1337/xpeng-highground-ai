import test from "node:test";
import assert from "node:assert/strict";

import {
  STALE_EVENT_MESSAGE,
  classifyApiFailure,
  commandRequestCanContinue,
  nextRequestGeneration,
  telemetryResponseState,
} from "../src/app-request-state.js";

test("遥测、授权和命令的 409 都归类为事件失效", () => {
  for (const operation of ["telemetry", "authorization", "command"]) {
    const failure = classifyApiFailure(operation, 409, "server detail");
    assert.equal(failure.kind, "stale-event");
    assert.equal(failure.invalidatesEvent, true);
    assert.match(failure.message, new RegExp(STALE_EVENT_MESSAGE));
  }
});

test("非 409 错误不会错误清除当前事件", () => {
  const failure = classifyApiFailure("command", 503, "temporarily unavailable");
  assert.equal(failure.kind, "http-error");
  assert.equal(failure.invalidatesEvent, false);
  assert.match(failure.message, /503.*temporarily unavailable/);
});

test("授权和命令的 404 会清除不存在的事件", () => {
  for (const operation of ["authorization", "command"]) {
    const failure = classifyApiFailure(operation, 404, "Event not found");
    assert.equal(failure.invalidatesEvent, true);
    assert.match(failure.message, /事件不存在.*事件已失效/);
  }

  assert.equal(
    classifyApiFailure("telemetry", 404, "Route not found").invalidatesEvent,
    false,
  );
});

test("只有最新代次的成功遥测响应可以写入事件 ID", () => {
  assert.deepEqual(telemetryResponseState(4, 5, true), {
    current: false,
    commitEvent: false,
  });
  assert.deepEqual(telemetryResponseState(5, 5, false), {
    current: true,
    commitEvent: false,
  });
  assert.deepEqual(telemetryResponseState(5, 5, true), {
    current: true,
    commitEvent: true,
  });
});

test("输入变化会推进代次并使在途遥测响应失效", () => {
  const requestGeneration = nextRequestGeneration(7);
  const generationAfterInput = nextRequestGeneration(requestGeneration);

  assert.deepEqual(
    telemetryResponseState(requestGeneration, generationAfterInput, true),
    { current: false, commitEvent: false },
  );
});

test("取消单次授权会使在途命令流程失效", () => {
  const requestGeneration = nextRequestGeneration(3);
  assert.equal(commandRequestCanContinue(
    requestGeneration,
    requestGeneration,
    "evt-current",
    "evt-current",
    true,
  ), true);

  const generationAfterCancel = nextRequestGeneration(requestGeneration);
  assert.equal(commandRequestCanContinue(
    requestGeneration,
    generationAfterCancel,
    "evt-current",
    "evt-current",
    false,
  ), false);
});
