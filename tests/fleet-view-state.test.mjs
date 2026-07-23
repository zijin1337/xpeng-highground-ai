import test from "node:test";
import assert from "node:assert/strict";

import {
  advanceFleetGeneration,
  fleetResponseCanCommit,
  fleetEvidenceState,
} from "../src/fleet-view-state.js";

test("only the latest response for the current snapshot may update evidence", () => {
  assert.equal(fleetResponseCanCommit(4, 4, "snap-4", "snap-4"), true);
  assert.equal(fleetResponseCanCommit(3, 4, "snap-4", "snap-4"), false);
  assert.equal(fleetResponseCanCommit(4, 4, "snap-3", "snap-4"), false);
  assert.equal(advanceFleetGeneration(4), 5);
});

test("browser and api evidence can never share server identifiers", () => {
  assert.deepEqual(fleetEvidenceState("browser"), {
    label: "SIMULATED · 浏览器规划 · 不写 SQLite",
    runId: null,
    inputHash: null,
    planHash: null,
    stale: false,
  });
  const api = fleetEvidenceState("api", {
    run_id: "fleet-1",
    input_sha256: "a".repeat(64),
    plan_sha256: "b".repeat(64),
  });
  assert.equal(api.label, "SIMULATED · SQLite 证据");
  assert.equal(api.runId, "fleet-1");
});

test("offline api keeps readable evidence but marks it stale", () => {
  const state = fleetEvidenceState("api-stale", {
    run_id: "fleet-1",
    input_sha256: "a".repeat(64),
    plan_sha256: "b".repeat(64),
  });
  assert.equal(state.stale, true);
  assert.match(state.label, /已过期/);
});

test("api evidence rejects missing or malformed server identifiers", () => {
  assert.throws(() => fleetEvidenceState("api", {}), /run_id/);
  assert.throws(() => fleetEvidenceState("api", {
    run_id: "fleet-1",
    input_sha256: "A".repeat(64),
    plan_sha256: "b".repeat(64),
  }), /SHA-256/);
});
