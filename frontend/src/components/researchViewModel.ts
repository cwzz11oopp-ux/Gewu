import type { Artifact, ExperimentProgress, RunRecord } from "../api/types";
import { findLatestArtifact, findLatestArtifactContent } from "../utils/presentation";

export type ResearchStatus = "empty" | "searching" | "thinking" | "running" | "failed" | "refuted" | "completed" | "ready" | "queued" | "revision_required" | "needs_plan_revision" | "policy_integrity_required" | "evidence_insufficient" | "rejected";

export type PaperItem = {
  id: string;
  title: string;
  authors: string;
  year: string;
  source: string;
  relevance?: number;
  status: "included" | "review" | "excluded";
  url?: string;
  sourceKind: "wiki" | "local" | "external";
  localDocumentId?: string;
};

export type EvidenceItem = {
  id: string;
  claim: string;
  sourceId: string;
  locator: string;
  stance: "support" | "conflict" | "gap";
};

export type HypothesisItem = {
  id: string;
  claim: string;
  method: string;
  mechanism: string;
  status: "candidate" | "selected" | "refuted" | "partial" | "evidence_insufficient" | "rejected" | "revision_required";
  scores: { falsifiability?: number; coverage?: number; novelty?: number };
  compositeScore?: number;
  reason: string;
  evidenceSources: Array<{ title: string; url?: string; stance: "support" | "conflict" }>;
  sourceGapIds: string[];
  sourcePaperIds: string[];
  sourceClaimIds: string[];
  sourceFutureWorkIds: string[];
  sourceCodeEvidenceIds: string[];
  provenanceAvailable: boolean;
};

export type ExperimentItem = {
  id: string;
  title: string;
  technicalName: string;
  purpose: string;
  status: "completed" | "running" | "failed" | "queued";
  metrics: Array<{ name: string; value: number }>;
  delta?: number;
  runtime: string;
  provider: string;
  dataset: string;
  failureReason: string;
  parameters: Record<string, unknown>;
  seeds: Array<string | number>;
  environment: Record<string, unknown>;
  log: string;
  logPath: string;
  metricsPath: string;
  deployedFiles: string[];
  attempts: Array<{ id: string; status: string; startedAt: string; endedAt: string; error: string }>;
  primaryMetric?: { name: string; value: number; direction: "higher" | "lower" | "unknown" };
  primaryMetricNames: string[];
  evolution?: { delta: number; status: "improved" | "declined" | "unchanged" | "not_comparable"; baselineId?: string };
  classification: "scientific" | "engineering";
  isRealExperiment: boolean;
  auditStatus: string;
  resultArtifactId: string;
  revisionArtifactId: string;
  revisionReason: string;
  scientificFeedback: string;
  completedAt: string;
  metricDirections: Record<string, "higher" | "lower" | "unknown">;
  metricContracts: Record<string, string>;
  declaredMetrics: string[];
  epochSeries: EpochSeries[];
  epochSeriesBySeed: EpochSeriesBySeed;
};

export type ScientificMetricSeries = {
  name: string;
  direction: "higher" | "lower" | "unknown";
  rows: Array<{ experimentId: string; artifactId: string; value: number; previousValue?: number; delta?: number; trend: "improved" | "declined" | "unchanged" | "not_comparable" }>;
};

export type EpochSeries = { metric: string; rows: Array<{ epoch: number; value: number }> };
export type EpochSeriesBySeed = Array<{ seed: number; series: EpochSeries[] }>;

export type ScientificFindings = {
  hypothesisStatus: string;
  conclusion: string;
  primaryMetric?: { name: string; value: number };
  comparison?: { baseline: string; variant: string; baselineValue: number; variantValue: number; delta: number };
  seedCount: number;
  parameterSummary: string;
  auditStatus: string;
  limitations: string[];
};

export type TreeNode = {
  id: string;
  kind: "Q" | "L" | "T" | "G" | "H" | "V" | "X" | "R" | "C" | "S" | "P" | "F";
  title: string;
  status: ResearchStatus;
  x: number;
  y: number;
  detail?: string;
  emphasis?: "selected" | "muted";
};

export type TreeEdge = { from: string; to: string; label: string; tone?: "support" | "conflict" | "neutral" };

export type ResearchViewModel = {
  question: string;
  title: string;
  runId: string;
  status: ResearchStatus;
  currentStage: string;
  papers: PaperItem[];
  evidence: EvidenceItem[];
  hypotheses: HypothesisItem[];
  hypothesisRounds: Array<{ roundId: string; roundIndex: number; parentRoundId: string; revisionReason: string; candidateIds: string[]; scientificFeedbackCount: number }>;
  experiments: ExperimentItem[];
  selectedHypothesis?: HypothesisItem;
  currentExperiment?: ExperimentItem;
  scientificFindings?: ScientificFindings;
  conclusion: string;
  boundaries: string[];
  reportSections: string[];
  researchSynthesis: {
    available: boolean;
    paperCount: number;
    themeCount: number;
    gapCount: number;
    futureWorkCount: number;
    papers: Array<{ id: string; title: string; url?: string }>;
    themes: Array<{ id: string; title: string; paperIds: string[]; claimIds: string[] }>;
    gaps: Array<{ id: string; title: string; description: string; paperIds: string[]; claimIds: string[]; futureWorkIds: string[] }>;
    literatureCoverage?: { decision: string; hardCapReached: boolean; coverageScore?: number; saturationScore?: number };
  };
  hypothesisLiterature: {
    retrievedCount: number;
    inputCount: number;
    irrelevantRemoved: number;
    duplicateMerged: number;
  };
  githubSource: { url: string; status: "not_provided" | "parsed" | "unavailable"; warning: string };
  nodes: TreeNode[];
  edges: TreeEdge[];
  updatedAt: string;
  reproducibility: Array<{ label: string; value: string; ready: boolean }>;
  reportAvailable: boolean;
  codePackageAvailable: boolean;
};

const isRecord = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === "object" && !Array.isArray(value));
const records = (value: unknown) => Array.isArray(value) ? value.filter(isRecord) : [];
const text = (...values: unknown[]) => {
  for (const value of values) if (typeof value === "string" && value.trim()) return value.trim();
  return "";
};
const number = (...values: unknown[]) => {
  for (const value of values) if (typeof value === "number" && Number.isFinite(value)) return value;
  return undefined;
};
const stringify = (value: unknown): string => {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(stringify).filter(Boolean).join(", ");
  return "";
};
const short = (value: string, max = 76) => Array.from(value).length > max ? `${Array.from(value).slice(0, max).join("")}…` : value;
const externalUrl = (value: unknown): string | undefined => {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return undefined;
  try {
    const parsed = new URL(raw.startsWith("//") ? `https:${raw}` : raw);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.toString() : undefined;
  } catch { return undefined; }
};
const statusForRun = (run: RunRecord | null, progress: ExperimentProgress | null): ResearchStatus => {
  if (!run) return "empty";
  if (run.status === "NEEDS_PLAN_REVISION") return "needs_plan_revision";
  if (run.status === "POLICY_INTEGRITY_REQUIRED") return "policy_integrity_required";
  if (run.status === "hypothesis_revision_required") return "revision_required";
  if (progress?.state === "failed" || ["failed", "FAILED_SYSTEM", "preflight_failed"].includes(run.status)) return "failed";
  if (progress?.process_alive || ["running", "queued", "stopping"].includes(run.status)) return "running";
  if (run.status === "completed") return "completed";
  return "ready";
};

