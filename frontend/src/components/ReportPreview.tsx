import type { Artifact } from "../api/types";
import { reportDownloadUrl } from "../api/client";
import { findLatestArtifact, findLatestExperimentResultForTask } from "../utils/presentation";

export function ReportPreview({
  report,
  runId,
  artifacts,
  isBusy,
  onRunStep,
}: {
  report: Record<string, unknown> | null;
  runId?: string;
  artifacts: Artifact[];
  isBusy: boolean;
  onRunStep: (stepId: string) => void;
}) {
  const evidence = findLatestArtifact(artifacts, "evidence");
  const result = findLatestExperimentResultForTask(artifacts);
  const revision = findLatestArtifact(artifacts, "revision");
  const reportArtifact = findLatestArtifact(artifacts, "report");
  const reportAuditArtifact = findLatestArtifact(artifacts, "report_audit");
  const hasEvidence = Array.isArray(evidence?.content.references)
    && evidence.content.references.some((reference) => (
      typeof reference === "object"
      && reference !== null
      && (reference as Record<string, unknown>).verified === true
    ));
  const hasAuditedResult = result?.content.is_real_experiment === true;
  const revisionMatchesResult = Boolean(revision && result && revision.parent_artifact_id === result.id);
  const requiresFollowUp = revision?.content.requires_follow_up === true;
  const hasReport = Boolean(report || reportArtifact);
  const audit = result?.content.audit && typeof result.content.audit === "object"
    ? result.content.audit as Record<string, unknown>
    : undefined;
  const auditIssues = Array.isArray(audit?.issues) ? audit.issues.map(String) : [];
  const reportFailures = Array.isArray(reportAuditArtifact?.content.hard_failures)
    ? reportAuditArtifact.content.hard_failures as Array<Record<string, unknown>>
    : [];
  const blockers: string[] = [];
  if (!hasEvidence) blockers.push("缺少已验证的参考文献。文献全文无需下载，但 DOI 或 arXiv 等元数据必须核验。");
  if (!result) blockers.push("尚未生成实验结果。");
  else if (!hasAuditedResult) blockers.push("实验文件已经生成，但完整性审计未通过，暂不能作为真实结果导出。");
  if (!revisionMatchesResult) blockers.push("最新实验结果尚未经过反馈评审。");
  else if (requiresFollowUp) blockers.push("反馈评审要求继续优化和验证。");
  const canGenerate = Boolean(runId && !blockers.length && !hasReport);

  return (
    <section className="panel section-card report-card">
      <div className="section-title">
        <span>G</span>
        <h2>研究汇报与产物导出</h2>
        <em className={`live-pill ${blockers.length ? "failed" : ""}`}>
          {hasReport ? "已生成" : blockers.length ? "尚未就绪" : "可以生成"}
        </em>
      </div>
      <p className="section-description">
        下载包只保留研究报告 Word、实验代码和实验结果。论文写作在报告完成后单独启动。
      </p>
      <ul className="report-checklist">
        <li className={hasEvidence ? "done" : "pending"}>
          真实文献 <span>{hasEvidence ? "元数据已核验" : "待核验"}</span>
        </li>
        <li className={hasAuditedResult ? "done" : "pending"}>
          实验结果 <span>{hasAuditedResult ? "审计通过" : result ? "审计未通过" : "待运行"}</span>
        </li>
        <li className={revisionMatchesResult && !requiresFollowUp ? "done" : "pending"}>
          反馈修正 <span>{revisionMatchesResult ? requiresFollowUp ? "需要继续验证" : "已完成" : "待评审"}</span>
        </li>
        <li className={hasReport ? "done" : "pending"}>
          汇报文件 <span>{hasReport ? "Word 与实验材料可下载" : "未生成"}</span>
        </li>
      </ul>

      {blockers.length ? (
        <div className="report-blockers">
          <strong>为什么现在还不能生成报告</strong>
          <ul>{blockers.map((item) => <li key={item}>{item}</li>)}</ul>
          {auditIssues.length ? (
            <details>
              <summary>查看完整性审计问题（{auditIssues.length} 项）</summary>
              <ol>{auditIssues.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ol>
            </details>
          ) : null}
        </div>
      ) : null}

      {!hasReport && reportFailures.length ? (
        <div className="report-blockers">
          <strong>上一次报告生成保留了可修复的事实冲突</strong>
          <ul>
            {reportFailures.map((item, index) => (
              <li key={`${index}-${String(item.code ?? "")}`}>
                {String(item.section_id ?? "相关章节")}：{String(item.claim ?? "存在无来源表述")}
                {item.required_correction ? `；建议修正为：${String(item.required_correction)}` : ""}
              </li>
            ))}
          </ul>
          <p>报告草稿与审核依据已保留；重新生成时会继续使用当前有效事实。</p>
        </div>
      ) : null}

      <div className="report-actions">
        {!hasReport ? (
          <button
            className="primary-button"
            disabled={!canGenerate || isBusy}
            onClick={() => onRunStep("report_export")}
            title={isBusy ? "科研 Pipeline 正在运行" : canGenerate ? "生成研究汇报" : blockers.join(" ")}
          >
            生成研究汇报
          </button>
        ) : null}
        {runId && hasReport ? (
          <>
            <a className="primary-button" href={reportDownloadUrl(runId)}>下载研究产物</a>
            <a className="secondary-button" href={reportDownloadUrl(runId, "docx")}>单独下载 Word</a>
          </>
        ) : null}
      </div>
      {runId && hasReport ? (
        <p className="report-footnote">下载包中不再包含 HTML、Markdown、MANIFEST 或文献全文。</p>
      ) : null}
    </section>
  );
}
