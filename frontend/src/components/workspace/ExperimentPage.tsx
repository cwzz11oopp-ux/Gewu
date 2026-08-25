import { AlertCircle, ArrowLeft, CheckCircle2, Clock3, FileText, FlaskConical, Search, Square, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import type { ExperimentProgress } from "../../api/types";
import { formatDuration, formatMetricName, formatMetricValue, scientificMetricSeries, type ExperimentItem, type ResearchViewModel, type ScientificMetricSeries } from "../researchViewModel";
import { PageHeader } from "./PageHeader";
import { LineChart } from "./MetricCharts";

type Props = { model: ResearchViewModel; progress: ExperimentProgress | null; focusExperimentId?: string; openLog?: boolean; busy: boolean; stopRequested: boolean; onStop: () => void; onOpenHypothesis: (id: string) => void };
type TimelineFilter = "all" | "scientific" | "engineering";
const statusLabel = { completed: "已完成", running: "运行中", failed: "失败", queued: "待运行" };
const icon = (status: ExperimentItem["status"]) => status === "failed" ? <AlertCircle size={16}/> : status === "completed" ? <CheckCircle2 size={16}/> : <Clock3 size={16}/>;
const metricOf = (item: ExperimentItem) => item.primaryMetric ?? item.metrics.find((metric) => !/std|var|time|elapsed/i.test(metric.name)) ?? item.metrics[0];
const isPercent = (name: string) => /accuracy|acc|auc|f1|precision|recall|准确/i.test(name);
const delta = (value: number, name: string) => `${value >= 0 ? "+" : ""}${isPercent(name) ? `${value.toFixed(3)} pp` : formatMetricValue(value)}`;
const eventType = (item: ExperimentItem) => item.classification === "scientific" ? (item.evolution?.baselineId ? "科学修订" : "科学基线") : item.status === "failed" ? "工程失败" : item.auditStatus ? "审计未通过" : "工程处置";
const compactFailure = (value: string) => value.match(/[A-Z][A-Z0-9_]{3,}/)?.[0] ?? value.split(/[\r\n:]/)[0];
const display = (value: unknown) => typeof value === "string" ? value.trim() : typeof value === "number" || typeof value === "boolean" ? String(value) : "";

function LogView({ model, item, progress, onClose }: { model: ResearchViewModel; item?: ExperimentItem; progress: ExperimentProgress | null; onClose: () => void }) {
  const [query, setQuery] = useState(""); const [remote, setRemote] = useState(""); const pre = useRef<HTMLPreElement>(null);
  const isCurrent = item?.id === model.currentExperiment?.id;
  useEffect(() => { let live = true; if (!model.runId || !isCurrent) return; api.getExperimentLog(model.runId).then((value) => { if (live) setRemote(value); }).catch(() => undefined); return () => { live = false; }; }, [isCurrent, model.runId]);
  const raw = (isCurrent ? progress?.log_tail || remote : item?.log) || "";
  const lines = raw.split(/\r?\n/).filter((line) => !query || line.toLowerCase().includes(query.toLowerCase()));
  useEffect(() => { if (pre.current) pre.current.scrollTop = pre.current.scrollHeight; }, [raw]);
  return <section className="experiment-log-view"><header><div><button className="icon-back" onClick={onClose}><ArrowLeft size={17}/></button><h2>运行日志 <em>Runtime Log</em></h2><span>{item?.id ?? "—"}</span></div><label className="log-controls"><Search size={15}/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索日志"/></label></header><pre ref={pre}>{lines.length ? lines.map((line, index) => <span key={index} className={/error|failed|exception|traceback/i.test(line) ? "log-error" : ""}>{line}{"\n"}</span>) : <span>{item?.logPath ? "该历史版本仅记录日志路径。" : "当前实验没有可读取的日志内容。"}</span>}</pre></section>;
}

function ExperimentDetailModal({ item, onClose, onLog }: { item: ExperimentItem; onClose: () => void; onLog: () => void }) {
  const dialog = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onCloseRef.current(); };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    dialog.current?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, []);
  const facts = [["Experiment ID", item.id], ["显示名称", item.title], ["类型", item.classification === "scientific" ? "科学实验" : "工程处置"], ["状态", statusLabel[item.status]], ["父修订", item.revisionArtifactId], ["数据集", item.dataset], ["随机种子", item.seeds.join(" / ")], ["计算资源", item.provider], ["Result Artifact", item.resultArtifactId], ["Audit", item.auditStatus]].filter(([, value]) => display(value));
  const notes = [["假设", item.purpose], ["修订原因", item.revisionReason], ["失败原因", item.failureReason], ["科学反馈", item.scientificFeedback]].filter(([, value]) => display(value));
  return <div className="experiment-detail-backdrop" role="presentation" onMouseDown={onClose}>
    <section ref={dialog} className="experiment-detail-dialog" role="dialog" aria-modal="true" aria-label={`${item.id} 实验详情`} tabIndex={-1} onMouseDown={(event) => event.stopPropagation()}>
      <header><div><span>{eventType(item)}</span><h2>{item.id}</h2><p>{item.title}</p></div><button className="icon-close" onClick={onClose} aria-label="关闭实验详情"><X size={22}/></button></header>
      <div className="drawer-content">
        <section className="detail-section"><h3>实验信息</h3><dl className="drawer-facts">{facts.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl></section>
        {item.metrics.length ? <section className="detail-section"><h3>指标</h3><dl className="drawer-facts">{item.metrics.map((metric) => <div key={metric.name}><dt>{metric.name === item.primaryMetric?.name ? "Primary Metric" : formatMetricName(metric.name)}</dt><dd>{formatMetricName(metric.name)} · {formatMetricValue(metric.value)}</dd></div>)}</dl></section> : null}
        {notes.length ? <section className="detail-section detail-wide"><h3>科学与处置记录</h3><dl className="drawer-notes">{notes.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl></section> : null}
        {item.epochSeries.length ? <EpochTraining item={item} /> : null}
        {Object.keys(item.parameters).length ? <section className="detail-section"><h3>实验计划与参数</h3><dl className="drawer-facts">{Object.entries(item.parameters).map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></section> : null}
        {item.attempts.length ? <section className="detail-section"><h3>运行尝试</h3><div className="drawer-attempts">{item.attempts.map((attempt) => <article key={attempt.id}><b>{attempt.id}</b><span>{attempt.status}</span>{attempt.error ? <small>{attempt.error}</small> : null}</article>)}</div></section> : null}
        <button className="button secondary drawer-log-button" onClick={onLog}><FileText size={18}/>查看运行日志</button>
      </div>
    </section>
  </div>;
}

function EpochTraining({ item }: { item: ExperimentItem }) {
  const [seed, setSeed] = useState<number | "mean">("mean");
  const bySeed = item.epochSeriesBySeed.find((entry) => entry.seed === seed);
  const active = seed === "mean" ? item.epochSeries : bySeed?.series ?? item.epochSeries;
  const primary = active.find((series) => series.metric === item.primaryMetric?.name) ?? active[0];
  if (!primary) return null;
  const labels = primary.rows.map((row) => String(row.epoch));
  const direction = item.metricDirections?.[primary.metric];
  return <section className="drawer-epochs detail-section detail-wide"><header><h3>训练过程 <em>Training Curve</em></h3>{item.epochSeriesBySeed.length ? <label className="epoch-seed-selector"><span>曲线</span><select value={seed === "mean" ? "mean" : String(seed)} onChange={(event) => setSeed(event.target.value === "mean" ? "mean" : Number(event.target.value))}><option value="mean">跨种子均值</option>{item.epochSeriesBySeed.map((entry) => <option key={entry.seed} value={String(entry.seed)}>seed {entry.seed}</option>)}</select></label> : null}</header><LineChart labels={labels} series={[{ name: formatMetricName(primary.metric), values: primary.rows.map((row) => row.value), color: "#087f5b" }]} yLabel={formatMetricName(primary.metric)} metricDirection={direction}/><div className="epoch-table-scroll"><table className="drawer-epoch-table"><thead><tr><th>epoch</th>{active.map((series) => <th key={series.metric}>{formatMetricName(series.metric)}</th>)}</tr></thead><tbody>{primary.rows.map((row) => <tr key={row.epoch}><td>{row.epoch}</td>{active.map((series) => { const value = series.rows.find((entry) => entry.epoch === row.epoch)?.value; return <td key={series.metric}>{value === undefined ? "—" : formatMetricValue(value)}</td>; })}</tr>)}</tbody></table></div></section>;
}

function labelsFor(series: ScientificMetricSeries) { return series.rows.map((_, index, rows) => rows.length === 1 ? "当前结果" : index === 0 ? "基线" : index === rows.length - 1 ? "最终" : `修订 ${index}`); }

function PerformanceEvolution({ model }: { model: ResearchViewModel }) {
  const series = useMemo(() => scientificMetricSeries(model.experiments), [model.experiments]);
  const primaryNames = new Set(model.experiments.flatMap((item) => item.primaryMetricNames));
  const primarySeries = series.filter((item) => primaryNames.has(item.name)).slice(0, 2);
  const latestScientific = [...model.experiments].reverse().find((item) => item.classification === "scientific" && item.status === "completed" && item.isRealExperiment && item.auditStatus === "passed");
  const loss = latestScientific?.epochSeries.find((item) => /^(train_)?loss$/i.test(item.metric))
    ?? latestScientific?.epochSeries.find((item) => /loss|损失/i.test(item.metric));
  if (!primarySeries.length && !loss) return <div className="chart-empty">尚无通过独立审计、且具有真实 Result Artifact 来源的主指标或 Loss。</div>;
  return <><header className="performance-header"><div><h2>性能演化 <em>Performance Evolution</em></h2><p>仅保留计划声明的 1–2 个主指标迭代曲线，以及最新正式实验的 Loss。</p></div></header><div className="performance-chart-list">{primarySeries.map((item) => { const labels = labelsFor(item); const tooltips = item.rows.map((row, index) => [labels[index], row.experimentId, formatMetricName(item.name), formatMetricValue(row.value), `Result Artifact ${row.artifactId}`].join("\n")); return <section className="performance-chart-panel" key={item.name}><h3>主指标：{formatMetricName(item.name)}</h3><LineChart labels={labels} series={[{ name: formatMetricName(item.name), values: item.rows.map((row) => row.value), color: "#087f5b", tooltips }]} yLabel={formatMetricName(item.name)} metricDirection={item.direction}/></section>; })}{loss ? <section className="performance-chart-panel" key="loss"><h3>训练 Loss</h3><LineChart labels={loss.rows.map((row) => String(row.epoch))} series={[{ name: formatMetricName(loss.metric), values: loss.rows.map((row) => row.value), color: "#2454e6" }]} yLabel={formatMetricName(loss.metric)} metricDirection="lower"/></section> : null}</div></>;
}

export function ExperimentPage({ model, progress, focusExperimentId, openLog, busy, stopRequested, onStop, onOpenHypothesis }: Props) {
  const [selectedId, setSelectedId] = useState(focusExperimentId ?? model.currentExperiment?.id ?? ""); const [drawerId, setDrawerId] = useState<string | null>(null); const [view, setView] = useState<"overview" | "log">(openLog ? "log" : "overview"); const [filter, setFilter] = useState<TimelineFilter>("all");
  useEffect(() => { if (focusExperimentId) { setSelectedId(focusExperimentId); setDrawerId(focusExperimentId); } if (openLog) setView("log"); }, [focusExperimentId, openLog]);
  const current = model.currentExperiment; const selected = model.experiments.find((item) => item.id === selectedId) ?? current; const drawer = model.experiments.find((item) => item.id === drawerId); const scientific = model.experiments.filter((item) => item.classification === "scientific"); const engineering = model.experiments.filter((item) => item.classification === "engineering"); const timeline = filter === "all" ? model.experiments : model.experiments.filter((item) => item.classification === filter); const findings = model.scientificFindings;
  const progressPercent = current?.status === "completed" ? 100 : progress?.timeout_seconds && progress.elapsed_seconds !== undefined ? Math.min(99, Math.round(progress.elapsed_seconds / progress.timeout_seconds * 100)) : progress?.process_alive ? 1 : 0; const runtime = current?.status === "running" ? formatDuration(progress?.elapsed_seconds) : current?.runtime !== "—" ? current?.runtime : formatDuration(progress?.elapsed_seconds);
  const openDrawer = (id: string) => { setSelectedId(id); setDrawerId(id); };
  if (view === "log") return <div className="gew-page experiment-page experiment-page-redesign"><section className="experiment-main"><PageHeader title="实验台" english="Experiment Bench" subtitle="读取当前研究保存的真实运行日志。"/><LogView model={model} item={selected} progress={progress} onClose={() => setView("overview")}/></section></div>;
  return <div className="gew-page experiment-page experiment-page-redesign"><section className="experiment-main"><PageHeader title="实验台" english="Experiment Bench" subtitle="以有效科学结果为主线，区分工程处置与科研结论。" actions={<span className="context-pill">关联假设 <button onClick={() => model.selectedHypothesis && onOpenHypothesis(model.selectedHypothesis.id)}>{model.selectedHypothesis?.id ?? "—"}</button></span>}/>
    <section className="run-overview-card"><header><div><h2>运行概览 <em>Run Overview</em></h2><p>{model.question || model.title}</p></div><span className={`run-status ${model.status}`}>{model.status === "completed" ? "已完成" : model.status === "running" ? "运行中" : model.status}</span></header><dl><div><dt>当前阶段</dt><dd>{model.currentStage}</dd></div><div><dt>有效科学实验</dt><dd>{scientific.length}</dd></div><div><dt>工程处置</dt><dd>{engineering.length}</dd></div><div><dt>数据集</dt><dd>{current?.dataset || "未声明"}</dd></div><div><dt>计算资源</dt><dd>{current?.provider || "未配置"}</dd></div><div><dt>最新运行时长</dt><dd>{runtime}</dd></div></dl></section>
    <section className="execution-timeline"><header><div><h2>实验历程 <em>Execution Timeline</em></h2><p>每条历史记录只在此处出现一次；点击节点查看真实 Artifact 详情。</p></div><div className="timeline-filters"><button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>全部 <span>{model.experiments.length}</span></button><button className={filter === "scientific" ? "active" : ""} onClick={() => setFilter("scientific")}>科学实验 <span>{scientific.length}</span></button><button className={filter === "engineering" ? "active" : ""} onClick={() => setFilter("engineering")}>工程处置 <span>{engineering.length}</span></button></div></header>{timeline.length ? <div className="execution-timeline-scroll"><div className="execution-timeline-track">{timeline.map((item) => { const metric = metricOf(item); return <button key={item.id} className={`execution-node ${item.classification} status-${item.status} ${item.id === selected?.id ? "is-selected" : ""}`} onClick={() => openDrawer(item.id)}><span className="execution-node-type">{eventType(item)}</span><b>{item.id}</b><strong>{item.title}</strong>{item.classification === "scientific" && metric ? <span className="execution-node-metric"><small>{formatMetricName(metric.name)}</small>{formatMetricValue(metric.value)}</span> : <span className="execution-node-event">{item.failureReason ? compactFailure(item.failureReason) : item.auditStatus ? `Audit ${item.auditStatus}` : statusLabel[item.status]}</span>}<footer>{icon(item.status)}{statusLabel[item.status]}</footer></button>; })}</div></div> : <div className="wide-empty"><FlaskConical size={24}/><span>研究计划生成后，真实实验迭代会显示在这里。</span></div>}</section>
    <section className="current-effective-card"><div><span>当前有效实验 <em>Current Effective Experiment</em></span><h2>{current?.id ?? "尚无有效实验"}</h2><p>{current?.title || "等待通过独立审计的真实实验结果"}</p></div>{current ? <dl><div><dt>状态</dt><dd>{statusLabel[current.status]}</dd></div><div><dt>{current.status === "completed" ? "完成度" : "运行进度"}</dt><dd className="numeric-value">{progressPercent}%<i><b style={{ width: `${progressPercent}%` }}/></i></dd></div><div><dt>Primary Metric</dt><dd>{metricOf(current) ? `${formatMetricName(metricOf(current)!.name)} ${formatMetricValue(metricOf(current)!.value)}` : "未记录"}</dd></div><div><dt>运行时长</dt><dd>{runtime}</dd></div><div><dt>数据集</dt><dd>{current.dataset}</dd></div></dl> : null}<div className="experiment-actions"><button className="button primary effective-detail-button" disabled={!current} onClick={() => current && openDrawer(current.id)}><FileText size={18}/>查看实验详情</button>{current?.status === "running" ? <button className="button danger-outline" disabled={!busy || stopRequested} onClick={onStop}><Square size={15}/>{stopRequested ? "停止中" : "停止运行"}</button> : null}</div></section>
    <div className="experiment-insight-grid"><section className="performance-evolution-card"><PerformanceEvolution model={model}/></section><section className="scientific-findings-card"><header><h2>当前科学结论 <em>Scientific Findings</em></h2></header>{findings ? <><dl><div><dt>假设状态</dt><dd className="finding-supported">{findings.hypothesisStatus || "已形成结论"}</dd></div>{findings.comparison ? <div><dt>核心比较</dt><dd>{findings.comparison.variant} {formatMetricValue(findings.comparison.variantValue)} · {findings.comparison.baseline} {formatMetricValue(findings.comparison.baselineValue)}</dd></div> : null}{findings.comparison ? <div><dt>性能差异</dt><dd className="finding-positive">{delta(findings.comparison.delta, findings.primaryMetric?.name || "")}</dd></div> : null}{findings.seedCount ? <div><dt>随机种子一致性</dt><dd>{findings.seedCount} / {findings.seedCount}</dd></div> : null}{findings.parameterSummary ? <div><dt>参数匹配</dt><dd>{findings.parameterSummary}</dd></div> : null}<div><dt>Audit</dt><dd className={findings.auditStatus === "passed" ? "finding-supported" : ""}>{findings.auditStatus || "未记录"}</dd></div></dl>{findings.limitations.length ? <section className="finding-limitations"><h3>主要限制</h3><ul>{findings.limitations.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}</> : <p className="finding-empty">尚未形成可展示的科学结论。</p>}</section></div>
  </section>{drawer ? <ExperimentDetailModal item={drawer} onClose={() => setDrawerId(null)} onLog={() => { setDrawerId(null); setView("log"); }}/> : null}</div>;
}
