import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

test("orchestrator is the sole admission gate before the production pipeline", async () => {
  const app = await source("src/App.tsx");
  const orchestrator = await source("../backend/app/workflow/orchestrator.py");
  const start = app.slice(app.indexOf("async function startResearch"), app.indexOf("async function loadRun"));
  assert.doesNotMatch(start, /api\.preflightRun/);
  assert.match(start, /current = await api\.startPipeline\(current\.id\)/);
  assert.match(orchestrator, /if not getattr\(run, "research_constraints_artifact_id", ""\):[\s\S]*preflight_run/);
  assert.match(orchestrator, /if result\.get\("blocking"\):[\s\S]*status="preflight_failed"/);
});

test("passed preflight starts the production pipeline", async () => {
  const app = await source("src/App.tsx");
  const start = app.slice(app.indexOf("async function startResearch"), app.indexOf("async function loadRun"));
  assert.match(start, /current = await api\.startPipeline\(current\.id\)/);
});

test("provider failures are serialized by the backend and surfaced through the common UI error path", async () => {
  const app = await source("src/App.tsx");
  const engine = await source("../backend/app/workflow/engine.py");
  assert.match(engine, /failure_state_for\(exc\)/);
  assert.match(engine, /recoverable = state in \{[\s\S]*"RECOVERABLE_PROVIDER_ERROR"[\s\S]*"POLICY_INTEGRITY_REQUIRED"/);
  assert.match(engine, /"interrupted" if recoverable else "failed"/);
  assert.match(app, /setErrorMessage\(userFacingError\(error\)\)/);
});

test("legacy runs remain loadable without Phase 1 fields", async () => {
  const app = await source("src/App.tsx");
  const types = await source("src/api/types.ts");
  assert.match(types, /research_constraints\?:/);
  assert.match(types, /research_constraints_artifact_id\?:/);
  assert.match(app, /problem: loaded\.problem_input/);
  assert.match(app, /constraints: loaded\.constraints \|\| ""/);
});

test("old plan revision states can continue through the existing recovery pipeline", async () => {
  const app = await source("src/App.tsx");
  const types = await source("src/api/types.ts");
  const viewModel = await source("src/components/researchViewModel.ts");
  const research = await source("src/components/workspace/ResearchPage.tsx");
  const atlas = await source("src/components/ResearchAtlas.tsx");
  const workbench = await source("src/pages/WorkbenchPage.tsx");
  const orchestrator = await source("../backend/app/workflow/orchestrator.py");

  assert.match(types, /isPlanRevisionRecoveryStatus[\s\S]*NEEDS_PLAN_REVISION/);
  const governanceStatuses = types.slice(types.indexOf("GOVERNANCE_RECOVERY_STATUSES"), types.indexOf("export function isGovernanceRecoveryStatus"));
  assert.match(governanceStatuses, /POLICY_INTEGRITY_REQUIRED/);
  assert.doesNotMatch(governanceStatuses, /NEEDS_PLAN_REVISION/);
  const continuePipeline = app.slice(app.indexOf("async function continuePipeline"), app.indexOf("async function addUserHypothesis"));
  assert.match(continuePipeline, /api\.startPipeline\(run\.id\)/);
  assert.doesNotMatch(continuePipeline, /NEEDS_PLAN_REVISION/);
  assert.match(workbench, /run\.status === "hypothesis_revision_required"[\s\S]*onRerunFrom[\s\S]*onContinuePipeline/);
  assert.match(viewModel, /NEEDS_PLAN_REVISION[\s\S]*needs_plan_revision/);
  assert.match(viewModel, /POLICY_INTEGRITY_REQUIRED[\s\S]*policy_integrity_required/);
  assert.match(research, /planRecoveryAvailable/);
  assert.match(research, /继续研究（重新审查计划）/);
  assert.match(research, /重新裁决/);
  assert.match(research, /研究治理状态完整性异常/);
  assert.match(research, /自动执行已停止/);
  assert.match(research, /disabled=\{!canStart\}/);
  assert.match(atlas, /disabled=\{!canRun \|\| Boolean\(governanceHold\)\}/);
  assert.match(atlas, /继续研究（重新审查计划）/);
  assert.match(atlas, /等待操作员恢复/);
  assert.match(orchestrator, /run\.status == "NEEDS_PLAN_REVISION"[\s\S]*recover_plan_review_for_continue/);
});
