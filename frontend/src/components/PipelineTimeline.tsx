import type { ComponentType } from "react";
import type { Artifact, ExperimentProgress } from "../api/types";
import { findLatestArtifact, hasSuccessfulExperimentResult, latestExperimentDiagnosis, latestExperimentResultFailure } from "../utils/presentation";
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  ClipboardList,
  FileText,
  FlaskConical,
  GitBranch,
  Network,
  RotateCcw,
  SearchCheck,
  Sparkles,
  TriangleAlert,
} from "lucide-react";

type StepConfig = {
  id: string;
  label: string;
  outputType: string;
  requiredTypes: string[];
  prerequisite: string;
  icon: ComponentType<{ size?: number; className?: string }>;
};

type VisualState = "current" | "completed" | "waiting" | "needs-revision" | "failed";

const STEPS: StepConfig[] = [
  {
    id: "problem_understanding",
    label: "问题理解",
    outputType: "problem",
    requiredTypes: [],
    prerequisite: "研究主题",
    icon: ClipboardList,
  },
  {
    id: "knowledge_integration",
    label: "文献与知识整合",
    outputType: "evidence",
    requiredTypes: ["problem"],
    prerequisite: "问题理解",
    icon: BookOpen,
  },
  {
    id: "hypothesis_generation",
    label: "假设生成",
    outputType: "hypothesis",
    requiredTypes: ["problem", "evidence"],
    prerequisite: "问题与文献证据",
    icon: GitBranch,
  },
  {
    id: "evidence_reasoning",
    label: "证据推理",
    outputType: "reasoning",
    requiredTypes: ["hypothesis", "evidence"],
    prerequisite: "候选假设与文献证据",
    icon: SearchCheck,
  },
  {
    id: "research_plan",
    label: "研究计划",
    outputType: "plan",
    requiredTypes: ["reasoning"],
    prerequisite: "证据推理",
    icon: ClipboardList,
  },
  {
    id: "experiment_task",
    label: "实验任务",
    outputType: "experiment_task",
    requiredTypes: ["plan"],
    prerequisite: "研究计划",
    icon: FlaskConical,
  },
  {
    id: "experiment_run_analysis",
    label: "实验运行与分析",
    outputType: "experiment_result",
    requiredTypes: ["experiment_task"],
    prerequisite: "实验任务",
    icon: Sparkles,
  },
  {
    id: "feedback_revision",
    label: "反馈修订",
    outputType: "revision",
    requiredTypes: ["experiment_result", "hypothesis"],
    prerequisite: "实验结果",
    icon: Network,
  },
  {
    id: "report_export",
    label: "报告导出",
    outputType: "report",
    requiredTypes: ["evidence", "experiment_result", "revision"],
    prerequisite: "反馈修订",
    icon: FileText,
  },
];

const STAGE_GROUPS = [
  { label: "研究定义", stepIds: ["problem_understanding", "knowledge_integration"] },
  { label: "假设与证据", stepIds: ["hypothesis_generation", "evidence_reasoning"] },
  { label: "计划与实验", stepIds: ["research_plan", "experiment_task", "experiment_run_analysis"] },
];

type Props = {
  runId?: string;
  artifacts: Artifact[];
  activeStepId: string | null;
  failedStepId: string | null;
  isBusy: boolean;
  experimentProgress: ExperimentProgress | null;
  onRerunFrom: (stepId: string) => void;
};

function artifactRecords(artifact: Artifact | undefined, key: string) {
  const value = artifact?.content[key];
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item)),
  );
}

function isVerifiedReference(reference: Record<string, unknown>) {
  const statuses = Array.isArray(reference.statuses) ? reference.statuses.map(String) : [];
  const verification = reference.verification;
  return reference.verified === true
    || statuses.includes("verified")
    || Boolean(verification && typeof verification === "object" && (verification as Record<string, unknown>).verified === true);
}

const stateLabels: Record<VisualState, string> = {
  current: "正在执行",
  completed: "已完成",
  waiting: "等待中",
  "needs-revision": "需要修订",
  failed: "执行失败",
};

