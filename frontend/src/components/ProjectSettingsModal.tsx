import { useEffect, useMemo, useState } from "react";
import { Cpu, Database, FlaskConical, History, Plus, Settings2, X } from "lucide-react";
import type { ExperimentSettings, ExperimentTestResult, ModelProviderConfig, ModelRoleConfig, RunRecord } from "../api/types";
import { api } from "../api/client";
import { summarizeResearchProblem } from "../utils/presentation";

const DEFAULT_SETTINGS: ExperimentSettings = {
  provider: "local_gpu",
  remote: { host: "", user: "", port: 22, ssh_key_path: "", project_dir: "", python: "python", cuda_visible_devices: "0", timeout_seconds: 0 },
  local: { enabled: true, workdir: "experiments", python: "python", cuda_visible_devices: "0", timeout_seconds: 0 },
  dataset: { source: "auto", dir: "datasets", mirror_url: "", download_retries: 5 },
};

const ROLES = [
  ["RESEARCH", "Research"], ["HYPOTHESIS_GENERATION", "Hypothesis Generation"],
  ["EVIDENCE_REASONING", "Evidence Reasoning"], ["RESEARCH_PLAN_GENERATION", "Research Plan Generation"],
  ["RESEARCH_PLAN_REVIEW", "Research Plan Review"], ["EXPERIMENT_CODE_GENERATION", "Experiment Code Generation"],
  ["CRITIC", "Critic"], ["WRITER", "Writer"],
] as const;

type Tab = "providers" | "roles" | "runtime" | "history";
type Props = { open: boolean; onClose: () => void; onLoadRun: (runId: string) => Promise<void>; onDeleteRun: (runId: string) => Promise<void>; onStatusRefresh: () => Promise<void>; isBusy: boolean };