function normalizePapers(artifacts: Artifact[]): PaperItem[] {
  const evidence = findLatestArtifactContent(artifacts, "evidence") ?? {};
  const refs = records(evidence.references);
  return refs.map((item, index) => {
    const identifiers = isRecord(item.identifiers) ? item.identifiers : {};
    const doi = text(item.doi, identifiers.doi).replace(/^https?:\/\/(dx\.)?doi\.org\//i, "");
    const arxiv = text(item.arxiv, identifiers.arxiv);
    const url = externalUrl(item.url) ?? externalUrl(item.source_url) ?? externalUrl(item.publisher_url)
      ?? externalUrl(item.semantic_scholar_url) ?? externalUrl(item.crossref_url)
      ?? (doi ? `https://doi.org/${encodeURI(doi)}` : undefined)
      ?? (arxiv ? `https://arxiv.org/abs/${encodeURIComponent(arxiv)}` : undefined);
    return {
      id: text(item.id, item.paper_id, item.reference_id) || `P${String(index + 1).padStart(2, "0")}`,
      title: text(item.title, item.name) || "未命名文献",
      authors: Array.isArray(item.authors) ? item.authors.map(stringify).filter(Boolean).join(", ") : text(item.authors) || "作者信息未提供",
      year: stringify(item.year) || "年份未知",
      source: text(item.venue, item.journal, item.source) || "来源未标注",
      relevance: number(item.relevance_score, item.score),
      status: item.excluded === true ? "excluded" : item.verified === false ? "review" : "included",
      url,
      sourceKind: text(item.source_kind) === "wiki" ? "wiki" : text(item.source_kind) === "local" ? "local" : "external",
      localDocumentId: text(item.local_document_id) || undefined,
    };
  });
}

function normalizeEvidence(artifacts: Artifact[]): EvidenceItem[] {
  const evidence = findLatestArtifactContent(artifacts, "evidence") ?? {};
  const registry = records((findLatestArtifactContent(artifacts, "reasoning") ?? {}).evidence_registry);
  const direct = records(evidence.items).concat(records(evidence.evidence)).concat(registry);
  const rows: Record<string, unknown>[] = direct.length ? direct : records(evidence.references).map((item, index): Record<string, unknown> => ({
    ...item,
    claim: text(item.claim, item.summary, item.abstract, item.title),
    source_id: text(item.id, item.paper_id),
  }));
  return rows.map((item, index) => {
    const stanceText = text(item.stance, item.type, item.verdict).toLowerCase();
    const stance: EvidenceItem["stance"] = /conflict|contradict|refut|反|冲突/.test(stanceText)
      ? "conflict" : /gap|missing|unknown|缺口|不足/.test(stanceText) ? "gap" : "support";
    return {
      id: text(item.id, item.evidence_id) || `E${String(index + 1).padStart(2, "0")}`,
      claim: short(text(item.claim, item.summary, item.reasoning, item.title) || "证据内容尚未结构化", 110),
      // A missing source ID is a provenance failure, not permission to attach the
      // record to an unrelated paper by its array position.
      sourceId: text(item.source_id, item.paper_id, item.reference_id),
      locator: text(item.locator, item.location, item.section, item.source) || "来源定位待补充",
      stance,
    };
  });
}

function normalizeHypotheses(artifacts: Artifact[]): HypothesisItem[] {
  const hypothesis = findLatestArtifactContent(artifacts, "hypothesis") ?? {};
  const reasoning = findLatestArtifactContent(artifacts, "reasoning") ?? {};
  const selection = findLatestArtifactContent(artifacts, "hypothesis_selection") ?? {};
  const candidates = records(hypothesis.candidates);
  const assessments = records(reasoning.candidate_assessments);
  const selectedIds = new Set([stringify(selection.selected_hypothesis_id), ...records(selection.selected).map((item) => text(item.id, item.hypothesis_id))].filter(Boolean));
  const selectedIndexes = new Set((Array.isArray(selection.selected_indexes) ? selection.selected_indexes : []).filter((item): item is number => typeof item === "number"));
  return candidates.map((item, index) => {
    const assessment = assessments.find((entry) => number(entry.candidate_index) === index) ?? {};
    const evaluation = isRecord(assessment.evaluation) ? assessment.evaluation : {};
    const evaluationScores = isRecord(evaluation.scores) ? evaluation.scores : {};
    const id = text(item.id, item.candidate_id, item.hypothesis_id, assessment.candidate_id) || `H${index + 1}`;
    const verdict = text(assessment.status, assessment.verdict, item.status).toLowerCase();
    const selected = selectedIds.has(id) || selectedIndexes.has(index);
    const status = selected ? "selected"
      : /evidence_insufficient|insufficient/.test(verdict) ? "evidence_insufficient"
      : /revision_required/.test(verdict) ? "revision_required"
      : /refut/.test(verdict) ? "refuted"
      : /reject|not_supported/.test(verdict) ? "rejected"
      : /partial|部分/.test(verdict) ? "partial" : "candidate";
    const scores = {
      falsifiability: number(assessment.falsifiability_score, assessment.falsifiability, evaluationScores.testability) !== undefined ? number(assessment.falsifiability_score, assessment.falsifiability) ?? number(evaluationScores.testability)! / 5 : undefined,
      coverage: number(assessment.evidence_coverage, assessment.coverage_score, evaluationScores.scientific_soundness) !== undefined ? number(assessment.evidence_coverage, assessment.coverage_score) ?? number(evaluationScores.scientific_soundness)! / 5 : undefined,
      novelty: number(assessment.novelty_score, assessment.novelty, evaluationScores.novelty) !== undefined ? number(assessment.novelty_score, assessment.novelty) ?? number(evaluationScores.novelty)! / 5 : undefined,
    };
    // The composite score is the server-computed 0..1 weighted score persisted on
    // the assessment.  For legacy runs it falls back to the average of the three
    // normalized sub-scores so the card always shows a single 0..1 value.
    const scoreValues = [scores.falsifiability, scores.coverage, scores.novelty].filter((item): item is number => typeof item === "number");
    const compositeScore = number(assessment.composite_score)
      ?? (scoreValues.length ? scoreValues.reduce((sum, item) => sum + item, 0) / scoreValues.length : undefined);
    return {
      id,
      claim: text(item.claim, item.hypothesis, item.objective) || "候选假设内容待生成",
      method: text(item.method) || "方法待规划阶段确定",
      mechanism: text(item.mechanism) || "机制说明待补充",
      status,
      scores,
      compositeScore,
      reason: /Highest server-computed weighted score/i.test(text(assessment.reasoning, assessment.reason, selection.selection_reason))
        ? "在满足有效性与可选择条件的候选中，该假设的服务端加权评分最高且超过自动选择阈值。"
        : text(assessment.reasoning, assessment.reason, selection.selection_reason) || "等待证据推理与选择记录",
      evidenceSources: records(item.evidence_basis).map((source) => ({
        title: text(source.source_title, source.title) || "未命名来源",
        url: externalUrl(source.source_url) ?? externalUrl(source.url),
        stance: /conflict|contradict|refut/i.test(text(source.stance, source.evidence_type)) ? "conflict" : "support",
      })),
      sourceGapIds: Array.isArray(item.source_gap_ids) ? item.source_gap_ids.map(stringify).filter(Boolean) : [],
      sourcePaperIds: Array.isArray(item.source_paper_ids) ? item.source_paper_ids.map(stringify).filter(Boolean) : [],
      sourceClaimIds: Array.isArray(item.source_claim_ids) ? item.source_claim_ids.map(stringify).filter(Boolean) : [],
      sourceFutureWorkIds: Array.isArray(item.source_future_work_ids) ? item.source_future_work_ids.map(stringify).filter(Boolean) : [],
      sourceCodeEvidenceIds: Array.isArray(item.source_code_evidence_ids) ? item.source_code_evidence_ids.map(stringify).filter(Boolean) : [],
      provenanceAvailable: text(item.provenance_status).toLowerCase() === "grounded" && Array.isArray(item.source_gap_ids) && item.source_gap_ids.length > 0,
    };
  });
}

function normalizeHypothesisRounds(artifacts: Artifact[]) {
  return artifacts.filter((artifact) => artifact.type === "hypothesis").map((artifact, index) => {
    const round = isRecord(artifact.content.hypothesis_round) ? artifact.content.hypothesis_round : {};
    const candidates = records(artifact.content.candidates);
    return {
      roundId: text(round.round_id) || `legacy-${artifact.id}`,
      roundIndex: number(round.round_index) ?? index + 1,
      parentRoundId: text(round.parent_round_id),
      revisionReason: text(round.revision_reason) || (index ? "Historical hypothesis artifact" : "Initial hypothesis generation"),
      candidateIds: Array.isArray(round.created_candidate_ids) ? round.created_candidate_ids.map(stringify).filter(Boolean) : candidates.map((item) => text(item.candidate_id, item.id)).filter(Boolean),
      scientificFeedbackCount: records(round.scientific_feedback).length,
    };
  });
}

function normalizeResearchSynthesis(artifacts: Artifact[]) {
  const synthesis = findLatestArtifactContent(artifacts, "research_synthesis") ?? {};
  const source = isRecord(synthesis.source_collection) ? synthesis.source_collection : {};
  const themes = records(synthesis.themes).map((item) => ({
    id: text(item.theme_id), title: text(item.title) || "Untitled theme",
    paperIds: Array.isArray(item.source_paper_ids) ? item.source_paper_ids.map(stringify).filter(Boolean) : [],
    claimIds: Array.isArray(item.source_claim_ids) ? item.source_claim_ids.map(stringify).filter(Boolean) : [],
  }));
  const gaps = records(synthesis.research_gaps).map((item) => ({
    id: text(item.gap_id), title: text(item.title) || "Untitled research gap", description: text(item.description),
    paperIds: Array.isArray(item.source_paper_ids) ? item.source_paper_ids.map(stringify).filter(Boolean) : [],
    claimIds: Array.isArray(item.source_claim_ids) ? item.source_claim_ids.map(stringify).filter(Boolean) : [],
    futureWorkIds: Array.isArray(item.source_future_work_ids) ? item.source_future_work_ids.map(stringify).filter(Boolean) : [],
  }));
  const papers = records(synthesis.papers).map((item) => ({
    id: text(item.paper_id), title: text(item.title) || "Untitled literature record", url: externalUrl(item.url),
  }));
  const coverage = isRecord(synthesis.literature_coverage) ? synthesis.literature_coverage : {};
  return {
    available: Boolean(synthesis.schema_version && source.paper_count !== undefined),
    paperCount: number(source.paper_count) ?? 0,
    themeCount: themes.length,
    gapCount: gaps.length,
    futureWorkCount: records(synthesis.future_work).length,
    papers,
    themes,
    gaps,
    literatureCoverage: Object.keys(coverage).length ? {
      decision: text(coverage.decision) || "continue",
      hardCapReached: coverage.hard_cap_reached === true,
      coverageScore: number(coverage.coverage_score),
      saturationScore: number(coverage.saturation_score),
    } : undefined,
  };
}

function duration(start: unknown, end: unknown) {
  if (typeof start !== "string" || typeof end !== "string") return "—";
  const seconds = Math.max(0, Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000));
  const hours = Math.floor(seconds / 3600).toString().padStart(2, "0");
  const minutes = Math.floor((seconds % 3600) / 60).toString().padStart(2, "0");
  const secs = (seconds % 60).toString().padStart(2, "0");
  return `${hours}:${minutes}:${secs}`;
}

