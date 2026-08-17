import { Check, FlaskConical, Lightbulb, Play, Route, ShieldCheck } from "lucide-react";
import type { Artifact, ExperimentProgress, RunRecord } from "../api/types";
import { ExperimentPanel } from "./ExperimentPanel";
import { HypothesisBoard } from "./HypothesisBoard";
import { ResearchSidebar, type ResearchView } from "./ResearchSidebar";
import { findLatestArtifactContent } from "../utils/presentation";

type Props = {
  view: Exclude<ResearchView, "atlas">; run: RunRecord | null;
  topic: { domain: string; problem: string; constraints: string }; activeStepId: string | null; failedStepId: string | null;
  running: boolean; progress: ExperimentProgress | null; onNavigate: (view: ResearchView) => void; onSettings: () => void;
  onAddUserHypothesis: (claim: string) => Promise<void>; onSelectHypothesis: (candidateIndex: number) => Promise<void>;
  onRerunFrom: (stepId: string) => void; onContinuePipeline: () => void; onStopPipeline: () => void; pipelineStopRequested: boolean;
};
const records = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item))) : [];
const text = (value: unknown, fallback: string) => typeof value === "string" && value.trim() ? value.trim() : fallback;

function PageHeading({ view, topic, running }: Pick<Props, "view" | "topic" | "running">) {
  const experiment = view === "experiments";
  return <header className="notebook-heading">
    <div><p>{experiment ? "实验台" : "假设推演"}<span>{experiment ? "Experiment Bench" : "Hypothesis Reasoning"}</span></p><small>{experiment ? "从实验设计到可审计结果，确保实验可重复、可追踪。" : "从证据出发，生成并选择可执行的研究假设。"}</small></div>
    <aside className="heading-step-note"><b>当前步骤：{experiment ? "设计实验" : "提出假设"}</b><span>{running ? "系统正在执行当前研究步骤。" : text(topic.problem, "基于现有证据生成可验证的研究路径。")}</span></aside>
  </header>;
}

function HypothesisEvidence({ artifacts }: { artifacts: Artifact[] }) {
  const evidence = records(findLatestArtifactContent(artifacts, "evidence")?.references).slice(0, 6);
  return <aside className="notebook-evidence-column"><header><span>A</span><strong>证据笔记</strong></header><p>已收集的文献与知识证据</p><div className="notebook-evidence-list">{evidence.length ? evidence.map((item, index) => <article key={`${String(item.title)}-${index}`}><b>{text(item.title, "已核验研究文献")}</b><small>{text(item.year, "来源待标注")}</small><em>{index < 2 ? "支持" : "中性"}</em></article>) : <div className="notebook-empty-note">等待文献与知识证据</div>}</div><footer><Lightbulb size={15} />证据将在推理时引用</footer></aside>;
}

function HypothesisNotes({ artifacts, running }: { artifacts: Artifact[]; running: boolean }) {
  const reasoning = findLatestArtifactContent(artifacts, "reasoning");
  const assessments = records(reasoning?.candidate_assessments);
  return <aside className="notebook-decision-column"><section><header><span>C</span><strong>证据推理</strong></header><p>为候选主张提供可追溯判断。</p><ul>{assessments.length ? assessments.slice(0, 3).map((item, index) => <li key={index}><Check size={14} />{text(item.reasoning, "证据推理已记录")}</li>) : <><li><Check size={14} />可检验、可证伪</li><li><Check size={14} />证据来源可追溯</li><li><Check size={14} />方法和数据可执行</li></>}</ul></section><section className="decision-paper"><strong>{running ? "推理进行中" : "研究者决策"}</strong><p>{running ? "系统正在比较证据与候选解释。" : "选择一个主假设后，即可进入实验设计。"}</p><span>每次选择均会保留在研究账本中。</span></section></aside>;
}

function ExperimentMeta({ artifacts, progress, failedStepId }: { artifacts: Artifact[]; progress: ExperimentProgress | null; failedStepId: string | null }) {
  const plan = findLatestArtifactContent(artifacts, "plan") ?? {};
  const task = findLatestArtifactContent(artifacts, "experiment_task") ?? {};
  return <aside className="bench-meta-column"><section><header><FlaskConical size={18} /><strong>实验执行状态</strong></header><dl><div><dt>状态</dt><dd>{progress?.state ?? (failedStepId ? "needs_recovery" : "等待实验设计")}</dd></div><div><dt>运行次数</dt><dd>{Array.isArray(task.attempts) ? task.attempts.length : 0} 次</dd></div><div><dt>计算资源</dt><dd>{text(task.provider, "local_gpu / remote_gpu")}</dd></div><div><dt>数据集</dt><dd>{text((plan.dataset as Record<string, unknown> | undefined)?.name, "未声明")}</dd></div></dl></section><section className="bench-tip"><ShieldCheck size={16} /><p>只有真实执行完成且审计通过的结果，才会进入科学判断。</p></section></aside>;
}

function ExperimentPrelude({ artifacts, progress }: { artifacts: Artifact[]; progress: ExperimentProgress | null }) {
  const task = findLatestArtifactContent(artifacts, "experiment_task") ?? {};
  return <section className="bench-prelude"><span>1<br/><small>冻结协议</small></span><i>→</i><span className={progress?.process_alive ? "active" : ""}>2<br/><small>执行与监测</small></span><i>→</i><span>3<br/><small>{Object.keys(task).length ? "结果审计" : "等待实验任务"}</small></span></section>;
}

export function ResearchStudio({ view, run, topic, activeStepId, failedStepId, running, progress, onNavigate, onSettings, onAddUserHypothesis, onSelectHypothesis, onRerunFrom, onContinuePipeline, onStopPipeline, pipelineStopRequested }: Props) {
  const artifacts = run?.artifacts ?? [];
  const hypotheses = view === "hypotheses";
  return <main className={`notebook-shell ${hypotheses ? "hypothesis-notebook" : "experiment-bench"}`}>
    <ResearchSidebar activeView={view} run={run} running={running} onNavigate={onNavigate} onSettings={onSettings} />
    <section className="notebook-page"><PageHeading view={view} topic={topic} running={running} />
      {hypotheses ? <section className="hypothesis-desk"><HypothesisEvidence artifacts={artifacts} /><section className="hypothesis-candidates"><header><span>B</span><div><strong>候选假设</strong><small>基于左侧证据生成的候选解释（可选择一个执行）</small></div></header><HypothesisBoard artifacts={artifacts} runId={run?.id} activeStepId={activeStepId} isBusy={running} onAddUserHypothesis={onAddUserHypothesis} onSelectHypothesis={onSelectHypothesis} /><footer className="desk-flow-note">选择一个候选假设 <i>→</i> 进入实验设计</footer></section><HypothesisNotes artifacts={artifacts} running={running} /></section> : <section className="experiment-desk"><ExperimentPrelude artifacts={artifacts} progress={progress} /><div className="experiment-body"><section className="experiment-primary"><ExperimentPanel artifacts={artifacts} runId={run?.id} isBusy={running} onRerunFrom={onRerunFrom} onContinuePipeline={onContinuePipeline} automaticPipelineRunning={running} pipelineStopRequested={pipelineStopRequested} onStopPipeline={onStopPipeline} progress={progress} /></section><ExperimentMeta artifacts={artifacts} progress={progress} failedStepId={failedStepId} /></div></section>}
    </section>
  </main>;
}
