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

  assert.match(app, /const EMPTY_TOPIC = \{\s*domain: "",\s*problem: "",\s*constraints: "",\s*\}/);
  assert.match(app, /function createResearch\(\)/);
  assert.match(app, /setRun\(null\)/);
  assert.match(app, /setTopicDraft\(EMPTY_TOPIC\)/);
  assert.match(workbench, /onStartResearch/);
  assert.match(workbench, /topicChanged \? "新建并开始研究" : "开始研究"/);
  assert.match(controls, />\s*创建研究\s*<\/button>/);
  assert.doesNotMatch(controls, /创建研究 Run/);
});

test("edited topic creates a new run instead of continuing mismatched artifacts", async () => {
  const app = await readSource("src/App.tsx");
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const startBody = extractFunction(app, "startResearch");

  assert.match(app, /function topicMatchesRun\(run: RunRecord, draft: typeof EMPTY_TOPIC\)/);
  assert.match(startBody, /current === null \|\| !topicMatchesRun\(current, topicDraft\)/);
  assert.match(startBody, /api\.createRun\(title, topicDraft\.problem, topicDraft\.domain, topicDraft\.constraints\)/);
  assert.match(workbench, /const topicChanged = Boolean\(/);
  assert.match(workbench, /检测到研究主题已修改。点击下方按钮会自动创建一个新 Run/);
  assert.match(workbench, /topicChanged \? "新建并开始研究" : "开始研究"/);
});

test("run-changing entry points are guarded and their controls are disabled while busy", async () => {
  const app = await readSource("src/App.tsx");
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const controls = await readSource("src/components/RunControls.tsx");
  const modal = await readSource("src/components/ProjectSettingsModal.tsx");
  const createBody = extractFunction(app, "createResearch");
  const startBody = extractFunction(app, "startResearch");
  const loadBody = extractFunction(app, "loadRun");
  const deleteBody = extractFunction(app, "deleteRun");

  assert.match(createBody, /^\s*if \(researchRunning \|\| activeStepId !== null\) return/);
  assert.match(startBody, /^\s*if \(!topicDraft\.problem\.trim\(\) \|\| researchRunning \|\| activeStepId !== null\) return/);
  assert.match(loadBody, /^\s*if \(researchRunning \|\| activeStepId !== null\) return/);
  assert.match(deleteBody, /^\s*if \(researchRunning \|\| activeStepId !== null\) return/);
  assert.match(workbench, /<RunControls[^>]*isBusy=\{isBusy\}/s);
  assert.match(app, /<ProjectSettingsModal[\s\S]*?isBusy=\{isBusy\}[\s\S]*?\/>/);
  assert.match(controls, /isBusy: boolean/);
  assert.equal((controls.match(/disabled=\{isBusy\}/g) ?? []).length, 2);
  assert.match(modal, /isBusy: boolean/);
  assert.match(extractFunction(modal, "loadRun"), /^\s*if \(isBusy\) return/);
  assert.match(extractFunction(modal, "deleteRun"), /^\s*if \(isBusy\) return/);
  assert.match(modal, /className="history-item"[^>]*disabled=\{isBusy\}/s);
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
  assert.match(workbench, /<HypothesisBoard[\s\S]*activeStepId=\{activeStepId\}/);
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

test("research and literature cards appear before the pipeline", async () => {
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const topicIndex = workbench.indexOf("research-topic-card");
  const literatureIndex = workbench.indexOf("<EvidenceTable");
  const pipelineIndex = workbench.indexOf("<PipelineTimeline");

  assert.ok(topicIndex >= 0 && literatureIndex > topicIndex && pipelineIndex > literatureIndex);
});

test("running-state banner spans the workspace without displacing the topic and literature cards", async () => {
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const styles = await readSource("src/styles.css");

  assert.match(workbench, /<main className="workspace-grid">[\s\S]*run-stop-banner[\s\S]*research-topic-card[\s\S]*<EvidenceTable/);
  assert.match(styles, /\.run-stop-banner\s*\{[\s\S]*?grid-column:\s*1 \/ -1;/);
});

test("stopped runs do not keep the running banner because of a stale step status", async () => {
  const app = await readSource("src/App.tsx");
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");

  assert.match(workbench, /const showStopControl = Boolean\(run && isBusy\)/);
  assert.doesNotMatch(workbench, /hasRunningStep|step\.status === "running"/);
  assert.match(app, /setPipelineStopRequested\(active && refreshed\.stop_requested\)/);
  assert.match(app, /setPipelineStopRequested\(active && stopped\.stop_requested\)/);
});

test("manual experiment and report controls are disabled while the pipeline is busy", async () => {
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const experimentPanel = await readSource("src/components/ExperimentPanel.tsx");
  const reportPreview = await readSource("src/components/ReportPreview.tsx");

  assert.match(workbench, /<ExperimentPanel[^>]*isBusy=\{isBusy\}/s);
  assert.match(workbench, /<ReportPreview[^>]*isBusy=\{isBusy\}/s);
  assert.match(experimentPanel, /isBusy: boolean/);
  assert.match(experimentPanel, /disabled=\{!canRerunExperiment \|\| isBusy\}/);
  assert.match(reportPreview, /isBusy: boolean/);
  assert.match(reportPreview, /disabled=\{!canGenerate \|\| isBusy\}/);
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

test("pipeline is a full-width grouped data-flow card with feedback and all visual states", async () => {
  const workbench = await readSource("src/pages/WorkbenchPage.tsx");
  const timeline = await readSource("src/components/PipelineTimeline.tsx");
  const styles = await readSource("src/styles.css");
  const leftRail = workbench.match(/<aside className="left-rail">[\s\S]*?<\/aside>/)?.[0] ?? "";

  assert.match(workbench, /<main className="workspace-grid">[\s\S]*<PipelineTimeline/);
  assert.doesNotMatch(leftRail, /<PipelineTimeline/);
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
  assert.match(timeline, /isBusy: boolean/);
  assert.match(timeline, /const canRerun = Boolean\(runId\)[^;]*&& !isBusy/);
  assert.match(workbench, /<PipelineTimeline[^>]*isBusy=\{isBusy\}/s);
  assert.match(styles, /\.pipeline-card[\s\S]*grid-column: 1 \/ -1/);
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
  assert.match(board, /const statusLabel = isSelected[\s\S]*\? "用户已选择"[\s\S]*: isReasoning[\s\S]*\? "正在推理"/);
  assert.match(board, /isReasoning \? "status-reasoning" :/);
  assert.match(board, /正在推理/);
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

test("experiment settings label CUDA indexes and render runtime diagnostics", async () => {
  const settings = await readSource("src/components/ProjectSettingsModal.tsx");
  const types = await readSource("src/api/types.ts");

  assert.match(settings, /CUDA 设备索引/);
  assert.match(settings, /device_names/);
  assert.match(settings, /python_version/);
  assert.match(settings, /dependency_status/);
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

test("frontend routes root and /v2 to V2 while preserving /legacy", async () => {
  const main = await readSource("src/main.tsx");

  assert.match(main, /const isLegacyRoute = window\.location\.pathname\.startsWith\("\/legacy"\)/);
  assert.match(main, /const RootApp = isLegacyRoute \? App : V2BetaPage/);
  assert.match(main, /document\.title = isLegacyRoute \? "AI Scientist Legacy Workbench" : "AI Scientist"/);
});

test("V2 exposes Greenfield and Repository Research with local-first bootstrap controls", async () => {
  const page = await readSource("src/pages/V2BetaPage.tsx");
  const settings = await readSource("src/pages/V2ProjectSettingsDrawer.tsx");
  const client = await readSource("src/api/client.ts");
  const research = await readSource("src/pages/v2Research.ts");

  assert.match(research, /"greenfield" \| "repository"/);
  assert.match(settings, /从零开始研究/);
  assert.match(settings, /基于已有代码研究/);
  assert.match(settings, /project\.researchKind === "repository"/);
  assert.match(settings, /本地 Repository 路径/);
  assert.match(settings, /Git URL/);
  assert.match(settings, /Dataset Root/);
  assert.match(settings, /本地数据集/);
  assert.match(settings, /自动匹配本地数据集/);
  assert.match(settings, /在线数据集/);
  assert.match(settings, /allowOnlineDatasetDownload/);
  assert.match(settings, /检查数据集/);
  assert.match(page, /api\.bootstrapGreenfield/);
  assert.match(page, /GREENFIELD RESEARCH BOOTSTRAP/);
  assert.match(page, /online_download_performed/);
  assert.match(client, /\/api\/v2\/research\/sessions\/bootstrap\/datasets\/inspect/);
  assert.match(client, /\/api\/v2\/research\/sessions\/bootstrap/);
});

test("V2 Research Workspace reuses versioned lifecycle and mature configuration APIs", async () => {
  const page = await readSource("src/pages/V2BetaPage.tsx");
  const client = await readSource("src/api/client.ts");
  const demo = await readSource("src/pages/v2Demo.ts");
  const presentation = await readSource("src/pages/v2Presentation.ts");

  const settings = await readSource("src/pages/V2ProjectSettingsDrawer.tsx");
  const literature = await readSource("src/pages/V2LiteratureWorkspace.tsx");
  const workspace = await readSource("src/pages/v2Workspace.ts");
  for (const label of ["研究工作台", "文献", "假设", "实验", "科研轨迹", "证据", "报告"]) assert.match(page, new RegExp(label));
  assert.match(page, /当前 Controller 决策/);
  assert.match(page, /完整 ExperimentRecord/);
  assert.match(page, /科研轨迹/);
  assert.match(page, /结论—证据图/);
  assert.match(page, /参数响应/);
  assert.match(page, /支持实验/);
  assert.match(page, /反证 \/ 边界证据/);
  assert.match(page, /审计 \/ 协议/);
  assert.match(page, /Research Question/);
  assert.match(page, /Linked experiments/);
  assert.match(page, /Diff summary/);
  assert.match(page, /Critic analysis/);
  assert.match(page, /本轮未持久化独立记录/);
  assert.match(page, /阶段进度为界面估算/);
  assert.match(workspace, /RESEARCH_STAGES/);
  assert.match(settings, /Local GPU/);
  assert.match(settings, /Remote GPU/);
  assert.match(settings, /api\.getExperimentSettings/);
  assert.match(settings, /api\.saveExperimentSettings/);
  assert.match(settings, /api\.testExperimentSettings/);
  assert.match(settings, /api\.saveQwenKey/);
  assert.match(settings, /不会回显/);
  assert.match(literature, /api\.searchLiterature/);
  assert.match(literature, /api\.uploadLiterature/);
  assert.match(literature, /api\.verifyLiterature/);
  assert.match(literature, /api\.attachLiteratureToV2Session/);
  assert.match(literature, /abstract_only/);
  assert.match(page, /api\.createV2Session/);
  assert.match(page, /api\.startV2Session/);
  assert.match(page, /api\.stopV2Session/);
  assert.match(client, /\/api\/v2\/research\/sessions/);
  assert.match(client, /\/summary/);
  assert.match(client, /\/events/);
  assert.match(client, /\/findings/);
  assert.match(client, /\/claims/);
  assert.match(client, /\/parameter-sweep/);
  assert.match(client, /\/trajectory/);
  assert.match(demo, /model_live_validation: "ready"/);
  assert.match(demo, /research_936ac26929a4/);
  assert.match(demo, /research_dc8671de582b/);
  assert.match(demo, /micrograd_live_exp_2_ablation/);
  assert.match(demo, /micrograd_live_exp_3_robustness/);
  assert.match(demo, /Final Conclusion/);
  assert.match(page, /Demo A/);
  assert.match(page, /Demo B/);
  assert.match(demo, /PARTIALLY_SUPPORTED/);
  assert.match(demo, /NOT_SUPPORTED/);
  assert.match(demo, /status: "stopped"/);
  assert.match(demo, /current_decision: null/);
  assert.match(demo, /iterations: 3/);
  assert.match(demo, /frontier: \[publicBranch, \.\.\.publicAlternativeBranches\]/);
  assert.match(presentation, /RUN_ABLATION: "消融实验"/);
  assert.match(presentation, /PARTIALLY_SUPPORTED: "部分支持"/);
  assert.match(presentation, /VALIDATED: "已验证"/);
  assert.doesNotMatch(page, />Research</);
  assert.doesNotMatch(page, />Frontier</);
  assert.doesNotMatch(page, />Trajectory</);
  assert.doesNotMatch(page, />Claims</);
  assert.doesNotMatch(page, /Agent Trace/);
});
