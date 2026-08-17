import type { ReactNode } from "react";
import type { Artifact } from "../api/types";
import { findLatestArtifactContent } from "../utils/presentation";

const PLAN_LABELS: Record<string, string> = {
  hypotheses: "实验假设",
  dataset: "数据集",
  comparisons: "对比设计",
  evaluations: "评估方案",
  methods: "方法",
  baselines: "基线",
  metrics: "指标",
  expected_result: "预期结果",
  expected_results: "预期结果",
  execution: "执行设置",
  resources: "资源配置",
  constraints: "实验约束",
  risks: "风险控制",
  normalization: "结构化记录",
};

const HIDDEN_PLAN_KEYS = new Set(["provider_mode", "fallback_used", "fallback_reason"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function labelFor(key: string) {
  return PLAN_LABELS[key] ?? key.split("_").join(" ");
}

function renderValue(value: unknown): ReactNode {
  if (value === null || value === undefined || value === "") return "未提供";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    if (value.length === 0) return "未提供";
    return (
      <ul className="plan-value-list">
        {value.map((item, index) => (
          <li key={index}>{renderValue(item)}</li>
        ))}
      </ul>
    );
  }
  if (isRecord(value)) {
    const entries = Object.entries(value).filter(([, entryValue]) => entryValue !== undefined && entryValue !== "");
    if (entries.length === 0) return "未提供";
    return (
      <dl className="plan-object">
        {entries.map(([key, entryValue]) => (
          <div key={key}>
            <dt>{labelFor(key)}</dt>
            <dd>{renderValue(entryValue)}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return String(value);
}

function getPlanSections(plan: Record<string, unknown> | undefined) {
  if (!plan) return [];
  return Object.entries(plan).filter(([key]) => !HIDDEN_PLAN_KEYS.has(key));
}

export function ArtifactEditor({ artifacts }: { artifacts: Artifact[] }) {
  const plan = findLatestArtifactContent(artifacts, "plan");
  const sections = getPlanSections(plan);

  return (
    <section className="panel section-card design-card plan-linked-design-card">
      <div className="section-title"><span>D</span><h2>实验设计</h2></div>
      {sections.length ? (
        <dl className="design-list dynamic-plan-list">
          {sections.map(([key, value]) => (
            <div key={key}>
              <dt>{labelFor(key)}</dt>
              <dd>{renderValue(value)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <div className="empty-state">等待研究计划生成</div>
      )}
    </section>
  );
}