export function ProjectSettingsModal({ open, onClose, onLoadRun, onDeleteRun, onStatusRefresh, isBusy }: Props) {
  const [tab, setTab] = useState<Tab>("roles");
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [settings, setSettings] = useState<ExperimentSettings>(DEFAULT_SETTINGS);
  const [providers, setProviders] = useState<ModelProviderConfig[]>([]);
  const [roles, setRoles] = useState<ModelRoleConfig>({});
  const [message, setMessage] = useState("");
  const [tests, setTests] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<ExperimentTestResult | null>(null);

  useEffect(() => {
    if (!open) return;
    Promise.all([api.listRuns(), api.getExperimentSettings(), api.providerStatus(), api.getModelRoles()])
      .then(([nextRuns, nextSettings, status, nextRoles]) => {
        setRuns(nextRuns);
        setSettings({ ...DEFAULT_SETTINGS, ...nextSettings, dataset: { ...DEFAULT_SETTINGS.dataset, ...(nextSettings.dataset ?? {}) } });
        setProviders((status.model_providers ?? []).map((provider) => ({ ...provider, api_key: "" })));
        setRoles(nextRoles);
      })
      .catch((error) => setMessage(error instanceof Error ? error.message : String(error)));
  }, [open]);

  const options = useMemo(() => providers.flatMap((provider) => provider.models.map((model) => ({
    value: `${provider.provider_id}::${model}`,
    label: `${provider.display_name} / ${model}`,
  }))), [providers]);

  if (!open) return null;

  function editProvider(id: string, patch: Partial<ModelProviderConfig>) {
    setProviders((items) => items.map((item) => item.provider_id === id ? { ...item, ...patch } : item));
  }

  function addProvider() {
    let index = providers.length + 1;
    while (providers.some((provider) => provider.provider_id === `provider_${index}`)) index += 1;
    const provider: ModelProviderConfig = {
      provider_id: `provider_${index}`,
      provider_type: "openai_compatible",
      display_name: `自定义 Provider ${index}`,
      base_url: "https://api.example.com/v1",
      api_key: "",
      models: ["model-name"],
      enabled: true,
      configured: false,
      connection_policy: { timeout_seconds: 120 },
    };
    setProviders((items) => [...items, provider]);
    setMessage("已添加配置卡，请填写信息后保存。");
  }

  async function saveProvider(provider: ModelProviderConfig) {
    try {
      const saved = await api.saveModelProvider(provider);
      editProvider(provider.provider_id, { ...saved, api_key: "", configured: saved.configured || provider.configured });
      setMessage(`${provider.display_name} 配置已保存。`);
      await onStatusRefresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }

  async function testProvider(id: string) {
    setTests((value) => ({ ...value, [id]: "测试中…" }));
    try {
      const result = await api.testModelProvider(id);
      setTests((value) => ({ ...value, [id]: result.ok ? "连接成功" : result.code || "连接失败" }));
    } catch (error) { setTests((value) => ({ ...value, [id]: error instanceof Error ? error.message : "连接失败" })); }
  }

  async function assign(role: string, value: string) {
    if (!value) return;
    const [provider_id, model] = value.split("::");
    const assignment = { provider_id, model };
    setRoles((current) => ({ ...current, [role]: assignment }));
    try { await api.saveModelRole(role, assignment); setMessage("模型分工已保存。"); }
    catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }

  async function saveRuntime() {
    try { setSettings(await api.saveExperimentSettings(settings)); setMessage("实验环境与数据配置已保存。"); await onStatusRefresh(); }
    catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }

  async function testRuntime() {
    try { const result = await api.testExperimentSettings(settings); setTestResult(result); setMessage(result.message); }
    catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }

  return <div className="modal-backdrop settings-backdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
    <section className="settings-drawer" role="dialog" aria-modal="true" aria-label="项目设置">
      <header className="settings-drawer-header">
        <div><span><Settings2 size={15}/> GEWU WORKSPACE</span><h2>项目设置</h2><p>统一管理模型、科研角色、实验环境与历史研究。</p></div>
        <button aria-label="关闭项目设置" onClick={onClose}><X size={20}/></button>
      </header>
      <div className="settings-drawer-body">
        <nav className="settings-nav" aria-label="设置分类">
          <button className={tab === "providers" ? "active" : ""} onClick={() => setTab("providers")}><Cpu size={17}/>模型 Provider</button>
          <button className={tab === "roles" ? "active" : ""} onClick={() => setTab("roles")}><Settings2 size={17}/>模型分工</button>
          <button className={tab === "runtime" ? "active" : ""} onClick={() => setTab("runtime")}><FlaskConical size={17}/>实验与数据</button>
          <button className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}><History size={17}/>历史研究</button>
        </nav>
        <main className="settings-workspace">
          {message && <div className="settings-message" role="status">{message}</div>}
          {tab === "providers" && <section>
            <div className="settings-section-title"><div><h3>模型 Provider</h3><p>兼容 OpenAI 协议的接口均可配置；密钥仅保存在本机后端。</p></div><button className="secondary-button" onClick={addProvider}><Plus size={15}/>添加 Provider</button></div>
            <div className="provider-card-grid">{providers.map((provider) => <article className="provider-config-card" key={provider.provider_id}>
              <header><div><input className="provider-name-input" aria-label="Provider 名称" value={provider.display_name} onChange={(e) => editProvider(provider.provider_id, { display_name: e.target.value })}/><span>{provider.provider_type} · {provider.provider_id}</span></div><b className={provider.configured ? "ready" : "pending"}>{tests[provider.provider_id] || (provider.configured ? "已配置" : "未配置")}</b></header>
              <label>API Key<input type="password" autoComplete="new-password" value={provider.api_key || ""} placeholder={provider.configured ? "已保存；填写新 Key 可更新" : "输入 API Key"} onChange={(e) => editProvider(provider.provider_id, { api_key: e.target.value })}/></label>
              <label>Base URL<input value={provider.base_url} onChange={(e) => editProvider(provider.provider_id, { base_url: e.target.value })}/></label>
              <label>可用模型<input value={provider.models.join(", ")} onChange={(e) => editProvider(provider.provider_id, { models: e.target.value.split(",").map((item) => item.trim()).filter(Boolean) })}/></label>
              <div className="provider-card-actions"><label className="provider-switch"><input type="checkbox" checked={provider.enabled} onChange={(e) => editProvider(provider.provider_id, { enabled: e.target.checked })}/>启用</label><button className="secondary-button" onClick={() => testProvider(provider.provider_id)}>测试连接</button><button className="primary-button" onClick={() => saveProvider(provider)}>保存</button></div>
            </article>)}</div>
          </section>}
          {tab === "roles" && <section><div className="settings-section-title"><div><h3>科研阶段模型分工</h3><p>让不同科研阶段使用合适的 Provider 与模型，修改后无需改代码。</p></div></div><div className="role-assignment-list">{ROLES.map(([role, label]) => { const assignment = roles[role]; return <label key={role}><span><strong>{label}</strong><small>{role}</small></span><select value={assignment ? `${assignment.provider_id}::${assignment.model}` : ""} onChange={(e) => assign(role, e.target.value)}><option value="">选择模型</option>{options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>; })}</div></section>}
          {tab === "runtime" && <section><div className="settings-section-title"><div><h3>实验环境与数据</h3><p>统一配置本地或远程 GPU，以及研究数据集目录。</p></div></div><div className="runtime-toggle"><button className={settings.provider === "local_gpu" ? "active" : ""} onClick={() => setSettings({ ...settings, provider: "local_gpu", local: { ...settings.local, enabled: true } })}>本地 GPU</button><button className={settings.provider === "remote_gpu" ? "active" : ""} onClick={() => setSettings({ ...settings, provider: "remote_gpu" })}>远程 GPU</button></div>{settings.provider === "local_gpu" ? <div className="settings-form"><label className="checkbox-row"><input type="checkbox" checked={settings.local.enabled} onChange={(e) => setSettings({ ...settings, local: { ...settings.local, enabled: e.target.checked } })}/>启用本地 GPU</label><label>项目目录<input value={settings.local.workdir} onChange={(e) => setSettings({ ...settings, local: { ...settings.local, workdir: e.target.value } })}/></label><label>Python 命令<input value={settings.local.python} onChange={(e) => setSettings({ ...settings, local: { ...settings.local, python: e.target.value } })}/></label><label>CUDA 设备<input value={settings.local.cuda_visible_devices} onChange={(e) => setSettings({ ...settings, local: { ...settings.local, cuda_visible_devices: e.target.value } })}/></label></div> : <div className="settings-form"><label>Host<input value={settings.remote.host} onChange={(e) => setSettings({ ...settings, remote: { ...settings.remote, host: e.target.value } })}/></label><label>User<input value={settings.remote.user} onChange={(e) => setSettings({ ...settings, remote: { ...settings.remote, user: e.target.value } })}/></label><label>Project dir<input value={settings.remote.project_dir} onChange={(e) => setSettings({ ...settings, remote: { ...settings.remote, project_dir: e.target.value } })}/></label><label>Python<input value={settings.remote.python} onChange={(e) => setSettings({ ...settings, remote: { ...settings.remote, python: e.target.value } })}/></label></div>}<div className="dataset-runtime-card"><Database size={18}/><div><strong>数据集目录</strong><small>示例：D:\\Gewu\\datasets。可直接填写具体数据集文件夹；填写父目录时按研究中的数据集名称匹配。</small></div><input value={settings.dataset.dir} onChange={(e) => setSettings({ ...settings, dataset: { ...settings.dataset, dir: e.target.value } })}/></div>{testResult && <p className={testResult.ok ? "runtime-result ok" : "runtime-result"}>{testResult.message}</p>}<div className="settings-actions"><button className="secondary-button" onClick={testRuntime}>测试环境</button><button className="primary-button" onClick={saveRuntime}>保存并应用</button></div></section>}
          {tab === "history" && <section><div className="settings-section-title"><div><h3>历史研究</h3><p>继续已有研究，或清理不再需要的记录。</p></div></div><div className="history-settings-list">{runs.map((run) => { const problem = summarizeResearchProblem(run.problem_input); return <article key={run.id}><button disabled={isBusy} onClick={async () => { await onLoadRun(run.id); onClose(); }}><strong>{problem.text}</strong><span>{run.id} · {run.status}</span></button><button className="danger-button" disabled={isBusy} onClick={async () => { if (window.confirm(`确定删除“${problem.fullText}”吗？`)) { await onDeleteRun(run.id); setRuns((items) => items.filter((item) => item.id !== run.id)); } }}>删除</button></article>; })}</div></section>}
        </main>
      </div>
    </section>
  </div>;
}