function displayExperimentTitle(rawName: string, purpose: string, fallback: string) {
  const isMachineName = /^[A-Za-z0-9_.\-/]+$/.test(rawName) && /[_-]/.test(rawName);
  if (!rawName || isMachineName) {
    const readablePurpose = /[\u4e00-\u9fff]/.test(purpose) ? short(purpose, 26) : "";
    return readablePurpose || fallback;
  }
  return short(rawName, 34);
}

function metricDirection(name: string, evaluations: Record<string, unknown>[] = []): "higher" | "lower" | "unknown" {
  const declared = evaluations.find((item) => text(item.metric, item.name) === name);
  const direction = text(declared?.direction, declared?.optimization, declared?.goal).toLowerCase();
  if (/higher|larger|maximi[sz]e|越高|增大/.test(direction)) return "higher";
  if (/lower|smaller|minimi[sz]e|越低|减小/.test(direction)) return "lower";
  return /loss|error|mae|mse|rmse|latency|time|耗时|损失|误差/i.test(name) ? "lower" : /accuracy|acc|f1|auc|precision|recall|准确|得分/i.test(name) ? "higher" : "unknown";
}

function attachEvolution(rows: ExperimentItem[]): ExperimentItem[] {
  return rows.map((row, index) => {
    const primary = row.primaryMetric;
    if (row.classification !== "scientific" || !primary || row.status !== "completed" || primary.direction === "unknown") return { ...row, evolution: { delta: 0, status: "not_comparable" } };
    const baseline = rows.slice(0, index).find((item) => item.classification === "scientific" && item.status === "completed" && item.primaryMetric?.name === primary.name && item.primaryMetric.direction === primary.direction);
    if (!baseline?.primaryMetric) return { ...row, evolution: { delta: 0, status: "not_comparable" } };
    const rawDelta = primary.value - baseline.primaryMetric.value;
    const delta = primary.direction === "lower" ? -rawDelta : rawDelta;
    return { ...row, evolution: { delta, baselineId: baseline.id, status: Math.abs(delta) < 1e-12 ? "unchanged" : delta > 0 ? "improved" : "declined" } };
  });
}

function primaryMetric(metrics: Array<{ name: string; value: number }>, evaluations: Record<string, unknown>[] = []) {
  const declared = evaluations.map((item) => text(item.metric, item.name)).filter(Boolean);
  const metric = declared.map((name) => metrics.find((item) => item.name === name)).find(Boolean)
    ?? metrics.find((item) => !/std|var|time|elapsed/i.test(item.name))
    ?? metrics[0];
  return metric ? { ...metric, direction: metricDirection(metric.name, evaluations) } : undefined;
}

function primaryMetricNames(phase2Protocol: Record<string, unknown>, fallback?: { name: string }) {
  const declared = Array.isArray(phase2Protocol.primary_metrics)
    ? phase2Protocol.primary_metrics.map(stringify).filter(Boolean)
    : [];
  const first = text(phase2Protocol.primary_metric, fallback?.name);
  return [...new Set([...declared, first].filter(Boolean))].slice(0, 2);
}

// These are established, structured Result-analysis limitations. Translation is
// presentation-only: persisted scientific conclusions remain verbatim artifacts.
function presentScientificLimitation(value: string) {
  const parameterMatch = value.match(/Parameter count \(([^)]+)\) was used as the proxy for capacity\..*?FLOPs.*?effective capacity.*?weight sharing.*?dense connections/i);
  if (parameterMatch) return `参数量（${parameterMatch[1]}）被用作容量的代理指标。该差异虽在 5% 容差内，但由于 CNN 的权重共享与 MLP 的稠密连接在计算量或有效容量上可能不同，仍可能存在轻微混杂。`;
  if (/The experiment was conducted on Fashion-MNIST.*?generalization to higher-resolution or color images.*?not guaranteed/i.test(value)) return "实验仅在 Fashion-MNIST（28×28 灰度图像）上进行，不能保证结论可泛化到更高分辨率或彩色图像（如 CIFAR-10）。";
  if (/The dataset was not de-duplicated.*?near-duplicate images.*?inflate absolute accuracy.*?relative gap/i.test(value)) return "本次运行未对数据集去重；Fashion-MNIST 中的近似重复样本可能轻微抬高绝对准确率，尽管本研究主要关注相对差距。";
  return value;
}

