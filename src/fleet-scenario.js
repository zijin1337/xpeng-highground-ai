export const FLEET_SCENARIO_URL = "./demo/scenarios/fleet-rainstorm-v1.json";

export async function loadFleetScenario(fetchImpl = fetch) {
  const response = await fetchImpl(FLEET_SCENARIO_URL, { cache: "no-store" });
  if (!response.ok) throw new Error(`车队场景加载失败 · HTTP ${response.status}`);
  const scenario = await response.json();
  if (scenario.schema_version !== 1 || scenario.stages?.length !== 6) {
    throw new Error("车队场景合同无效");
  }
  return scenario;
}
