import { useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronRight, Wrench } from "lucide-react";
import type { Artifact, ExperimentProgress } from "../api/types";
import { api, experimentFileUrl } from "../api/client";
import {
  buildExperimentMetricComparisons,
  buildExperimentMetricRows,
  findLatestArtifactContent,
  findLatestExperimentResultForTask,
  findMetricSeries,
  formatMetricValue,
  getMetricSeriesBounds,
  latestExperimentDiagnosis,
  latestExperimentResultFailure,
} from "../utils/presentation";

function displayValue(value: unknown) {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function formatTimestamp(value: unknown) {
  if (typeof value !== "string" || !value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatDuration(seconds: number) {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remaining = total % 60;
  return `${hours ? `${hours} 小时 ` : ""}${String(minutes).padStart(2, "0")} 分 ${String(remaining).padStart(2, "0")} 秒`;
}

const verdictLabels = {
  improved: "提升",
  declined: "下降",
  unchanged: "持平",
} as const;

export function ExperimentPanel({
  artifacts,
  runId,
  isBusy,
  onRerunFrom,
  onContinuePipeline,
  automaticPipelineRunning,
  pipelineStopRequested,
  onStopPipeline,
  progress,
}: {
  artifacts: Artifact[];
  runId?: string;
  isBusy: boolean;
  onRerunFrom: (stepId: string) => void;
  onContinuePipeline: () => void;
  automaticPipelineRunning: boolean;
  pipelineStopRequested: boolean;
  onStopPipeline: () => void;
  progress: ExperimentProgress | null;
}) {
  const [runtimeAction, setRuntimeAction] = useState("");
  const plan = findLatestArtifactContent(artifacts, "plan");
  const task = findLatestArtifactContent(artifacts, "experiment_task");
  const bundle = findLatestArtifactContent(artifacts, "experiment_bundle");
  const manifest = (bundle?.manifest ?? {}) as Record<string, unknown>;
  const result = findLatestExperimentResultForTask(artifacts)?.content;
  const revision = findLatestArtifactContent(artifacts, "revision");
  const iterationAnalysis = findLatestArtifactContent(artifacts, "iteration_analysis");
  const iterationEvidence = findLatestArtifactContent(artifacts, "iteration_evidence");
  const iterationDecision = findLatestArtifactContent(artifacts, "iteration_decision");
  const researchState = findLatestArtifactContent(artifacts, "research_state");
  const report = findLatestArtifactContent(artifacts, "report");
  const datasetProfile = findLatestArtifactContent(artifacts, "dataset_profile") as Record<string, any> | undefined;
  const baselineProfile = findLatestArtifactContent(artifacts, "baseline_profile") as Record<string, any> | undefined;
  const resultEvidence = findLatestArtifactContent(artifacts, "result_evidence") as Record<string, any> | undefined;
  const fairContract = findLatestArtifactContent(artifacts, "fair_experiment_contract") as Record<string, any> | undefined;
  const phase2Protocol = task?.phase2_protocol as Record<string, any> | undefined;
  const historicalDiagnosis = latestExperimentDiagnosis(artifacts);
  const historicalFailure = latestExperimentResultFailure(artifacts);
  const liveExperimentRunning = progress?.process_alive === true
    && ["running", "orphaned", "stalled"].includes(progress.state);
  // A new live attempt supersedes the previous terminal artifact for current-state presentation.
  // Historical failures remain in the attempt table and Agent trace for auditability.
  const diagnosis = liveExperimentRunning ? null : historicalDiagnosis;
  const experimentFailure = liveExperimentRunning ? null : historicalFailure;
  const diagnosisEvidence = Array.isArray(diagnosis?.evidence) ? diagnosis.evidence.map(displayValue) : [];
  const repairResult = diagnosis?.repair_result && typeof diagnosis.repair_result === "object"
    ? diagnosis.repair_result as Record<string, unknown>
    : undefined;
  const metrics = (result?.metrics ?? {}) as Record<string, unknown>;
  const analysis = (result?.analysis ?? {}) as Record<string, unknown>;
  const parameters = (result?.parameters ?? manifest.parameters ?? {}) as Record<string, unknown>;
  const parameterEntries = Object.entries(parameters);
  const seeds = (result?.seeds ?? manifest.seeds ?? []) as unknown[];
  const attempts = (Array.isArray(result?.attempts) ? result.attempts : []) as Array<Record<string, unknown>>;
  const environment = (result?.environment ?? {}) as Record<string, unknown>;
  const deviceNames = Array.isArray(environment.device_names)
    ? environment.device_names.map(displayValue).join(", ")
    : "";
  const expectedMetrics = Array.isArray(manifest.expected_metrics) ? manifest.expected_metrics : [];
  const metricRows = buildExperimentMetricRows(plan, metrics, expectedMetrics);
  const metricComparisons = buildExperimentMetricComparisons(metrics, analysis);
  const series = findMetricSeries(metrics);
  const seriesBounds = series ? getMetricSeriesBounds(series.values) : { minimum: 0, maximum: 0, range: 1 };
  const canRerunExperiment = Boolean(runId && task);
  const canContinuePipeline = Boolean(runId && result && !experimentFailure && !report);
  const experimentId = String(result?.experiment_id ?? manifest.experiment_id ?? task?.experiment_id ?? "等待生成");
  const resultId = String(result?.result_id ?? manifest.result_id ?? task?.result_id ?? "等待生成");
  const expectedMetricsPath = resultId === "等待生成" ? "等待实验结果生成" : `results/${resultId}.json`;
  const metricsPath = String(result?.metrics_path ?? expectedMetricsPath);
  const logPath = String(result?.log_path ?? task?.log_path ?? "等待实验结果生成");
  const dataset = String(manifest.dataset ?? task?.dataset ?? "未声明");
  const entrypoint = String(manifest.entrypoint ?? "train.py");
  const lifecycleMessage = liveExperimentRunning
    ? progress?.state === "running" ? "正在执行" : "后台跟踪已中断"
    : !plan ? "等待实验设计" : !task ? "等待实验任务" : !result ? "任务已生成" : experimentFailure ? "执行失败" : "已完成";
  const continueLabel = !revision
    ? "继续反馈评审"
    : revision.requires_follow_up === true
      ? "继续下一轮验证"
      : "继续生成报告";
  const revisionNotes = Array.isArray(revision?.revisions)
    ? revision.revisions.map(displayValue).filter(Boolean)
    : [];
  const measuredFacts = Array.isArray(iterationAnalysis?.measured_facts)
    ? iterationAnalysis.measured_facts.map(displayValue).filter(Boolean)
    : [];
  const knowledgeGaps = Array.isArray(iterationAnalysis?.knowledge_gaps)
    ? iterationAnalysis.knowledge_gaps.map(displayValue).filter(Boolean)
    : [];
  const iterationQueries = Array.isArray(iterationEvidence?.query_specs)
    ? iterationEvidence.query_specs as Array<Record<string, unknown>>
    : [];
  const iterationReferences = Array.isArray(iterationEvidence?.references)
    ? iterationEvidence.references as Array<Record<string, unknown>>
    : [];
  const optimizationCandidates = Array.isArray(iterationDecision?.optimization_candidates)
    ? iterationDecision.optimization_candidates as Array<Record<string, unknown>>
    : [];
  const selectedDirection = iterationDecision?.selected_direction
    && typeof iterationDecision.selected_direction === "object"
    ? iterationDecision.selected_direction as Record<string, unknown>
    : undefined;
  const factConflicts = Array.isArray(researchState?.conflicts)
    ? researchState.conflicts as Array<Record<string, unknown>>
    : [];
  const iterationContract = plan?.iteration_contract
    && typeof plan.iteration_contract === "object"
    ? plan.iteration_contract as Record<string, unknown>
    : undefined;
  const contractChanges = Array.isArray(iterationContract?.required_changes)
    ? iterationContract.required_changes.map(displayValue).filter(Boolean)
    : [];
  const contractControls = Array.isArray(selectedDirection?.fixed_controls)
    ? selectedDirection.fixed_controls.map(displayValue).filter(Boolean)
    : [];
  const revisionIteration = Number(revision?.iteration ?? 0);
  const optimizationReason = displayValue(
    revision?.feedback || revision?.required_revision || "上一轮实验结果需要进一步验证。",
  );
  const optimizationDirection = displayValue(
    revision?.next_action || revision?.required_revision || "根据反馈修订实验方案并再次验证。",
  );
  const canTerminate = Boolean(
    runId
    && progress?.experiment_id
    && progress.process_alive
    && ["running", "orphaned", "stalled"].includes(progress.state),
  );

  async function terminateCurrentAttempt(clearAttempt: boolean) {
    if (!runId || !progress?.experiment_id || runtimeAction) return;
    const message = clearAttempt
      ? "确认终止当前实验并清除此 attempt 的运行目录吗？历史 Artifact、数据集和其他尝试不会删除。"
      : "确认终止当前实验吗？日志和运行产物会保留。";
    if (!window.confirm(message)) return;
    setRuntimeAction(clearAttempt ? "正在终止并清除…" : "正在终止…");
    try {
      await api.terminateExperiment(runId, progress.experiment_id, clearAttempt);
      setRuntimeAction(clearAttempt ? "当前尝试已终止并清除" : "实验已终止，运行记录已保留");
    } catch (error) {
      setRuntimeAction(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <section className="panel section-card experiment-runner-card">
      <div className="section-title">
        <span>E</span>
        <h2>实验执行与结果</h2>
        <em className={`live-pill ${experimentFailure ? "failed" : ""}`}>{lifecycleMessage}</em>
      </div>

      <div className="runner-meta">
        <span><strong>{experimentId}</strong> → {resultId}</span>
        <span>Provider: <strong>{String(result?.provider ?? "local_gpu / remote_gpu")}</strong></span>
        <span>数据集: <strong>{dataset}</strong></span>
        <span>运行: <strong>{attempts.length || 0} 次</strong></span>
      </div>

      <div className="experiment-environment">
        <span>Python {displayValue(environment.python_version || "待运行")}</span>
        <span>PyTorch {displayValue(environment.torch_version || "待运行")}</span>
        <span>CUDA {displayValue(environment.torch_cuda || "未记录")}</span>
        <span>GPU {deviceNames || "未记录"}</span>
      </div>

      {revision ? (
        <article className="experiment-iteration-card" aria-live="polite">
          <div className="experiment-iteration-heading">
            <div>
              <span className="eyebrow">实验优化闭环</span>
              <h3>第 {revisionIteration || 1} 轮优化迭代</h3>
            </div>
            <span className={`experiment-iteration-state ${automaticPipelineRunning ? "running" : "paused"}`}>
              {pipelineStopRequested ? "等待当前步骤结束" : automaticPipelineRunning ? "自动迭代中" : "等待继续"}
            </span>
          </div>
          <div className="experiment-iteration-grid">
            <section>
              <span>为什么进入本轮</span>
              <p>{optimizationReason}</p>
            </section>
            <section>
              <span>下一步优化方向</span>
              <p>{optimizationDirection}</p>
            </section>
          </div>
          {revisionNotes.length ? (
            <div className="experiment-iteration-notes">
              <strong>计划调整</strong>
              <ul>{revisionNotes.slice(0, 4).map((note, index) => <li key={`${index}-${note}`}>{note}</li>)}</ul>
            </div>
          ) : null}
          {measuredFacts.length || knowledgeGaps.length ? (
            <div className="experiment-iteration-notes">
              <strong>本轮结果分析</strong>
              <ul>
                {measuredFacts.slice(0, 3).map((item, index) => <li key={`fact-${index}-${item}`}>{item}</li>)}
                {knowledgeGaps.slice(0, 2).map((item, index) => <li key={`gap-${index}-${item}`}>待补证：{item}</li>)}
              </ul>
            </div>
          ) : null}
          {iterationQueries.length || iterationReferences.length ? (
            <details className="experiment-iteration-notes">
              <summary>定向资料查询：{iterationQueries.length} 个问题，获得 {iterationReferences.length} 条已核验证据</summary>
              <ul>
                {iterationQueries.map((item, index) => (
                  <li key={`query-${index}-${displayValue(item.query)}`}>
                    {displayValue(item.question || item.reason || item.query)}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          {optimizationCandidates.length ? (
            <div className="experiment-iteration-notes">
              <strong>Qwen 比较的优化方向</strong>
              <ul>
                {optimizationCandidates.map((item, index) => {
                  const name = displayValue(item.name || `候选方向 ${index + 1}`);
                  const selected = name === displayValue(selectedDirection?.name || "");
                  return (
                    <li key={`direction-${index}-${name}`}>
                      {selected ? "已选择：" : "候选："}{name}
                      {item.changed_variable ? `（主要改变：${displayValue(item.changed_variable)}）` : ""}
                    </li>
                  );
                })}
              </ul>
              {iterationDecision?.selection_reason ? <p>选择理由：{displayValue(iterationDecision.selection_reason)}</p> : null}
            </div>
          ) : null}
          {factConflicts.length ? (
            <details className="experiment-iteration-notes">
              <summary>事实版本解析：已处理 {factConflicts.length} 项历史冲突</summary>
              <ul>
                {factConflicts.map((item, index) => (
                  <li key={`conflict-${index}-${displayValue(item.code)}`}>
                    {displayValue(item.resolution || item.code)}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          {iterationContract ? (
            <details className="experiment-iteration-notes">
              <summary>下一轮实验合同</summary>
              <ul>
                {contractChanges.slice(0, 4).map((item, index) => (
                  <li key={`contract-change-${index}-${item}`}>改变项：{item}</li>
                ))}
                {contractControls.slice(0, 4).map((item, index) => (
                  <li key={`contract-control-${index}-${item}`}>固定条件：{item}</li>
                ))}
                {selectedDirection?.success_rule ? (
                  <li>成功规则：{displayValue(selectedDirection.success_rule)}</li>
                ) : null}
                {selectedDirection?.stop_rule ? (
                  <li>停止规则：{displayValue(selectedDirection.stop_rule)}</li>
                ) : null}
              </ul>
            </details>
          ) : null}
          <div className="experiment-iteration-footer">
            <span>
              当前执行：<strong>{progress?.experiment_id || experimentId}</strong>
              {progress?.state ? ` · ${progress.state}` : ""}
            </span>
            {automaticPipelineRunning ? (
              <button className="danger-button" disabled={pipelineStopRequested} onClick={onStopPipeline}>
                {pipelineStopRequested ? "正在停止…" : "停止运行"}
              </button>
            ) : null}
          </div>
        </article>
      ) : null}

      {progress && progress.state !== "idle" ? (
        <article className={`experiment-progress-card state-${progress.state}`} aria-live="polite">
          <div className="experiment-progress-heading">
            <div>
              <span className="experiment-live-dot" />
              <strong>{progress.state === "running" ? "实验正在运行" : `实验状态：${progress.state}`}</strong>
            </div>
            <b>{formatDuration(progress.elapsed_seconds ?? 0)}</b>
          </div>
          <div className="experiment-progress-track"><span /></div>
          <div className="experiment-progress-stats">
            <span><small>当前阶段</small><strong>{progress.phase === "training" ? "模型训练与评估" : progress.phase || "准备中"}</strong></span>
            <span><small>进程</small><strong>PID {progress.pid ?? "-"} · {progress.process_alive ? "运行中" : "已结束"}</strong></span>
            <span><small>GPU</small><strong>{progress.gpu?.utilization_percent ?? "-"}% · {progress.gpu?.temperature_c ?? "-"}°C</strong></span>
            <span><small>显存</small><strong>{progress.gpu?.memory_used_mb ?? "-"} / {progress.gpu?.memory_total_mb ?? "-"} MB</strong></span>
            <span><small>日志</small><strong>{progress.log_bytes ?? 0} bytes</strong></span>
            <span><small>结果文件</small><strong>{progress.result_ready ? "已生成" : "等待生成"}</strong></span>
          </div>
          <div className="experiment-waiting-note">
            <span>训练时间不设上限</span>
            <span>页面每 2 分钟自动刷新运行状态</span>
          </div>
          {canTerminate ? (
            <div className="button-row experiment-runtime-actions">
              <button className="secondary-button" disabled={Boolean(runtimeAction)} onClick={() => terminateCurrentAttempt(false)}>
                终止实验
              </button>
              <button className="danger-button" disabled={Boolean(runtimeAction)} onClick={() => terminateCurrentAttempt(true)}>
                终止并清除此尝试
              </button>
            </div>
          ) : null}
          {runtimeAction ? <p className="experiment-runtime-message">{runtimeAction}</p> : null}
          {progress.log_tail ? <pre className="experiment-live-log">{progress.log_tail}</pre> : null}
        </article>
      ) : null}

      {diagnosis ? (
        <article className={`experiment-diagnosis-card ${diagnosis.resolved === true ? "resolved" : "unresolved"}`}>
          <div className="experiment-diagnosis-heading">
            <div className="experiment-diagnosis-title">
              <span className="experiment-diagnosis-icon" aria-hidden="true">
                {diagnosis.resolved === true ? <CheckCircle2 size={20} /> : <AlertTriangle size={20} />}
              </span>
              <div>
                <span className="eyebrow">DIAGNOSTIC AGENT</span>
                <h3>故障诊断专家</h3>
              </div>
            </div>
            <span className={`experiment-diagnosis-status ${diagnosis.resolved === true ? "resolved" : "error"}`}>
              {diagnosis.resolved === true ? "修复并验证成功" : "需要处理"}
            </span>
          </div>
          <div className="experiment-diagnosis-summary">
            <span>诊断摘要</span>
            <p>{displayValue(diagnosis.user_message || diagnosis.root_cause)}</p>
          </div>
          <dl className="experiment-diagnosis-details">
            <div className="diagnosis-field compact"><dt>错误类型</dt><dd>{displayValue(diagnosis.category || "unknown")}</dd></div>
            <div className="diagnosis-field compact"><dt>错误代码</dt><dd className="diagnosis-code">{displayValue(diagnosis.error_code || "-")}</dd></div>
            <div className="diagnosis-field wide"><dt>根本原因</dt><dd>{displayValue(diagnosis.root_cause || "尚未识别")}</dd></div>
            <div className="diagnosis-field compact"><dt><Wrench size={13} />修复动作</dt><dd>{displayValue(diagnosis.repair_action || "none")}</dd></div>
            <div className="diagnosis-field compact"><dt>修复结果</dt><dd>{displayValue(repairResult?.status || "未执行")}</dd></div>
            <div className="diagnosis-field wide next-action"><dt><ChevronRight size={14} />下一步</dt><dd>{displayValue(diagnosis.next_action || "查看运行日志")}</dd></div>
          </dl>
          {diagnosisEvidence.length ? (
            <details className="experiment-diagnosis-evidence">
              <summary>查看诊断证据（{diagnosisEvidence.length} 条）</summary>
              <ul>{diagnosisEvidence.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
            </details>
          ) : null}
        </article>
      ) : null}

      {metricComparisons.length ? (
        <div className="metric-comparison-grid" aria-label="实验指标对比">
          {metricComparisons.map((comparison) => (
            <article className="metric-comparison-card" key={comparison.key}>
              <div>
                <span>{comparison.label}</span>
                <em className={`metric-verdict ${comparison.verdict}`}>{verdictLabels[comparison.verdict]}</em>
              </div>
              <strong>{formatMetricValue(comparison.improved)}</strong>
              <small>
                基线 {formatMetricValue(comparison.baseline)}
                <b className={comparison.delta > 0 ? "positive" : comparison.delta < 0 ? "negative" : "neutral"}>
                  {comparison.delta > 0 ? "+" : ""}{formatMetricValue(comparison.delta)}
                </b>
              </small>
            </article>
          ))}
        </div>
      ) : null}

      {(datasetProfile || baselineProfile || resultEvidence) ? (
        <article className="experiment-result-block phase2-evidence-block" aria-label="Phase 2 evidence">
          <h3>可信基线与确定性证据</h3>
          <dl className="experiment-config-list">
            <div><dt>DatasetProfile</dt><dd>{displayValue(datasetProfile?.contract_id || "等待数据检查")}</dd></div>
            <div><dt>Baseline</dt><dd>{displayValue(baselineProfile?.name || "等待基线")}</dd></div>
            <div><dt>复现状态</dt><dd>{displayValue(baselineProfile?.reproduction_status || "未记录")}</dd></div>
            <div><dt>阶段</dt><dd>{displayValue(resultEvidence?.stage || phase2Protocol?.stage || "smoke")}</dd></div>
            <div><dt>Primary / Secondary</dt><dd>{displayValue(fairContract?.primary_metric || "未声明")} / {displayValue(fairContract?.secondary_metrics || [])}</dd></div>
            <div><dt>Epoch / Seed</dt><dd>{displayValue(phase2Protocol?.epochs || fairContract?.epochs || "未声明")} / {displayValue(phase2Protocol?.seeds || fairContract?.seeds || [])}</dd></div>
            <div><dt>Baseline vs Idea</dt><dd>{displayValue(resultEvidence?.baseline?.mean ?? "等待配对结果")} / {displayValue(resultEvidence?.idea?.mean ?? "等待配对结果")}</dd></div>
            <div><dt>Mean / Std / Paired Delta</dt><dd>{displayValue(resultEvidence?.mean_delta ?? "-")} / {displayValue(resultEvidence?.delta_std ?? "-")} / {displayValue(resultEvidence?.paired_delta ?? {})}</dd></div>
            <div><dt>当前路由动作</dt><dd>{displayValue(resultEvidence?.route || "等待可比较结果")}</dd></div>
          </dl>
        </article>
      ) : null}

      <div className="runner-grid">
        <article className="experiment-result-block experiment-log-block">
          <h3>产物与日志</h3>
          <pre className="log-window">{result
            ? experimentFailure
              ? `[日志] ${logPath || "未生成"}\n[结果] ${metricsPath || "未生成"}\n[状态] 实验失败\n[错误] ${experimentFailure}`
              : `[日志] ${logPath}\n[结果] ${metricsPath}\n[状态] 真实实验结果已写入 artifact`
            : task
              ? `[任务] ${entrypoint}\n[状态] 等待实验运行`
              : plan
                ? "[等待] 等待生成实验任务"
                : "[等待] 等待实验设计"}</pre>
          {runId && result ? (
            <div className="experiment-file-actions">
              <a className="secondary-button" href={experimentFileUrl(runId, "result")}>下载结果 JSON</a>
              <a className="secondary-button" href={experimentFileUrl(runId, "log")}>下载完整日志</a>
              <a className="secondary-button" href={experimentFileUrl(runId, "code")}>下载训练代码</a>
              <a className="secondary-button" href={experimentFileUrl(runId, "manifest")}>下载运行清单</a>
            </div>
          ) : null}
        </article>

        <article className="experiment-result-block experiment-config-block">
          <h3>运行配置</h3>
          <dl className="experiment-config-list">
            <div><dt>入口</dt><dd>{entrypoint}</dd></div>
            <div><dt>Seeds</dt><dd>{seeds.length ? seeds.map(displayValue).join(", ") : "默认"}</dd></div>
            <div><dt>GPU</dt><dd>{manifest.requires_gpu === true ? "必需" : "非必需"}</dd></div>
            {parameterEntries.map(([name, value]) => (
              <div key={name}><dt>{name}</dt><dd>{displayValue(value)}</dd></div>
            ))}
          </dl>
        </article>

        <article className="experiment-result-block experiment-metrics-block">
          <h3>指标明细</h3>
          <table className="metrics-table">
            {metricComparisons.length ? (
              <>
                <thead><tr><th>指标</th><th>基线</th><th>改进后</th><th>变化</th><th>结论</th></tr></thead>
                <tbody>
                  {metricComparisons.map((comparison) => (
                    <tr key={comparison.key}>
                      <td>{comparison.label}</td>
                      <td>{formatMetricValue(comparison.baseline)}</td>
                      <td>{formatMetricValue(comparison.improved)}</td>
                      <td>{comparison.delta > 0 ? "+" : ""}{formatMetricValue(comparison.delta)}</td>
                      <td><span className={`mini-tag metric-${comparison.verdict}`}>{verdictLabels[comparison.verdict]}</span></td>
                    </tr>
                  ))}
                </tbody>
              </>
            ) : (
              <>
                <thead><tr><th>指标</th><th>方向</th><th>判定方式</th><th>结果</th></tr></thead>
                <tbody>
                  {metricRows.length ? metricRows.map((row) => (
                    <tr key={row.key}><td>{row.label}</td><td>{row.direction}</td><td>{row.criterion}</td><td>{row.result}</td></tr>
                  )) : <tr><td colSpan={4}>等待实验指标</td></tr>}
                </tbody>
              </>
            )}
          </table>
        </article>

        <article className="experiment-result-block experiment-attempts-block">
          <h3>运行记录</h3>
          <table className="experiment-attempt-table">
            <thead><tr><th>Attempt</th><th>状态</th><th>开始</th><th>结束</th><th>错误</th></tr></thead>
            <tbody>
              {attempts.length ? attempts.map((attempt, index) => (
                <tr key={String(attempt.attempt ?? index)}>
                  <td>{displayValue(attempt.attempt ?? index + 1)}</td>
                  <td><span className={`mini-tag ${attempt.status === "failed" ? "error" : ""}`}>{displayValue(attempt.status ?? "unknown")}</span></td>
                  <td>{formatTimestamp(attempt.start_time)}</td>
                  <td>{formatTimestamp(attempt.end_time)}</td>
                  <td>{displayValue(attempt.error_code || "-")}</td>
                </tr>
              )) : <tr><td colSpan={5}>等待实验运行记录</td></tr>}
            </tbody>
          </table>
        </article>

        {series ? (
          <article className="experiment-result-block experiment-chart-block">
            <h3>{series.key} 趋势</h3>
            <svg className="metric-chart" viewBox="0 0 320 180" role="img" aria-label={`${series.key} 趋势`}>
              <polyline points={series.values.map((value, index) => {
                const x = 20 + (285 * index) / (series.values.length - 1);
                const y = 150 - ((value - seriesBounds.minimum) / seriesBounds.range) * 120;
                return `${x},${y}`;
              }).join(" ")} />
              {series.values.map((value, index) => {
                const x = 20 + (285 * index) / (series.values.length - 1);
                const y = 150 - ((value - seriesBounds.minimum) / seriesBounds.range) * 120;
                return <circle key={index} cx={x} cy={y} r="3" />;
              })}
            </svg>
          </article>
        ) : null}
      </div>

      <div className="button-row experiment-actions">
        <button
          className="secondary-button"
          disabled={!canRerunExperiment || isBusy}
          onClick={() => onRerunFrom("experiment_run_analysis")}
          title={isBusy ? "科研 Pipeline 正在运行" : canRerunExperiment ? "使用当前实验 Bundle 重新运行并继续反馈" : "请先生成实验任务"}
        >
          重新运行实验
        </button>
        {canContinuePipeline ? (
          <button className="primary-button" disabled={isBusy} onClick={onContinuePipeline}>
            {continueLabel}
          </button>
        ) : null}
      </div>
    </section>
  );
}