function metricContracts(metrics: Array<{ name: string; value: number }>, evaluations: Record<string, unknown>[], datasetFingerprint: string, dataset: string) {
  const directions: Record<string, "higher" | "lower" | "unknown"> = {};
  const contracts: Record<string, string> = {};
  for (const metric of metrics) {
    const evaluation = evaluations.find((item) => text(item.metric, item.name) === metric.name) ?? {};
    directions[metric.name] = metricDirection(metric.name, evaluations);
    // The signature is sourced from the recorded dataset and evaluation plan, not inferred in the UI.
    contracts[metric.name] = [datasetFingerprint || dataset, metric.name, text(evaluation.direction, evaluation.optimization, evaluation.goal), text(evaluation.method, evaluation.protocol)].join("|");
  }
  return { directions, contracts };
}

function epochSeriesFrom(epochMetrics: unknown): EpochSeries[] {
  const rows = Array.isArray(epochMetrics) ? epochMetrics.filter(isRecord) : [];
  if (!rows.length) return [];
  const metricNames = [...new Set(rows.flatMap((row) => Object.keys(row).filter((key) => key !== "epoch" && key !== "step")))]
    .filter((metric) => rows.some((row) => typeof row[metric] === "number" && Number.isFinite(row[metric])));
  return metricNames.map((metric) => ({
    metric,
    rows: rows
      .map((row) => {
        const epoch = typeof row.epoch === "number" ? row.epoch : typeof row.step === "number" ? row.step : undefined;
        const value = row[metric];
        if (epoch === undefined || typeof value !== "number" || !Number.isFinite(value)) return undefined;
        return { epoch, value };
      })
      .filter((row): row is { epoch: number; value: number } => Boolean(row))
      .sort((a, b) => a.epoch - b.epoch),
  })).filter((series) => series.rows.length > 0);
}

function epochSeriesBySeedFrom(result: Record<string, unknown>): EpochSeriesBySeed {
  const seedResults = Array.isArray(result.seed_results) ? result.seed_results.filter(isRecord) : [];
  return seedResults.flatMap((item) => {
    const seed = typeof item.seed === "number" ? item.seed : Number(item.seed);
    if (!Number.isFinite(seed)) return [];
    const series = epochSeriesFrom(item.epoch_metrics);
    return series.length ? [{ seed, series }] : [];
  });
}

