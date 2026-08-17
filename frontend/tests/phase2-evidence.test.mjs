import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("Phase 2 evidence shown in the experiment panel is Artifact-backed", async () => {
  const panel = await readFile(new URL("../src/components/ExperimentPanel.tsx", import.meta.url), "utf8");
  for (const artifact of ["dataset_profile", "baseline_profile", "fair_experiment_contract", "result_evidence"]) {
    assert.match(panel, new RegExp(`findLatestArtifactContent\\(artifacts, "${artifact}"\\)`));
  }
  for (const label of ["DatasetProfile", "复现状态", "Primary / Secondary", "Epoch / Seed", "Baseline vs Idea", "Mean / Std / Paired Delta", "当前路由动作"]) {
    assert.match(panel, new RegExp(label));
  }
  assert.doesNotMatch(panel, /demo-[123]|mock evidence/i);
});
