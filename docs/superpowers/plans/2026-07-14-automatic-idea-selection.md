# Automatic Idea Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Automatically review every generated hypothesis with idea-selection, persist an auditable evaluation, and use the server-selected highest-scoring Idea in later workflow steps.

**Architecture:** Add a focused IdeaSelectionAgent and a typed selection contract. Insert idea_selection between hypothesis_generation and evidence_reasoning; it writes idea_review and hypothesis_selection. The frontend runs this step automatically and renders its result without a manual confirmation.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, React, TypeScript, Node test runner.

## Global Constraints

- Load skills/idea-selection/SKILL.md through SkillLoader; do not copy the Skill into Python.
- Weights: novelty 20%, scientific soundness 20%, impact 15%, testability 20%, execution feasibility 20%, reproducibility/compliance 5%.
- The server computes the winner; model winner fields are ignored.
- Select the highest score even for REVISE, PIVOT, STOP, or EVIDENCE_INSUFFICIENT, retaining risks and gates.
- A provider/API failure is never a supervisor revision. Empty normalized candidates raise HYPOTHESIS_CANDIDATES_EMPTY immediately.

---

### Task 1: Add the typed Idea evaluation contract and Qwen agent

**Files:**

- Create: backend/app/workflow/idea_selection.py
- Create: backend/app/agents/idea_selection.py
- Create: tests/backend/test_idea_selection.py

**Interfaces:**

- Produces IdeaSelectionAgent.review(problem: dict, constraints: str, evidence: list[dict], candidates: list[dict], instructions: str) -> dict.
- Produces normalize_idea_review(raw: dict, candidate_count: int) -> dict and select_top_evaluation(evaluations: list[dict]) -> dict.
- select_top_evaluation returns selected_index, selected, weighted_score, and selection_reason.

- [ ] **Step 1: Write the failing contract tests**

~~~
import pytest
from backend.app.workflow.idea_selection import normalize_idea_review, select_top_evaluation


def item(index, scores, decision="REVISE"):
    return {
        "candidate_index": index, "idea_card": {"claim": f"c{index}"},
        "evidence_ledger": [], "closest_prior_work": [],
        "gates": {"testability": "PASS"}, "scores": scores, "mde": {},
        "risks": [], "decision": decision, "confidence": "medium", "unknowns": [],
    }


def test_server_weighted_selection_ignores_model_selected_index():
    weak = item(0, {"novelty": 5, "scientific_soundness": 1, "impact": 1, "testability": 1, "execution_feasibility": 1, "reproducibility_compliance": 1})
    strong = item(1, {"novelty": 3, "scientific_soundness": 5, "impact": 5, "testability": 5, "execution_feasibility": 5, "reproducibility_compliance": 5})
    review = normalize_idea_review({"selected_index": 0, "evaluations": [weak, strong]}, 2)

    assert select_top_evaluation(review["evaluations"])["selected_index"] == 1


def test_server_breaks_score_ties_by_lowest_candidate_index():
    scores = {"novelty": 4, "scientific_soundness": 4, "impact": 4, "testability": 4, "execution_feasibility": 4, "reproducibility_compliance": 4}
    review = normalize_idea_review({"evaluations": [item(1, scores), item(0, scores)]}, 2)

    assert select_top_evaluation(review["evaluations"])["selected_index"] == 0


def test_contract_rejects_missing_candidate_review():
    scores = {"novelty": 1, "scientific_soundness": 1, "impact": 1, "testability": 1, "execution_feasibility": 1, "reproducibility_compliance": 1}
    with pytest.raises(ValueError, match="IDEA_SELECTION_OUTPUT_INVALID"):
        normalize_idea_review({"evaluations": [item(0, scores)]}, 2)
~~~

- [ ] **Step 2: Run the tests and verify failure**

Run: ./.venv/Scripts/python.exe -m pytest tests/backend/test_idea_selection.py -q

Expected: collection fails because backend.app.workflow.idea_selection does not exist.

- [ ] **Step 3: Implement the contract and agent**

~~~
# backend/app/workflow/idea_selection.py
WEIGHTS = {
    "novelty": 0.20, "scientific_soundness": 0.20, "impact": 0.15,
    "testability": 0.20, "execution_feasibility": 0.20,
    "reproducibility_compliance": 0.05,
}
DECISIONS = {"GO", "REVISE", "PIVOT", "STOP", "EVIDENCE_INSUFFICIENT"}