function normalizeExperiments(artifacts: Artifact[], progress: ExperimentProgress | null, run: RunRecord | null): ExperimentItem[] {
  const tasks = artifacts.filter((item) => item.type === "experiment_task");
  const results = artifacts.filter((item) => item.type === "experiment_result");
  const failures = artifacts.filter((item) => ["experiment_failure", "experiment_diagnosis"].includes(item.type));
  const candidateAttempts = artifacts.filter((item) => item.type === "experiment_candidate_attempt");
  const revisions = artifacts.filter((item) => item.type === "revision");
  const used = new Set<string>();
  const rows: ExperimentItem[] = [];
  for (const [index, taskArtifact] of tasks.entries()) {
    const task = taskArtifact.content;
    const id = text(task.experiment_id) || `X${String(index + 1).padStart(2, "0")}`;
    const resultArtifact = [...results].reverse().find((item) => text(item.content.experiment_id) === id && !used.has(item.id));
    if (resultArtifact) used.add(resultArtifact.id);
    const result = resultArtifact?.content ?? {};
    const analysis = isRecord(result.analysis) ? result.analysis : {};
    const audit = isRecord(result.audit) ? result.audit : {};
    const metrics = isRecord(result.metrics) ? result.metrics : {};
    const metricRows = Object.entries(metrics).filter(([, value]) => typeof value === "number" && Number.isFinite(value)).map(([name, value]) => ({ name, value: value as number }));
    const evaluations = records(isRecord(task.plan) ? task.plan.evaluations : task.evaluations);
    const attemptRows = records(result.attempts);
    const latestAttempt = attemptRows[attemptRows.length - 1] ?? {};
    const diagnosis = [...failures].reverse().find((item) => text(item.content.experiment_id) === id)?.content ?? {};
    const isCurrent = progress?.experiment_id === id;
    const failed = text(result.status, latestAttempt.status).toLowerCase() === "failed";
    const completed = Boolean(resultArtifact && !failed);
    const isRealExperiment = result.is_real_experiment === true && text(audit.integrity_status).toLowerCase() === "passed";
    const revisionArtifact = resultArtifact ? [...revisions].reverse().find((item) => item.parent_artifact_id === resultArtifact.id) : undefined;
    const revision = revisionArtifact?.content ?? {};
    const purpose = text(task.research_purpose, isRecord(task.hypothesis) ? task.hypothesis.objective : "", task.hypothesis) || "验证当前研究假设";
    const technicalName = text(task.name, (isRecord(task.plan) ? task.plan.name : ""));
    const taskPlan = isRecord(task.plan) ? task.plan : {};
    const planDataset = isRecord(taskPlan.dataset) ? taskPlan.dataset : {};
    const dataset = text(
      isRecord(task.dataset) ? task.dataset.name : task.dataset,
      isRecord(task.manifest) ? task.manifest.dataset : "",
      isRecord(result.environment) ? result.environment.dataset : "",
      planDataset.canonical_name,
      planDataset.display_name,
      planDataset.directory_name,
      planDataset.name,
    ) || "未声明";
    const datasetFingerprint = text(isRecord(result.environment) ? result.environment.dataset_fingerprint : "", isRecord(task.dataset) ? task.dataset.content_fingerprint : "", isRecord(task.manifest) ? task.manifest.dataset_contract_id : "");
    const metricMetadata = metricContracts(metricRows, evaluations, datasetFingerprint, dataset);
    rows.push({
      id,
      title: displayExperimentTitle(technicalName, purpose, `实验 ${index + 1}`),
      technicalName,
      purpose,
      status: isCurrent && progress?.process_alive ? "running" : failed ? "failed" : completed ? "completed" : "queued",
      metrics: metricRows,
      delta: number(analysis.delta, analysis.improvement),
      runtime: duration(result.start_time, result.end_time),
      provider: text(result.provider, task.provider) || "未配置",
      dataset,
      failureReason: text(diagnosis.user_message, diagnosis.root_cause, result.error, latestAttempt.error_code),
      parameters: isRecord(result.parameters) ? result.parameters : isRecord(task.parameters) ? task.parameters : {},
      seeds: Array.isArray(result.seeds) ? result.seeds.filter((item): item is string | number => typeof item === "string" || typeof item === "number") : [],
      environment: isRecord(result.environment) ? result.environment : {},
      log: text(result.log_tail, result.stdout_tail),
      logPath: text(result.log_path),
      metricsPath: text(result.metrics_path, result.result_path),
      deployedFiles: Array.isArray(result.deployed_files) ? result.deployed_files.map(stringify).filter(Boolean) : [],
      attempts: attemptRows.map((attempt, attemptIndex) => ({
        id: text(attempt.attempt_id, attempt.id) || `attempt-${attemptIndex + 1}`,
        status: text(attempt.status) || "unknown",
        startedAt: text(attempt.start_time, attempt.started_at),
        endedAt: text(attempt.end_time, attempt.ended_at),
        error: text(attempt.error_code, attempt.error, attempt.message),
      })),
      primaryMetric: primaryMetric(metricRows, evaluations),
      primaryMetricNames: primaryMetricNames(
        isRecord(task.phase2_protocol) ? task.phase2_protocol : {},
        primaryMetric(metricRows, evaluations),
      ),
      classification: isRealExperiment ? "scientific" : "engineering",
      isRealExperiment,
      auditStatus: text(audit.integrity_status),
      resultArtifactId: resultArtifact?.id ?? "",
      revisionArtifactId: revisionArtifact?.id ?? "",
      revisionReason: text(revision.required_revision, revision.selection_reason, revision.revision_reason, diagnosis.repair_action),
      scientificFeedback: text(revision.feedback, revision.next_action, revision.summary),
      completedAt: text(result.end_time, resultArtifact?.created_at),
      metricDirections: metricMetadata.directions,
      metricContracts: metricMetadata.contracts,
      declaredMetrics: evaluations.map((item) => text(item.metric, item.name)).filter(Boolean),
      epochSeries: epochSeriesFrom(result.epoch_metrics ?? result.training_history),
      epochSeriesBySeed: epochSeriesBySeedFrom(result),
    });
  }
  for (const resultArtifact of results.filter((item) => !used.has(item.id))) {
    const result = resultArtifact.content;
    const baseId = text(result.experiment_id) || `X${String(rows.length + 1).padStart(2, "0")}`;
    const id = `${baseId}·v${resultArtifact.version}`;
    const metrics = isRecord(result.metrics) ? result.metrics : {};
    const analysis = isRecord(result.analysis) ? result.analysis : {};
    const audit = isRecord(result.audit) ? result.audit : {};
    const attemptRows = records(result.attempts);
    const task = isRecord(result.task) ? result.task : {};
    const metricRows = Object.entries(metrics).filter(([, value]) => typeof value === "number" && Number.isFinite(value)).map(([name, value]) => ({ name, value: value as number }));
    const purpose = text(isRecord(result.task) ? result.task.hypothesis : "", analysis.objective) || "验证研究假设";
    const technicalName = text(result.name, isRecord(result.task) ? result.task.name : "");
    const evaluations = records(task.evaluations);
    const taskPlan = isRecord(task.plan) ? task.plan : {};
    const planDataset = isRecord(taskPlan.dataset) ? taskPlan.dataset : {};
    const dataset = text(
      isRecord(result.environment) ? result.environment.dataset : "",
      isRecord(task.dataset) ? task.dataset.name : task.dataset,
      isRecord(task.manifest) ? task.manifest.dataset : "",
      planDataset.canonical_name,
      planDataset.display_name,
      planDataset.directory_name,
      planDataset.name,
    ) || "未声明";
    const datasetFingerprint = text(isRecord(result.environment) ? result.environment.dataset_fingerprint : "", isRecord(task.dataset) ? task.dataset.content_fingerprint : "", isRecord(task.manifest) ? task.manifest.dataset_contract_id : "");
    const metricMetadata = metricContracts(metricRows, evaluations, datasetFingerprint, dataset);
    const isRealExperiment = result.is_real_experiment === true && text(audit.integrity_status).toLowerCase() === "passed";
    const revisionArtifact = [...revisions].reverse().find((item) => item.parent_artifact_id === resultArtifact.id);
    const revision = revisionArtifact?.content ?? {};
    rows.push({
      id,
      title: displayExperimentTitle(technicalName, purpose, `实验 ${rows.length + 1}`),
      technicalName,
      purpose,
      status: text(result.status, records(result.attempts)[records(result.attempts).length - 1]?.status).toLowerCase() === "failed" ? "failed" : "completed",
      metrics: metricRows,
      runtime: duration(result.start_time, result.end_time),
      provider: text(result.provider) || "未配置",
      dataset,
      failureReason: text(result.error),
      parameters: isRecord(result.parameters) ? result.parameters : {},
      seeds: Array.isArray(result.seeds) ? result.seeds.filter((item): item is string | number => typeof item === "string" || typeof item === "number") : [],
      environment: isRecord(result.environment) ? result.environment : {},
      log: text(result.log_tail),
      logPath: text(result.log_path),
      metricsPath: text(result.metrics_path, result.result_path),
      deployedFiles: Array.isArray(result.deployed_files) ? result.deployed_files.map(stringify).filter(Boolean) : [],
      attempts: attemptRows.map((attempt, attemptIndex) => ({
        id: text(attempt.attempt_id, attempt.id) || `attempt-${attemptIndex + 1}`,
        status: text(attempt.status) || "unknown",
        startedAt: text(attempt.start_time, attempt.started_at),
        endedAt: text(attempt.end_time, attempt.ended_at),
        error: text(attempt.error_code, attempt.error, attempt.message),
      })),
      primaryMetric: primaryMetric(metricRows, evaluations),
      primaryMetricNames: primaryMetricNames(
        isRecord(task.phase2_protocol) ? task.phase2_protocol : {},
        primaryMetric(metricRows, evaluations),
      ),
      classification: isRealExperiment ? "scientific" : "engineering",
      isRealExperiment,
      auditStatus: text(audit.integrity_status),
      resultArtifactId: resultArtifact.id,
      revisionArtifactId: revisionArtifact?.id ?? "",
      revisionReason: text(revision.required_revision, revision.selection_reason, revision.revision_reason, result.diagnosis && isRecord(result.diagnosis) ? result.diagnosis.repair_action : ""),
      scientificFeedback: text(revision.feedback, revision.next_action, revision.summary),
      completedAt: text(result.end_time, resultArtifact.created_at),
      metricDirections: metricMetadata.directions,
      metricContracts: metricMetadata.contracts,
      declaredMetrics: evaluations.map((item) => text(item.metric, item.name)).filter(Boolean),
      epochSeries: epochSeriesFrom(result.epoch_metrics ?? result.training_history),
      epochSeriesBySeed: epochSeriesBySeedFrom(result),
    });
  }
  // Candidate artifacts are written during code-generation validation, before
  // an executable task exists.  Surface a single engineering record so a
  // rejected design is visible immediately instead of appearing as an empty
  // experiment timeline.
  const taskIds = new Set(tasks.map((item) => text(item.content.experiment_id)).filter(Boolean));
  const pendingCandidates = new Map<string, Artifact[]>();
  for (const candidate of candidateAttempts) {
    const manifest = isRecord(candidate.content.manifest) ? candidate.content.manifest : {};
    const id = text(manifest.experiment_id, candidate.content.experiment_id);
    if (!id || taskIds.has(id)) continue;
    pendingCandidates.set(id, [...(pendingCandidates.get(id) ?? []), candidate]);
  }
  for (const [id, candidates] of pendingCandidates) {
    const latestCandidate = candidates[candidates.length - 1].content;
    const manifest = isRecord(latestCandidate.manifest) ? latestCandidate.manifest : {};
    const latestIssues = Array.isArray(latestCandidate.validation_issues) ? latestCandidate.validation_issues.map(stringify).filter(Boolean) : [];
    const accepted = latestCandidate.accepted === true;
    rows.push({
      id,
      title: "实验代码生成与校验",
      technicalName: "experiment_bundle_preflight",
      purpose: "正在生成可执行实验代码并校验数据集、指标和烟雾测试。",
      status: accepted ? "queued" : "failed",
      metrics: [], runtime: "—", provider: "本地代码生成", dataset: text(manifest.dataset) || "未声明",
      failureReason: latestIssues.join("；"), parameters: isRecord(manifest.parameters) ? manifest.parameters : {},
      seeds: Array.isArray(manifest.seeds) ? manifest.seeds.filter((item): item is string | number => typeof item === "string" || typeof item === "number") : [],
      environment: {}, log: "", logPath: "", metricsPath: "", deployedFiles: [],
      attempts: candidates.map((candidate, index) => ({
        id: text(candidate.content.attempt_id) || `candidate-${index + 1}`,
        status: candidate.content.accepted === true ? "accepted" : "rejected",
        startedAt: candidate.created_at, endedAt: candidate.created_at,
        error: Array.isArray(candidate.content.validation_issues) ? candidate.content.validation_issues.map(stringify).filter(Boolean).join("；") : "",
      })),
      classification: "engineering", isRealExperiment: false, auditStatus: "",
      resultArtifactId: "", revisionArtifactId: "", revisionReason: "", scientificFeedback: "",
      completedAt: candidates[candidates.length - 1].created_at,
      primaryMetricNames: [], metricDirections: {}, metricContracts: {}, declaredMetrics: [], epochSeries: [], epochSeriesBySeed: [],
    });
  }
  // Show the experiment as soon as its design step starts.  The durable task
  // artifact is intentionally created only after validation, so relying on it
  // alone made the bench look delayed for several minutes.
  if (run?.current_step === "experiment_task" && !rows.some((item) => item.status === "running")) {
    const id = progress?.experiment_id || `experiment_${tasks.length + pendingCandidates.size + 1}`;
    if (!rows.some((item) => item.id === id)) rows.push({
      id, title: "正在生成实验方案", technicalName: "experiment_design", purpose: "正在生成并校验可执行实验代码。",
      status: "running", metrics: [], runtime: "—", provider: "代码生成", dataset: "未声明", failureReason: "",
      parameters: {}, seeds: [], environment: {}, log: "", logPath: "", metricsPath: "", deployedFiles: [], attempts: [],
      classification: "engineering", isRealExperiment: false, auditStatus: "", resultArtifactId: "", revisionArtifactId: "",
      revisionReason: "", scientificFeedback: "", completedAt: "", primaryMetricNames: [], metricDirections: {}, metricContracts: {}, declaredMetrics: [], epochSeries: [], epochSeriesBySeed: [],
    });
  }
  return attachEvolution(rows);
}

