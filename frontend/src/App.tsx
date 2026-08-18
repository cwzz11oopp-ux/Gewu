import { useEffect, useRef, useState } from "react";
import { Cpu, FlaskConical, UserCircle } from "lucide-react";
import { api } from "./api/client";
import { isGovernanceRecoveryStatus } from "./api/types";
import type { ExperimentProgress, ProviderStatus, RunRecord } from "./api/types";
import { ProjectSettingsModal } from "./components/ProjectSettingsModal";
import { findLatestArtifact, latestExperimentResultFailure } from "./utils/presentation";
import { WorkbenchPage } from "./pages/WorkbenchPage";

const EMPTY_TOPIC = {
  domain: "",
  problem: "",
  constraints: "",
  githubRepositoryUrl: "",
};

function topicMatchesRun(run: RunRecord, draft: typeof EMPTY_TOPIC) {
  return (
    run.problem_input.trim() === draft.problem.trim()
    && (run.domain ?? "").trim() === draft.domain.trim()
    && (run.constraints ?? "").trim() === draft.constraints.trim()
    && (run.github_repository_url ?? "").trim() === draft.githubRepositoryUrl.trim()
  );
}

const BACKEND_CONNECTION_ERROR = "无法连接后端服务";
const AUTO_RESUME_AFTER_RERUN = new Set([
  "experiment_task",
  "experiment_run_analysis",
]);
const ACTIVE_RUN_STATUSES = new Set(["queued", "running", "stopping"]);

function userFacingError(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  if (message.includes("REPORT_FACT_AUDIT_FAILED")) {
    return "报告中仍有一处可定位的事实表述与权威实验产物冲突。修订草稿和具体依据已保留，请在报告区域查看后重新生成。";
  }
  return message;
}

