import { useMemo, useState } from "react";
import type { Artifact } from "../api/types";
import { findLatestArtifact } from "../utils/presentation";

const MAX_CANDIDATES = 5;

const assessmentLabels: Record<string, string> = {
  verified: "验证通过",
  evidence_insufficient: "证据不足",
  rejected: "未通过",
  revised: "已自动修订",
  reviewed: "已评估",
};

type Props = {
  artifacts: Artifact[];
  runId?: string;
  activeStepId: string | null;
  isBusy: boolean;
  onAddUserHypothesis: (claim: string) => Promise<void>;
  onSelectHypothesis: (candidateIndex: number) => Promise<void>;
};

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function claimFrom(value: unknown, fallback: string) {
  const record = asRecord(value);
  return typeof record?.claim === "string" && record.claim.trim() ? record.claim : fallback;
}

function textFrom(value: unknown, key: string) {
  const record = asRecord(value);
  return typeof record?.[key] === "string" ? String(record[key]).trim() : "";
}

function evidenceBasis(value: unknown) {
  const record = asRecord(value);
  if (!Array.isArray(record?.evidence_basis)) return [];
  return record.evidence_basis
    .map(asRecord)
    .filter((item): item is Record<string, unknown> => Boolean(item));
}

function stringList(value: unknown) {
  return Array.isArray(value)
    ? value.map((item) => String(item ?? "").trim()).filter(Boolean)
    : [];
}