function normalizeScientificFindings(artifacts: Artifact[], experiments: ExperimentItem[]): ScientificFindings | undefined {
  const current = [...experiments].reverse().find((item) => item.classification === "scientific" && item.status === "completed");
  if (!current) return undefined;
  const result = artifacts.find((item) => item.id === current.resultArtifactId)?.content ?? {};
  const analysis = isRecord(result.analysis) ? result.analysis : {};
  const conclusion = findLatestArtifactContent(artifacts, "scientific_conclusion") ?? {};
  const comparisons = records(analysis.comparisons);
  const comparison = comparisons.find((item) => text(item.metric) === current.primaryMetric?.name) ?? comparisons.find((item) => /accuracy|准确/i.test(text(item.metric)));
  const baselineValue = comparison ? number(comparison.baseline_value) : undefined;
  const variantValue = comparison ? number(comparison.variant_value) : undefined;
  const comparisonDelta = comparison ? number(comparison.difference) : undefined;
  const cnnParameters = number(isRecord(result.metrics) ? result.metrics["CNN Parameters"] : undefined);
  const mlpParameters = number(isRecord(result.metrics) ? result.metrics["MLP Parameters"] : undefined);
  const parameterDifference = number(isRecord(result.metrics) ? result.metrics["Parameter Difference"] : undefined);
  const limitationValue = analysis.limitations ?? conclusion.limitations;
  const limitations = Array.isArray(limitationValue) ? limitationValue.map(stringify).filter(Boolean).map(presentScientificLimitation).slice(0, 3) : [];
  return {
    hypothesisStatus: text(conclusion.hypothesis_status, analysis.verdict),
    conclusion: text(conclusion.current_conclusion, conclusion.conclusion, analysis.summary),
    primaryMetric: current.primaryMetric ? { name: current.primaryMetric.name, value: current.primaryMetric.value } : undefined,
    comparison: comparison && baselineValue !== undefined && variantValue !== undefined && comparisonDelta !== undefined ? {
      baseline: text(comparison.baseline), variant: text(comparison.variant),
      baselineValue, variantValue, delta: comparisonDelta,
    } : undefined,
    seedCount: current.seeds.length,
    parameterSummary: cnnParameters !== undefined && mlpParameters !== undefined
      ? `CNN ${cnnParameters.toLocaleString()} · MLP ${mlpParameters.toLocaleString()}${parameterDifference !== undefined ? `（差 ${parameterDifference.toLocaleString()}）` : ""}` : "",
    auditStatus: current.auditStatus,
    limitations,
  };
}

function normalizeConclusion(artifacts: Artifact[], report: Record<string, unknown> | null) {
  const artifact = findLatestArtifact(artifacts, "report")?.content ?? {};
  const revision = findLatestArtifactContent(artifacts, "revision") ?? {};
  const source = report ?? artifact;
  const conclusion = text(
    source["Research Conclusion"],
    source.final_conclusion,
    source.conclusion,
    source.executive_summary,
    source["Paper Abstract"],
    revision.conclusion,
    revision.summary,
  );
  const boundaryValue = source.Limitations ?? source.conclusion_boundary ?? source.limitations ?? revision.limitations;
  const allBoundaries = Array.isArray(boundaryValue)
    ? boundaryValue.map(stringify).filter(Boolean)
    : text(boundaryValue) ? [text(boundaryValue)] : [];
  const chineseBoundaries = allBoundaries.filter((item) => /[\u3400-\u9fff]/.test(item));
  const boundaries = chineseBoundaries.length ? chineseBoundaries : allBoundaries;
  const narrativeSections = records(source["Narrative Sections"]);
  const sections = narrativeSections.length
    ? narrativeSections.map((item) => text(item.title, item.id)).filter(Boolean).slice(0, 8)
    : Object.keys(source).filter((key) => !["final_conclusion", "conclusion", "conclusion_boundary", "limitations"].includes(key)).slice(0, 8);
  return { conclusion, boundaries, sections };
}

