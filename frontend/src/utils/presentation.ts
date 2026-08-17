import type { Artifact } from "../api/types";

function normalizedString(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const normalized = value.trim();
  return normalized || fallback;
}

export type ExperimentMetricRow = {
  key: string;
  label: string;
  direction: string;
  criterion: string;
  result: string;
  source: "plan" | "result";
};

export type ExperimentMetricComparison = {
  key: string;
  label: string;
  baseline: number;
  improved: number;
  delta: number;
  verdict: "improved" | "declined" | "unchanged";
};

const METRIC_LABELS: Record<string, string> = {
  accuracy: "准确率",
  f1: "F1 分数",
  f1_score: "F1 分数",
  loss: "损失",
};

function metricLabel(key: string) {
  return METRIC_LABELS[key] ?? key.replace(/_/g, " ");
}

export function formatMetricValue(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value ?? "-");
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(4);
}

export function buildExperimentMetricComparisons(
  resultMetrics: Record<string, unknown>,
  analysis?: Record<string, unknown>,
): ExperimentMetricComparison[] {
  const comparisons: ExperimentMetricComparison[] = [];

  const addComparison = (key: string, baseline: number, improved: number) => {
    const delta = Number((improved - baseline).toPrecision(12));
    comparisons.push({
      key,
      label: metricLabel(key),
      baseline,
      improved,
      delta,
      verdict: Math.abs(delta) < 1e-12 ? "unchanged" : delta > 0 ? "improved" : "declined",
    });
  };

  const analyzedComparisons = Array.isArray(analysis?.comparisons) ? analysis.comparisons : [];
  for (const [index, item] of analyzedComparisons.entries()) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const record = item as Record<string, unknown>;
    const baseline = record.baseline_value;
    const improved = record.variant_value;
    if (
      typeof baseline !== "number"
      || !Number.isFinite(baseline)
      || typeof improved !== "number"
      || !Number.isFinite(improved)
    ) continue;
    const metric = normalizedString(record.metric, `metric_${index + 1}`);
    const delta = Number((improved - baseline).toPrecision(12));
    comparisons.push({
      key: `${metric}_${index}`,
      label: metricLabel(metric),
      baseline,
      improved,
      delta,
      verdict: Math.abs(delta) < 1e-12 ? "unchanged" : delta > 0 ? "improved" : "declined",
    });
  }

  if (comparisons.length) return comparisons;

  for (const [key, baseline] of Object.entries(resultMetrics)) {
    if (!key.startsWith("baseline_") || typeof baseline !== "number" || !Number.isFinite(baseline)) continue;
    const metricKey = key.slice("baseline_".length);
    const improved = resultMetrics[`improved_${metricKey}`];
    if (typeof improved !== "number" || !Number.isFinite(improved)) continue;
    addComparison(metricKey, baseline, improved);
  }

  return comparisons;
}

export function findLatestArtifact(artifacts: Artifact[], type: string) {
  for (let index = artifacts.length - 1; index >= 0; index -= 1) {
    if (artifacts[index].type === type) return artifacts[index];
  }
  return undefined;
}

export function findLatestArtifactContent(artifacts: Artifact[], type: string) {
  return findLatestArtifact(artifacts, type)?.content;
}

export function findLatestExperimentResultForTask(artifacts: Artifact[]) {
  const task = findLatestArtifact(artifacts, "experiment_task");
  const experimentId = task?.content.experiment_id;
  if (!experimentId) return findLatestArtifact(artifacts, "experiment_result");
  return [...artifacts].reverse().find(
    (artifact) => artifact.type === "experiment_result"
      && artifact.content.experiment_id === experimentId,
  );
}

export function isFailedExperimentResultContent(content: Record<string, unknown> | undefined) {
  if (!content) return false;
  if (content.status === "failed" || content.verdict === "failed") return true;
  const attempts = content.attempts;
  if (!Array.isArray(attempts) || attempts.length === 0) return false;
  const latestAttempt = attempts[attempts.length - 1];
  return Boolean(
    latestAttempt
      && typeof latestAttempt === "object"
      && !Array.isArray(latestAttempt)
      && (latestAttempt as Record<string, unknown>).status === "failed",
  );
}

export function latestExperimentDiagnosis(artifacts: Artifact[]) {
  const result = findLatestExperimentResultForTask(artifacts)?.content;
  const failure = findLatestArtifact(artifacts, "experiment_failure")?.content;
  if (!result && !failure) return undefined;
  const embedded = result?.diagnosis ?? failure?.diagnosis;
  if (embedded && typeof embedded === "object" && !Array.isArray(embedded)) {
    return embedded as Record<string, unknown>;
  }
  return findLatestArtifact(artifacts, "experiment_diagnosis")?.content;
}

