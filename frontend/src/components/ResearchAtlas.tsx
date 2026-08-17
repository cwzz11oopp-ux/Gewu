import { ArrowUpRight, Check, FlaskConical, NotebookPen, Play, TriangleAlert } from "lucide-react";
import type { Artifact, ExperimentProgress, RunRecord } from "../api/types";
import { findLatestArtifactContent } from "../utils/presentation";
import { ResearchSidebar, type ResearchView } from "./ResearchSidebar";

type Props = {
  run: RunRecord | null;
  topic: { domain: string; problem: string; constraints: string };
  activeStepId: string | null;
  failedStepId: string | null;
  running: boolean;
  progress: ExperimentProgress | null;
  onNavigate: (view: ResearchView) => void;
  onProblemChange: (problem: string) => void;
  onStart: () => void;
  onContinue: () => void;
  onSettings: () => void;
  onCreate: () => void;
};

const stages = [
  ["problem_understanding", "定义问题", "结构化研究范围"],
  ["knowledge_integration", "检索证据", "验证可追溯文献"],
  ["hypothesis_generation", "提出假设", "生成可验证方向"],
  ["research_plan", "设计实验", "冻结控制变量"],
  ["experiment_run_analysis", "执行实验", "运行与审计结果"],
  ["report_export", "得出结论", "导出受限报告"],
] as const;

const artifactFor: Record<string, string> = {
  problem_understanding: "problem", knowledge_integration: "evidence", hypothesis_generation: "hypothesis",
  research_plan: "plan", experiment_run_analysis: "experiment_result", report_export: "report",
};

const asRecords = (value: unknown): Record<string, unknown>[] => Array.isArray(value)
  ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object" && !Array.isArray(item))) : [];
const value = (input: unknown, fallback: string) => typeof input === "string" && input.trim() ? input.trim() : fallback;

function statusFor(id: string, run: RunRecord | null, active: string | null, failed: string | null) {
  if (id === active) return "active";
  if (id === failed) return "failed";
  return run?.artifacts.some((item) => item.type === artifactFor[id]) ? "complete" : "waiting";
}

