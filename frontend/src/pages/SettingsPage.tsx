import { BookOpen, ClipboardCheck, FileText, FlaskConical, Lightbulb } from "lucide-react";
import type { ProviderStatus } from "../api/types";

export function SettingsPage({ status }: { status: ProviderStatus | null }) {
  const llm = status?.llm;
  const literature = status?.literature;
  const experiment = status?.experiment;
  const competitionReady = Boolean(llm?.ready && literature?.ready && experiment?.ready);
  return (
    <section className="status-strip" aria-label="Provider and project status">
      <article className="status-card">
        <div className="status-icon blue"><ClipboardCheck size={26} /></div>
        <div>
          <span className="eyebrow">整体项目状态</span>
          <strong>{competitionReady ? "已就绪" : "进行中"}</strong>
          <div className="progress-track"><span style={{ width: competitionReady ? "100%" : "65%" }} /></div>
          <small>{competitionReady ? "所有 Provider 已配置" : "仍有 Provider 配置未完成"}</small>
        </div>
      </article>
      <article className="status-card">
        <div className="status-icon purple"><BookOpen size={27} /></div>
        <div>
          <span className="eyebrow">真实文献证据</span>
          <strong>{literature?.ready ? "已就绪" : "需检查"}</strong>
          <small>{literature?.mode ?? "未加载"}，仅允许已验证引用</small>
        </div>
      </article>
      <article className="status-card">
        <div className="status-icon green"><FlaskConical size={27} /></div>
        <div>
          <span className="eyebrow">真实实验状态</span>
          <strong>{experiment?.ready ? "可运行" : "未就绪"}</strong>
          <small>{experiment?.code || experiment?.warning || experiment?.mode || "等待 Provider 配置"}</small>
        </div>
      </article>
      <article className="status-card">
        <div className="status-icon blue"><FileText size={26} /></div>
        <div>
          <span className="eyebrow">报告导出状态</span>
          <strong>{competitionReady ? "可导出" : "未就绪"}</strong>
          <small>{competitionReady ? "比赛要求已满足" : "比赛导出条件未满足"}</small>
        </div>
      </article>
      <article className="status-card next-step">
        <div className="status-icon amber"><Lightbulb size={28} /></div>
        <div>
          <span className="eyebrow">下一步建议</span>
          <strong>{llm?.ready ? "运行科研流程" : "配置 Qwen"}</strong>
          <small>{llm?.ready ? "生成并审查 artifacts" : llm?.code ?? "正在读取 Provider 状态"}</small>
        </div>
      </article>
    </section>
  );
}