def weighted_score(scores: dict) -> float:
    return sum(float(scores[key]) * weight for key, weight in WEIGHTS.items())


def select_top_evaluation(evaluations: list[dict]) -> dict:
    winner = sorted(
        evaluations,
        key=lambda value: (-weighted_score(value["scores"]), value["candidate_index"]),
    )[0]
    return {
        "selected_index": winner["candidate_index"],
        "selected": winner,
        "weighted_score": weighted_score(winner["scores"]),
        "selection_reason": "Highest server-computed weighted idea score.",
    }
~~~

Implement normalize_idea_review to require exactly one evaluation for every index in range(candidate_count), six numeric scores in [0, 5], valid decisions, and dict/list-shaped audit fields. Raise ValueError with IDEA_SELECTION_OUTPUT_INVALID for every invalid response. Preserve evaluations in index order and discard a model selected_index.

~~~
# backend/app/agents/idea_selection.py
class IdeaSelectionAgent:
    name = "Idea Selection Agent"

    def __init__(self, llm_provider):
        self.llm_provider = llm_provider

    def review(self, problem, constraints, evidence, candidates, *, instructions=""):
        return self.llm_provider.generate_json(
            "idea_selection.review",
            {"problem": problem, "constraints": constraints, "evidence": evidence, "candidates": candidates},
            {"evaluations": [{"candidate_index": "integer", "idea_card": "object", "evidence_ledger": ["object"], "closest_prior_work": ["object"], "gates": "object", "scores": "object", "mde": "object", "risks": ["string"], "decision": "GO|REVISE|PIVOT|STOP|EVIDENCE_INSUFFICIENT", "confidence": "low|medium|high", "unknowns": ["string"]}]},
            instructions=instructions,
        )
~~~

- [ ] **Step 4: Run the contract tests and verify success**

Run: ./.venv/Scripts/python.exe -m pytest tests/backend/test_idea_selection.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add backend/app/workflow/idea_selection.py backend/app/agents/idea_selection.py tests/backend/test_idea_selection.py
git commit -m "feat: add automatic idea selection contract"
~~~

### Task 2: Integrate automatic selection and downstream guards

**Files:**

- Modify: backend/app/workflow/steps.py
- Modify: backend/app/workflow/skills.py
- Modify: backend/app/workflow/engine.py
- Modify: backend/app/agents/supervisor.py
- Modify: backend/app/api/runs.py
- Modify: tests/backend/test_workflow_engine.py
- Modify: tests/backend/test_api.py
- Modify: tests/backend/test_supervisor_agent.py

**Interfaces:**

- Consumes Task 1’s IdeaSelectionAgent.review, normalize_idea_review, select_top_evaluation, and WEIGHTS.
- Produces step idea_selection, Artifact types idea_review and hypothesis_selection.
- evidence_reasoning and research_plan require the automatic hypothesis_selection.

- [ ] **Step 1: Write failing workflow tests**

~~~
def make_engine_and_run(tmp_path, llm):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository, llm, MockLiteratureProvider(), MockExperimentProvider()
    )
    return engine, repository.create_run("train cnn", "automatic selection")


def prepared_candidate_run(tmp_path):
    engine, run = make_engine_and_run(tmp_path, RecordingLLM())
    for step_id in [
        "problem_understanding", "knowledge_integration", "hypothesis_generation",
    ]:
        run = engine.run_step(run.id, step_id)
    return engine, run


