import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const readSource = (path) => readFile(new URL(`../${path}`, import.meta.url), "utf8");

function extractFunction(source, name) {
  const signature = new RegExp(`(?:async\\s+)?function\\s+${name}\\b`);
  const match = signature.exec(source);
  assert.ok(match, `missing function ${name}`);
  const start = source.indexOf("{", match.index);
  let depth = 0;
  for (let index = start; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") depth -= 1;
    if (depth === 0) return source.slice(start + 1, index);
  }
  assert.fail(`unterminated function ${name}`);
}

test("create research resets to a blank topic and Part A starts research", async () => {
  const app = await readSource("src/App.tsx");
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const controls = await readSource("src/components/RunControls.tsx");

  assert.match(app, /const EMPTY_TOPIC = \{\s*domain: "",\s*problem: "",\s*constraints: "",\s*githubRepositoryUrl: "",\s*\}/);
  assert.match(app, /function createResearch\(\)/);
  assert.match(app, /setRun\(null\)/);
  assert.match(app, /setTopicDraft\(EMPTY_TOPIC\)/);
  assert.match(workbench, /onStartResearch/);
  assert.match(workbench, /onCreate=\{onCreate\}/);
  assert.match(workbench, /onStart=\{handleResearchStart\}/);
  assert.match(controls, />\s*创建研究\s*<\/button>/);
  assert.doesNotMatch(controls, /创建研究 Run/);
});

test("hypothesis revision starts an append-only hypothesis round while other runs continue normally", async () => {
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");

  assert.match(workbench, /onStartResearch, onRerunFrom, onContinuePipeline/);
  assert.match(workbench, /const handleResearchStart = !run\s*\? onStartResearch\s*: run\.status === "hypothesis_revision_required"\s*\? \(\) => onRerunFrom\("hypothesis_generation"\)\s*: governanceHold\s*\? \(\) => undefined\s*: onContinuePipeline/);
  assert.match(workbench, /onStart=\{handleResearchStart\}/);
});

