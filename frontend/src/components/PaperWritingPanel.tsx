import { useEffect, useState } from "react";
import { api, paperDownloadUrl } from "../api/client";
import type { PaperWritingState } from "../api/types";

const ACTIVE = new Set(["queued", "planning", "writing", "auditing", "revising"]);

const EMPTY_STATE: PaperWritingState = {
  status: "not_started",
  stage: "尚未开始",
  progress: 0,
  config: {},
  plan: {},
  sections: [],
  audit: {},
  active_skill: "",
  current_section: "",
  completed_sections: 0,
  total_sections: 0,
  stop_requested: false,
  error: "",
  started_at: "",
  updated_at: "",
  completed_at: "",
};

export function PaperWritingPanel({
  runId,
  hasReport,
}: {
  runId?: string;
  hasReport: boolean;
}) {
  const [state, setState] = useState<PaperWritingState>(EMPTY_STATE);
  const [venue, setVenue] = useState("未指定");
  const [language, setLanguage] = useState<"zh-CN" | "en">("zh-CN");
  const [paperType, setPaperType] = useState("实验研究论文");
  const [authors, setAuthors] = useState("");
  const [notes, setNotes] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!runId || !hasReport) {
      setState(EMPTY_STATE);
      return;
    }
    let cancelled = false;
    const refresh = async () => {
      try {
        const value = await api.getPaperWriting(runId);
        if (!cancelled) setState(value);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, ACTIVE.has(state.status) ? 2_000 : 5_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [runId, hasReport, state.status]);

  async function perform(action: () => Promise<PaperWritingState>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      setState(await action());
      setFeedback("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  const planSections = Array.isArray(state.plan.sections)
    ? state.plan.sections as Array<Record<string, unknown>>
    : [];
  const contributions = Array.isArray(state.plan.contributions)
    ? state.plan.contributions.map(String)
    : [];
  const auditIssues = Array.isArray(state.audit.issues)
    ? state.audit.issues.map(String)
    : [];

  return (
    <section className="panel section-card paper-writing-card">
      <div className="section-title">
        <span>H</span>
        <h2>进一步撰写论文</h2>
        <em className={`live-pill ${state.status === "failed" ? "failed" : ""}`}>
          {statusLabel(state.status)}
        </em>
      </div>
      <p className="section-description">
        这是报告完成后的独立流程。Qwen 会调用论文 Skill 规划、逐章写作并进行实验数值与引用审计。
      </p>

      {!hasReport ? <p className="inline-note">研究报告完成后才能开始论文写作。</p> : null}

      {hasReport && state.status === "not_started" ? (
        <div className="paper-settings">
          <label>
            投稿方向或期刊
            <input value={venue} onChange={(event) => setVenue(event.target.value)} placeholder="例如：中文核心期刊、ICLR" />
          </label>
          <label>
            论文语言
            <select value={language} onChange={(event) => setLanguage(event.target.value as "zh-CN" | "en")}>
              <option value="zh-CN">中文</option>
              <option value="en">英文</option>
            </select>
          </label>
          <label>
            论文类型
            <input value={paperType} onChange={(event) => setPaperType(event.target.value)} />
          </label>
          <label>
            作者信息
            <input value={authors} onChange={(event) => setAuthors(event.target.value)} placeholder="可稍后补充" />
          </label>
          <label className="paper-wide-field">
            写作要求
            <textarea value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="例如：突出负结果、控制在 8 页以内" />
          </label>
          <button
            className="primary-button"
            disabled={busy}
            onClick={() => runId && perform(() => api.startPaperWriting(runId, {
              venue,
              language,
              paper_type: paperType,
              authors,
              notes,
            }))}
          >
            开始规划论文
          </button>
        </div>
      ) : null}

      {ACTIVE.has(state.status) ? (
        <div className="paper-progress">
          <div className="paper-progress-head">
            <strong>{state.stage}</strong>
            <span>{state.progress}%</span>
          </div>
          <div className="paper-progress-track"><i style={{ width: `${state.progress}%` }} /></div>
          <dl>
            <div><dt>正在使用</dt><dd>{state.active_skill || "准备中"}</dd></div>
            <div><dt>当前章节</dt><dd>{state.current_section || "—"}</dd></div>
            <div><dt>章节进度</dt><dd>{state.completed_sections}/{state.total_sections || "—"}</dd></div>
          </dl>
          <button
            className="danger-button"
            disabled={state.stop_requested || busy}
            onClick={() => runId && perform(() => api.stopPaperWriting(runId))}
          >
            {state.stop_requested ? "正在停止" : "暂停论文写作"}
          </button>
        </div>
      ) : null}

      {state.status === "waiting_plan_confirmation" ? (
        <div className="paper-checkpoint">
          <h3>论文大纲等待确认</h3>
          <h4>{String(state.plan.title || "未命名论文")}</h4>
          {contributions.length ? (
            <>
              <strong>计划主张的贡献</strong>
              <ul>{contributions.map((item) => <li key={item}>{item}</li>)}</ul>
            </>
          ) : null}
          <strong>章节安排</strong>
          <ol>
            {planSections.map((section, index) => (
              <li key={String(section.id || index)}>
                <b>{String(section.title || `第 ${index + 1} 节`)}</b>
                <span>{String(section.purpose || "")}</span>
              </li>
            ))}
          </ol>
          <textarea
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="需要修改大纲时填写意见；留空表示确认并继续写作。"
          />
          <div className="report-actions">
            <button
              className="primary-button"
              disabled={busy}
              onClick={() => runId && perform(() => api.confirmPaperPlan(runId, feedback))}
            >
              {feedback.trim() ? "让 Qwen 修改大纲" : "确认大纲并开始写作"}
            </button>
          </div>
        </div>
      ) : null}

      {state.status === "waiting_final_confirmation" ? (
        <div className="paper-checkpoint">
          <h3>论文初稿等待最终确认</h3>
          <p>{String(state.audit.summary || "Qwen 已完成初稿审计。")}</p>
          {auditIssues.length ? (
            <div className="report-blockers">
              <strong>审计发现的问题</strong>
              <ul>{auditIssues.map((item) => <li key={item}>{item}</li>)}</ul>
            </div>
          ) : null}
          <details>
            <summary>查看已完成章节（{state.sections.length}）</summary>
            {state.sections.map((section, index) => (
              <article key={section.id || index}>
                <h4>{section.title}</h4>
                <p>{section.content?.slice(0, 500)}</p>
              </article>
            ))}
          </details>
          <textarea
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder="填写修改意见会让 Qwen 逐章修订；留空表示确认最终稿。"
          />
          <button
            className="primary-button"
            disabled={busy}
            onClick={() => runId && perform(() => api.finalizePaper(runId, feedback))}
          >
            {feedback.trim() ? "提交修改意见" : "确认最终稿"}
          </button>
        </div>
      ) : null}

      {state.status === "completed" && runId ? (
        <div className="paper-complete">
          <p>论文已经通过最终确认，可以分别下载可编辑 Word 与 LaTeX 源文件。</p>
          <div className="report-actions">
            <a className="primary-button" href={paperDownloadUrl(runId, "docx")}>下载论文 Word</a>
            <a className="secondary-button" href={paperDownloadUrl(runId, "latex")}>下载 LaTeX 源文件</a>
          </div>
        </div>
      ) : null}

      {state.status === "interrupted" ? (
        <div className="report-blockers">
          <strong>论文写作已暂停</strong>
          <p>{state.error || "可以继续生成或重新开始。"}</p>
          <button
            className="primary-button"
            disabled={busy}
            onClick={() => runId && perform(() => api.confirmPaperPlan(runId))}
          >
            从当前阶段继续
          </button>
        </div>
      ) : null}

      {state.status === "failed" || error ? (
        <div className="report-blockers">
          <strong>论文写作出现问题</strong>
          <p>{error || state.error}</p>
        </div>
      ) : null}
    </section>
  );
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    not_started: "可选",
    queued: "等待开始",
    planning: "规划中",
    waiting_plan_confirmation: "等待确认大纲",
    writing: "写作中",
    auditing: "审计中",
    waiting_final_confirmation: "等待确认初稿",
    revising: "修订中",
    completed: "已完成",
    interrupted: "已暂停",
    failed: "失败",
  };
  return labels[status] || status;
}