def test_empty_hypotheses_stop_before_selection_or_reasoning(tmp_path):
    class EmptyLLM(RecordingLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "hypothesis.generate":
                return {"candidates": []}
            return super().generate_json(task, inputs, schema_hint, instructions)

    engine, run = make_engine_and_run(tmp_path, EmptyLLM())
    engine.run_step(run.id, "problem_understanding")
    engine.run_step(run.id, "knowledge_integration")

    with pytest.raises(ValueError, match="HYPOTHESIS_CANDIDATES_EMPTY"):
        engine.run_step(run.id, "hypothesis_generation")

    types = {artifact.type for artifact in engine.repository.get_run(run.id).artifacts}
    assert not {"hypothesis", "idea_review", "hypothesis_selection", "reasoning"} & types


def test_idea_selection_persists_highest_scoring_candidate(tmp_path):
    engine, run = prepared_candidate_run(tmp_path)
    run = engine.run_step(run.id, "idea_selection")

    latest = {artifact.type: artifact for artifact in run.artifacts}
    assert latest["hypothesis_selection"].content["selected_indexes"] == [1]
    assert latest["hypothesis_selection"].content["selection_mode"] == "automatic_weighted_review"
    assert latest["hypothesis_selection"].content["selected"][0]["decision"] == "EVIDENCE_INSUFFICIENT"
    assert len(latest["idea_review"].content["evaluations"]) == 2


def test_evidence_reasoning_requires_automatic_selection(tmp_path):
    engine, run = prepared_candidate_run(tmp_path)

    with pytest.raises(ValueError, match="HYPOTHESIS_SELECTION_REQUIRED"):
        engine.run_step(run.id, "evidence_reasoning")
~~~

Update RecordingLLM so idea_selection.review returns two valid evaluations and candidate 1 has the largest server-computed score. Use EVIDENCE_INSUFFICIENT for that winner.

- [ ] **Step 2: Run focused tests and verify failure**

Run: ./.venv/Scripts/python.exe -m pytest tests/backend/test_workflow_engine.py tests/backend/test_supervisor_agent.py tests/backend/test_api.py -q

Expected: FAIL because idea_selection is unknown and empty candidates are currently persisted.

- [ ] **Step 3: Implement workflow ordering, review, and guards**

~~~
# backend/app/workflow/steps.py
ORDER = [
    "problem_understanding", "knowledge_integration", "hypothesis_generation",
    "idea_selection", "evidence_reasoning", "research_plan", "experiment_task",
    "experiment_run_analysis", "feedback_revision", "report_export",
]

# backend/app/workflow/skills.py
"idea_selection": StepAssignment("idea", ("idea-selection",)),
~~~

Instantiate IdeaSelectionAgent in WorkflowEngine.__init__. In the hypothesis producer, normalize raw output and raise ValueError("HYPOTHESIS_CANDIDATES_EMPTY") if candidates is empty before _produce_validated can persist an Artifact or request revision.

Add the idea_selection branch: load problem, evidence, and candidates; call the agent with loaded Skill instructions; validate the result; compute the winner; and persist:

~~~
review_content = {"evaluations": evaluations, "weights": WEIGHTS}
selection_content = {
    "selected": [winner["selected"]],
    "selected_indexes": [winner["selected_index"]],
    "selection_mode": "automatic_weighted_review",
    "weighted_score": winner["weighted_score"],
    "selection_reason": winner["selection_reason"],
}
~~~

Add idea_selection to Supervisor’s known steps. Require hypothesis_selection before evidence_reasoning and pass its selected entry, rather than the full candidate set, to CriticAgent. Keep the research-plan guard.

Delete WorkflowEngine.select_hypotheses and the /api/runs/{run_id}/hypotheses/select endpoint. In add_user_hypothesis, append/replace the candidate, remove unlocked Artifacts from idea_selection onward, save, and call run_step(run_id, "idea_selection") to reevaluate every candidate automatically.

- [ ] **Step 4: Run focused backend tests and verify success**

Run: ./.venv/Scripts/python.exe -m pytest tests/backend/test_idea_selection.py tests/backend/test_workflow_engine.py tests/backend/test_supervisor_agent.py tests/backend/test_api.py -q

Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add backend/app/workflow/steps.py backend/app/workflow/skills.py backend/app/workflow/engine.py backend/app/agents/supervisor.py backend/app/api/runs.py tests/backend/test_workflow_engine.py tests/backend/test_supervisor_agent.py tests/backend/test_api.py
git commit -m "feat: select highest-scoring idea automatically"
~~~

### Task 3: Replace manual controls with the automatic review display

**Files:**

- Modify: frontend/src/App.tsx
- Modify: frontend/src/api/client.ts
- Modify: frontend/src/components/HypothesisBoard.tsx
- Modify: frontend/src/components/PipelineTimeline.tsx
- Modify: frontend/src/pages/WorkbenchPage.tsx
- Modify: frontend/tests/ui-contract.test.mjs

**Interfaces:**

- Consumes idea_review and hypothesis_selection Artifacts from Task 2.
- HypothesisBoard receives onAddUserHypothesis only.
- Initial automation ends after idea_selection; continuation begins with evidence_reasoning.

- [ ] **Step 1: Write failing UI contract assertions**

~~~
assert.match(appSource, /"hypothesis_generation",\s*"idea_selection"/);
assert.doesNotMatch(appSource, /onSelectHypotheses/);
assert.match(boardSource, /自动选择/);
assert.doesNotMatch(boardSource, /确认选择/);
assert.match(timelineSource, /id: "idea_selection"/);
~~~

- [ ] **Step 2: Run the UI contract test and verify failure**

Run: cd frontend; node --test tests/ui-contract.test.mjs

Expected: FAIL because the UI still runs evidence reasoning before selection and exposes manual confirmation.

- [ ] **Step 3: Implement automatic-flow UI**

~~~
const INITIAL_STEPS = [
  "problem_understanding", "knowledge_integration",
  "hypothesis_generation", "idea_selection",
];
const CONTINUATION_STEPS = [
  "evidence_reasoning", "research_plan", "experiment_task",
  "experiment_run_analysis", "feedback_revision", "report_export",
];
~~~

Remove selectHypotheses, api.selectHypotheses, and all onSelectHypotheses props. In HypothesisBoard, read the chosen index from hypothesis_selection.content.selected_indexes[0]; join candidates to idea_review.content.evaluations by candidate_index; show an “自动选择” marker, weighted score, decision, risks, and unknowns. Remove checkboxes, selection state, replacement-target state, and confirmation footer. Keep add-candidate; Task 2 makes it trigger automatic re-evaluation.

Add an idea_selection timeline entry between hypothesis generation and evidence reasoning. Make evidence reasoning require hypothesis_selection and evidence, labelled “自动 Idea 选择”.

- [ ] **Step 4: Run frontend verification**

Run: cd frontend; node --test tests/ui-contract.test.mjs; npm.cmd run build

Expected: both commands PASS.

- [ ] **Step 5: Commit**

~~~
git add frontend/src/App.tsx frontend/src/api/client.ts frontend/src/components/HypothesisBoard.tsx frontend/src/components/PipelineTimeline.tsx frontend/src/pages/WorkbenchPage.tsx frontend/tests/ui-contract.test.mjs
git commit -m "feat: display automatic idea selection"
~~~

### Task 4: Document and verify end-to-end behavior

**Files:**

- Modify: docs/runbook.md
- Modify: docs/agent_architecture.md
- Modify: tests/backend/test_end_to_end_dev_mode.py

**Interfaces:**

- Documents idea_selection as the automatic, evidence-led decision step.
- End-to-end execution never calls the removed manual selection route.

- [ ] **Step 1: Write the failing end-to-end test**

~~~
for step_id in [
    "problem_understanding", "knowledge_integration", "hypothesis_generation",
    "idea_selection", "evidence_reasoning", "research_plan",
]:
    run = engine.run_step(run.id, step_id)

selection = next(item for item in run.artifacts if item.type == "hypothesis_selection")
assert selection.content["selection_mode"] == "automatic_weighted_review"
assert "idea_selection.review" in llm.tasks
~~~

- [ ] **Step 2: Run end-to-end test and verify failure**

Run: ./.venv/Scripts/python.exe -m pytest tests/backend/test_end_to_end_dev_mode.py -q

Expected: FAIL until Task 2 exists.

- [ ] **Step 3: Update operational documentation**

Add to docs/runbook.md that hypotheses are automatically reviewed using idea-selection, the highest server-computed weighted score is selected, and non-GO decisions remain visible as risk states. Add to docs/agent_architecture.md a row for idea_selection, IdeaSelectionAgent, idea-selection, idea_review, and hypothesis_selection.

- [ ] **Step 4: Run complete verification**

Run: ./.venv/Scripts/python.exe -m pytest tests/backend -q

Run: cd frontend; node --test tests/ui-contract.test.mjs; npm.cmd run build

Expected: all commands PASS.

- [ ] **Step 5: Commit**

~~~
git add docs/runbook.md docs/agent_architecture.md tests/backend/test_end_to_end_dev_mode.py
git commit -m "docs: describe automatic idea selection"
~~~
