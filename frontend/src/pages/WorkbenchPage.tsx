import { useEffect, useMemo, useState } from "react";
import type { ExperimentProgress, RunRecord } from "../api/types";
import { isGovernanceRecoveryStatus } from "../api/types";
import { ResearchSidebar, type ResearchView } from "../components/ResearchSidebar";
import { buildResearchViewModel } from "../components/researchViewModel";
import { ResearchPage } from "../components/workspace/ResearchPage";
import { IdeaPage } from "../components/workspace/IdeaPage";
import { ExperimentPage } from "../components/workspace/ExperimentPage";
import { ResultsPage } from "../components/workspace/ResultsPage";

type TopicDraft = { domain: string; problem: string; constraints: string; githubRepositoryUrl: string; knowledgeBaseId: string };

type Props = {
  run: RunRecord | null;
  report: Record<string, unknown> | null;
  topicDraft: TopicDraft;
  activeStepId: string | null;
  failedStepId: string | null;
  researchRunning: boolean;
  pipelineStopRequested: boolean;
  onTopicDraftChange: (draft: TopicDraft) => void;
  onCreate: () => void;
  onStartResearch: () => void;
  onRunStep: (stepId: string) => void;
  onRerunFrom: (stepId: string) => void;
  onContinuePipeline: () => void;
  onStopPipeline: () => void;
  onOpenSettings: () => void;
  onAddUserHypothesis: (claim: string, replacementIndex?: number) => Promise<void>;
  onSelectHypothesis: (candidateIndex: number) => Promise<void>;
  onRegenerateHypothesis: () => Promise<void>;
  onRunRefresh: () => Promise<void>;
  experimentProgress: ExperimentProgress | null;
  onHome: () => void;
};

export function WorkbenchPage({
  run, report, topicDraft, activeStepId, failedStepId, researchRunning, pipelineStopRequested,
  onTopicDraftChange, onCreate, onStartResearch, onRerunFrom, onContinuePipeline, onStopPipeline,
  onOpenSettings, onSelectHypothesis, onRegenerateHypothesis, onRunRefresh, experimentProgress, onHome,
}: Props) {
  const [activeView, setActiveView] = useState<ResearchView>("research");
  const [focusHypothesisId, setFocusHypothesisId] = useState<string>();
  const [focusExperimentId, setFocusExperimentId] = useState<string>();
  const [openExperimentLog, setOpenExperimentLog] = useState(false);
  const model = useMemo(() => buildResearchViewModel(run, report, experimentProgress), [run, report, experimentProgress]);
  const busy = researchRunning || activeStepId !== null;
  const governanceHold = run && isGovernanceRecoveryStatus(run.status) ? run.status : null;
  const handleResearchStart = !run
    ? onStartResearch
    : run.status === "hypothesis_revision_required"
      ? () => onRerunFrom("hypothesis_generation")
      : governanceHold
        ? () => undefined
        : onContinuePipeline;
  useEffect(() => { window.scrollTo({ top: 0, left: 0, behavior: "instant" }); }, [activeView]);
  const content = activeView === "research" ? <ResearchPage
    model={model}
    run={run}
    question={topicDraft.problem}
    busy={busy}
    activeStepId={activeStepId}
    failedStepId={failedStepId}
    onQuestionChange={(problem) => onTopicDraftChange({ ...topicDraft, problem })}
    githubRepositoryUrl={topicDraft.githubRepositoryUrl}
    onGithubRepositoryUrlChange={(githubRepositoryUrl) => onTopicDraftChange({ ...topicDraft, githubRepositoryUrl })}
    artifacts={run?.artifacts ?? []}
    knowledgeBaseId={topicDraft.knowledgeBaseId}
    onKnowledgeBaseIdChange={(knowledgeBaseId) => onTopicDraftChange({ ...topicDraft, knowledgeBaseId })}
    onRunRefresh={onRunRefresh}
    onCreate={onCreate}
    onStart={handleResearchStart}
    onStop={onStopPipeline}
    stopRequested={pipelineStopRequested}
    governanceHold={governanceHold}
    onOpenHypothesis={(id) => { setFocusHypothesisId(id); setActiveView("idea"); }}
    onOpenExperiment={(id, log = false) => { setFocusExperimentId(id); setOpenExperimentLog(log); setActiveView("experiment"); }}
  /> : activeView === "idea" ? <IdeaPage
    model={model}
    busy={busy}
    onSelectHypothesis={onSelectHypothesis}
    focusHypothesisId={focusHypothesisId}
    onRegenerate={onRegenerateHypothesis}
    onOpenExperiment={(id) => { setFocusExperimentId(id); setOpenExperimentLog(false); setActiveView("experiment"); }}
  /> : activeView === "experiment" ? <ExperimentPage
    model={model}
    progress={experimentProgress}
    busy={busy}
    stopRequested={pipelineStopRequested}
    onStop={onStopPipeline}
    focusExperimentId={focusExperimentId}
    openLog={openExperimentLog}
    onOpenHypothesis={(id) => { setFocusHypothesisId(id); setActiveView("idea"); }}
  /> : <ResultsPage model={model} run={run}/>;

  return <main className="gew-shell">
    <ResearchSidebar activeView={activeView} onNavigate={setActiveView} onSettings={onOpenSettings} onHome={onHome}/>
    <div className="gew-stage" data-view={activeView}>{content}</div>
  </main>;
}
