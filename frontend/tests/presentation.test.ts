import assert from "node:assert/strict";
import test from "node:test";
import {
  buildExperimentMetricRows,
  buildExperimentMetricComparisons,
  formatMetricValue,
  findLatestArtifact,
  findLatestArtifactContent,
  findMetricSeries,
  getMetricSeriesBounds,
  hasSuccessfulExperimentResult,
  latestExperimentDiagnosis,
  latestExperimentResultFailure,
  summarizeExperimentFailure,
  formatAuthors,
  formatReferenceIdentifier,
  formatReferenceTitle,
  summarizeResearchProblem,
} from "../src/utils/presentation.ts";

const artifact = (type: string, version: number, content: Record<string, unknown>) => ({
  id: `${type}-${version}`,
  run_id: "run-1",
  type,
  version,
  title: type,
  content,
  source_step: type,
  locked: false,
  created_by: "test",
  created_at: `2026-07-12T00:00:0${version}Z`,
});

test("research summary keeps exactly eight Unicode characters", () => {
  assert.deepEqual(summarizeResearchProblem("研究可信训练方案"), {
    text: "研究可信训练方案",
    fullText: "研究可信训练方案",
  });
});

test("research summary truncates after eight Unicode characters", () => {
  assert.deepEqual(summarizeResearchProblem("研究可信训练方案优化"), {
    text: "研究可信训练方案…",
    fullText: "研究可信训练方案优化",
  });
  assert.equal(summarizeResearchProblem("😀12345678").text, "😀1234567…");
});

test("research summary supplies the approved empty fallback", () => {
  assert.deepEqual(summarizeResearchProblem("   "), {
    text: "未填写研究问题",
    fullText: "未填写研究问题",
  });
});

test("reference presentation formats title and authors", () => {
  assert.equal(formatReferenceTitle("  Paper title  "), "Paper title");
  assert.equal(formatReferenceTitle(null), "未命名论文");
  assert.equal(formatAuthors(["Ada", "Grace"]), "Ada, Grace");
  assert.equal(formatAuthors([]), "作者未知");
});

test("reference identifier prefers DOI and falls back to journal", () => {
  assert.equal(
    formatReferenceIdentifier({ identifiers: { doi: " 10.1000/test " }, source: "JMLR" }),
    "10.1000/test",
  );
  assert.equal(formatReferenceIdentifier({ identifiers: {}, source: " JMLR " }), "JMLR");
  assert.equal(formatReferenceIdentifier({}), "期刊未知");
});

test("latest artifact helpers search from newest to oldest", () => {
  const artifacts = [
    artifact("plan", 1, { objective: "old" }),
    artifact("hypothesis", 1, { claim: "claim" }),
    artifact("plan", 2, { objective: "new" }),
  ];

  assert.equal(findLatestArtifact(artifacts, "plan")?.version, 2);
  assert.deepEqual(findLatestArtifactContent(artifacts, "plan"), { objective: "new" });
  assert.equal(findLatestArtifact(artifacts, "missing"), undefined);
  assert.equal(findLatestArtifactContent(artifacts, "missing"), undefined);
});

test("failed experiment results are not treated as successful completion", () => {
  const artifacts = [
    artifact("experiment_result", 1, {
      provider: "local_gpu",
      status: "failed",
      verdict: "failed",
      error: "LOCAL_EXPERIMENT_DEPENDENCY_MISSING",
      attempts: [{ attempt: 1, status: "failed", error_code: "LOCAL_EXPERIMENT_DEPENDENCY_MISSING" }],
      metrics: {},
    }),
  ];

  assert.equal(hasSuccessfulExperimentResult(artifacts), false);
  assert.equal(latestExperimentResultFailure(artifacts), "实验失败：缺少依赖");
});

test("diagnostic result supplies the user-facing failure reason", () => {
  const artifacts = [
    artifact("experiment_diagnosis", 1, {
      category: "dataset",
      user_message: "数据集下载损坏，已隔离缓存并准备重试。",
      repair_action: "quarantine_corrupt_dataset_download",
    }),
    artifact("experiment_result", 1, {
      status: "failed",
      verdict: "failed",
      error: "EXPERIMENT_DATASET_DOWNLOAD_FAILED:cifar-10",
      diagnosis: {
        category: "dataset",
        user_message: "数据集下载损坏，已隔离缓存并准备重试。",
      },
    }),
  ];

  assert.equal(latestExperimentDiagnosis(artifacts)?.category, "dataset");
  assert.equal(
    latestExperimentResultFailure(artifacts),
    "数据集下载损坏，已隔离缓存并准备重试。",
  );
});

test("experiment failure summaries hide tracebacks and explain dataset misses", () => {
  assert.equal(
    summarizeExperimentFailure(
      "LOCAL_EXPERIMENT_RUN_FAILED: RuntimeError: Dataset not found or corrupted. You can use download=True to download it\nFile D:/x/train.py datasets.CIFAR10",
    ),
    "实验失败：本地缺少 CIFAR-10 数据集",
  );
  assert.equal(
    summarizeExperimentFailure("LOCAL_EXPERIMENT_DEPENDENCY_MISSING:sklearn. Install it with pip"),
    "实验失败：缺少依赖 sklearn",
  );
});

test("successful experiment results satisfy completion", () => {
  const artifacts = [
    artifact("experiment_result", 1, {
      provider: "local_gpu",
      metrics: { accuracy: 0.9 },
      attempts: [{ attempt: 1, status: "completed" }],
    }),
  ];

  assert.equal(hasSuccessfulExperimentResult(artifacts), true);
  assert.equal(latestExperimentResultFailure(artifacts), null);
});