export function PipelineTimeline({
  runId,
  artifacts,
  activeStepId,
  failedStepId,
  isBusy,
  experimentProgress,
  onRerunFrom,
}: Props) {
  const artifactTypes = new Set(artifacts.map((artifact) => artifact.type));
  const evidenceArtifact = findLatestArtifact(artifacts, "evidence");
  const hypothesisArtifact = findLatestArtifact(artifacts, "hypothesis");
  const reasoningArtifact = findLatestArtifact(artifacts, "reasoning");
  const selectionArtifact = findLatestArtifact(artifacts, "hypothesis_selection");
  const planArtifact = findLatestArtifact(artifacts, "plan");
  const experimentArtifact = findLatestArtifact(artifacts, "experiment_task");
  const resultArtifact = findLatestArtifact(artifacts, "experiment_result");
  const revisionArtifact = findLatestArtifact(artifacts, "revision");
  const reportArtifact = findLatestArtifact(artifacts, "report");

  const references = artifactRecords(evidenceArtifact, "references");
  const verifiedLiteratureCount = references.filter(isVerifiedReference).length;
  const candidateCount = artifactRecords(hypothesisArtifact, "candidates").length;
  const assessments = artifactRecords(reasoningArtifact, "candidate_assessments");
  const reasonedCount = assessments.length;
  const revisedCount = assessments.filter((assessment) => assessment.was_revised === true).length;
  const feedbackIteration = Number(revisionArtifact?.content.iteration ?? 0);
  const requiresFollowUp = revisionArtifact?.content.requires_follow_up === true;
  const liveExperimentRunning = experimentProgress?.process_alive === true
    && ["running", "orphaned", "stalled"].includes(experimentProgress.state);
  const experimentFailure = liveExperimentRunning ? null : latestExperimentResultFailure(artifacts);
  const experimentDiagnosis = liveExperimentRunning ? null : latestExperimentDiagnosis(artifacts);
  const hasSatisfiedOutput = (type: string) => (
    type === "experiment_result" ? hasSuccessfulExperimentResult(artifacts) : artifactTypes.has(type)
  );

  const summaries: Record<string, string> = {
    problem_understanding: artifactTypes.has("problem") ? "研究问题已结构化" : "等待解析研究问题",
    knowledge_integration: evidenceArtifact ? `已核验文献 ${verifiedLiteratureCount} 篇` : "等待检索与核验文献",
    hypothesis_generation: hypothesisArtifact ? `候选假设 ${candidateCount} 个` : "等待生成候选假设",
    evidence_reasoning: reasoningArtifact
      ? selectionArtifact
        ? `已推理 ${reasonedCount} 个 · 用户已选择`
        : `已推理 ${reasonedCount} 个 · 等待用户选择`
      : "等待逐项证据推理",
    research_plan: planArtifact ? `研究计划 v${planArtifact.version}` : "等待制定研究计划",
    experiment_task: experimentArtifact ? `实验任务 v${experimentArtifact.version}` : "等待生成实验任务",
    experiment_run_analysis: liveExperimentRunning
      ? experimentProgress?.state === "running" ? "当前实验正在运行" : "实验仍在运行，但后台已无法继续跟踪"
      : experimentFailure
      ? String(experimentDiagnosis?.user_message || "实验失败，诊断专家已记录原因")
      : resultArtifact ? `实验结果 v${resultArtifact.version}` : "等待运行并分析实验",
    feedback_revision: revisionArtifact ? `第 ${feedbackIteration} 轮 · ${requiresFollowUp ? "需要继续验证" : "修订完成"}` : "等待结果反馈",
    report_export: reportArtifact ? "报告已生成" : "等待报告",
  };

  function visualState(step: StepConfig): VisualState {
    if (step.id === "experiment_run_analysis" && liveExperimentRunning) return "current";
    if (activeStepId === step.id) return "current";
    if (failedStepId === step.id) return "failed";
    if (step.id === "experiment_run_analysis" && experimentFailure) return "failed";
    if (step.id === "feedback_revision" && revisionArtifact && requiresFollowUp) return "needs-revision";
    if (hasSatisfiedOutput(step.outputType)) return "completed";
    return "waiting";
  }

  function renderNode(step: StepConfig) {
    const Icon = step.icon;
    const state = visualState(step);
    const missingPrerequisites = step.requiredTypes.filter((type) => !hasSatisfiedOutput(type));
    const canRerun = Boolean(runId) && missingPrerequisites.length === 0 && activeStepId === null && !isBusy;
    const rerunTitle = isBusy ? "科研 Pipeline 正在运行" : canRerun ? `从这里重新运行：${step.label}` : `需先完成：${step.prerequisite}`;

    return (
      <article className={`pipeline-node state-${state}`} key={step.id} aria-live={state === "current" ? "polite" : undefined}>
        <div className="pipeline-node-heading">
          <span className="pipeline-node-icon"><Icon size={19} /></span>
          <strong>{step.label}</strong>
          {state === "completed" ? <CheckCircle2 className="pipeline-state-icon" size={17} /> : null}
          {state === "failed" ? <TriangleAlert className="pipeline-state-icon" size={17} /> : null}
        </div>
        <p>{summaries[step.id]}</p>
        <div className="pipeline-node-footer">
          <span className="pipeline-state-label">{stateLabels[state]}</span>
          <button className="pipeline-rerun" disabled={!canRerun} onClick={() => onRerunFrom(step.id)} title={rerunTitle}>
            <RotateCcw size={13} /> 重跑
          </button>
        </div>
      </article>
    );
  }

  const feedbackStep = STEPS.find((step) => step.id === "feedback_revision")!;
  const reportStep = STEPS.find((step) => step.id === "report_export")!;

  return (
    <section className="panel section-card pipeline-card">
      <div className="pipeline-header">
        <div>
          <span className="eyebrow">AUTOMATIC RESEARCH PIPELINE</span>
          <h2>自动科研 Pipeline</h2>
        </div>
        <span className="pipeline-run-state">
          {activeStepId ? `正在执行：${STEPS.find((step) => step.id === activeStepId)?.label ?? activeStepId}` : "数据流自动衔接"}
        </span>
      </div>

      <div className="pipeline-stage-track">
        {STAGE_GROUPS.map((group, groupIndex) => (
          <div className="pipeline-stage-wrap" key={group.label}>
            <section className="pipeline-stage-group">
              <h3>{group.label}</h3>
              <div className="pipeline-stage-nodes">
                {group.stepIds.map((stepId, nodeIndex) => {
                  const step = STEPS.find((item) => item.id === stepId)!;
                  return (
                    <div className="pipeline-node-wrap" key={stepId}>
                      {renderNode(step)}
                      {nodeIndex < group.stepIds.length - 1 ? (
                        <span className="pipeline-connector" aria-hidden="true"><ArrowRight size={18} /></span>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            </section>
            {groupIndex < STAGE_GROUPS.length - 1 ? (
              <span className="pipeline-connector stage-connector" aria-hidden="true"><ArrowRight size={20} /></span>
            ) : null}
          </div>
        ))}
      </div>

      <section className="pipeline-feedback-loop">
        <div className="feedback-loop-copy">
          <span className="feedback-iteration">{revisionArtifact ? `第 ${feedbackIteration} 轮验证` : "等待首轮反馈"}</span>
          <strong>实验反馈闭环</strong>
          <p>实验结果进入反馈修订；需要补充证据时，修订后的研究计划回流到实验任务并启动下一轮验证。</p>
        </div>
        <div className="feedback-loop-node">{renderNode(feedbackStep)}</div>
        <div className="feedback-loop-arrow" aria-label="反馈回流至研究计划或实验任务">
          <RotateCcw size={22} />
          <span>回流至研究计划 / 实验任务</span>
        </div>
        <span className="pipeline-connector feedback-to-report" aria-hidden="true"><ArrowRight size={20} /></span>
        <div className="pipeline-report-node">{renderNode(reportStep)}</div>
      </section>
    </section>
  );
}
