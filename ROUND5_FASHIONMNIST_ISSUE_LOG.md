# Round 5 Fashion-MNIST Issue Log

Canonical Run ID: `round5_fashionmnist_stability_001`  
Official Project Root: `D:\Gewu`

All observed errors, retries, recovery actions, validation failures, and integrity concerns will be appended below in the required issue format. No issue is recorded at initialization.

## FM-001

### Severity
P0

### Stage
Runtime

### Run ID
Canonical: `round5_fashionmnist_stability_001`; failed runtime run: `run_4bd6315d89b7`

### Timestamp
2026-08-14 17:33:40 +08:00

### Symptom
The formal `POST /api/runs` response persisted the Chinese Fashion-MNIST research question as question-mark characters.

### Expected Behavior
The exact user research question must be persisted and supplied to Research without character loss.

### Actual Behavior
The Run was created, but `problem_input` in the returned persisted record contained `?` characters instead of the submitted Chinese text.

### Evidence
API response for `run_4bd6315d89b7` at creation; no workflow step or artifact was started.

### Agent
None; failure occurred before Agent dispatch.

### Skill ID
None.

### Tool / Command
`POST http://127.0.0.1:8000/api/runs` via the active PowerShell/API invocation path.

### State Before
No Run existed.

### State After
`run_4bd6315d89b7` persisted in `created` state with corrupted problem input and no artifacts.

### Artifact
None.

### Root Cause
Unconfirmed. The active invocation path did not preserve non-ASCII request content.

### Recovery Attempt
Retain the failed unstarted Run. Retry the same formal API request using JSON Unicode escape sequences, without changing Workflow, State, or Agent prompts.

### Recovery Result
PASS for the retry: `run_7ab4216dbfe8` was created through the same formal endpoint with ASCII JSON `\\u` escapes. An independent UTF-8 JSON-store read verified the persisted code points match the submitted Chinese question.

### Impact on Scientific Result
Blocks use of the failed Run; it has not produced a scientific result.

### Recommended Fix
Determine and document a Unicode-safe API invocation path; do not use the corrupted Run for the Round 5 research workflow.

### Blocks Round 5
NO for the canonical retry. The failed Run remains preserved as evidence.

## FM-002

### Severity
P0

### Stage
Runtime

### Run ID
`round5_fashionmnist_stability_001` → `run_7ab4216dbfe8`

### Timestamp
2026-08-14 17:35:08 +08:00

### Symptom
`problem_understanding` stopped before Agent dispatch with `DATASET_SELECTED_DIRECTORY_NOT_FOUND:fashion-mnist:under=D:\\Gewu\\datasets\\fashionmnist`.

### Expected Behavior
The configured official dataset root must resolve the existing selected Fashion-MNIST directory so the Workflow can inspect and bind a real dataset contract.

### Actual Behavior
The configuration named the dataset directory itself. The existing resolver searches for a selected dataset directory beneath the configured root, so it did not accept that same directory as the root.

### Evidence
Workflow event `evt_4dc52ada31f6`; source-path inspection of the configured resolver; read-only IDX validation of the existing four gzip files: image magic `2051`, label magic `2049`, 60,000 training examples, 10,000 test examples, and 28×28 dimensions.

### Agent
None; failure occurred before Research Agent dispatch.

### Skill ID
None.

### Tool / Command
`POST /api/runs/run_7ab4216dbfe8/pipeline/start` and read-only IDX header validation.

### State Before
Run was `running` at `problem_understanding` with no artifacts.

### State After
Run is `failed` at `problem_understanding`, automatic mode disabled, no artifacts, and one workflow event retained.

### Artifact
None.

### Root Cause
Confirmed runtime configuration / resolver-root mismatch, not an unavailable dataset and not a fake-data fallback.

### Recovery Attempt
Use the existing settings API to set `dataset.dir` to `D:\\Gewu\\datasets`, the parent root containing the existing `fashionmnist` directory. Then use the existing rerun/start API path from `problem_understanding`.

### Recovery Result
PASS: Dataset Inspector bound local contract `dataset_32db1f45ec300996` at `D:\Gewu\datasets\fashionmnist` with four files and fingerprint `sha256:32db1f45ec3009968baff6d4e05e5d02ef1e7a42373e802b9a9fcc5a2c3494b8`. The rerun entered Research and has progressed through Research Plan. No source code, dataset file, model prompt, fallback data, or state record was manually changed.

### Impact on Scientific Result
No scientific output exists yet; this P0 blocks all E2E stages until recovery succeeds.

### Recommended Fix
Document the configured-root convention in the UI/runtime validation, while preserving the current Workflow during this test.

### Blocks Round 5
NO. Recovery has passed dataset binding; subsequent workflow stages are independently validated.

## FM-003

### Severity
P1

### Stage
Experiment Task

### Run ID
`round5_fashionmnist_stability_001` → `run_7ab4216dbfe8`

### Timestamp
2026-08-14 17:56:41 +08:00

### Symptom
The Supervisor rejected the first generated experiment-task candidate before execution.

### Expected Behavior
Generated training code must safely convert GPU/gradient-carrying tensors before NumPy conversion.