function buildResearchMap(
  question: string,
  papers: PaperItem[],
  hypotheses: HypothesisItem[],
  experiments: ExperimentItem[],
  conclusion: string,
  synthesis: ResearchViewModel["researchSynthesis"],
  reasoningAvailable: boolean,
  selectedHypothesis: HypothesisItem | undefined,
  plan: Record<string, unknown>,
) {
  const nodes: TreeNode[] = [];
  const edges: TreeEdge[] = [];

  // Layout: a horizontal central axis carries the main flow, with the candidate
  // fork and the experiment chain mirrored above/below it.  Candidates fan from
  // 假设生成 in two symmetric rows (first half above, second half below) and
  // rejoin at 推理; experiments form a rightward serpentine — odd rounds on the
  // upper side, even rounds mirrored below — ending at the conclusion.
  const nodeH = 108;
  const originX = 28;
  const originY = 34;
  const stepX = 240;   // horizontal step between columns
  const band = 142;    // vertical offset of candidate/experiment rows from the axis
  const literatureCount = synthesis.available ? synthesis.paperCount : papers.length;
  const scientificExperiments = experiments.filter((item) => item.classification === "scientific");

  // The axis sits at the vertical middle so the candidate column and the
  // serpentine stay symmetric above and below it, keeping the page height
  // bounded for any candidate count.
  const candidateGap = 140;
  const halfExtent = Math.max(
    hypotheses.length ? (hypotheses.length - 1) / 2 * candidateGap + nodeH / 2 : 0,
    scientificExperiments.length ? band + nodeH / 2 : 0,
    nodeH / 2,
  );
  const axisY = originY + 48 + halfExtent;

  let colX = originX;
  const placeAxis = (node: Omit<TreeNode, "x" | "y">): TreeNode => {
    const placed: TreeNode = { ...node, x: colX, y: axisY - nodeH / 2 };
    nodes.push(placed);
    colX += stepX;
    return placed;
  };

  // --- 1. Main flow along the horizontal axis ---
  const questionNode = placeAxis({ id: "Q1", kind: "Q", title: short(question || "尚未开始研究", 54), status: question ? "ready" : "empty" });
  const literatureNode = placeAxis({ id: "LITERATURE", kind: "L", title: `文献检索 · ${literatureCount} 篇`, status: literatureCount ? "completed" : "empty", detail: synthesis.available ? `共 ${synthesis.paperCount} 篇已验证文献` : "Provenance unavailable for this historical run." });
  const hypothesisNode = placeAxis({ id: "HYPOTHESES", kind: "H", title: `假设生成 · ${hypotheses.length}`, status: hypotheses.length ? "completed" : "empty", detail: hypotheses.length ? `已生成 ${hypotheses.length} 个候选假设` : "等待生成候选假设" });
  edges.push({ from: questionNode.id, to: literatureNode.id, label: "", tone: "neutral" });
  edges.push({ from: literatureNode.id, to: hypothesisNode.id, label: "", tone: "neutral" });

  const hypothesisNodeStatus = (item: HypothesisItem): ResearchStatus =>
    item.status === "selected" ? "completed"
      : item.status === "refuted" || item.status === "rejected" ? "refuted"
      : item.status === "evidence_insufficient" ? "evidence_insufficient"
      : item.status === "revision_required" ? "revision_required"
      : "ready";

  // --- 2. Candidate fork: a single vertical column centered on the axis — one
  // node per candidate, symmetric for any count (odd: middle candidate sits on
  // the axis; even: axis falls between the two middle candidates); no links
  // between candidates ---
  const hasSelection = hypotheses.some((item) => item.status === "selected");
  const candidates: TreeNode[] = hypotheses.map((item, index) => {
    const isSelected = item.status === "selected";
    const score = typeof item.compositeScore === "number" ? ` · 评分 ${item.compositeScore.toFixed(2)}` : "";
    return {
      id: item.id,
      kind: "H" as const,
      title: short(item.claim || "候选假设内容待生成", 42),
      status: hypothesisNodeStatus(item),
      x: colX,
      y: axisY + (index - (hypotheses.length - 1) / 2) * candidateGap - nodeH / 2,
      detail: isSelected ? `已选择${score}` : `候选假设${score}`,
      emphasis: isSelected ? "selected" : hasSelection ? "muted" : undefined,
    };
  });
  nodes.push(...candidates);
  for (const candidate of candidates) edges.push({ from: hypothesisNode.id, to: candidate.id, label: "", tone: "neutral" });
  colX += stepX;

  // --- 3. Rejoin on the axis: 推理 → 选择 → 计划 ---
  const reasoningNode = placeAxis({ id: "REASONING", kind: "V", title: "假设推理完成", status: reasoningAvailable ? "completed" : "empty", detail: reasoningAvailable ? "已对每个候选完成证据推理与评分" : "等待证据推理" });
  for (const candidate of candidates) edges.push({ from: candidate.id, to: reasoningNode.id, label: "", tone: "neutral" });
  const selectionNode = placeAxis({
    id: "SELECTION", kind: "S",
    title: selectedHypothesis ? `已选择 · ${selectedHypothesis.id} · 综合评分 ${selectedHypothesis.compositeScore?.toFixed(2) ?? "—"}` : "待选择假设",
    status: selectedHypothesis ? "completed" : (hypotheses.length ? "ready" : "empty"),
    detail: selectedHypothesis?.claim ?? "点击候选假设进行人工选择",
  });
  edges.push({ from: reasoningNode.id, to: selectionNode.id, label: "", tone: "neutral" });

  const planDataset = text((plan.dataset as Record<string, unknown> | undefined)?.display_name, (plan.dataset as Record<string, unknown> | undefined)?.name, (plan.dataset as Record<string, unknown> | undefined)?.directory_name) || "未声明";
  const planModel = short(text((plan.method as Record<string, unknown> | undefined)?.name) || "未设计", 40);
  const planSeedCount = Array.isArray(plan.seeds) ? plan.seeds.length : 0;
  const planNode = placeAxis({ id: "PLAN", kind: "P", title: `实验计划 · ${planDataset} · ${planModel}${planSeedCount ? ` · ${planSeedCount} seeds` : ""}`, status: Object.keys(plan).length ? "completed" : "empty", detail: "冻结数据集、方法、种子与评估协议" });
  edges.push({ from: selectionNode.id, to: planNode.id, label: "", tone: "neutral" });

  // --- 4. Experiment serpentine: odd rounds on the upper side, even rounds mirrored below ---
  const engineeringCountFor = (id: string) => experiments.filter((item) => item.classification === "engineering" && (item.id === id || item.id.startsWith(`${id}·`))).length;
  const experimentLabel = (item: ExperimentItem, index: number) => {
    const match = /experiment[_\-]?(\d+)/i.exec(item.id);
    return match ? `实验 ${match[1]}` : `实验 ${index + 1}`;
  };
  const expTopY = axisY - band - nodeH / 2;
  const expBottomY = axisY + band - nodeH / 2;
  let previous = planNode;
  scientificExperiments.forEach((item, index) => {
    const round = index + 1;
    const onTop = round % 2 === 1;
    const y = onTop ? expTopY : expBottomY;
    const engineering = engineeringCountFor(item.id);
    const experiment: TreeNode = { id: `EXP-${item.id}`, kind: "X", title: `${experimentLabel(item, index)}${engineering ? ` · 工程重试 ×${engineering}` : ""}`, status: item.status, x: colX, y, detail: `${item.title}${item.primaryMetric ? ` · ${item.primaryMetric.name} ${item.primaryMetric.value}` : ""}` };
    nodes.push(experiment);
    edges.push({ from: previous.id, to: experiment.id, label: "", tone: "neutral" });
    previous = experiment;
    if (index + 1 < scientificExperiments.length) {
      const feedback: TreeNode = { id: `FEEDBACK-${round}`, kind: "F", title: "反馈 / 修订", status: "completed", x: colX + stepX, y, detail: item.revisionReason || item.scientificFeedback || "上一轮结果的科学反馈" };
      nodes.push(feedback);
      edges.push({ from: experiment.id, to: feedback.id, label: "", tone: "neutral" });
      previous = feedback;
      colX += stepX;
    }
  });

  // --- 5. Conclusion on the axis after the last round ---
  if (conclusion) {
    const conclusionNode: TreeNode = { id: "CONCLUSION", kind: "C", title: short(conclusion, 58), status: "completed", x: previous.x + stepX, y: axisY - nodeH / 2 };
    nodes.push(conclusionNode);
    edges.push({ from: previous.id, to: conclusionNode.id, label: "", tone: "neutral" });
  }

  return { nodes, edges };
}