export function ResearchAtlas({ run, topic, activeStepId, failedStepId, running, progress, onNavigate, onProblemChange, onStart, onContinue, onSettings, onCreate }: Props) {
  const artifacts = run?.artifacts ?? [];
  const evidence = asRecords(findLatestArtifactContent(artifacts, "evidence")?.references).slice(0, 3);
  const candidates = asRecords(findLatestArtifactContent(artifacts, "hypothesis")?.candidates);
  const selected = asRecords(findLatestArtifactContent(artifacts, "hypothesis_selection")?.selected);
  const plan = findLatestArtifactContent(artifacts, "plan") ?? {};
  const decision = findLatestArtifactContent(artifacts, "iteration_decision") ?? {};
  const claim = value(selected[0]?.claim ?? candidates[0]?.claim, "等待系统生成可验证假设");
  const activeIndex = Math.max(0, stages.findIndex(([id]) => id === activeStepId));
  const workflowProgress = running ? Math.max(12, (activeIndex + 1) / stages.length * 100) : 0;
  const canRun = Boolean(topic.problem.trim()) && !running;
  const primaryAction = run ? onContinue : onStart;

  return <main className="atlas-shell">
    <ResearchSidebar activeView="atlas" run={run} running={running} onNavigate={onNavigate} onSettings={onSettings} />
    <section className="atlas-page">
      <header className="atlas-topbar">
        <div><strong>格物（GEWU） AI 科学家</strong><span>科研过程自动化平台 · V1.5</span></div>
        <button className="atlas-new-research" disabled={running} onClick={onCreate}>＋ 开始新研究</button>
      </header>
      <div className="atlas-heading">
        <div><p>研究地图 <span>Research Atlas</span></p>{run ? <h1>{value(topic.problem, "从一个清晰的问题开始研究")}</h1> : <textarea className="atlas-question-editor" aria-label="研究问题" value={topic.problem} onChange={(event) => onProblemChange(event.target.value)} placeholder="输入你想验证的研究问题…" />}<small>围绕当前假设，整合证据、推断与下一步实验，形成可执行的研究路径。</small><div className="atlas-legend"><span className="support">证据支持</span><span className="conflict">证据冲突</span><span className="method">方法/数据</span><span className="pending">待验证</span></div></div>
        <section className={running ? "atlas-now running" : "atlas-now"}><span>当前状态</span><strong>{progress?.process_alive ? "实验正在执行" : running ? `正在${stages[activeIndex]?.[1]}` : "等待开始"}</strong><div><i style={{ width: `${workflowProgress}%` }} /></div></section>
        <button className="atlas-primary" disabled={!canRun} onClick={primaryAction}><Play size={16} fill="currentColor" />{run ? "运行下一步" : "开始研究"}<ArrowUpRight size={18} /></button>
      </div>
      <div className={running ? "atlas-canvas is-running" : "atlas-canvas"}>
        <svg className="atlas-links" viewBox="0 0 1000 620" preserveAspectRatio="none" aria-hidden="true">
          <path className="atlas-link support" d="M172 160 C305 135 365 218 449 280"/><path className="atlas-link conflict" d="M826 175 C718 153 674 215 558 283"/><path className="atlas-link method" d="M190 442 C297 428 368 382 450 344"/><path className="atlas-link next" d="M556 350 C632 422 695 442 796 432"/>
          {running && <><circle className="atlas-pulse" r="6"><animateMotion dur="2.8s" repeatCount="indefinite" path="M172 160 C305 135 365 218 449 280"/></circle><circle className="atlas-pulse" r="6"><animateMotion dur="2.4s" repeatCount="indefinite" path="M190 442 C297 428 368 382 450 344"/></circle><circle className="atlas-pulse" r="6"><animateMotion dur="2.1s" repeatCount="indefinite" path="M556 350 C632 422 695 442 796 432"/></circle></>}
        </svg>
        <section className="atlas-cluster evidence"><h2><b>1</b>证据支持</h2>{evidence.length ? evidence.map((item, index) => <article key={`${String(item.title)}-${index}`}><strong>{value(item.title, "已验证研究文献")}</strong><span>{value(item.year, "已核验")}</span><em>支持</em></article>) : <p>等待检索与验证文献</p>}</section>
        <section className="atlas-cluster conflict"><h2><b>2</b>待证伪边界</h2><article><strong>{value(decision.reason, "识别关键混淆因素")}</strong><span>审计中</span><em className="muted">待验证</em></article><article><strong>{value(decision.evidence_gap, "补充可区分的对照证据")}</strong><span>研究缺口</span><em className="warn">缺口</em></article></section>
        <section className="atlas-hypothesis"><header><span>当前假设</span><b>H1</b></header><p>{claim}</p><footer><span>{value(topic.domain, "研究方向待定义")}</span><span>{run?.feedback_iteration ? `第 ${run.feedback_iteration} 轮` : "待证据推理"}</span></footer></section>
        <section className="atlas-cluster methods"><h2><b>3</b>方法与数据</h2><article><strong>{value((plan.dataset as Record<string, unknown> | undefined)?.name, "数据集待确认")}</strong><span>数据与切分</span><em>受控</em></article><article><strong>{value((plan.method as Record<string, unknown> | undefined)?.name, "方法待设计")}</strong><span>对照与指标</span><em>受控</em></article></section>
        <section className="atlas-experiment"><header><FlaskConical size={18}/><strong>下一次实验</strong><span>建议</span></header><p>{value(decision.required_change, "完成假设选择后，系统将生成可执行的受控实验计划。")}</p><ul><li><Check size={14}/>固定数据、方法与评估协议</li><li><Check size={14}/>仅改变可解释的单一变量</li><li><Check size={14}/>审计结果后更新科学判断</li></ul></section>
        <section className="atlas-research-note"><header><NotebookPen size={17}/><strong>研究笔记</strong></header><p>{value(decision.reason, "从一个可验证的问题开始，系统会持续记录证据、风险与下一轮实验方向。")}</p><footer><span>研究员</span><time>{new Date().toLocaleDateString("zh-CN")}</time></footer></section>
      </div>
    </section>
    <aside className="atlas-context"><section><p>研究流程 <span>V1.5</span></p><ol>{stages.map(([id, label, detail], index) => { const state = statusFor(id, run, activeStepId, failedStepId); return <li className={state} key={id}><i>{state === "complete" ? <Check size={13}/> : index + 1}</i><div><strong>{label}</strong><span>{state === "active" ? "正在运行" : state === "failed" ? "需要恢复" : detail}</span></div></li>; })}</ol></section><section><p>运行环境</p><dl><div><dt>执行状态</dt><dd>{progress?.state ?? (running ? "running" : "idle")}</dd></div><div><dt>研究 Run</dt><dd>{run?.id?.slice(-10) ?? "尚未创建"}</dd></div><div><dt>当前策略</dt><dd>{value(decision.action, "等待决策")}</dd></div></dl></section>{failedStepId && <section className="atlas-warning"><TriangleAlert size={18}/><div><strong>需要恢复</strong><p>未形成科学结论；请查看诊断后重试。</p></div></section>}</aside>
  </main>;
}
