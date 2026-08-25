import type { Artifact, ExperimentProgress, ExperimentSettings, ExperimentTestResult, LocalLiteratureDocument, PaperWritingState, ProviderStatus, ResearchKnowledgeBase, ResearchWikiStats, RunRecord } from "./types";

const API_BASE = (
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000"
).replace(/\/+$/, "");

export function experimentFileUrl(runId: string, fileKind: "result" | "log" | "code" | "manifest" | "environment") {
  return `${API_BASE}/api/runs/${encodeURIComponent(runId)}/experiment-files/${fileKind}`;
}

export function reportDownloadUrl(
  runId: string,
  format: "zip" | "docx" = "zip",
) {
  return `${API_BASE}/api/runs/${encodeURIComponent(runId)}/report/download?format=${format}`;
}

export function experimentPackageUrl(runId: string) {
  return `${API_BASE}/api/runs/${encodeURIComponent(runId)}/experiment-package/download`;
}

export function paperDownloadUrl(runId: string, format: "docx" | "latex") {
  return `${API_BASE}/api/runs/${encodeURIComponent(runId)}/paper-writing/download?format=${format}`;
}

export function literatureFileUrl(documentId: string) {
  return `${API_BASE}/api/literature/documents/${encodeURIComponent(documentId)}/file`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = init?.body instanceof FormData
    ? init.headers
    : { "Content-Type": "application/json", ...init?.headers };
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
    });
  } catch (error) {
    if (error instanceof TypeError && /fetch/i.test(error.message)) {
      throw new Error(`无法连接后端服务（${API_BASE}）。请确认后端正在运行。`);
    }
    throw error;
  }
  if (!response.ok) {
    const text = await response.text();
    let message = text;
    try {
      const parsed = JSON.parse(text);
      message = parsed?.detail?.message ?? parsed?.detail?.code ?? text;
    } catch {
      message = text;
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

async function requestText(path: string): Promise<string> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`);
  return response.text();
}

export const api = {
  createRun(title: string, problem_input: string, domain = "", constraints = "", github_repository_url = "", research_constraints: Record<string, unknown> = {}, knowledge_base_id = "default") {
    return request<RunRecord>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ title, problem_input, domain, constraints, github_repository_url: github_repository_url || null, research_constraints, knowledge_base_id }),
    });
  },
  listRuns() {
    return request<RunRecord[]>("/api/runs");
  },
  getRun(runId: string) {
    return request<RunRecord>(`/api/runs/${runId}`);
  },
  getExperimentProgress(runId: string) {
    return request<ExperimentProgress>(`/api/runs/${runId}/experiment-progress`);
  },
  getExperimentLog(runId: string) {
    return requestText(`/api/runs/${runId}/experiment-files/log`);
  },
  terminateExperiment(runId: string, experimentId: string, clearAttempt = false) {
    return request<{ terminated: boolean; cleared: boolean; attempt_id: string }>(
      `/api/runs/${runId}/experiments/${experimentId}/terminate`,
      { method: "POST", body: JSON.stringify({ clear_attempt: clearAttempt }) },
    );
  },
  deleteRun(runId: string) {
    return request<{ deleted: boolean; run_id: string }>(`/api/runs/${runId}`, { method: "DELETE" });
  },
  runStep(runId: string, stepId: string) {
    return request<RunRecord>(`/api/runs/${runId}/steps/${stepId}/run`, { method: "POST" });
  },
  startPipeline(runId: string) {
    return request<RunRecord>(`/api/runs/${runId}/pipeline/start`, { method: "POST" });
  },
  preflightRun(runId: string) {
    return request<{ blocking: boolean; checks: Array<{name: string; ok: boolean; code?: string; detail?: string}> }>(`/api/runs/${runId}/preflight`, { method: "POST" });
  },
  stopPipeline(runId: string) {
    return request<RunRecord>(`/api/runs/${runId}/pipeline/stop`, { method: "POST" });
  },
  addUserHypothesis(runId: string, claim: string, replacement_index?: number) {
    return request<RunRecord>(`/api/runs/${runId}/hypotheses/user`, {
      method: "POST",
      body: JSON.stringify({ claim, replacement_index }),
    });
  },
  selectHypothesis(runId: string, candidate_index: number) {
    return request<RunRecord>(`/api/runs/${runId}/hypotheses/select`, {
      method: "POST",
      body: JSON.stringify({ candidate_index }),
    });
  },
  regenerateHypothesis(runId: string) {
    return request<RunRecord>(`/api/runs/${runId}/hypotheses/regenerate`, { method: "POST" });
  },
  rerunFrom(runId: string, stepId: string) {
    return request<RunRecord>(`/api/runs/${runId}/steps/${stepId}/rerun-from`, { method: "POST" });
  },
  getReport(runId: string) {
    return request<Record<string, unknown>>(`/api/runs/${runId}/report`);
  },
  getPaperWriting(runId: string) {
    return request<PaperWritingState>(`/api/runs/${runId}/paper-writing`);
  },
  startPaperWriting(
    runId: string,
    settings: {
      venue: string;
      language: "zh-CN" | "en";
      paper_type: string;
      authors: string;
      notes: string;
    },
  ) {
    return request<PaperWritingState>(`/api/runs/${runId}/paper-writing/start`, {
      method: "POST",
      body: JSON.stringify(settings),
    });
  },
  confirmPaperPlan(runId: string, feedback = "") {
    return request<PaperWritingState>(`/api/runs/${runId}/paper-writing/confirm-plan`, {
      method: "POST",
      body: JSON.stringify({ feedback }),
    });
  },
  finalizePaper(runId: string, feedback = "") {
    return request<PaperWritingState>(`/api/runs/${runId}/paper-writing/finalize`, {
      method: "POST",
      body: JSON.stringify({ feedback }),
    });
  },
  stopPaperWriting(runId: string) {
    return request<PaperWritingState>(`/api/runs/${runId}/paper-writing/stop`, {
      method: "POST",
    });
  },
  providerStatus() {
    return request<ProviderStatus>("/api/settings/providers");
  },
  getExperimentSettings() {
    return request<ExperimentSettings>("/api/settings/experiment");
  },
  saveExperimentSettings(settings: ExperimentSettings) {
    return request<ExperimentSettings>("/api/settings/experiment", {
      method: "POST",
      body: JSON.stringify(settings),
    });
  },
  testExperimentSettings(settings: ExperimentSettings) {
    return request<ExperimentTestResult>("/api/settings/experiment/test", {
      method: "POST",
      body: JSON.stringify(settings),
    });
  },
  listLiterature(knowledgeBaseId?: string) {
    const query = knowledgeBaseId ? `?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}` : "";
    return request<LocalLiteratureDocument[]>(`/api/literature/documents${query}`);
  },
  uploadLiterature(
    file: File,
    metadata: {
      title: string;
      authors: string;
      year: string;
      abstract: string;
      doi: string;
      arxiv: string;
    },
    knowledgeBaseId = "default",
  ) {
    const body = new FormData();
    body.append("file", file);
    body.append("title", metadata.title || file.name.replace(/\.[^.]+$/, ""));
    body.append("authors", metadata.authors);
    body.append("year", metadata.year);
    body.append("abstract", metadata.abstract);
    body.append("doi", metadata.doi);
    body.append("arxiv", metadata.arxiv);
    body.append("knowledge_base_id", knowledgeBaseId);
    return request<LocalLiteratureDocument>("/api/literature/documents", {
      method: "POST",
      body,
    });
  },
  verifyLiterature(documentId: string) {
    return request<LocalLiteratureDocument>(`/api/literature/documents/${documentId}/verify`, {
      method: "POST",
    });
  },
  attachLiterature(runId: string, documentId: string) {
    return request<Artifact>(`/api/runs/${runId}/literature/${documentId}/attach`, {
      method: "POST",
    });
  },
  addLiteratureToWiki(documentId: string, knowledgeBaseId = "default") {
    return request<{ node_ids: string[] }>(`/api/literature/documents/${documentId}/wiki?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}`, {
      method: "POST",
    });
  },
  getResearchWikiStats(knowledgeBaseId = "default") {
    return request<ResearchWikiStats>(`/api/research-wiki/stats?knowledge_base_id=${encodeURIComponent(knowledgeBaseId)}`);
  },
  listResearchKnowledgeBases() {
    return request<ResearchKnowledgeBase[]>("/api/research-wiki/knowledge-bases");
  },
  deleteLiterature(documentId: string) {
    return request<{ deleted: boolean; document_id: string }>(`/api/literature/documents/${documentId}`, {
      method: "DELETE",
    });
  },
  saveQwenKey(api_key: string, base_url = "", model = "qwen-max") {
    return request<{ configured: boolean; model: string }>("/api/settings/qwen-key", {
      method: "POST",
      body: JSON.stringify({ api_key, base_url: base_url || undefined, model }),
    });
  },
  saveModelProvider(provider: import("./types").ModelProviderConfig) {
    return request<import("./types").ModelProviderConfig>(`/api/settings/providers/${provider.provider_id}`, {
      method: "PUT",
      body: JSON.stringify(provider),
    });
  },
  testModelProvider(providerId: string) {
    return request<{ ok: boolean; code?: string; connection?: string; last_test?: string }>(`/api/settings/providers/${providerId}/test`, { method: "POST" });
  },
  getModelRoles() {
    return request<import("./types").ModelRoleConfig>("/api/settings/model-roles");
  },
  saveModelRole(role: string, assignment: { provider_id: string; model: string }) {
    return request<{ provider_id: string; model: string }>(`/api/settings/model-roles/${role}`, {
      method: "PUT",
      body: JSON.stringify(assignment),
    });
  },
};