export function buildResearchViewModel(run: RunRecord | null, report: Record<string, unknown> | null, progress: ExperimentProgress | null): ResearchViewModel {
  const artifacts = run?.artifacts ?? [];
  const papers = normalizePapers(artifacts);
  const evidence = normalizeEvidence(artifacts);
  const hypotheses = normalizeHypotheses(artifacts);
  const hypothesisRounds = normalizeHypothesisRounds(artifacts);
  const researchSynthesis = normalizeResearchSynthesis(artifacts);
  const hypothesisEvent = [...(run?.events ?? [])].reverse().find((item) => (
    item.step_id === "hypothesis_generation"
    && typeof item.input_summary.valid_evidence_count === "number"
  ));
  const hypothesisPipeline = isRecord(hypothesisEvent?.input_summary.hypothesis_card_pipeline)
    ? hypothesisEvent.input_summary.hypothesis_card_pipeline
    : {};
  const experiments = normalizeExperiments(artifacts, progress, run);
  const { conclusion, boundaries, sections } = normalizeConclusion(artifacts, report);
  const question = text(run?.problem_input, findLatestArtifactContent(artifacts, "problem")?.problem_statement);
  const selectedForMap = hypotheses.find((item) => item.status === "selected");
  const reasoningAvailable = artifacts.some((item) => item.type === "reasoning");
  const plan = findLatestArtifactContent(artifacts, "plan") ?? {};
  const tree = buildResearchMap(question, papers, hypotheses, experiments, conclusion, researchSynthesis, reasoningAvailable, selectedForMap, plan);
  const currentExperiment = [...experiments].reverse().find((item) => item.classification === "scientific" && item.status === "completed")
    ?? experiments.find((item) => item.status === "running")
    ?? experiments[experiments.length - 1];
  const selectedHypothesis = hypotheses.find((item) => item.status === "selected") ?? hypotheses[hypotheses.length - 1];
  const environment = currentExperiment?.environment ?? {};
  const latestResult = artifacts.find((item) => item.id === currentExperiment?.resultArtifactId)?.content ?? {};
  const audit = isRecord(latestResult.audit) ? latestResult.audit : {};
  const verifiedCodeFile = records(audit.verified_files)[0] ?? {};
  const codeVersion = text(environment.git_commit, latestResult.git_commit, verifiedCodeFile.sha256);
  const manifest = isRecord(findLatestArtifactContent(artifacts, "experiment_task")?.manifest) ? findLatestArtifactContent(artifacts, "experiment_task")?.manifest as Record<string, unknown> : {};
  const githubArtifact = findLatestArtifactContent(artifacts, "github_source") ?? {};
  const githubStatus = text(githubArtifact.github_source_status) === "parsed" ? "parsed" as const : text(githubArtifact.github_source_status) === "unavailable" ? "unavailable" as const : "not_provided" as const;
  return {
    question,
    title: text(run?.title) || "未命名研究",
    runId: run?.id ?? "",
    status: statusForRun(run, progress),
    currentStage: text(run?.current_step).replace(/_/g, " ") || "尚未开始",
    papers,
    evidence,
    hypotheses,
    hypothesisRounds,
    experiments,
    selectedHypothesis,
    currentExperiment,
    scientificFindings: normalizeScientificFindings(artifacts, experiments),
    conclusion,
    boundaries,
    reportSections: sections,
    researchSynthesis,
    hypothesisLiterature: {
      retrievedCount: number(hypothesisEvent?.input_summary.synthesis_paper_count) ?? researchSynthesis.paperCount,
      inputCount: number(hypothesisEvent?.input_summary.valid_evidence_count) ?? 0,
      irrelevantRemoved: number(hypothesisPipeline.irrelevant_removed) ?? 0,
      duplicateMerged: number(hypothesisPipeline.duplicate_merged) ?? 0,
    },
    githubSource: {
      url: text(githubArtifact.repository_url, run?.github_repository_url),
      status: githubStatus,
      warning: Array.isArray(githubArtifact.warnings) ? githubArtifact.warnings.map(stringify).filter(Boolean).join("；") : "",
    },
    ...tree,
    updatedAt: text(currentExperiment?.environment.updated_at, run?.events[run.events.length - 1]?.timestamp) || "—",
    reproducibility: [
      { label: "数据集指纹", value: text(environment.dataset_fingerprint) || "未提供", ready: Boolean(text(environment.dataset_fingerprint)) },
      { label: "运行环境", value: [text(environment.python_version), text(environment.torch_version)].filter(Boolean).join(" · ") || "未记录", ready: Boolean(text(environment.python_version)) },
      { label: "随机种子", value: currentExperiment?.seeds.map(String).join(" / ") || stringify(manifest.seeds) || "未记录", ready: Boolean(currentExperiment?.seeds.length || manifest.seeds) },
      { label: "代码版本", value: codeVersion || "未记录", ready: Boolean(codeVersion) },
      { label: "完整性审计", value: text(audit.integrity_status) || "未执行", ready: text(audit.integrity_status) === "passed" },
      { label: "实验产物", value: Array.isArray(latestResult.deployed_files) ? `${latestResult.deployed_files.length} 个文件` : "未记录", ready: Array.isArray(latestResult.deployed_files) && latestResult.deployed_files.length > 0 },
    ],
    reportAvailable: Boolean(report) || artifacts.some((item) => item.type === "report"),
    codePackageAvailable: artifacts.some((item) => item.type === "experiment_bundle" && records(item.content.files).length > 0),
  };
}

export function comparableMetricSeries(experiments: ExperimentItem[]) {
  const successful = experiments.filter((item) => item.classification === "scientific" && item.status === "completed" && item.metrics.length);
  if (successful.length < 2) return null;
  const names = successful[0].metrics.map((item) => item.name).filter((name) => !/std|var|elapsed|time/i.test(name));
  const name = names.find((candidate) => successful.every((item) => item.metrics.some((metric) => metric.name === candidate)));
  if (!name) return null;
  const direction = successful.find((item) => item.primaryMetric?.name === name)?.primaryMetric?.direction ?? "unknown";
  return { name, direction, rows: successful.map((item) => ({ id: item.id, value: item.metrics.find((metric) => metric.name === name)!.value })) };
}

const COMMON_LOSS_METRICS = ["loss", "train_loss", "val_loss", "test_loss"];

/** Core metrics are the declared plan evaluation metrics plus common loss terms. */
export function coreMetricNames(experiments: ExperimentItem[]): Set<string> {
  const declared = [...new Set(experiments.flatMap((item) => item.declaredMetrics))].filter(Boolean);
  return new Set([...declared, ...COMMON_LOSS_METRICS]);
}

/**
 * Produces selector-ready, provenance-preserving series.  A series may only
 * connect points that share the persisted metric/evaluation and dataset contract.
 */
export function scientificMetricSeries(experiments: ExperimentItem[]): ScientificMetricSeries[] {
  const valid = experiments
    .filter((item) => item.classification === "scientific" && item.status === "completed" && item.isRealExperiment && item.auditStatus === "passed" && item.resultArtifactId)
    .slice()
    .sort((left, right) => left.completedAt.localeCompare(right.completedAt));
  const core = coreMetricNames(valid);
  // The main selector shows only declared evaluation metrics plus any common
  // loss metrics that actually exist; component-level diagnostics (e.g. KL per
  // latent dimension) remain available in the experiment detail drawer.
  const names = [...new Set(valid.flatMap((item) => item.metrics.map((metric) => metric.name)))].filter((name) => core.has(name)).slice(0, 6);
  const output: ScientificMetricSeries[] = [];
  for (const name of names) {
    const first = valid.find((item) => item.metrics.some((metric) => metric.name === name));
    if (!first) continue;
    const contract = first.metricContracts[name];
    const direction = first.metricDirections[name] ?? "unknown";
    const resultRows = valid.filter((item) => item.metricContracts[name] === contract).map((item) => ({
      experimentId: item.id,
      artifactId: item.resultArtifactId,
      value: item.metrics.find((metric) => metric.name === name)!.value,
    }));
    const rows: ScientificMetricSeries["rows"] = resultRows.map((row, index, all) => {
      const previous = all[index - 1];
      if (!previous || direction === "unknown") return { ...row, trend: "not_comparable" as const };
      const rawDelta = row.value - previous.value;
      const delta = direction === "lower" ? -rawDelta : rawDelta;
      return { ...row, previousValue: previous.value, delta, trend: Math.abs(delta) < 1e-12 ? "unchanged" as const : delta > 0 ? "improved" as const : "declined" as const };
    });
    if (rows.length) output.push({ name, direction, rows });
  }
  return output;
}

export function evolutionLabel(item: ExperimentItem) {
  if (!item.evolution || item.evolution.status === "not_comparable") return "无可比较基线";
  if (item.evolution.status === "unchanged") return "与基线持平";
  return `${item.evolution.status === "improved" ? "相对基线改善" : "相对基线下降"} ${formatMetricValue(Math.abs(item.evolution.delta))}`;
}

export function formatDuration(seconds: number | undefined) {
  if (seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "—";
  const whole = Math.ceil(seconds);
  return [Math.floor(whole / 3600), Math.floor((whole % 3600) / 60), whole % 60].map((value) => String(value).padStart(2, "0")).join(":");
}

export function formatMetricName(name: string) {
  return name.replace(/_/g, " ").replace(/\bmean\b/gi, "μ").replace(/\bstd\b/gi, "σ");
}

export function formatMetricValue(value: number) {
  const absolute = Math.abs(value);
  if (absolute > 0 && absolute < 0.001) return value.toExponential(2);
  if (absolute >= 100) return value.toFixed(1);
  if (absolute >= 1) return value.toFixed(2);
  return value.toFixed(4);
}
