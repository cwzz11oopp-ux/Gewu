import { ArrowRight, BookOpen, Check, ExternalLink, Lightbulb } from "lucide-react";
import { useEffect, useState } from "react";
import type { HypothesisItem, ResearchViewModel } from "../researchViewModel";
import { PageHeader } from "./PageHeader";

type Props = { model: ResearchViewModel; busy: boolean; focusHypothesisId?: string; onSelectHypothesis: (index: number) => Promise<void>; onOpenExperiment: (id?: string) => void };
const paperStatus = { included: "已纳入", review: "待审查", excluded: "已排除" };
const hypothesisStatus = { candidate: "候选", selected: "已选择", refuted: "已反驳", partial: "部分支持", evidence_insufficient: "证据不足", rejected: "已拒绝", revision_required: "需修订" };

function score(item: HypothesisItem) {
  const values = Object.values(item.scores).filter((value): value is number => typeof value === "number");
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : undefined;
}

export function IdeaPage({ model, busy, focusHypothesisId, onSelectHypothesis, onOpenExperiment }: Props) {
  const [selectedId, setSelectedId] = useState(focusHypothesisId ?? model.selectedHypothesis?.id ?? model.hypotheses[0]?.id ?? "");
  useEffect(() => { if (focusHypothesisId) setSelectedId(focusHypothesisId); }, [focusHypothesisId]);
  useEffect(() => { if (!selectedId && model.hypotheses[0]) setSelectedId(model.hypotheses[0].id); }, [model.hypotheses, selectedId]);
  const selected = model.hypotheses.find((item) => item.id === selectedId) ?? model.hypotheses[0];
  const selectedIndex = model.hypotheses.findIndex((item) => item.id === selected?.id);
  const support = selected?.evidenceSources.filter((item) => item.stance === "support") ?? [];
  const conflicts = selected?.evidenceSources.filter((item) => item.stance === "conflict") ?? [];

  async function choose() {
    if (!selected || busy || selectedIndex < 0) return;
    if (selected.status !== "selected") await onSelectHypothesis(selectedIndex);
    onOpenExperiment(model.currentExperiment?.id);
  }

  return <div className="gew-page idea-page"><section className="idea-main">
    <PageHeader title="Idea" english="Literature & Candidate Ideas" subtitle="查看系统已检索的真实文献，以及基于证据形成的候选研究假设。" meta={<div className="page-context"><b>Q1</b><span>/</span><span title={model.question}>{model.question || "尚未定义研究问题"}</span></div>}/>
    <div className="idea-workbench idea-workbench-v2">
      <section className="idea-column literature-column"><header><h2><BookOpen size={19}/>文献 <em>Literature</em></h2><span>{model.papers.length} 篇</span></header><div className="paper-list">{model.papers.length ? model.papers.map((paper, index) => <article className="paper-row-v2" key={`${paper.id}-${index}`}>
        <span className="paper-index">{index + 1}</span><div>{paper.url ? <a href={paper.url} target="_blank" rel="noreferrer" title="在新标签页打开原始来源">{paper.title}<ExternalLink size={14}/></a> : <strong>{paper.title}</strong>}<small>{paper.authors}</small><small>{paper.source} · {paper.year}</small></div><em className={paper.status}>{paperStatus[paper.status]}</em>
      </article>) : <div className="column-empty"><BookOpen size={24}/><strong>暂无文献记录</strong><span>Research Agent 完成检索后，真实论文会显示在这里。</span></div>}</div></section>
      <section className="idea-column candidate-column candidate-column-v2"><header><h2><Lightbulb size={19}/>候选想法 <em>Candidate Ideas</em></h2><span>{model.hypotheses.length} 个</span></header><div className="hypothesis-list">{model.hypotheses.length ? model.hypotheses.map((item) => <button key={item.id} className={`hypothesis-card-v2 status-${item.status} ${item.id === selected?.id ? "is-selected" : ""}`} onClick={() => setSelectedId(item.id)}>
        <div className="candidate-head"><span className="id-badge">{item.id}</span><em>{hypothesisStatus[item.status]}</em></div><p>{item.claim}</p><footer><span>综合评分</span><b>{score(item)?.toFixed(2) ?? "—"}</b></footer>
      </button>) : <div className="column-empty"><Lightbulb size={24}/><strong>暂无候选假设</strong><span>Hypothesis Agent 的真实结果会显示在这里。</span></div>}</div>
        {selected ? <section className="candidate-evidence-chain"><header><h3>证据链 <em>Evidence Chain</em></h3></header><div><strong>支持来源：{support.length} 篇</strong>{support.length ? support.map((item, index) => item.url ? <a key={`${item.title}-${index}`} href={item.url} target="_blank" rel="noreferrer">P{String(index + 1).padStart(2, "0")} · {item.title}<ExternalLink size={13}/></a> : <span key={`${item.title}-${index}`}>P{String(index + 1).padStart(2, "0")} · {item.title}</span>) : <small>当前 Candidate 未提供可追溯来源。</small>}</div>{conflicts.length ? <div className="conflict"><strong>冲突来源：{conflicts.length} 篇</strong>{conflicts.map((item) => <span key={item.title}>{item.title}</span>)}</div> : null}</section> : null}
        <section className="selection-reason"><h3>选择理由 <em>Selection Reason</em></h3><p>{selected?.reason || "尚无选择记录。"}</p><ul><li><Check size={15}/>可验证指标明确</li><li><Check size={15}/>数据与实验环境可用</li><li><Check size={15}/>关联来源可追溯</li></ul></section>
        <button className="button primary choose-idea" disabled={!selected || busy} onClick={choose}>选择 {selected?.id ?? "假设"} 并进入实验台 <ArrowRight size={17}/></button>
      </section>
    </div>
  </section><aside className="gew-inspector idea-inspector"><header><h2>想法检视器</h2><em>Idea Inspector</em></header><section><span className="inspector-kicker">当前选择</span><p className="inspector-selected"><b>{selected?.id ?? "—"}</b>{selected?.claim ?? "尚未选择候选假设"}</p></section><section><h3>摘要</h3><dl className="inspector-facts"><div><dt>状态</dt><dd>{selected ? hypothesisStatus[selected.status] : "—"}</dd></div><div><dt>来源文献</dt><dd>{selected?.evidenceSources.length ?? 0}</dd></div><div><dt>支持证据</dt><dd className="success">{support.length}</dd></div><div><dt>冲突证据</dt><dd className={conflicts.length ? "danger" : ""}>{conflicts.length}</dd></div><div><dt>关联实验</dt><dd>{model.currentExperiment?.id ?? "尚未生成"}</dd></div></dl></section>{model.currentExperiment ? <section className="inspector-navigation"><button className="text-button" onClick={() => onOpenExperiment(model.currentExperiment?.id)}>前往关联实验 <ArrowRight size={15}/></button></section> : null}</aside></div>;
}