export function latestExperimentResultFailure(artifacts: Artifact[]) {
  const engineeringFailure = findLatestArtifact(artifacts, "experiment_failure");
  if (engineeringFailure) {
    const failure = engineeringFailure.content;
    const diagnosis = latestExperimentDiagnosis(artifacts);
    const message = diagnosis?.user_message;
    if (typeof message === "string" && message.trim()) return message.trim();
    return summarizeExperimentFailure(String(failure.error || failure.error_code || "EXPERIMENT_RUN_FAILED"));
  }
  const artifact = findLatestExperimentResultForTask(artifacts);
  if (!isFailedExperimentResultContent(artifact?.content)) return null;
  const content = artifact?.content ?? {};
  const attempts = Array.isArray(content.attempts) ? content.attempts : [];
  const latestAttempt = attempts[attempts.length - 1];
  const attemptError = latestAttempt && typeof latestAttempt === "object" && !Array.isArray(latestAttempt)
    ? (latestAttempt as Record<string, unknown>).error_code
    : "";
  const diagnosis = latestExperimentDiagnosis(artifacts);
  const diagnosedMessage = diagnosis?.user_message;
  if (typeof diagnosedMessage === "string" && diagnosedMessage.trim()) return diagnosedMessage.trim();
  return summarizeExperimentFailure(String(content.error || attemptError || "EXPERIMENT_RUN_FAILED"));
}

export function summarizeExperimentFailure(error: string) {
  if (!error.trim()) return "实验失败";
  if (error.includes("Dataset not found or corrupted")) {
    if (error.includes("CIFAR10") || error.includes("cifar")) return "实验失败：本地缺少 CIFAR-10 数据集";
    if (error.includes("MNIST")) return "实验失败：本地缺少 MNIST 数据集";
    return "实验失败：本地缺少实验数据集";
  }
  if (error.includes("LOCAL_EXPERIMENT_DEPENDENCY_MISSING")) {
    const match = error.match(/LOCAL_EXPERIMENT_DEPENDENCY_MISSING:([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)/);
    return match ? `实验失败：缺少依赖 ${match[1]}` : "实验失败：缺少依赖";
  }
  if (error.includes("LOCAL_EXPERIMENT_CUDA_UNAVAILABLE")) return "实验失败：本地 CUDA 不可用";
  if (error.includes("LOCAL_EXPERIMENT_TIMEOUT")) return "实验失败：运行超时";
  if (error.includes("LOCAL_EXPERIMENT_RUN_FAILED")) return "实验失败：本地实验运行失败";
  return "实验失败";
}

export function hasSuccessfulExperimentResult(artifacts: Artifact[]) {
  const artifact = findLatestExperimentResultForTask(artifacts);
  return Boolean(artifact && !isFailedExperimentResultContent(artifact.content));
}

export function buildExperimentMetricRows(
  plan: Record<string, unknown> | undefined,
  resultMetrics: Record<string, unknown>,
  expectedMetrics: unknown[] = [],
): ExperimentMetricRow[] {
  const rows: ExperimentMetricRow[] = [];
  const known = new Set<string>();
  const declared = new Set(expectedMetrics.filter((item): item is string => typeof item === "string"));
  const evaluations = Array.isArray(plan?.evaluations) ? plan.evaluations : [];

  for (const evaluation of evaluations) {
    if (!evaluation || typeof evaluation !== "object" || Array.isArray(evaluation)) continue;
    const record = evaluation as Record<string, unknown>;
    const key = normalizedString(record.metric, "");
    if (!key || known.has(key)) continue;

    known.add(key);
    rows.push({
      key,
      label: key,
      direction: normalizedString(record.direction, "未指定"),
      criterion: normalizedString(record.method, "未指定"),
      result: key in resultMetrics ? String(resultMetrics[key]) : "待运行",
      source: "plan",
    });
  }

  for (const [key, value] of Object.entries(resultMetrics)) {
    if (known.has(key)) continue;
    rows.push({
      key,
      label: key,
      direction: "结果输出",
      criterion: declared.has(key) ? "实验运行清单声明的结果指标" : "运行结果未在实验设计中声明",
      result: String(value),
      source: "result",
    });
  }

  return rows;
}

export function findMetricSeries(resultMetrics: Record<string, unknown>) {
  for (const [key, value] of Object.entries(resultMetrics)) {
    if (
      Array.isArray(value) &&
      value.length >= 2 &&
      Array.from(value).every((item) => typeof item === "number" && Number.isFinite(item))
    ) {
      return { key, values: value };
    }
  }
  return null;
}

export function getMetricSeriesBounds(values: number[]) {
  if (!values.length) return { minimum: 0, maximum: 0, range: 1 };

  let minimum = values[0];
  let maximum = values[0];
  for (const value of values) {
    if (value < minimum) minimum = value;
    if (value > maximum) maximum = value;
  }

  return { minimum, maximum, range: maximum - minimum || 1 };
}

function truncateUnicode(value: string, maxCharacters: number): string {
  const characters = Array.from(value);
  return characters.length > maxCharacters
    ? `${characters.slice(0, maxCharacters).join("")}…`
    : value;
}

export function summarizeResearchProblem(value: unknown) {
  const fullText = normalizedString(value, "未填写研究问题");
  return { text: truncateUnicode(fullText, 8), fullText };
}

export function formatReferenceTitle(value: unknown): string {
  return normalizedString(value, "未命名论文");
}

export function formatAuthors(value: unknown): string {
  if (!Array.isArray(value)) return "作者未知";
  const authors = value
    .filter((author): author is string => typeof author === "string")
    .map((author) => author.trim())
    .filter(Boolean);
  return authors.length ? authors.join(", ") : "作者未知";
}

export function formatReferenceIdentifier(reference: Record<string, unknown>): string {
  const identifiers = reference.identifiers;
  const doi =
    identifiers && typeof identifiers === "object"
      ? normalizedString((identifiers as Record<string, unknown>).doi, "")
      : "";
  if (doi) return doi;
  return normalizedString(reference.source, "期刊未知");
}
