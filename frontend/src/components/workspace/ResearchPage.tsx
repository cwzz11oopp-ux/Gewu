import { AlertTriangle, ArrowRight, Check, CircleDot, Clock3, Focus, Maximize2, Minus, Plus, Scan, Undo2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ResearchViewModel, TreeNode } from "../researchViewModel";
import { PageHeader } from "./PageHeader";

type Props = {
  model: ResearchViewModel; question: string; busy: boolean; activeStepId: string | null; failedStepId: string | null;
  onQuestionChange: (value: string) => void; onCreate: () => void; onStart: () => void;
  githubRepositoryUrl: string; onGithubRepositoryUrlChange: (value: string) => void;
  onOpenHypothesis: (id: string) => void; onOpenExperiment: (id: string, log?: boolean) => void;
};

const stageLabels: Record<string, string> = {
  problem_understanding: "定义研究问题", knowledge_integration: "文献与证据检索", hypothesis_generation: "候选假设生成",
  evidence_reasoning: "证据推理", research_plan: "实验设计", experiment_task: "实验准备",
  experiment_run_analysis: "实验验证", evidence_audit: "证据审计", report_export: "结论与报告",
};
const kindLabel: Record<TreeNode["kind"], string> = { Q: "问题", L: "文献集", T: "主题", G: "研究缺口", H: "假设", V: "证据审查", X: "实验", R: "结果", C: "结论" };
const stateLabel = { completed: "已完成", running: "运行中", failed: "失败", refuted: "已反驳", thinking: "推理中", ready: "就绪", queued: "待运行", searching: "检索中", empty: "暂无", revision_required: "需修订", evidence_insufficient: "证据不足", rejected: "已拒绝" };

function NodeCard({ node, selected, onSelect }: { node: TreeNode; selected: boolean; onSelect: () => void }) {
  return <button className={`tree-node kind-${node.kind.toLowerCase()} state-${node.status} ${selected ? "is-selected" : ""}`} style={{ left: node.x, top: node.y }} onClick={onSelect}>
    <span className="node-eyebrow"><b>{node.kind}</b>{node.id}</span>
    <strong title={node.title}>{node.title}</strong>
    <small><CircleDot size={12}/>{stateLabel[node.status]}</small>
  </button>;
}