export function HypothesisBoard({
  artifacts,
  runId,
  activeStepId,
  isBusy,
  onAddUserHypothesis,
  onSelectHypothesis,
}: Props) {
  const [draftOpen, setDraftOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const hypothesisArtifact = findLatestArtifact(artifacts, "hypothesis");
  const reasoningArtifact = findLatestArtifact(artifacts, "reasoning");
  const selectionArtifact = findLatestArtifact(artifacts, "hypothesis_selection");
  const candidates = useMemo(() => {
    const value = hypothesisArtifact?.content.candidates;
    if (!Array.isArray(value)) return [];
    return value
      .filter((candidate): candidate is Record<string, unknown> => Boolean(asRecord(candidate)))
      .slice(0, MAX_CANDIDATES);
  }, [hypothesisArtifact]);
  const assessmentsByCandidateIndex = useMemo(() => {
    const assessments = new Map<number, Record<string, unknown>>();
    const value = reasoningArtifact?.content.candidate_assessments;
    if (!Array.isArray(value)) return assessments;
    for (const item of value) {
      const assessment = asRecord(item);
      if (typeof assessment?.candidate_index === "number") {
        assessments.set(assessment.candidate_index, assessment);
      }
    }
    return assessments;
  }, [reasoningArtifact]);
  const isReasoning = activeStepId === "evidence_reasoning";
  const selectedIndexes = Array.isArray(selectionArtifact?.content.selected_indexes)
    ? selectionArtifact.content.selected_indexes
    : [];
  const selectedIndex = typeof selectedIndexes[0] === "number" ? selectedIndexes[0] : null;
  const selectionRequired = Boolean(reasoningArtifact && selectedIndex === null && !isReasoning);
  const canSubmitDraft = Boolean(runId && draft.trim() && !isBusy);

  async function submitUserHypothesis() {
    const claim = draft.trim();
    if (!runId || !claim || !canSubmitDraft) return;
    await onAddUserHypothesis(claim);
    setDraft("");
    setDraftOpen(false);
  }

  return (
    <section className="panel section-card hypothesis-card">
      <div className="section-title">
        <span>C</span>
        <h2>候选假设与证据推理</h2>
        <button className="secondary-button" disabled={!runId || isBusy} onClick={() => setDraftOpen(true)}>新增假设</button>
      </div>
      {selectionRequired ? (
        <div className="hypothesis-selection-notice" role="status">
          <strong>假设推理已完成，请选择一个假设</strong>
          <p>模型已经补充了每个候选的证据、风险和可验证方式。请选择你希望继续研究的方向，选择后系统才会生成研究计划并进入实验阶段。</p>
        </div>
      ) : selectedIndex !== null ? (
        <div className="hypothesis-selection-notice selected" role="status">
          <strong>已选择候选 {String(selectedIndex + 1).padStart(2, "0")}</strong>
          <p>系统将以你的选择作为唯一后续研究方向。</p>
        </div>
      ) : null}
      {draftOpen ? (
        <div className="hypothesis-input">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="输入你的候选假设。提交后系统会重新运行证据推理，逐项核验全部候选。"
          />
          <div className="button-row">
            <button className="secondary-button" onClick={() => setDraftOpen(false)}>取消</button>
            <button className="primary-button" disabled={!canSubmitDraft} onClick={submitUserHypothesis}>提交并重新运行证据推理</button>
          </div>
        </div>
      ) : null}
      {candidates.length ? (
        <div className="hypothesis-grid">
          {candidates.map((candidate, index) => {
            const assessment = assessmentsByCandidateIndex.get(index);
            const rawStatus = typeof assessment?.status === "string" ? assessment.status : "waiting";
            const isSelected = selectedIndex === index;
            const statusLabel = isSelected
              ? "用户已选择"
              : isReasoning
              ? "正在推理"
              : assessmentLabels[rawStatus] ?? "等待推理";
            const wasRevised = !isReasoning && assessment?.was_revised === true;
            const currentClaim = claimFrom(candidate, "未命名候选假设");
            const originalClaim = claimFrom(assessment?.original_hypothesis, currentClaim);
            const revisedClaim = claimFrom(assessment?.revised_hypothesis, currentClaim);
            const revisionReason = typeof assessment?.revision_reason === "string" && assessment.revision_reason.trim()
              ? assessment.revision_reason
              : "证据推理对假设边界进行了调整。";

            const displayedHypothesis = wasRevised ? assessment?.revised_hypothesis : candidate;
            const method = textFrom(displayedHypothesis, "method") || textFrom(candidate, "method");
            const mechanism = textFrom(displayedHypothesis, "mechanism") || textFrom(candidate, "mechanism");
            const revisedGrounds = evidenceBasis(displayedHypothesis);
            const grounds = revisedGrounds.length ? revisedGrounds : evidenceBasis(candidate);
            const evaluation = asRecord(assessment?.evaluation);
            const decision = typeof evaluation?.decision === "string" ? evaluation.decision : "";
            const confidence = typeof evaluation?.confidence === "string" ? evaluation.confidence : "";
            const risks = stringList(evaluation?.risks);
            const unknowns = stringList(evaluation?.unknowns);
            const gates = Object.entries(asRecord(evaluation?.gates) ?? {});
            const scores = Object.entries(asRecord(evaluation?.scores) ?? {})
              .filter(([, value]) => typeof value === "number");

            return (
              <article className={`hypothesis-tile ${isSelected ? "status-selected" : isReasoning ? "status-reasoning" : `status-${rawStatus}`}`} key={`${currentClaim}-${index}`}>
                <div className="hypothesis-tile-heading">
                  <h3>候选 {String(index + 1).padStart(2, "0")}</h3>
                  <span className="hypothesis-status">{statusLabel}</span>
                </div>
                {wasRevised ? (
                  <dl className="hypothesis-revision">
                    <dt>原始假设</dt><dd>{originalClaim}</dd>
                    <dt>修订假设</dt><dd>{revisedClaim}</dd>
                    <dt>修订原因</dt><dd>{revisionReason}</dd>
                  </dl>
                ) : (
                  <dl>
                    <dt>当前假设</dt><dd>{currentClaim}</dd>
                  </dl>
                )}
                <div className="hypothesis-method-card">
                  <strong>拟采用的方法</strong>
                  <p>{method || "等待补充可执行的方法与干预变量。"}</p>
                  {mechanism ? <><strong>作用机制</strong><p>{mechanism}</p></> : null}
                </div>
                <div className="hypothesis-evidence-basis">
                  <strong>依据与证据类型</strong>
                  {grounds.length ? (
                    <ul>
                      {grounds.slice(0, 3).map((item, basisIndex) => {
                        const statement = String(item.statement ?? "");
                        const sourceTitle = String(item.source_title ?? "");
                        const sourceUrl = String(item.source_url ?? "");
                        const evidenceType = String(item.evidence_type ?? "INFERENCE");
                        return (
                          <li key={`${statement}-${basisIndex}`}>
                            <span className="evidence-kind">{evidenceType}</span>
                            <span>{statement}</span>
                            {sourceUrl ? <a href={sourceUrl} target="_blank" rel="noreferrer">{sourceTitle || "来源"}</a> : sourceTitle ? <small>{sourceTitle}</small> : null}
                          </li>
                        );
                      })}
                    </ul>
                  ) : <p>当前为待验证推断，尚无可追溯依据。</p>}
                </div>
                {assessment ? (
                  <div className="hypothesis-model-review">
                    <div className="hypothesis-review-heading">
                      <strong>模型评估</strong>
                      {decision ? <span>{decision}{confidence ? ` · 置信度 ${confidence}` : ""}</span> : null}
                    </div>
                    {gates.length ? (
                      <div className="hypothesis-review-tags">
                        {gates.map(([name, value]) => (
                          <span key={name}>{name}: {String(value)}</span>
                        ))}
                      </div>
                    ) : null}
                    {scores.length ? (
                      <dl className="hypothesis-score-grid">
                        {scores.map(([name, value]) => (
                          <div key={name}><dt>{name}</dt><dd>{String(value)}</dd></div>
                        ))}
                      </dl>
                    ) : null}
                    {risks.length ? (
                      <div className="hypothesis-review-list">
                        <strong>主要风险</strong>
                        <ul>{risks.map((item) => <li key={item}>{item}</li>)}</ul>
                      </div>
                    ) : null}
                    {unknowns.length ? (
                      <div className="hypothesis-review-list">
                        <strong>仍需确认</strong>
                        <ul>{unknowns.map((item) => <li key={item}>{item}</li>)}</ul>
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {selectionRequired ? (
                  <button
                    className="primary-button hypothesis-select-button"
                    disabled={isBusy}
                    onClick={() => onSelectHypothesis(index)}
                  >
                    选择此假设并继续
                  </button>
                ) : null}
              </article>
            );
          })}
        </div>
      ) : (
        <div className="empty-state">等待生成候选假设</div>
      )}
    </section>
  );
}
