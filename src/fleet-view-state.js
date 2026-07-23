const SHA256_PATTERN = /^[0-9a-f]{64}$/;

export function advanceFleetGeneration(currentGeneration) {
  return currentGeneration + 1;
}

export function fleetResponseCanCommit(
  requestGeneration,
  latestGeneration,
  requestedSnapshotId,
  currentSnapshotId,
) {
  return requestGeneration === latestGeneration
    && Boolean(requestedSnapshotId)
    && requestedSnapshotId === currentSnapshotId;
}

function validatedApiEvidence(payload) {
  if (typeof payload?.run_id !== "string" || payload.run_id.length === 0) {
    throw new Error("API fleet evidence requires a nonempty run_id");
  }
  if (!SHA256_PATTERN.test(payload.input_sha256 ?? "")
    || !SHA256_PATTERN.test(payload.plan_sha256 ?? "")) {
    throw new Error("API fleet evidence requires lowercase SHA-256 hashes");
  }
  return {
    runId: payload.run_id,
    inputHash: payload.input_sha256,
    planHash: payload.plan_sha256,
  };
}

export function fleetEvidenceState(mode, payload = null) {
  if (mode === "browser") {
    return {
      label: "SIMULATED · 浏览器规划 · 不写 SQLite",
      runId: null,
      inputHash: null,
      planHash: null,
      stale: false,
    };
  }
  if (mode !== "api" && mode !== "api-stale") {
    throw new Error(`Unsupported fleet evidence mode: ${mode}`);
  }

  const evidence = validatedApiEvidence(payload);
  const stale = mode === "api-stale";
  return {
    label: stale
      ? "SIMULATED · SQLite 证据已过期 · 待提交新快照"
      : "SIMULATED · SQLite 证据",
    ...evidence,
    stale,
  };
}