export default function App() {
  const [run, setRun] = useState<RunRecord | null>(null);
  const [report, setReport] = useState<Record<string, unknown> | null>(null);
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [commandBuffer, setCommandBuffer] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [activeStepId, setActiveStepId] = useState<string | null>(null);
  const [failedStepId, setFailedStepId] = useState<string | null>(null);
  const [researchRunning, setResearchRunning] = useState(false);
  const [pipelineStopRequested, setPipelineStopRequested] = useState(false);
  const [experimentProgress, setExperimentProgress] = useState<ExperimentProgress | null>(null);
  const [topicDraft, setTopicDraft] = useState(EMPTY_TOPIC);
  const mutationInFlightRef = useRef(false);
  const stopSequenceRef = useRef(0);
  const isBusy = researchRunning || activeStepId !== null;

  function tryAcquireMutation() {
    if (mutationInFlightRef.current) return false;
    mutationInFlightRef.current = true;
    return true;
  }

  function releaseMutation() {
    mutationInFlightRef.current = false;
  }

  useEffect(() => {
    let cancelled = false;
    const checkBackend = async () => {
      try {
        const nextStatus = await api.providerStatus();
        if (cancelled) return;
        setStatus(nextStatus);
        setErrorMessage((message) => message.includes(BACKEND_CONNECTION_ERROR) ? "" : message);
      } catch (error) {
        if (!cancelled) setErrorMessage(error instanceof Error ? error.message : String(error));
      }
    };
    void checkBackend();
    const timer = window.setInterval(checkBackend, 5_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadLatestRun = async () => {
      try {
        const runs = await api.listRuns();
        if (cancelled || !runs.length) return;
        const latest = runs[runs.length - 1];
        const loaded = await api.getRun(latest.id);
        if (cancelled) return;
        setRun(loaded);
        setTopicDraft({ domain: loaded.domain || "", problem: loaded.problem_input, constraints: loaded.constraints || "", githubRepositoryUrl: loaded.github_repository_url || "" });
        const active = ACTIVE_RUN_STATUSES.has(loaded.status);
        setResearchRunning(active);
        setActiveStepId(active ? loaded.current_step : null);
        const failed = loaded.steps.find((step) => step.status === "failed");
        setFailedStepId(failed?.id ?? (loaded.status === "failed" ? loaded.current_step : null));
        setPipelineStopRequested(active && loaded.stop_requested);
      } catch (error) {
        if (!cancelled) setErrorMessage(error instanceof Error ? error.message : String(error));
      }
    };
    void loadLatestRun();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!run?.id) {
      setExperimentProgress(null);
      return;
    }
    let cancelled = false;
    const refresh = async () => {
      try {
        const value = await api.getExperimentProgress(run.id);
        if (!cancelled) setExperimentProgress(value);
      } catch {
        // The experiment request remains authoritative when progress polling is unavailable.
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 5_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [run?.id]);

  useEffect(() => {
    if (!run?.id) return;
    let cancelled = false;
    const refresh = async () => {
      try {
        const refreshed = await api.getRun(run.id);
        if (cancelled) return;
        setRun(refreshed);
        const active = ACTIVE_RUN_STATUSES.has(refreshed.status);
        setResearchRunning(active);
        setActiveStepId(active ? refreshed.current_step : null);
        setPipelineStopRequested(active && refreshed.stop_requested);
        const failedStep = refreshed.steps.find((step) => step.status === "failed");
        setFailedStepId(failedStep?.id ?? (refreshed.status === "failed" ? refreshed.current_step : null));
        if (refreshed.status === "completed" && findLatestArtifact(refreshed.artifacts, "report")) {
          setReport(await api.getReport(refreshed.id));
        }
      } catch (error) {
        if (!cancelled) setErrorMessage(error instanceof Error ? error.message : String(error));
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 2_000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [run?.id, run?.status]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA") return;
      if (event.key.length !== 1) return;
      const next = `${commandBuffer}${event.key}`.slice(-8);
      setCommandBuffer(next);
      if (next.endsWith("/api")) {
        event.preventDefault();
        setCommandBuffer("");
        configureQwenKey();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [commandBuffer]);

  async function refreshStatus() {
    setStatus(await api.providerStatus());
    setErrorMessage((message) => message.includes(BACKEND_CONNECTION_ERROR) ? "" : message);
  }

  async function configureQwenKey() {
    const key = window.prompt("请输入 Qwen API Key。密钥只保存到本机后端，不会回显。");
    if (!key) return;
    await api.saveQwenKey(key.trim());
    await refreshStatus();
  }

  async function createResearch() {
    if (researchRunning || activeStepId !== null) return;
    if (!tryAcquireMutation()) return;
    try {
      setRun(null);
      setReport(null);
      setErrorMessage("");
      setActiveStepId(null);
      setFailedStepId(null);
      setResearchRunning(false);
      setTopicDraft(EMPTY_TOPIC);
    } finally {
      await Promise.resolve();
      releaseMutation();
    }
  }

  async function startResearch() {
    if (!topicDraft.problem.trim() || researchRunning || activeStepId !== null) return;
    if (!tryAcquireMutation()) return;
    setPipelineStopRequested(false);
    setResearchRunning(true);
    let accepted = false;
    try {
      await runAction(async () => {
        setReport(null);
        let current = run;
        if (current === null || !topicMatchesRun(current, topicDraft)) {
          const title = topicDraft.problem.slice(0, 40) || "未命名研究";
          current = await api.createRun(title, topicDraft.problem, topicDraft.domain, topicDraft.constraints, topicDraft.githubRepositoryUrl);
          setRun(current);
        }
        current = await api.startPipeline(current.id);
        accepted = true;
        setRun(current);
        const active = ACTIVE_RUN_STATUSES.has(current.status);
        setResearchRunning(active);
        setActiveStepId(active ? current.current_step : null);
      });
    } finally {
      if (!accepted) setResearchRunning(false);
      setPipelineStopRequested(false);
      releaseMutation();
    }
  }

  async function loadRun(runId: string) {
    if (researchRunning || activeStepId !== null) return;
    if (!tryAcquireMutation()) return;
    try {
      const loaded = await api.getRun(runId);
      setRun(loaded);
      setTopicDraft({
        domain: loaded.domain || "",
        problem: loaded.problem_input,
        constraints: loaded.constraints || "",
        githubRepositoryUrl: loaded.github_repository_url || "",
      });
      setReport(null);
      setErrorMessage("");
      const active = ACTIVE_RUN_STATUSES.has(loaded.status);
      setActiveStepId(active ? loaded.current_step : null);
      const failed = loaded.steps.find((step) => step.status === "failed");
      setFailedStepId(failed?.id ?? (loaded.status === "failed" ? loaded.current_step : null));
      setResearchRunning(active);
      setPipelineStopRequested(active && loaded.stop_requested);
    } finally {
      releaseMutation();
    }
  }

  async function refreshCurrentRun() {
    if (!run) return;
    setRun(await api.getRun(run.id));
  }

  async function deleteRun(runId: string) {
    if (researchRunning || activeStepId !== null) return;
    if (!tryAcquireMutation()) return;
    try {
      await api.deleteRun(runId);
      if (run?.id === runId) {
        setRun(null);
        setReport(null);
        setActiveStepId(null);
        setFailedStepId(null);
        setResearchRunning(false);
      }
    } finally {
      releaseMutation();
    }
  }

  async function runStep(stepId: string) {
    if (!run || researchRunning || activeStepId !== null) return;
    if (!tryAcquireMutation()) return;
    try {
      await runAction(async () => {
        await executeStep(run, stepId);
      });
    } finally {
      releaseMutation();
    }
  }

  async function refreshRunAfterFailure(runId: string) {
    try {
      const refreshed = await api.getRun(runId);
      setRun(refreshed);
    } catch {
      // Preserve the original mutation error when reconciliation also fails.
    }
    setReport(null);
  }

  async function rerunFrom(stepId: string) {
    if (!run || researchRunning || activeStepId !== null) return;
    if (!tryAcquireMutation()) return;
    const stopSequence = stopSequenceRef.current;
    try {
      await runAction(async () => {
        setActiveStepId(stepId);
        setFailedStepId(null);
        let updated: RunRecord;
        try {
          updated = await api.rerunFrom(run.id, stepId);
          if (stopSequence !== stopSequenceRef.current) {
            await refreshRunAfterFailure(run.id);
            return;
          }
          setRun(updated);
          setReport(null);
          const experimentFailure = stepId === "experiment_run_analysis"
            ? latestExperimentResultFailure(updated.artifacts)
            : null;
          if (experimentFailure) {
            throw new Error(experimentFailure);
          }
        } catch (error) {
          await refreshRunAfterFailure(run.id);
          if (stopSequence !== stopSequenceRef.current) return;
          setFailedStepId(stepId);
          throw error;
        } finally {
          setActiveStepId(null);
        }
        if (stopSequence !== stopSequenceRef.current) return;
        if (AUTO_RESUME_AFTER_RERUN.has(stepId)) {
          updated = await api.startPipeline(updated.id);
          setRun(updated);
          setResearchRunning(true);
        }
      });
    } finally {
      releaseMutation();
    }
  }

  async function continuePipeline() {
    if (!run || researchRunning || activeStepId !== null) return;
    if (isGovernanceRecoveryStatus(run.status)) return;
    if (!tryAcquireMutation()) return;
    setPipelineStopRequested(false);
    setResearchRunning(true);
    let accepted = false;
    try {
      await runAction(async () => {
        const updated = await api.startPipeline(run.id);
        accepted = true;
        setRun(updated);
        const active = ACTIVE_RUN_STATUSES.has(updated.status);
        setResearchRunning(active);
        setActiveStepId(active ? updated.current_step : null);
      });
    } finally {
      if (!accepted) setResearchRunning(false);
      setPipelineStopRequested(false);
      releaseMutation();
    }
  }

  async function addUserHypothesis(claim: string, replacementIndex?: number) {
    if (!run || researchRunning || activeStepId !== null) return;
    if (!tryAcquireMutation()) return;
    try {
      await runAction(async () => {
        setActiveStepId("evidence_reasoning");
        setFailedStepId(null);
        try {
          setRun(await api.addUserHypothesis(run.id, claim, replacementIndex));
          setReport(null);
        } catch (error) {
          await refreshRunAfterFailure(run.id);
          setFailedStepId("evidence_reasoning");
          throw error;
        } finally {
          setActiveStepId(null);
        }
      });
    } finally {
      releaseMutation();
    }
  }

  async function selectHypothesis(candidateIndex: number) {
    if (!run || researchRunning || activeStepId !== null) return;
    if (!tryAcquireMutation()) return;
    setPipelineStopRequested(false);
    let accepted = false;
    try {
      await runAction(async () => {
        setActiveStepId("evidence_reasoning");
        setFailedStepId(null);
        try {
          const updated = await api.selectHypothesis(run.id, candidateIndex);
          accepted = true;
          setRun(updated);
          setReport(null);
          setResearchRunning(true);
        } catch (error) {
          await refreshRunAfterFailure(run.id);
          setFailedStepId("evidence_reasoning");
          throw error;
        } finally {
          setActiveStepId(null);
        }
      });
    } finally {
      if (!accepted) setResearchRunning(false);
      setPipelineStopRequested(false);
      releaseMutation();
    }
  }

  async function executeStep(startRun: RunRecord, stepId: string) {
    setActiveStepId(stepId);
    setFailedStepId(null);
    try {
      const updated = await api.runStep(startRun.id, stepId);
      setRun(updated);
      const experimentFailure = stepId === "experiment_run_analysis"
        ? latestExperimentResultFailure(updated.artifacts)
        : null;
      if (experimentFailure) {
        throw new Error(experimentFailure);
      }
      if (stepId === "report_export") {
        setReport(await api.getReport(updated.id));
      }
      return updated;
    } catch (error) {
      await refreshRunAfterFailure(startRun.id);
      setFailedStepId(stepId);
      throw error;
    } finally {
      setActiveStepId(null);
    }
  }

  async function stopAutomaticPipeline() {
    if (!run || pipelineStopRequested) return;
    const confirmed = window.confirm(
      "确定停止当前研究吗？已完成的步骤和产物会保留，当前步骤将标记为已中断，可稍后从该步骤重试。",
    );
    if (!confirmed) return;
    stopSequenceRef.current += 1;
    setPipelineStopRequested(true);
    let accepted = false;
    await runAction(async () => {
      if (
        experimentProgress?.process_alive === true
        && experimentProgress.experiment_id
      ) {
        try {
          await api.terminateExperiment(run.id, experimentProgress.experiment_id, false);
        } catch (error) {
          setErrorMessage(
            `实验进程未能立即终止：${error instanceof Error ? error.message : String(error)}`,
          );
        }
      }
      const stopped = await api.stopPipeline(run.id);
      accepted = true;
      setRun(stopped);
      const active = ACTIVE_RUN_STATUSES.has(stopped.status);
      setResearchRunning(active);
      setActiveStepId(active ? stopped.current_step : null);
      setPipelineStopRequested(active && stopped.stop_requested);
    });
    if (!accepted) setPipelineStopRequested(false);
  }

  async function runAction(action: () => Promise<void>) {
    try {
      setErrorMessage("");
      await action();
    } catch (error) {
      setErrorMessage(userFacingError(error));
    }
  }

  return (
    <div className="app-frame">
      <header className="topbar legacy-header">
        <div className="brand-block">
          <img className="brand-logo" src="/gewu-logo.png" alt="格物标识" />
          <div>
            <h1>格物（GEWU）</h1>
            <span>AI Scientist 科研过程自动化平台</span>
          </div>
        </div>
        <div className="topbar-actions">
          <button className="top-pill topbar-button" onClick={configureQwenKey} title="点击配置 Qwen API Key，也可以在页面直接输入 /api">
            <Cpu size={15} /> {status?.llm?.ready ? "Qwen 已配置" : "配置 Qwen"}
          </button>
          <span className="top-pill"><FlaskConical size={15} /> 实验: {status?.experiment?.mode ?? "Remote GPU"}</span>
          <span className="user-pill"><UserCircle size={20} /> 研究员</span>
        </div>
      </header>
      {errorMessage ? <div className="error-banner">{errorMessage}</div> : null}
      <WorkbenchPage
        run={run}
        report={report}
        topicDraft={topicDraft}
        activeStepId={activeStepId}
        failedStepId={failedStepId}
        researchRunning={researchRunning}
        pipelineStopRequested={pipelineStopRequested}
        onTopicDraftChange={setTopicDraft}
        onCreate={createResearch}
        onStartResearch={startResearch}
        onRunStep={runStep}
        onRerunFrom={rerunFrom}
        onContinuePipeline={continuePipeline}
        onStopPipeline={stopAutomaticPipeline}
        onOpenSettings={() => { if (!isBusy) setSettingsOpen(true); }}
        onAddUserHypothesis={addUserHypothesis}
        onSelectHypothesis={selectHypothesis}
        onRunRefresh={refreshCurrentRun}
        experimentProgress={experimentProgress}
      />
      <ProjectSettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        onLoadRun={loadRun}
        onDeleteRun={deleteRun}
        onStatusRefresh={refreshStatus}
        isBusy={isBusy}
      />
    </div>
  );
}