### Actual Behavior
Static validation reported `EXPERIMENT_CODE_TENSOR_NUMPY_UNSAFE:train.py:line=117: detach tensors before NumPy conversion with tensor.detach().cpu().numpy()`.

### Evidence
Workflow event `evt_7e0701db56bf`, `attempt: 1`, `limit: 5`, `status: revision_requested`. Candidate output remains retained by the run.

### Agent
Supervisor Agent.

### Skill ID
Supervisor validation runtime.

### Tool / Command
Automatic generated-code validation within `experiment_task`.

### State Before
`experiment_task` running, prior research plan completed.

### State After
`experiment_task` remains running and the built-in revision loop is active; no experiment execution has started.

### Artifact
First candidate output retained in runtime state; no cleanup performed.

### Root Cause
Generated candidate violated the runtime's Tensor-to-NumPy safety rule. Root cause in the generated candidate is known; remediation remains the Agent's responsibility.

### Recovery Attempt
Automatic Supervisor-directed revision. Candidate 1 was rejected at `train.py:117`; candidate 2 was rejected at `train.py:99`. The workflow is now on attempt 2 of at most 5. No manual source or generated-code edit has been made.

### Recovery Result
FAIL after the initial automatic cycle (six rejected candidates). A P0 Skill-only recovery was then applied and two further formal recovery cycles were run through the existing APIs. The strengthened third cycle eliminated the initial Tensor rule in its early candidates, but exhausted its six-candidate budget on additional bundle-contract failures and ended with a final Tensor rule violation. No candidate reached execution.

### Impact on Scientific Result
No training or performance metrics were produced from the rejected candidate, so it cannot affect the scientific conclusion.

### Recommended Fix
Require safe conversion at generation time and preserve this validator as a pre-execution gate.

### Blocks Round 5
YES. No valid `ExperimentBundle` was produced after three complete Supervisor cycles; downstream experiment execution, Critic-on-results, and Writer cannot run.

## FM-004

### Severity
P0

### Stage
Experiment / Workflow / State

### Run ID
`round5_fashionmnist_stability_001` → `run_7ab4216dbfe8`

### Timestamp
2026-08-14 18:18:55 +08:00

### Symptom
The complete Experiment stage could not produce a valid, executable Bundle after three formal Supervisor recovery cycles (18 candidate-generation attempts in total).

### Expected Behavior
At least one generated candidate should satisfy the existing general Bundle contract, execute on the locked local Fashion-MNIST dataset, and create auditable runtime artifacts.

### Actual Behavior
The final state is `failed` at `experiment_task`; `experiment_run_analysis`, `feedback_revision`, and `report_export` remain `pending`. No canonical experiment directory, command, stdout, stderr, metrics, checkpoint, figure, or training history exists.

### Evidence
Final Supervisor event `evt_62d1e37f9bb6` and final Run state. The third cycle additionally surfaced `EXPERIMENT_REQUIREMENT_MISSING:sklearn`, invalid smoke-test branching, forbidden runtime download, and missing class-metric source checks before its final Tensor conversion rejection. All events are retained in `backend/data/runs.json`.

### Agent
Experiment Skill generated candidates; Supervisor Agent rejected them; Workflow Orchestrator stopped the pipeline.

### Skill ID
`experiment-implementation`; actual invocation traces are recorded for upstream stages. The final rejected candidate itself has no accepted Bundle artifact, so no executable experiment-code artifact can truthfully be claimed.

### Tool / Command
Three formal `POST /rerun-from` + `POST /pipeline/start` recovery cycles using the official API and the normal Supervisor validator.

### State Before
Research, Hypothesis, Critic evidence review, automatic hypothesis selection, and Research Plan were completed. Dataset contract was locked.

### State After
`experiment_task=failed`; downstream result analysis, feedback, and Writer steps remain pending. Upstream 16 artifacts remain preserved.

### Artifact
No runtime experiment artifact was created. The retained state/events and the absence of `D:\Gewu\experiments\run_7ab4216dbfe8` are the execution evidence.

### Root Cause
Confirmed systemic instability in generated Bundle compliance and automatic repair convergence. The P0 Skill prompt omission was reduced by the emergency fix, but the general code-generation/revision loop still could not satisfy all pre-execution contracts within its bounded retries.

### Recovery Attempt
Two existing rerun/recovery cycles followed the first failure. Between them, one minimal general Skill P0 fix was added; no Fashion-MNIST-specific workflow, Agent, template, metric, experiment group, data, state, or result was injected.

### Recovery Result
FAIL. The pipeline never reached actual execution.

### Impact on Scientific Result
There is no valid empirical answer to the submitted question. Any claimed accuracy, F1, stability, class-level effect, CUDA-training result, Critic-on-result, or Writer conclusion would be fabricated.

### Recommended Fix
Preserve rejected candidate source as first-class attempt artifacts, add contract-aware generation/repair checks before consuming retry budget, and validate a generated real-local-dataset Bundle under the same complete contract before another E2E run. Do not lower current validators.

### Blocks Round 5
YES.
