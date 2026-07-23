import { readFile } from "node:fs/promises";

import { planFleet, projectFleetPlan } from "../src/fleet-planner.js";

const scenarioPath = process.argv[2];
if (!scenarioPath) throw new Error("scenario path is required");
const scenario = JSON.parse(await readFile(scenarioPath, "utf8"));
const output = scenario.stages.map((stage) => ({
  stage_id: stage.stage_id,
  projection: projectFleetPlan(planFleet(stage.snapshot, { now: stage.snapshot.captured_at })),
}));
process.stdout.write(JSON.stringify(output));