test("edited topic creates a new run instead of continuing mismatched artifacts", async () => {
  const app = await readSource("src/App.tsx");
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const startBody = extractFunction(app, "startResearch");

  assert.match(app, /function topicMatchesRun\(run: RunRecord, draft: typeof EMPTY_TOPIC\)/);
  assert.match(startBody, /current === null \|\| !topicMatchesRun\(current, topicDraft\)/);
  assert.match(startBody, /api\.createRun\(title, topicDraft\.problem, topicDraft\.domain, topicDraft\.constraints, topicDraft\.githubRepositoryUrl\)/);
  assert.match(workbench, /onQuestionChange=\{\(problem\) => onTopicDraftChange/);
  assert.match(workbench, /githubRepositoryUrl=\{topicDraft\.githubRepositoryUrl\}/);
});

test("run-changing entry points are guarded and their current workspace controls receive busy state", async () => {
  const app = await readSource("src/App.tsx");
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const modal = await readSource("src/components/ProjectSettingsModal.tsx");
  const createBody = extractFunction(app, "createResearch");
  const startBody = extractFunction(app, "startResearch");
  const loadBody = extractFunction(app, "loadRun");
  const deleteBody = extractFunction(app, "deleteRun");

  assert.match(createBody, /^\s*if \(researchRunning \|\| activeStepId !== null\) return/);
  assert.match(startBody, /^\s*if \(!topicDraft\.problem\.trim\(\) \|\| researchRunning \|\| activeStepId !== null\) return/);
  assert.match(loadBody, /^\s*if \(researchRunning \|\| activeStepId !== null\) return/);
  assert.match(deleteBody, /^\s*if \(researchRunning \|\| activeStepId !== null\) return/);
  assert.match(workbench, /const busy = researchRunning \|\| activeStepId !== null/);
  assert.match(workbench, /busy=\{busy\}/);
  assert.match(app, /<ProjectSettingsModal[\s\S]*?isBusy=\{isBusy\}/);
  assert.match(modal, /isBusy: boolean/);
  assert.match(modal, /disabled=\{isBusy\}/);
  assert.match(modal, /className="danger-button"[^>]*disabled=\{isBusy\}/s);
});

test("all run mutations synchronously own one ref through their complete operation", async () => {
  const app = await readSource("src/App.tsx");

  assert.match(app, /import \{ useEffect, useRef, useState \} from "react"/);
  assert.match(app, /const mutationInFlightRef = useRef\(false\)/);
  assert.match(app, /function tryAcquireMutation\(\)[\s\S]*mutationInFlightRef\.current[\s\S]*mutationInFlightRef\.current = true/);
  assert.match(app, /function releaseMutation\(\)[\s\S]*mutationInFlightRef\.current = false/);

  for (const functionName of [
    "createResearch",
    "startResearch",
    "loadRun",
    "deleteRun",
    "runStep",
    "rerunFrom",
    "addUserHypothesis",
  ]) {
    const body = extractFunction(app, functionName);
    assert.match(body, /!tryAcquireMutation\(\)/, `${functionName} must synchronously acquire`);
    assert.match(body, /finally \{[\s\S]*releaseMutation\(\)/, `${functionName} must release in finally`);
  }

  const startBody = extractFunction(app, "startResearch");
  assert.ok(startBody.indexOf("tryAcquireMutation") < startBody.indexOf("api.startPipeline"));
  assert.ok(startBody.indexOf("api.startPipeline") < startBody.indexOf("releaseMutation"));
  const executeBody = extractFunction(app, "executeStep");
  assert.ok(executeBody.indexOf("await api.getReport") >= 0);
  assert.doesNotMatch(executeBody, /releaseMutation/);
});

test("workflow satisfaction and recovery are owned by the durable backend", async () => {
  const app = await readSource("src/App.tsx");
  const client = await readSource("src/api/client.ts");
  const orchestrator = await readSource("../backend/app/workflow/orchestrator.py");

  assert.doesNotMatch(app, /function isStepSatisfied|function pendingSteps/);
  assert.match(client, /startPipeline\(runId: string\)/);
  assert.match(orchestrator, /def recover\(self\)/);
  assert.match(orchestrator, /def _next_step\([\s\S]*?cls,[\s\S]*?run,/);
  assert.match(orchestrator, /_result_for_task/);
});

test("frontend delegates the ordered pipeline to the backend orchestrator", async () => {
  const app = await readSource("src/App.tsx");
  const client = await readSource("src/api/client.ts");
  const orchestrator = await readSource("../backend/app/workflow/orchestrator.py");
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const timeline = await readSource("src/components/PipelineTimeline.tsx");

  assert.doesNotMatch(app, /const PIPELINE_STEPS|function runAutomaticPipeline/);
  assert.match(app, /api\.startPipeline/);
  assert.match(client, /pipeline\/start/);
  assert.match(orchestrator, /"problem_understanding"[\s\S]*"knowledge_integration"[\s\S]*"hypothesis_generation"[\s\S]*"evidence_reasoning"[\s\S]*"research_plan"/);
  for (const source of [app, workbench, timeline]) {
    assert.doesNotMatch(source, /idea_selection/);
    assert.doesNotMatch(source, /pauseRequested|toggleAutomation|canToggleAutomation|AutomationStatus/);
  }
  assert.doesNotMatch(app, /INITIAL_STEPS|CONTINUATION_STEPS/);
  assert.doesNotMatch(timeline, /<Pause|<Play|onToggleAutomation/);
});

test("agent trace shows waiting state and actual skill invocation table", async () => {
  const trace = await readSource("src/components/AgentTrace.tsx");

  assert.doesNotMatch(trace, /demo-[123]/);
  assert.doesNotMatch(trace, /events\.length \? events :/);
  assert.match(trace, /events\.length === 0/);
  assert.match(trace, /等待真实执行记录/);
  assert.match(trace, /等待中/);
  assert.match(trace, /output_summary\?\.status/);
  assert.match(trace, /output_summary\?\.accepted/);
  assert.match(trace, /mini-tag error/);
  assert.match(trace, /本次实际调用的 Skills/);
  assert.match(trace, /skill_invocations/);
  assert.match(trace, /load_mode/);
});

test("step API calls expose active and failed step state and always clear activity", async () => {
  const app = await readSource("src/App.tsx");
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const addHypothesisBody = extractFunction(app, "addUserHypothesis");
  const runStepBody = extractFunction(app, "runStep");
  const rerunBody = extractFunction(app, "rerunFrom");

  assert.match(app, /useState<string \| null>\(null\)/);
  assert.match(app, /setActiveStepId\(stepId\)[\s\S]*await api\.runStep\([\s\S]*finally \{\s*setActiveStepId\(null\)/);
  assert.match(app, /setActiveStepId\(stepId\)[\s\S]*await api\.rerunFrom\([\s\S]*finally \{\s*setActiveStepId\(null\)/);
  assert.match(app, /setFailedStepId\(stepId\)/);
  assert.match(addHypothesisBody, /setActiveStepId\("evidence_reasoning"\)/);
  assert.match(addHypothesisBody, /setFailedStepId\(null\)/);
  assert.match(addHypothesisBody, /await api\.addUserHypothesis/);
  assert.match(addHypothesisBody, /catch \(error\) \{[\s\S]*setFailedStepId\("evidence_reasoning"\)/);
  assert.match(addHypothesisBody, /finally \{\s*setActiveStepId\(null\)/);
  assert.ok(addHypothesisBody.indexOf("setActiveStepId") < addHypothesisBody.indexOf("await api.addUserHypothesis"));
  assert.match(runStepBody, /^\s*if \(!run \|\| researchRunning \|\| activeStepId !== null\) return/);
  assert.match(rerunBody, /^\s*if \(!run \|\| researchRunning \|\| activeStepId !== null\) return/);
  assert.match(workbench, /activeStepId=\{activeStepId\}/);
  assert.match(workbench, /failedStepId=\{failedStepId\}/);
  assert.match(workbench, /busy=\{busy\}/);
});

test("failed experiment result does not count as completed pipeline output", async () => {
  const app = await readSource("src/App.tsx");
  const presentation = await readSource("src/utils/presentation.ts");
  const timeline = await readSource("src/components/PipelineTimeline.tsx");
  const experimentPanel = await readSource("src/components/ExperimentPanel.tsx");
  const reportPreview = await readSource("src/components/ReportPreview.tsx");

  assert.match(app, /latestExperimentResultFailure/);
  assert.match(presentation, /hasSuccessfulExperimentResult/);
  assert.match(presentation, /artifact\.content\.experiment_id === experimentId/);
  assert.match(app, /stepId === "experiment_run_analysis"[\s\S]*latestExperimentResultFailure\(updated\.artifacts\)/);
  assert.match(timeline, /latestExperimentResultFailure\(artifacts\)/);
  assert.match(timeline, /latestExperimentDiagnosis\(artifacts\)/);
  assert.match(timeline, /hasSatisfiedOutput\(step\.outputType\)/);
  assert.match(experimentPanel, /experimentFailure \? "执行失败" : "已完成"/);
  assert.match(experimentPanel, /故障诊断专家/);
  assert.match(experimentPanel, /diagnosis\.root_cause/);
  assert.match(experimentPanel, /diagnosis\.repair_action/);
  assert.match(experimentPanel, /diagnosisEvidence/);
  assert.match(reportPreview, /hasAuditedResult/);
  assert.doesNotMatch(reportPreview, /artifactTypes\.has\("experiment_result"\) \|\| Boolean\(report\?\.Results\)/);
});

test("automatic continuation keeps the actual downstream failed step", async () => {
  const app = await readSource("src/App.tsx");
  const rerunBody = extractFunction(app, "rerunFrom");
  const automaticResume = rerunBody.indexOf("if (AUTO_RESUME_AFTER_RERUN.has(stepId))");

  assert.ok(automaticResume > 0);
  assert.match(rerunBody.slice(0, automaticResume), /setFailedStepId\(stepId\)/);
  assert.doesNotMatch(rerunBody.slice(automaticResume), /setFailedStepId\(stepId\)/);
  assert.match(rerunBody.slice(automaticResume), /api\.startPipeline\(updated\.id\)/);
});

test("stopping invalidates an in-flight rerun and prevents its late response from restarting the pipeline", async () => {
  const app = await readSource("src/App.tsx");
  const rerunBody = extractFunction(app, "rerunFrom");
  const stopBody = extractFunction(app, "stopAutomaticPipeline");

  assert.match(app, /const stopSequenceRef = useRef\(0\)/);
  assert.match(rerunBody, /const stopSequence = stopSequenceRef\.current/);
  assert.ok((rerunBody.match(/stopSequence !== stopSequenceRef\.current/g) ?? []).length >= 3);
  assert.match(rerunBody, /stopSequence !== stopSequenceRef\.current[\s\S]*return;[\s\S]*AUTO_RESUME_AFTER_RERUN/);
  assert.match(stopBody, /stopSequenceRef\.current \+= 1[\s\S]*setPipelineStopRequested\(true\)/);
});

test("failed mutations reconcile the run before preserving the original step error", async () => {
  const app = await readSource("src/App.tsx");
  const refreshBody = extractFunction(app, "refreshRunAfterFailure");

  assert.match(refreshBody, /await api\.getRun\(runId\)/);
  assert.match(refreshBody, /setRun\(refreshed\)/);
  assert.match(refreshBody, /catch \{/);
  assert.match(refreshBody, /setReport\(null\)/);

  for (const [functionName, failedStep] of [
    ["rerunFrom", "stepId"],
    ["addUserHypothesis", '"evidence_reasoning"'],
    ["executeStep", "stepId"],
  ]) {
    const body = extractFunction(app, functionName);
    const refreshIndex = body.indexOf("await refreshRunAfterFailure");
    const failedIndex = body.indexOf(`setFailedStepId(${failedStep})`);
    assert.ok(refreshIndex >= 0, `${functionName} must refresh after a failed mutation`);
    assert.ok(failedIndex > refreshIndex, `${functionName} must mark failure after refresh`);
    assert.match(body, /throw error/);
  }
});

test("backend automatic research performs a bounded feedback loop before export", async () => {
  const app = await readSource("src/App.tsx");
  const orchestrator = await readSource("../backend/app/workflow/orchestrator.py");
  const rerunBody = extractFunction(app, "rerunFrom");

  assert.match(orchestrator, /MAX_FEEDBACK_ITERATIONS = 4/);
  assert.match(orchestrator, /requires_follow_up.*is True/);
  assert.match(orchestrator, /iteration >= max_feedback_iterations/);
  assert.match(orchestrator, /return "experiment_task"/);
  assert.match(orchestrator, /return "experiment_run_analysis"/);
  assert.match(orchestrator, /return "feedback_revision"/);
  assert.match(orchestrator, /return "report_export"/);
  assert.match(app, /AUTO_RESUME_AFTER_RERUN[\s\S]*"experiment_task"[\s\S]*"experiment_run_analysis"/);
  assert.match(rerunBody, /AUTO_RESUME_AFTER_RERUN\.has\(stepId\)[\s\S]*api\.startPipeline\(updated\.id\)/);
});

test("workspace routes research, idea, experiment, and results views through one model", async () => {
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  assert.match(workbench, /buildResearchViewModel\(run, report, experimentProgress\)/);
  assert.match(workbench, /<ResearchPage/);
  assert.match(workbench, /<IdeaPage/);
  assert.match(workbench, /<ExperimentPage/);
  assert.match(workbench, /<ResultsPage/);
});

test("workspace passes run activity state into the current research and experiment views", async () => {
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  assert.match(workbench, /const busy = researchRunning \|\| activeStepId !== null/);
  assert.match(workbench, /busy=\{busy\}/);
  assert.match(workbench, /stopRequested=\{pipelineStopRequested\}/);
});

test("stopped runs do not keep the running banner because of a stale step status", async () => {
  const app = await readSource("src/App.tsx");
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");

  assert.match(workbench, /const busy = researchRunning \|\| activeStepId !== null/);
  assert.doesNotMatch(workbench, /hasRunningStep|step\.status === "running"/);
  assert.match(app, /setPipelineStopRequested\(active && refreshed\.stop_requested\)/);
  assert.match(app, /setPipelineStopRequested\(active && stopped\.stop_requested\)/);
});

test("manual experiment and results views receive the pipeline busy state", async () => {
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  assert.match(workbench, /<ExperimentPage[\s\S]*busy=\{busy\}/);
  assert.match(workbench, /<ResearchPage[\s\S]*busy=\{busy\}/);
});

test("latest feedback requiring follow-up blocks report export", async () => {
  const reportPreview = await readSource("src/components/ReportPreview.tsx");

  assert.match(reportPreview, /findLatestArtifact\(artifacts, "revision"\)/);
  assert.match(reportPreview, /revision\?\.content\.requires_follow_up === true/);
  assert.match(reportPreview, /else if \(requiresFollowUp\) blockers\.push/);
  assert.match(reportPreview, /反馈评审要求继续优化和验证/);
});

test("report fetch stays active until it completes or fails", async () => {
  const app = await readSource("src/App.tsx");
  const executeBody = extractFunction(app, "executeStep");
  const runStepBody = extractFunction(app, "runStep");

  const reportIndex = executeBody.indexOf("await api.getReport(updated.id)");
  const finallyIndex = executeBody.indexOf("finally");
  assert.ok(reportIndex >= 0 && reportIndex < finallyIndex);
  assert.doesNotMatch(runStepBody, /api\.getReport/);
  assert.match(app, /refreshed\.status === "completed"[\s\S]*await api\.getReport\(refreshed\.id\)/);
});

test("report audit failures are translated and retain actionable diagnosis", async () => {
  const app = await readSource("src/App.tsx");
  const reportPreview = await readSource("src/components/ReportPreview.tsx");

  assert.match(app, /function userFacingError/);
  assert.match(app, /REPORT_FACT_AUDIT_FAILED/);
  assert.match(app, /修订草稿和具体依据已保留/);
  assert.match(reportPreview, /"report_audit"/);
  assert.match(reportPreview, /上一次报告生成保留了可修复的事实冲突/);
  assert.match(reportPreview, /required_correction/);
});

test("pipeline retains grouped steps and visual state contracts", async () => {
  const timeline = await readSource("src/components/PipelineTimeline.tsx");
  assert.match(timeline, /pipeline-stage-group/);
  assert.match(timeline, /pipeline-connector/);
  assert.match(timeline, /pipeline-feedback-loop/);
  assert.match(timeline, /feedback-loop-arrow/);
  assert.match(timeline, /revisionArtifact \? `第 \$\{feedbackIteration\} 轮验证` : "等待首轮反馈"/);
  assert.match(timeline, /"current"|state-current/);
  assert.match(timeline, /"completed"|state-completed/);
  assert.match(timeline, /"waiting"|state-waiting/);
  assert.match(timeline, /"needs-revision"|state-needs-revision/);
  assert.match(timeline, /"failed"|state-failed/);
  assert.match(timeline, /runId/);
});

test("pipeline nodes summarize artifact counts, versions, iteration, and report status", async () => {
  const timeline = await readSource("src/components/PipelineTimeline.tsx");

  assert.match(timeline, /verifiedLiteratureCount/);
  assert.match(timeline, /candidateCount/);
  assert.match(timeline, /reasonedCount/);
  assert.match(timeline, /revisedCount/);
  assert.match(timeline, /plan.*\.version|planArtifact\?\.version/);
  assert.match(timeline, /experiment.*\.version|experimentArtifact\?\.version/);
  assert.match(timeline, /result.*\.version|resultArtifact\?\.version/);
  assert.match(timeline, /feedbackIteration/);
  assert.match(timeline, /报告已生成|等待报告/);
});

test("hypothesis board renders candidate reasoning and requires user selection", async () => {
  const board = await readSource("src/components/HypothesisBoard.tsx");

  assert.match(board, /content\.candidate_assessments/);
  assert.match(board, /activeStepId === "evidence_reasoning"/);
  assert.match(board, /assessmentsByCandidateIndex\.size/);
  assert.match(board, /正在处理 CAND-/);
  assert.match(board, /status-reasoning/);
  assert.match(board, /verified: "验证通过"/);
  assert.match(board, /evidence_insufficient: "证据不足"/);
  assert.match(board, /rejected: "未通过"/);
  assert.match(board, /revised: "已自动修订"/);
  assert.match(board, /original_hypothesis/);
  assert.match(board, /revised_hypothesis/);
  assert.match(board, /revision_reason/);
  assert.match(board, /hypothesis-method-card/);
  assert.match(board, /hypothesis-evidence-basis/);
  assert.match(board, /evidence_basis/);
  assert.match(board, /evidence_type/);
  assert.match(board, /content\.selected_indexes/);
  assert.match(board, /selectionRequired/);
  assert.match(board, /onSelectHypothesis\(index\)/);
  assert.match(board, /hypothesis-model-review/);
  assert.match(board, /evaluation\?\.risks/);
  assert.match(board, /evaluation\?\.unknowns/);
  assert.match(board, /原始假设/);
  assert.match(board, /修订假设/);
  assert.match(board, /修订原因/);
  assert.match(board, /重新运行证据推理/);
  assert.match(board, /isBusy: boolean/);
  assert.match(board, /const canSubmitDraft = Boolean\(runId && draft\.trim\(\) && !isBusy\)/);
  assert.match(board, /新增假设<\/button>/);
  assert.match(board, /disabled=\{!runId \|\| isBusy\}/);
  assert.match(board, /disabled=\{!canSubmitDraft\}/);
  assert.doesNotMatch(board, /自动选择|自动 Idea 选择|weighted_score|selection_reason|Idea Selection/);
});

test("literature summary is collapsed until details are requested", async () => {
  const evidence = await readSource("src/components/EvidenceTable.tsx");
  const client = await readSource("src/api/client.ts");

  assert.match(evidence, /useState\(false\)/);
  assert.match(evidence, /查看全部文献/);
  assert.match(evidence, /收起文献/);
  assert.match(evidence, /sources\.calls/);
  assert.match(evidence, /warnings/);
  assert.match(client, /127\.0\.0\.1:8000/);
});

test("experiment design renders dynamic plan sections instead of fixed metric rows", async () => {
  const editor = await readSource("src/components/ArtifactEditor.tsx");
  const experimentPanel = await readSource("src/components/ExperimentPanel.tsx");

  assert.match(editor, /getPlanSections/);
  assert.match(editor, /Object\.entries\(plan\)/);
  assert.match(editor, /dynamic-plan-list/);
  assert.doesNotMatch(editor, /GPU 配置/);
  assert.doesNotMatch(editor, /生成实验任务/);
  assert.match(experimentPanel, /buildExperimentMetricRows\(plan, metrics, expectedMetrics\)/);
  assert.doesNotMatch(experimentPanel, /accuracy/);
  assert.doesNotMatch(experimentPanel, /hallucination_rate/);
  assert.doesNotMatch(experimentPanel, /reward/);
  assert.doesNotMatch(experimentPanel, /onRunStep/);
});

test("execution derives its rows and optional chart from plan and result artifacts", async () => {
  const experimentPanel = await readSource("src/components/ExperimentPanel.tsx");

  assert.match(experimentPanel, /findLatestArtifactContent\(artifacts, "plan"\)/);
  assert.match(experimentPanel, /findLatestArtifactContent\(artifacts, "experiment_task"\)/);
  assert.match(experimentPanel, /findLatestExperimentResultForTask\(artifacts\)/);
  assert.match(experimentPanel, /buildExperimentMetricRows\(plan, metrics, expectedMetrics\)/);
  assert.match(experimentPanel, /buildExperimentMetricComparisons\(metrics, analysis\)/);
  assert.match(experimentPanel, /findMetricSeries\(metrics\)/);
  assert.match(experimentPanel, /实验优化闭环/);
  assert.doesNotMatch(experimentPanel, /RESEARCH OPTIMIZATION LOOP/);
  assert.match(experimentPanel, /"iteration_analysis"/);
  assert.match(experimentPanel, /"iteration_evidence"/);
  assert.match(experimentPanel, /"iteration_decision"/);
  assert.match(experimentPanel, /"research_state"/);
  assert.match(experimentPanel, /Qwen 比较的优化方向/);
  assert.match(experimentPanel, /事实版本解析/);
  assert.match(experimentPanel, /下一轮实验合同/);
  assert.match(experimentPanel, /series \? \(/);
  assert.doesNotMatch(experimentPanel, /20,140 55,105/);
});

test("experiment settings expose dataset-parent-directory guidance and runtime API contracts", async () => {
  const settings = await readSource("src/components/ProjectSettingsModal.tsx");
  const types = await readSource("src/api/types.ts");

  assert.match(settings, /数据集父目录/);
  assert.match(settings, /D:\\\\Gewu\\\\datasets/);
  assert.match(settings, /系统自动解析具体数据集目录/);
  assert.match(settings, /api\.testExperimentSettings/);
  assert.match(types, /device_names\?: string\[\]/);
  assert.match(types, /available_device_indexes\?: number\[\]/);
});

test("experiment panel renders stable experiment to result relationship", async () => {
  const experimentPanel = await readSource("src/components/ExperimentPanel.tsx");

  assert.match(experimentPanel, /experiment_id/);
  assert.match(experimentPanel, /result_id/);
  assert.match(experimentPanel, /→/);
  assert.match(experimentPanel, /experiment_bundle/);
  assert.match(experimentPanel, /onRerunFrom\("experiment_run_analysis"\)/);
  assert.match(experimentPanel, /Object\.entries\(parameters\)/);
  assert.match(experimentPanel, /运行配置/);
  assert.match(experimentPanel, /buildExperimentMetricComparisons/);
  assert.match(experimentPanel, /继续反馈评审/);
  assert.match(experimentPanel, /attempts/);
  assert.match(experimentPanel, /environment\.python_version/);
  assert.match(experimentPanel, /environment\.device_names/);
  assert.match(experimentPanel, /attempt\.status/);
  assert.match(experimentPanel, /attempt\.error_code/);
});

test("hypotheses and plan-linked experiment cards span the desktop workspace", async () => {
  const editor = await readSource("src/components/ArtifactEditor.tsx");
  const styles = await readSource("src/styles.css");

  assert.match(editor, /section-card design-card plan-linked-design-card/);
  assert.match(styles, /\.hypothesis-card,[\s\S]*\.design-card,[\s\S]*\.experiment-runner-card[\s\S]*grid-column: 1 \/ -1/);
  assert.match(styles, /\.hypothesis-grid[\s\S]*repeat\(2, minmax\(0, 1fr\)\)/);
  assert.doesNotMatch(styles, /repeat\(2, minmax\(240px, 1fr\)\)/);
});

test("literature and hypotheses still use B and C labels", async () => {
  const evidence = await readSource("src/components/EvidenceTable.tsx");
  const hypotheses = await readSource("src/components/HypothesisBoard.tsx");

  assert.match(evidence, /<span>B<\/span>/);
  assert.match(hypotheses, /<span>C<\/span>/);
});

test("local literature can be uploaded, attached to a run, and added to wiki", async () => {
  const evidence = await readSource("src/components/EvidenceTable.tsx");
  const client = await readSource("src/api/client.ts");
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");

  assert.match(evidence, /accept="\.pdf,\.txt,\.md"/);
  assert.match(evidence, /api\.uploadLiterature/);
  assert.match(evidence, /api\.attachLiterature/);
  assert.match(evidence, /api\.addLiteratureToWiki/);
  assert.match(evidence, /api\.verifyLiterature/);
  assert.match(client, /FormData/);
  assert.match(client, /body instanceof FormData/);
  assert.match(workbench, /onRunRefresh/);
});

test("local literature upload accepts verification metadata and shows provenance", async () => {
  const evidence = await readSource("src/components/EvidenceTable.tsx");
  const client = await readSource("src/api/client.ts");

  assert.match(evidence, /name="literature-doi"/);
  assert.match(evidence, /name="literature-arxiv"/);
  assert.match(evidence, /name="literature-authors"/);
  assert.match(evidence, /name="literature-year"/);
  assert.match(evidence, /source_kind/);
  assert.match(client, /body\.append\("doi", metadata\.doi/);
  assert.match(client, /body\.append\("arxiv", metadata\.arxiv/);
  assert.match(client, /body\.append\("authors", metadata\.authors/);
  assert.match(client, /body\.append\("year", metadata\.year/);
});

test("frontend boots the maintained workspace from one root entry point", async () => {
  const main = await readSource("src/main.tsx");

  assert.match(main, /import App from "\.\/App"/);
  assert.match(main, /createRoot\(/);
  assert.match(main, /<App \/>/);
});

test("maintained workspace exposes local-first repository and dataset inputs", async () => {
  const research = await readSource("src/components/workspace/ResearchPage.tsx");
  const settings = await readSource("src/components/ProjectSettingsModal.tsx");
  const client = await readSource("src/api/client.ts");

  assert.match(research, /githubRepositoryUrl/);
  assert.match(research, /GitHub/);
  assert.match(settings, /数据集父目录/);
  assert.match(settings, /api\.getExperimentSettings/);
  assert.match(settings, /api\.saveExperimentSettings/);
  assert.match(settings, /api\.testExperimentSettings/);
  assert.match(client, /createRun\(/);
});

test("maintained workspace reuses durable lifecycle and mature configuration APIs", async () => {
  const app = await readSource("src/App.tsx");
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const client = await readSource("src/api/client.ts");
  assert.match(app, /api\.startPipeline/);
  assert.match(app, /api\.stopPipeline/);
  assert.match(workbench, /ResearchSidebar/);
  assert.match(workbench, /ResultsPage/);
  assert.match(client, /pipeline\/start/);
  assert.match(client, /pipeline\/stop/);
  assert.match(client, /getExperimentSettings/);
});
