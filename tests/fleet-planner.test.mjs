import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import { planFleet, projectFleetPlan } from "../src/fleet-planner.js";

const scenario = JSON.parse(await readFile(
  new URL("../demo/scenarios/fleet-rainstorm-v1.json", import.meta.url),
  "utf8",
));

test("JavaScript planner matches all six shared fleet stages", () => {
  assert.equal(scenario.stages.length, 6);
  for (const stage of scenario.stages) {
    const plan = planFleet(stage.snapshot, { now: stage.snapshot.captured_at });
    assert.deepEqual(projectFleetPlan(plan), stage.expect, stage.stage_id);
    assert.ok(plan.vehicles.every((vehicle) => vehicle.authorized_to_move === false));
  }
});

test("browser plans omit server-only evidence fields", () => {
  const plan = planFleet(scenario.stages[0].snapshot);
  assert.equal("run_id" in plan, false);
  assert.equal("input_sha256" in plan, false);
  assert.equal("plan_sha256" in plan, false);
});

test("planner rejects snapshots outside the fleet contract", () => {
  const sourceMode = structuredClone(scenario.stages[0].snapshot);
  sourceMode.source_mode = "LIVE_CONTROL";
  assert.throws(() => planFleet(sourceMode), /source_mode/);

  const duplicate = structuredClone(scenario.stages[0].snapshot);
  duplicate.vehicles[1].telemetry.vehicle_id = duplicate.vehicles[0].telemetry.vehicle_id;
  assert.throws(() => planFleet(duplicate), /vehicle_id/);

  const siteMismatch = structuredClone(scenario.stages[0].snapshot);
  siteMismatch.vehicles[0].telemetry.site_id = "other-site";
  assert.throws(() => planFleet(siteMismatch), /site_id/);
});