test("an old successful result cannot satisfy the latest experiment task", () => {
  const artifacts = [
    artifact("experiment_task", 1, { experiment_id: "experiment_1" }),
    artifact("experiment_result", 1, {
      experiment_id: "experiment_1",
      metrics: { accuracy: 0.9 },
      attempts: [{ status: "completed" }],
    }),
    artifact("experiment_task", 2, { experiment_id: "experiment_2" }),
  ];

  assert.equal(hasSuccessfulExperimentResult(artifacts), false);
  artifacts.push(artifact("experiment_result", 2, {
    experiment_id: "experiment_2",
    metrics: { accuracy: 0.91 },
    attempts: [{ status: "completed" }],
  }));
  assert.equal(hasSuccessfulExperimentResult(artifacts), true);
});

test("plan metric rows retain order and merge matching result values", () => {
  assert.deepEqual(
    buildExperimentMetricRows(
      { evaluations: [
        { metric: "测试准确率", direction: "↑", method: "p<0.05" },
        { metric: "标准差", direction: "↓", method: "3 seeds" },
      ] },
      { 测试准确率: 0.93 },
    ),
    [
      { key: "测试准确率", label: "测试准确率", direction: "↑", criterion: "p<0.05", result: "0.93", source: "plan" },
      { key: "标准差", label: "标准差", direction: "↓", criterion: "3 seeds", result: "待运行", source: "plan" },
    ],
  );
});

test("result-only metrics append after plan metrics", () => {
  assert.deepEqual(
    buildExperimentMetricRows({ evaluations: [{ metric: "loss" }] }, { loss: 0.2, elapsed_seconds: 12 }),
    [
      { key: "loss", label: "loss", direction: "未指定", criterion: "未指定", result: "0.2", source: "plan" },
      { key: "elapsed_seconds", label: "elapsed_seconds", direction: "结果输出", criterion: "运行结果未在实验设计中声明", result: "12", source: "result" },
    ],
  );
});

test("experiment metric comparisons pair baseline and improved outputs", () => {
  assert.deepEqual(
    buildExperimentMetricComparisons({
      baseline_accuracy: 0.8,
      improved_accuracy: 0.84,
      baseline_f1_score: 0.75,
      improved_f1_score: 0.75,
      elapsed_seconds: 12,
    }),
    [
      { key: "accuracy", label: "准确率", baseline: 0.8, improved: 0.84, delta: 0.04, verdict: "improved" },
      { key: "f1_score", label: "F1 分数", baseline: 0.75, improved: 0.75, delta: 0, verdict: "unchanged" },
    ],
  );
  assert.equal(formatMetricValue(0.123456), "0.1235");
  assert.equal(formatMetricValue(3), "3");
});

test("experiment metric comparisons use structured analysis independently of result field names", () => {
  assert.deepEqual(
    buildExperimentMetricComparisons(
      {
        relu_accuracy_mean: 0.60932,
        relu_f1_mean: 0.6094954243956531,
        mish_accuracy_mean: 0.59786,
        mish_f1_mean: 0.5975222273946823,
        relu_accuracy_std: 0.0075,
        mish_accuracy_std: 0.0035,
      },
      {
        comparisons: [{
          baseline: "任意基线方案",
          variant: "任意改进方案",
          metric: "准确率",
          baseline_value: 0.60932,
          variant_value: 0.59786,
          difference: -0.01146,
        }, {
          baseline: "任意基线方案",
          variant: "任意改进方案",
          metric: "F1分数",
          baseline_value: 0.6094954243956531,
          variant_value: 0.5975222273946823,
        }],
      },
    ),
    [
      { key: "准确率_0", label: "准确率", baseline: 0.60932, improved: 0.59786, delta: -0.01146, verdict: "declined" },
      { key: "F1分数_1", label: "F1分数", baseline: 0.6094954243956531, improved: 0.5975222273946823, delta: -0.011973197001, verdict: "declined" },
    ],
  );
});

test("manifest expected metrics are not mislabeled as undeclared outputs", () => {
  assert.deepEqual(
    buildExperimentMetricRows(
      { evaluations: [] },
      { custom_score_mean: 0.42 },
      ["custom_score_mean"],
    ),
    [{
      key: "custom_score_mean",
      label: "custom_score_mean",
      direction: "结果输出",
      criterion: "实验运行清单声明的结果指标",
      result: "0.42",
      source: "result",
    }],
  );
});

test("only finite numeric sequences qualify as a chart series", () => {
  assert.deepEqual(findMetricSeries({ notes: ["a", "b"], accuracy: [0.7, 0.8, 0.9] }), { key: "accuracy", values: [0.7, 0.8, 0.9] });
  assert.equal(findMetricSeries({ accuracy: [0.7], loss: [0.1, Number.NaN] }), null);
  assert.equal(findMetricSeries({ series: new Array(2) }), null);
});

test("chart series bounds handle long result sequences without spreading arguments", () => {
  const values = Array.from({ length: 200_000 }, (_, index) => index % 17);

  assert.deepEqual(getMetricSeriesBounds(values), { minimum: 0, maximum: 16, range: 16 });
  assert.deepEqual(getMetricSeriesBounds([4, 4, 4]), { minimum: 4, maximum: 4, range: 1 });
});
