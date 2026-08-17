import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../src/components/researchViewModel.ts", import.meta.url), "utf8");
const experimentPage = await readFile(new URL("../src/components/workspace/ExperimentPage.tsx", import.meta.url), "utf8");
const chart = await readFile(new URL("../src/components/workspace/MetricCharts.tsx", import.meta.url), "utf8");

test("research view model declares metric direction and makes scientific status depend on real audited results", () => {
  assert.match(source, /function metricDirection\(/);
  assert.match(source, /primaryMetric\?: \{ name: string; value: number; direction: "higher" \| "lower" \| "unknown" \}/);
  assert.match(source, /primary\.direction === "lower" \? -rawDelta : rawDelta/);
  assert.match(source, /classification: "scientific" \| "engineering"/);
  assert.match(source, /result\.is_real_experiment === true && text\(audit\.integrity_status\)\.toLowerCase\(\) === "passed"/);
  assert.match(source, /classification: isRealExperiment \? "scientific" : "engineering"/);
  assert.match(source, /function normalizeScientificFindings\(/);
  assert.match(source, /function presentScientificLimitation\(/);
  assert.match(source, /presentation-only: persisted scientific conclusions remain verbatim artifacts/);
  assert.match(source, /function evolutionLabel\(/);
});

test("experiment detail is a real drawer and chart tooltips expose metric semantics", () => {
  assert.match(experimentPage, /function Drawer\(/);
  assert.match(experimentPage, /setDrawerId\(id\)/);
  assert.match(experimentPage, /Result Artifact/);
  assert.match(experimentPage, /科学与处置记录/);
  assert.match(chart, /metricDirection = "unknown"/);
  assert.match(chart, /数值越高越好/);
  assert.match(chart, /数值越低越好/);
  assert.match(chart, /item\.tooltips\?\.\[index\]/);
});

test("timeline has one filtered entry point and performance excludes engineering remediation", () => {
  assert.match(source, /function displayExperimentTitle\(/);
  assert.match(source, /technicalName: string/);
  assert.match(experimentPage, /type TimelineFilter = "all" \| "scientific" \| "engineering"/);
  assert.match(experimentPage, /实验历程 <em>Execution Timeline<\/em>/);
  assert.match(experimentPage, /filter === "scientific"/);
  assert.doesNotMatch(experimentPage, /迭代贡献/);
  assert.match(source, /function scientificMetricSeries\(/);
  assert.match(source, /item\.classification === "scientific" && item\.status === "completed" && item\.isRealExperiment/);
  assert.match(experimentPage, /function PerformanceEvolution\(/);
  assert.match(experimentPage, /className="metric-selector"/);
  assert.match(experimentPage, /当前仅有 1 个有效实验结果/);
  assert.match(experimentPage, /Result Artifact \$\{row\.artifactId\}/);
  assert.match(source, /const currentExperiment = \[\.\.\.experiments\]\.reverse\(\)\.find\(\(item\) => item\.classification === "scientific"/);
});