export function ResearchPage({ model, question, busy, activeStepId, failedStepId, onQuestionChange, githubRepositoryUrl, onGithubRepositoryUrlChange, onCreate, onStart, onOpenHypothesis, onOpenExperiment }: Props) {
  const [selectedId, setSelectedId] = useState(model.currentExperiment?.id ?? model.selectedHypothesis?.id ?? "Q1");
  const [selectedGapId, setSelectedGapId] = useState("");
  const [showGapPapers, setShowGapPapers] = useState(false);
  const [zoom, setZoom] = useState(1);
  const viewportRef = useRef<HTMLDivElement>(null);
  const selected = model.nodes.find((item) => item.id === selectedId) ?? model.nodes[0];
  const nodeMap = useMemo(() => new Map(model.nodes.map((node) => [node.id, node])), [model.nodes]);
  const canvasWidth = Math.max(1120, ...model.nodes.map((node) => node.x + 196));
  const canvasHeight = Math.max(590, ...model.nodes.map((node) => node.y + 124));
  const canStart = Boolean(question.trim()) && !busy && model.status !== "failed";
  const stage = stageLabels[activeStepId ?? ""] ?? stageLabels[model.currentStage.replace(/ /g, "_")] ?? model.currentStage;

  const fitView = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const next = Math.min(1, Math.max(.62, (viewport.clientWidth - 28) / canvasWidth));
    setZoom(next);
    viewport.scrollTo({ left: 0, top: 0, behavior: "smooth" });
  }, [canvasWidth]);
  useEffect(() => { const id = window.setTimeout(fitView, 50); return () => window.clearTimeout(id); }, [fitView, model.runId]);

  function locateCurrent() {
    const current = model.nodes.find((node) => node.status === "running") ?? model.nodes.find((node) => node.id === model.currentExperiment?.id) ?? selected;
    const viewport = viewportRef.current;
    if (!current || !viewport) return;
    viewport.scrollTo({ left: Math.max(0, current.x * zoom - viewport.clientWidth / 2 + 90), top: Math.max(0, current.y * zoom - viewport.clientHeight / 2 + 55), behavior: "smooth" });
    setSelectedId(current.id);
  }

  const selectedGap = model.researchSynthesis.gaps.find((item) => item.id === selectedGapId);
  const gapPapers = selectedGap ? model.researchSynthesis.papers.filter((paper) => selectedGap.paperIds.includes(paper.id)) : [];
  const revisionRequired = model.status === "revision_required";
  const action = selected?.kind === "H" ? { label: "在 Idea 中查看", run: () => onOpenHypothesis(selected.id) }
    : selected?.kind === "X" ? { label: "在实验台查看", run: () => onOpenExperiment(selected.id) }
    : undefined;

  return <div className="gew-page research-page">
    <section className="gew-main-column">
      <PageHeader title="研究" english="Research" subtitle="围绕当前问题整合文献、证据、假设与实验，形成可验证的研究路径。" actions={<button className="button secondary" onClick={onCreate} disabled={busy}>＋ 新研究</button>}/>
      <section className="research-question-panel"><label>研究问题</label><div className="research-question-content"><div className="research-question-fields"><textarea value={question} onChange={(event) => onQuestionChange(event.target.value)} placeholder="输入你想研究的问题……" rows={3}/><label className="github-source-input">GitHub 源码仓库（可选）<input value={githubRepositoryUrl} onChange={(event) => onGithubRepositoryUrlChange(event.target.value)} placeholder="https://github.com/owner/repository" disabled={busy || Boolean(model.runId)}/></label></div><button className="button primary" disabled={!canStart} onClick={onStart}>{busy ? "研究进行中" : model.status === "failed" ? "本 Run 已失败" : revisionRequired ? "等待假设修订" : model.runId ? "继续研究" : "开始研究"}<ArrowRight size={17}/></button></div></section>
      <section className="current-research-strip"><span className="strip-label">当前研究</span><div><small>当前阶段</small><strong>{revisionRequired ? "假设修订" : stage || "尚未开始"}</strong></div><div><small>当前假设</small><button onClick={() => model.selectedHypothesis && onOpenHypothesis(model.selectedHypothesis.id)} disabled={!model.selectedHypothesis}>{model.selectedHypothesis?.id ?? "—"}</button></div><div><small>当前实验</small><button onClick={() => model.currentExperiment && onOpenExperiment(model.currentExperiment.id)} disabled={!model.currentExperiment}>{model.currentExperiment?.id ?? "—"}</button></div><div><small>研究状态</small><strong className={`status-text ${model.status}`}><i/>{model.status === "running" ? "进行中" : model.status === "completed" ? "已完成" : model.status === "failed" ? "研究失败" : revisionRequired ? "需要假设修订" : model.runId ? "已暂停 / 待继续" : "尚未开始"}</strong></div></section>
      <section className="research-tree-section">
        <header className="section-toolbar"><h2>研究地图 <em>Research Map</em></h2><div className="tree-tools"><button onClick={() => setZoom((value) => Math.max(.55, value - .1))} title="缩小"><Minus size={16}/></button><span>{Math.round(zoom * 100)}%</span><button onClick={() => setZoom((value) => Math.min(1.35, value + .1))} title="放大"><Plus size={16}/></button><button onClick={fitView} title="适应画布"><Scan size={16}/></button><button onClick={() => { setZoom(1); viewportRef.current?.scrollTo({ left: 0, top: 0, behavior: "smooth" }); }} title="重置"><Undo2 size={16}/></button><button onClick={locateCurrent} title="定位当前节点"><Focus size={16}/></button><button onClick={() => viewportRef.current?.requestFullscreen?.()} title="全屏"><Maximize2 size={16}/></button></div><div className="tree-legend">{Object.entries(kindLabel).map(([kind, label]) => <span key={kind}><b>{kind}</b>{label}</span>)}</div></header>
        <div className="tree-viewport" ref={viewportRef}><div className="tree-canvas" style={{ width: canvasWidth, height: canvasHeight, transform: `scale(${zoom})`, transformOrigin: "0 0" }}>
          <svg className="tree-edges" width={canvasWidth} height={canvasHeight} aria-hidden="true"><defs><marker id="gew-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10z"/></marker></defs>{model.edges.map((edge, index) => { const from = nodeMap.get(edge.from); const to = nodeMap.get(edge.to); if (!from || !to) return null; const x1 = from.x + 176, y1 = from.y + 54, x2 = to.x, y2 = to.y + 54, mid = x1 + Math.max(24, (x2 - x1) / 2); const labelWidth = Math.max(38, Array.from(edge.label).length * 13 + 14); const labelX = x1 + Math.max(18, (mid - x1) / 2); return <g key={`${edge.from}-${edge.to}-${index}`} className={`edge-${edge.tone ?? "neutral"}`}><path d={`M${x1} ${y1} H${mid} V${y2} H${x2}`} markerEnd="url(#gew-arrow)"/><rect className="tree-edge-label-bg" x={labelX - labelWidth / 2} y={y1 - 21} width={labelWidth} height={20} rx={4}/><text className="tree-edge-label" x={labelX} y={y1 - 7} textAnchor="middle">{edge.label}</text></g>; })}</svg>
          {model.nodes.map((node) => <NodeCard key={node.id} node={node} selected={selected?.id === node.id} onSelect={() => setSelectedId(node.id)}/>)}
          {!model.papers.length && <div className="tree-empty"><Clock3 size={22}/><strong>等待真实研究数据</strong><span>启动研究后，真实文献、证据、假设与实验会动态生成。</span></div>}
        </div></div>
      </section>
    </section>
    <aside className="gew-inspector research-inspector"><header><h2>研究检视器</h2><em>Research Inspector</em></header>
      <section><span className="inspector-kicker">当前状态</span><dl className="inspector-facts"><div><dt>当前阶段</dt><dd>{stage || "尚未开始"}</dd></div><div><dt>当前 Hypothesis</dt><dd>{model.selectedHypothesis?.id ?? "—"}</dd></div><div><dt>当前 Experiment</dt><dd>{model.currentExperiment?.id ?? "—"}</dd></div><div><dt>当前问题</dt><dd>{model.question || "尚未定义研究问题"}</dd></div><div><dt>下一步</dt><dd>{busy ? "等待当前任务完成" : model.status === "failed" ? "保留失败记录并查看错误摘要" : model.runId ? "继续研究流程" : "创建并开始研究"}</dd></div><div><dt>风险 / 异常</dt><dd className={failedStepId ? "danger" : ""}>{failedStepId || "未发现流程异常"}</dd></div></dl></section>
      <section className="selected-node-detail"><span>选中节点</span><h3><b>{selected?.kind}</b>{selected?.id ?? "—"}</h3><p>{selected?.title ?? "选择节点查看摘要。"}</p><p>{selected?.detail}</p><dl><div><dt>类型</dt><dd>{selected ? kindLabel[selected.kind] : "—"}</dd></div><div><dt>状态</dt><dd>{selected ? stateLabel[selected.status] : "—"}</dd></div><div><dt>关联对象</dt><dd>{selected ? model.edges.filter((edge) => edge.from === selected.id || edge.to === selected.id).length : 0}</dd></div></dl>{action ? <button className="text-button" onClick={action.run}>{action.label} <ArrowRight size={15}/></button> : null}</section>
      {model.hypothesisRounds.length ? <section className="selected-node-detail"><span>Hypothesis rounds</span>{model.hypothesisRounds.map((round) => <div key={round.roundId}><strong>Round {round.roundIndex}</strong><small>{round.candidateIds.join(", ") || "Candidates unavailable"}</small><small>{round.revisionReason}</small></div>)}</section> : null}
      {selected?.id === "GAPS" ? <section className="selected-node-detail"><span>Gap provenance</span>{model.researchSynthesis.available ? <><h3>Research gaps · {model.researchSynthesis.gapCount}</h3>{model.researchSynthesis.gaps.map((gap) => <button className="text-button" key={gap.id} onClick={() => { setSelectedGapId(gap.id); setShowGapPapers(false); }}>{gap.id} · {gap.title}</button>)}{selectedGap ? <div><p>{selectedGap.description}</p><dl><div><dt>Related papers</dt><dd>{selectedGap.paperIds.length}</dd></div><div><dt>Limitations / claims</dt><dd>{selectedGap.claimIds.length}</dd></div><div><dt>Future work</dt><dd>{selectedGap.futureWorkIds.length}</dd></div><div><dt>Hypotheses</dt><dd>{model.hypotheses.filter((item) => item.sourceGapIds.includes(selectedGap.id)).map((item) => item.id).join(", ") || "Provenance unavailable"}</dd></div></dl><button className="text-button" onClick={() => setShowGapPapers((value) => !value)}>Related papers ({selectedGap.paperIds.length})</button>{showGapPapers ? <div>{gapPapers.length ? gapPapers.map((paper) => paper.url ? <a key={paper.id} href={paper.url} target="_blank" rel="noreferrer">{paper.title}</a> : <span key={paper.id}>{paper.title}</span>) : <small>Provenance unavailable</small>}</div> : null}</div> : null}</> : <p>Provenance unavailable</p>}</section> : null}
      {revisionRequired ? <section className="inspector-alert"><AlertTriangle size={17}/><div><strong>Hypothesis revision required</strong><span>{model.hypotheses.length} candidates reviewed · 0 currently selectable</span></div></section> : failedStepId ? <section className="inspector-alert"><AlertTriangle size={17}/><div><strong>{model.status === "failed" ? "研究失败" : "研究流程需要恢复"}</strong><span>失败阶段：{failedStepId}</span></div></section> : <section className="inspector-ok"><Check size={16}/><span>当前研究状态已同步</span></section>}
    </aside>
  </div>;
}
