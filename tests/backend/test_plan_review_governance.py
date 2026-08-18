from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
from threading import Event

import pytest

import backend.app.agents.planner as planner_module
import backend.app.workflow.engine as engine_module
from backend.app.providers.experiment import MockExperimentProvider
from backend.app.providers.literature import MockLiteratureProvider
from backend.app.providers.llm import MockLLMProvider
from backend.app.agents.supervisor import SupervisorAgent
from backend.app.storage.repository import Repository
from backend.app.workflow.engine import WorkflowEngine
from backend.app.workflow.orchestrator import WorkflowOrchestrator
from backend.app.workflow.plan_review_governance import (
    PlanReviewPolicyIntegrityError,
    adjudicate_review,
    fix_map_issues,
    is_plan_governance_accepted,
)
from backend.app.workflow.skills import SkillLoader, SkillRegistry


POLICY = {
    "schema_version": 1,
    "policy_id": "test-bounded-review",
    "policy_sha256": "test-sha",
    "blocker_classes": ["MISSING_EXECUTABLE_COMPARATOR", "PRIMARY_ENDPOINT_UNDEFINED"],
    "reopen_rules": {"allowed_bases": ["regression", "new_evidence"]},
    "new_blocker_rules": {
        "after_initial_round_allowed_bases": ["regression", "new_evidence"]
    },
    "max_content_revisions": 2,
    "problem_anchor": {"original_question": "Q", "selected_primary_claim": "H"},
}


def _issue(
    issue_id: str = "PRI-control",
    *,
    blocker_class: str = "MISSING_EXECUTABLE_COMPARATOR",
    severity: str = "BLOCKER",
    status: str = "OPEN",
    field: str = "additional_sections",
    **extra,
):
    return {
        "issue_id": issue_id,
        "blocker_class": blocker_class,
        "severity": severity,
        "title": f"Issue {issue_id}",
        "contract_fields": [field],
        "evidence": [f"Evidence for {issue_id}"],
        "reason": f"Reason for {issue_id}",
        "required_fix": f"Fix {issue_id}",
        "status": status,
        **extra,
    }


def _review(*issues, verdict="REVISE", closed=()):
    return {
        "verdict": verdict,
        "issues": list(issues),
        "closed_issue_ids": list(closed),
        "required_changes": [],
        "suggested_fixes": [],
        "revised_plan_guidance": [],
        "experiment_feasibility": (
            "FEASIBLE" if verdict == "ACCEPT" else "FEASIBLE_AFTER_REVISION"
        ),
    }


class BoundedReviewLLM(MockLLMProvider):
    fallback = False
    mode = "fixture"

    def __init__(self, reviews):
        self.reviews = list(reviews)
        self.calls = []
        self.revision_count = 0

    def generate_json(self, task, inputs, schema_hint, instructions=""):
        self.calls.append((task, deepcopy(inputs), instructions))
        if task == "planning.review_plan":
            result = self.reviews.pop(0)
            if isinstance(result, Exception):
                raise result
            result = deepcopy(result)
            candidate_id = inputs.get("current_candidate_plan_id", "")
            for issue in result.get("issues") or []:
                if issue.get("status") == "CLOSED":
                    issue.setdefault("resolution", "The current candidate resolves the issue.")
                    issue.setdefault("evidence_artifact_ids", [candidate_id])
            return result
        if task == "planning.revise_from_review":
            self.revision_count += 1
            blocker_ids = [
                item["issue_id"] for item in inputs["open_validated_blockers"]
            ]
            return {
                **deepcopy(inputs["current_candidate"]),
                "additional_sections": {
                    "bounded_revision": self.revision_count,
                },
                "fix_map": {
                    issue_id: ["additional_sections"] for issue_id in blocker_ids
                },
            }
        return super().generate_json(task, inputs, schema_hint, instructions)


def _ready_engine(tmp_path, llm, *, skill_loader=None):
    repo = Repository(str(tmp_path))
    engine = WorkflowEngine(
        repo,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
        skill_loader=skill_loader,
    )
    run = repo.create_run("Study a generic intervention", "bounded review")
    for step in (
        "problem_understanding",
        "knowledge_integration",
        "hypothesis_generation",
        "evidence_reasoning",
    ):
        run = engine.run_step(run.id, step)
    return repo, engine, engine.select_hypothesis(run.id, 0)


def test_research_plan_assignment_adds_governance_without_replacing_existing_skills():
    skills = SkillRegistry().skills_for("research_plan")
    assert skills == (
        "research-refine",
        "hypothesis-experiment-gate",
        "experiment-plan",
        "plan-review-governance",
    )


def test_skill_loader_loads_structured_policy_and_engine_has_no_blocker_class_copy():
    root = Path(__file__).resolve().parents[2]
    loaded = SkillLoader(root).load_policy("plan-review-governance")
    assert loaded.content["policy_id"] == "bounded-plan-review-v1"
    assert len(loaded.sha256) == 64
    engine_text = (root / "backend/app/workflow/engine.py").read_text(encoding="utf-8")
    for blocker_class in loaded.content["blocker_classes"]:
        assert blocker_class not in engine_text
    assert "BLOCKER_CLASSES" not in engine_text


@pytest.mark.parametrize("missing", ["blocker_class", "contract_fields", "evidence", "required_fix"])
def test_incomplete_blocker_schema_cannot_block(missing):
    issue = _issue()
    issue.pop(missing)
    result = adjudicate_review(
        [], _review(issue), frozen_policy=POLICY, round_index=1
    )
    assert result.verdict == "ACCEPT"
    assert result.validated_open_blocker_ids == ()
    assert result.issues[0]["status"] == "REJECTED"


def test_warning_and_suggestion_never_block_and_initial_valid_blocker_can_block():
    nonblocking = adjudicate_review(
        [],
        _review(
            _issue("PRI-warning", severity="WARNING"),
            _issue("PRI-suggestion", severity="SUGGESTION"),
        ),
        frozen_policy=POLICY,
        round_index=1,
    )
    assert nonblocking.verdict == "ACCEPT"
    blocking = adjudicate_review(
        [], _review(_issue()), frozen_policy=POLICY, round_index=1
    )
    assert blocking.verdict == "REVISE"
    assert blocking.validated_open_blocker_ids == ("PRI-control",)


def test_revision_new_old_problem_cannot_become_blocker_but_new_evidence_can():
    unqualified = adjudicate_review(
        [],
        _review(_issue("PRI-late")),
        frozen_policy=POLICY,
        round_index=2,
    )
    assert unqualified.verdict == "ACCEPT"
    assert unqualified.issues[0]["status"] == "REJECTED"

    qualified = adjudicate_review(
        [],
        _review(
            _issue(
                "PRI-evidence",
                new_blocker_basis="new_evidence",
                evidence_artifact_ids=["art-new"],
            )
        ),
        frozen_policy=POLICY,
        round_index=2,
        new_evidence_artifact_ids=("art-new",),
    )
    assert qualified.verdict == "REVISE"
    assert qualified.validated_open_blocker_ids == ("PRI-evidence",)


def test_closed_issue_only_reopens_for_chronology_validated_regression():
    opened = adjudicate_review(
        [], _review(_issue()), frozen_policy=POLICY, round_index=1
    )
    closed = adjudicate_review(
        opened.issues,
        _review(_issue(status="CLOSED", resolution="resolved", evidence_artifact_ids=["plan-2"]), closed=("PRI-control",)),
        frozen_policy=POLICY,
        round_index=2,
        changed_fields=("additional_sections",),
        candidate_plan_id="plan-2",
        review_id="review-2",
    )
    assert closed.issues[0]["status"] == "CLOSED"

    no_regression = adjudicate_review(
        closed.issues,
        _review(_issue(status="REOPENED", reopen_basis="regression")),
        frozen_policy=POLICY,
        round_index=3,
        changed_fields=(),
        candidate_plan_id="plan-3",
        review_id="review-3",
    )
    assert no_regression.verdict == "ACCEPT"
    assert no_regression.issues[0]["status"] == "CLOSED"

    reopened = adjudicate_review(
        closed.issues,
        _review(_issue(status="REOPENED", reopen_basis="regression", evidence_artifact_ids=["plan-3"])),
        frozen_policy=POLICY,
        round_index=3,
        changed_fields=("additional_sections",),
        candidate_plan_id="plan-3",
        review_id="review-3",
    )
    assert reopened.verdict == "REVISE"
    assert reopened.issues[0]["status"] == "REOPENED"


def test_closed_blocker_with_legal_closure_evidence_does_not_require_required_fix():
    opened = adjudicate_review(
        [], _review(_issue()), frozen_policy=POLICY, round_index=1
    )
    closed_issue = _issue(
        status="CLOSED",
        required_fix=None,
        resolution="The revised plan restores the control.",
        evidence_artifact_ids=["plan-2"],
    )
    closed = adjudicate_review(
        opened.issues,
        _review(closed_issue, verdict="ACCEPT", closed=("PRI-control",)),
        frozen_policy=POLICY,
        round_index=2,
        changed_fields=("additional_sections",),
        candidate_plan_id="plan-2",
        review_id="review-2",
    )
    assert closed.verdict == "ACCEPT"
    assert closed.issues[0]["status"] == "CLOSED"


@pytest.mark.parametrize("term", ["tensor axis", "dtype", "FFT window", "loader mapping"])
def test_execution_resolvable_blocker_is_delegated_to_experiment(term):
    result = adjudicate_review(
        [],
        _review(
            _issue(
                title=f"{term} needs confirmation",
                reason=f"{term} is an implementation detail",
                required_fix=f"Decide {term} in runtime code",
            )
        ),
        frozen_policy=POLICY,
        round_index=1,
    )
    assert result.verdict == "ACCEPT"
    assert result.validated_open_blocker_ids == ()
    assert result.issues[0]["severity"] == "WARNING"
    assert result.issues[0]["adjudication_reason"] == (
        "execution_resolvable_issue_delegated_to_experiment"
    )


def test_revision_prompt_contains_open_closed_ledger_and_frozen_anchor(tmp_path):
    issue_a = _issue("PRI-A")
    issue_b = _issue(
        "PRI-B", blocker_class="PRIMARY_ENDPOINT_UNDEFINED"
    )
    llm = BoundedReviewLLM(
        [
            _review(issue_a, issue_b),
            _review(issue_a, _issue("PRI-B", blocker_class="PRIMARY_ENDPOINT_UNDEFINED", status="CLOSED"), closed=("PRI-B",)),
            _review(_issue("PRI-A", status="CLOSED"), verdict="ACCEPT", closed=("PRI-A",)),
        ]
    )
    _, engine, run = _ready_engine(tmp_path, llm)
    completed = engine.run_step(run.id, "research_plan")
    revision_inputs = [
        inputs for task, inputs, _ in llm.calls if task == "planning.revise_from_review"
    ]
    assert len(revision_inputs) == 2
    assert {item["issue_id"] for item in revision_inputs[1]["open_validated_blockers"]} == {"PRI-A"}
    assert {item["issue_id"] for item in revision_inputs[1]["closed_issue_ledger"]} == {"PRI-B"}
    assert revision_inputs[1]["frozen_problem_anchor"]["original_question"] == "Study a generic intervention"
    assert any(artifact.type == "plan" for artifact in completed.artifacts)


def test_policy_is_frozen_per_run_when_disk_policy_changes(tmp_path):
    source_root = Path(__file__).resolve().parents[2]
    copied_root = tmp_path / "skill-repository"
    copied_skills = copied_root / "skills"
    for skill_id in SkillRegistry().skills_for("research_plan"):
        shutil.copytree(
            source_root / "skills" / skill_id,
            copied_skills / skill_id,
        )

    llm = BoundedReviewLLM(
        [_review(verdict="ACCEPT"), _review(verdict="ACCEPT")]
    )
    repo, engine, run = _ready_engine(tmp_path / "run-store", llm)
    engine.skill_loader = SkillLoader(copied_root)
    first = engine.run_step(run.id, "research_plan")
    frozen = next(
        item for item in first.artifacts if item.type == "plan_review_policy"
    )
    frozen_sha = frozen.content["policy_sha256"]

    policy_path = copied_skills / "plan-review-governance" / "policy.json"
    changed = json.loads(policy_path.read_text(encoding="utf-8"))
    changed["blocker_classes"] = ["CHANGED_FOR_NEW_RUN_ONLY"]
    policy_path.write_text(json.dumps(changed), encoding="utf-8")
    assert SkillLoader(copied_root).load_policy("plan-review-governance").sha256 != frozen_sha

    second = engine.run_step(run.id, "research_plan")
    policies = [item for item in second.artifacts if item.type == "plan_review_policy"]
    assert len(policies) == 1
    assert policies[0].content["policy_sha256"] == frozen_sha
    assert "CHANGED_FOR_NEW_RUN_ONLY" not in policies[0].content["blocker_classes"]


def test_resume_preserves_policy_ledger_and_candidate_lineage(tmp_path):
    llm = BoundedReviewLLM(
        [
            _review(_issue()),
            RuntimeError("MODEL_REQUEST_FAILED:provider=deepseek"),
            _review(_issue(status="CLOSED"), verdict="ACCEPT", closed=("PRI-control",)),
        ]
    )
    repo, engine, run = _ready_engine(tmp_path, llm)
    with pytest.raises(RuntimeError, match="provider=deepseek"):
        engine.run_step(run.id, "research_plan")
    interrupted = repo.get_run(run.id)
    policy_ids = [item.id for item in interrupted.artifacts if item.type == "plan_review_policy"]
    ledger_ids = [item.id for item in interrupted.artifacts if item.type == "plan_review_issue_ledger"]
    candidate_ids = [item.id for item in interrupted.artifacts if item.type == "research_plan_candidate"]

    resumed = engine.run_step(run.id, "research_plan")
    assert [item.id for item in resumed.artifacts if item.type == "plan_review_policy"] == policy_ids
    assert [item.id for item in resumed.artifacts if item.type == "plan_review_issue_ledger"][: len(ledger_ids)] == ledger_ids
    assert [item.id for item in resumed.artifacts if item.type == "research_plan_candidate"][: len(candidate_ids)] == candidate_ids
    assert any(item.type == "plan" for item in resumed.artifacts)


def test_legacy_run_without_governance_artifacts_remains_readable(tmp_path):
    repo = Repository(str(tmp_path))
    created = repo.create_run("legacy question", "legacy")
    loaded = repo.get_run(created.id)
    assert loaded.artifacts == []
    assert not any(
        item.type in {"plan_review_policy", "plan_review_issue_ledger"}
        for item in loaded.artifacts
    )


def test_naked_closed_ids_and_incomplete_closure_cannot_close_open_blocker():
    opened = adjudicate_review([], _review(_issue()), frozen_policy=POLICY, round_index=1)
    naked = adjudicate_review(
        opened.issues,
        _review(verdict="ACCEPT", closed=("PRI-control",)),
        frozen_policy=POLICY,
        round_index=2,
        changed_fields=("additional_sections",),
        candidate_plan_id="plan-2",
        review_id="review-2",
    )
    assert naked.validated_open_blocker_ids == ("PRI-control",)
    incomplete = adjudicate_review(
        opened.issues,
        _review(_issue(status="CLOSED")),
        frozen_policy=POLICY,
        round_index=2,
        changed_fields=("additional_sections",),
        candidate_plan_id="plan-2",
        review_id="review-2",
    )
    assert incomplete.issues[0]["status"] == "OPEN"


def test_closed_issue_reopens_for_chronological_new_evidence_only():
    closed = [{**_issue(), "status": "CLOSED", "validated_blocker": False, "introduced_round": 1}]
    repeated = adjudicate_review(
        closed,
        _review(_issue(status="REOPENED")),
        frozen_policy=POLICY,
        round_index=2,
        candidate_plan_id="plan-2",
        review_id="review-2",
    )
    assert repeated.issues[0]["status"] == "CLOSED"
    policy = deepcopy(POLICY)
    policy["reopen_rules"] = {"allowed_bases": ["regression", "new_evidence"]}
    reopened = adjudicate_review(
        closed,
        _review(_issue(status="REOPENED", reopen_basis="new_evidence", evidence_artifact_ids=["art-new"])),
        frozen_policy=policy,
        round_index=2,
        new_evidence_artifact_ids=("art-new",),
        candidate_plan_id="plan-2",
        review_id="review-2",
    )
    assert reopened.validated_open_blocker_ids == ("PRI-control",)


@pytest.mark.parametrize(
    ("fix_map", "changed", "code"),
    [
        ({}, ("additional_sections",), "MISSING"),
        ({"PRI-control": ["additional_sections"], "PRI-fake": ["additional_sections"]}, ("additional_sections",), "EXTRA"),
        ({"PRI-control": ["procedure"]}, ("procedure",), "UNRELATED"),
        ({"PRI-control": ["additional_sections"]}, ("procedure",), "UNCHANGED"),
    ],
)
def test_fix_map_rejects_non_exact_or_non_material_mapping(fix_map, changed, code):
    issues = fix_map_issues(
        fix_map,
        open_blockers=[_issue()],
        changed_fields=changed,
    )
    assert any(code in item for item in issues)


def test_fix_map_accepts_exact_material_mapping():
    assert fix_map_issues(
        {"PRI-control": ["additional_sections"]},
        open_blockers=[_issue()],
        changed_fields=("additional_sections",),
    ) == []


def test_fix_map_accepts_five_blockers_with_real_canonical_changes():
    fields = ("dataset", "comparisons", "procedure", "evaluations", "risks")
    blockers = [
        _issue(f"BLOCKER-{index}", field=field)
        for index, field in enumerate(fields, start=1)
    ]

    assert fix_map_issues(
        {
            blocker["issue_id"]: [blocker["contract_fields"][0]]
            for blocker in blockers
        },
        open_blockers=blockers,
        changed_fields=fields,
    ) == []


def test_fix_map_does_not_require_unrelated_plan_diff_fields_to_be_declared():
    assert fix_map_issues(
        {"PRI-control": ["dataset"]},
        open_blockers=[_issue(field="dataset")],
        changed_fields=("dataset", "risks"),
    ) == []


@pytest.mark.parametrize(
    ("fix_map", "changed", "code"),
    [
        ({"PRI-control": ["dataset.selection.strategy"]}, ("dataset",), "UNKNOWN_FIELD"),
        ({"PRI-control": ["procedure"]}, ("procedure",), "UNRELATED"),
        ({"PRI-control": ["dataset"]}, ("procedure",), "UNCHANGED"),
    ],
)
def test_fix_map_still_rejects_unknown_unrelated_and_unchanged_fields(
    fix_map, changed, code
):
    issues = fix_map_issues(
        fix_map,
        open_blockers=[_issue(field="dataset")],
        changed_fields=changed,
    )

    assert any(code in item for item in issues)


def test_one_bounded_fix_map_repair_creates_one_revision_candidate(tmp_path):
    class RepairOnceLLM(BoundedReviewLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "planning.revise_from_review":
                self.calls.append((task, deepcopy(inputs), instructions))
                if inputs.get("fix_map_repair") is True:
                    assert inputs["open_validated_blocker_ids"] == ["PRI-control"]
                    assert inputs["allowed_canonical_fields_by_blocker"] == {
                        "PRI-control": ["additional_sections"]
                    }
                    assert "additional_sections" in inputs[
                        "actual_changed_contract_fields"
                    ]
                    return {
                        "fix_map": {"PRI-control": ["additional_sections"]}
                    }
                self.revision_count += 1
                return {
                    **deepcopy(inputs["current_candidate"]),
                    "additional_sections": {"bounded_revision": 1},
                    "fix_map": {"PRI-control": ["procedure"]},
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    llm = RepairOnceLLM(
        [
            _review(_issue()),
            _review(
                _issue(status="CLOSED"),
                verdict="ACCEPT",
                closed=("PRI-control",),
            ),
        ]
    )
    _, engine, run = _ready_engine(tmp_path, llm)

    completed = engine.run_step(run.id, "research_plan")

    candidates = [
        item
        for item in completed.artifacts
        if item.type == "research_plan_candidate"
    ]
    revision_calls = [
        call for call in llm.calls if call[0] == "planning.revise_from_review"
    ]
    assert len(revision_calls) == 2
    assert len(candidates) == 2
    assert candidates[-1].content["revision_attempt"] == 1
    assert candidates[-1].content["normalized_plan"]["fix_map"] == {
        "PRI-control": ["additional_sections"]
    }
    assert is_plan_governance_accepted(completed.artifacts)


def test_two_invalid_fix_maps_stop_recoverably_without_candidate_or_budget(tmp_path):
    class InvalidRepairLLM(BoundedReviewLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "planning.revise_from_review":
                self.calls.append((task, deepcopy(inputs), instructions))
                if inputs.get("fix_map_repair") is True:
                    return {"fix_map": {"PRI-control": ["procedure"]}}
                self.revision_count += 1
                return {
                    **deepcopy(inputs["current_candidate"]),
                    "additional_sections": {"bounded_revision": 1},
                    "fix_map": {"PRI-control": ["procedure"]},
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    llm = InvalidRepairLLM([_review(_issue())])
    repo, engine, run = _ready_engine(tmp_path, llm)
    orchestrator = WorkflowOrchestrator(repo, lambda: engine)

    orchestrator._drive(run.id)

    stopped = repo.get_run(run.id)
    candidates = [
        item
        for item in stopped.artifacts
        if item.type == "research_plan_candidate"
    ]
    revision_calls = [
        call for call in llm.calls if call[0] == "planning.revise_from_review"
    ]
    assert stopped.status == "RECOVERABLE_PROVIDER_ERROR"
    assert len(revision_calls) == 2
    assert len(candidates) == 1
    assert candidates[0].content["revision_attempt"] == 0


def test_production_supervisor_reviewer_cannot_adjudicate_research_plan(tmp_path):
    class RejectingReviewer:
        def __init__(self):
            self.calls = 0

        def review(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("parallel plan reviewer must not run")

    repo, engine, run = _ready_engine(
        tmp_path, BoundedReviewLLM([_review(verdict="ACCEPT")])
    )
    rejecting = RejectingReviewer()
    engine.supervisor_agent = SupervisorAgent(engine.skill_registry, rejecting)
    completed = engine.run_step(run.id, "research_plan")
    assert rejecting.calls == 0
    assert any(item.type == "plan" for item in completed.artifacts)
    with pytest.raises(ValueError, match="PLAN_REVIEW_GOVERNANCE_ONLY"):
        SupervisorAgent.revision_limit("research_plan")
    with pytest.raises(ValueError, match="PLAN_REVIEW_GOVERNANCE_ONLY"):
        engine._produce_validated(run.id, "research_plan", lambda revision: {})


def test_orchestrator_stops_at_revision_budget_without_round_four(tmp_path):
    llm = BoundedReviewLLM([_review(_issue()), _review(_issue()), _review(_issue())])
    repo, engine, run = _ready_engine(tmp_path, llm)
    orchestrator = WorkflowOrchestrator(repo, lambda: engine)
    orchestrator._drive(run.id)
    stopped = repo.get_run(run.id)
    assert stopped.status == "NEEDS_PLAN_REVISION"
    assert stopped.automatic is False
    assert len([item for item in stopped.artifacts if item.type == "research_plan_candidate"]) == 3
    assert len([call for call in llm.calls if call[0] == "planning.review_plan"]) == 3
    assert len([call for call in llm.calls if call[0] == "planning.revise_from_review"]) == 2
    call_count = len(llm.calls)
    orchestrator.start(run.id)
    still_stopped = repo.get_run(run.id)
    assert still_stopped.status == "NEEDS_PLAN_REVISION"
    assert len(llm.calls) == call_count
    assert len([item for item in still_stopped.artifacts if item.type == "research_plan_candidate"]) == 3


def test_historical_execution_blocker_recovers_without_rewriting_ledger(tmp_path):
    llm = BoundedReviewLLM([_review(_issue()), _review(_issue()), _review(_issue())])
    repo, engine, run = _ready_engine(tmp_path, llm)
    WorkflowOrchestrator(repo, lambda: engine)._drive(run.id)
    stopped = repo.get_run(run.id)
    original_ledger = next(
        item for item in reversed(stopped.artifacts)
        if item.type == "plan_review_issue_ledger"
    )
    original_ledger_hash = original_ledger.content["ledger_payload_sha256"]
    latest_review = next(
        item for item in reversed(stopped.artifacts) if item.type == "plan_review"
    )
    latest_review.content["issues"][0].update(
        title="tensor axis needs runtime confirmation",
        reason="tensor axis is an implementation detail",
        required_fix="Confirm the tensor axis in the loader/harness.",
    )
    repo.save_run(stopped)

    assert engine.recover_plan_review_for_continue(run.id) is True
    recovered = repo.get_run(run.id)
    assert original_ledger.content["ledger_payload_sha256"] == original_ledger_hash
    recovery = next(
        item for item in recovered.artifacts
        if item.type == "plan_review_recovery_adjudication"
    )
    assert recovery.content["validated_open_blocker_ids"] == []
    completed = engine.run_step(run.id, "research_plan")
    assert is_plan_governance_accepted(completed.artifacts)


def test_continue_endpoint_path_reaches_experiment_after_old_plan_recovery(tmp_path):
    llm = BoundedReviewLLM([_review(_issue()), _review(_issue()), _review(_issue())])
    repo, engine, run = _ready_engine(tmp_path, llm)
    WorkflowOrchestrator(repo, lambda: engine)._drive(run.id)
    stopped = repo.get_run(run.id)
    latest_review = next(
        item for item in reversed(stopped.artifacts) if item.type == "plan_review"
    )
    latest_review.content["issues"][0].update(
        title="dtype and tensor axis need loader confirmation",
        reason="The loader/runtime can verify dtype and tensor axis semantics.",
        required_fix="Confirm the dtype and axis in the Loader Validator.",
    )
    repo.save_run(stopped)
    revisions_before = llm.revision_count
    experiment_boundary = Event()

    class ReplayBoundaryEngine:
        universal_scientific_stability = True
        max_feedback_iterations = engine.max_feedback_iterations

        def recover_plan_review_for_continue(self, run_id):
            return engine.recover_plan_review_for_continue(run_id)

        def run_step(self, run_id, step_id):
            if step_id == "experiment_task":
                experiment_boundary.set()
                raise RuntimeError("TEST_EXPERIMENT_BOUNDARY")
            return engine.run_step(run_id, step_id)

    orchestrator = WorkflowOrchestrator(repo, ReplayBoundaryEngine)
    orchestrator.start(run.id)
    assert experiment_boundary.wait(3)
    worker = orchestrator._threads.get(run.id)
    if worker is not None:
        worker.join(3)
    continued = repo.get_run(run.id)
    assert llm.revision_count == revisions_before
    assert is_plan_governance_accepted(continued.artifacts)
    assert continued.status != "NEEDS_PLAN_REVISION"
    assert not any(
        item.type == "plan_revision_required"
        and item.created_at > stopped.updated_at
        for item in continued.artifacts
    )


@pytest.mark.parametrize("corruption", ["missing", "multiple", "policy_hash", "skill_hash"])
def test_policy_corruption_fails_closed_with_recoverable_state(tmp_path, corruption):
    repo, engine, run = _ready_engine(
        tmp_path, BoundedReviewLLM([_review(verdict="ACCEPT")])
    )
    completed = engine.run_step(run.id, "research_plan")
    stored = repo.get_run(completed.id)
    policy = next(item for item in stored.artifacts if item.type == "plan_review_policy")
    if corruption == "missing":
        stored.artifacts = [item for item in stored.artifacts if item.id != policy.id]
    elif corruption == "multiple":
        stored.artifacts.append(policy.model_copy(update={"id": "art_duplicate_policy"}))
    elif corruption == "policy_hash":
        policy.content["blocker_classes"] = ["CORRUPTED"]
    else:
        policy.content["skill_snapshots"][0]["normalized_content"] += "\ncorrupted"
    repo.save_run(stored)
    with pytest.raises(PlanReviewPolicyIntegrityError):
        engine.run_step(stored.id, "research_plan")
    failed = repo.get_run(stored.id)
    assert failed.status == "POLICY_INTEGRITY_REQUIRED"
    assert failed.automatic is False
    step = next(item for item in failed.steps if item.id == "research_plan")
    assert step.status == "interrupted"
    assert step.error["recoverable"] is True


def test_disk_skill_change_after_freeze_does_not_change_resume_prompt(tmp_path):
    source_root = Path(__file__).resolve().parents[2]
    copied_root = tmp_path / "skill-repository"
    copied_skills = copied_root / "skills"
    for skill_id in SkillRegistry().skills_for("research_plan"):
        shutil.copytree(source_root / "skills" / skill_id, copied_skills / skill_id)
    llm = BoundedReviewLLM([
        RuntimeError("MODEL_REQUEST_FAILED:provider=deepseek"),
        _review(verdict="ACCEPT"),
    ])
    repo, engine, run = _ready_engine(tmp_path / "run-store", llm)
    engine.skill_loader = SkillLoader(copied_root)
    with pytest.raises(RuntimeError, match="provider=deepseek"):
        engine.run_step(run.id, "research_plan")
    first_prompt = [call[2] for call in llm.calls if call[0] == "planning.review_plan"][0]
    skill_path = copied_skills / "research-refine" / "SKILL.md"
    skill_path.write_text(skill_path.read_text(encoding="utf-8") + "\nDISK_DRIFT_MARKER", encoding="utf-8")
    engine.run_step(run.id, "research_plan")
    prompts = [call[2] for call in llm.calls if call[0] == "planning.review_plan"]
    assert prompts == [first_prompt, first_prompt]
    assert "DISK_DRIFT_MARKER" not in prompts[1]


def test_true_legacy_plan_history_creates_explicit_migration_boundary(tmp_path):
    repo, engine, run = _ready_engine(
        tmp_path, BoundedReviewLLM([_review(verdict="ACCEPT")])
    )
    legacy = repo.add_artifact(
        run.id,
        "research_plan_candidate",
        "Legacy Research Plan Candidate",
        {"normalized_plan": {"objective": "legacy", "procedure": {"steps": []}}},
        "research_plan",
        "legacy planner",
    )
    completed = engine.run_step(run.id, "research_plan")
    migrations = [item for item in completed.artifacts if item.type == "plan_governance_migration"]
    policies = [item for item in completed.artifacts if item.type == "plan_review_policy"]
    assert len(migrations) == 1 and len(policies) == 1
    assert migrations[0].parent_artifact_id == legacy.id
    assert migrations[0].content["legacy"] is True
    assert legacy.id in migrations[0].content["previous_plan_lineage"]
    assert policies[0].parent_artifact_id == migrations[0].id


def test_legacy_final_plan_must_migrate_and_pass_governance_before_experiment(tmp_path):
    repo, engine, run = _ready_engine(
        tmp_path, BoundedReviewLLM([_review(verdict="ACCEPT")])
    )
    legacy = repo.add_artifact(
        run.id,
        "plan",
        "Legacy Final Research Plan",
        {"objective": "legacy objective", "procedure": {"steps": ["legacy step"]}},
        "research_plan",
        "legacy planner",
    )
    before = repo.get_run(run.id)
    assert WorkflowOrchestrator._next_step(before) == "research_plan"
    assert is_plan_governance_accepted(before.artifacts) is False

    completed = engine.run_step(run.id, "research_plan")

    migrations = [
        item for item in completed.artifacts if item.type == "plan_governance_migration"
    ]
    policies = [item for item in completed.artifacts if item.type == "plan_review_policy"]
    candidates = [
        item for item in completed.artifacts if item.type == "research_plan_candidate"
    ]
    plans = [item for item in completed.artifacts if item.type == "plan"]
    assert len(migrations) == 1 and len(policies) == 1
    assert migrations[0].content["legacy_plan_id"] == legacy.id
    assert migrations[0].content["migration_payload_sha256"]
    assert candidates[0].content["normalized_plan"]["objective"] == "legacy objective"
    assert plans[-1].parent_artifact_id == candidates[-1].id
    assert is_plan_governance_accepted(completed.artifacts) is True
    assert WorkflowOrchestrator._next_step(completed) == "experiment_task"


def test_frozen_package_drives_actual_review_and_revision_provider_prompts(
    tmp_path, monkeypatch
):
    class PromptFreezeLLM(BoundedReviewLLM):
        def __init__(self):
            super().__init__([])
            self.raw_calls = []
            self.review_attempt = 0
            self.revise_attempt = 0

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task in {"planning.review_plan", "planning.revise_from_review"}:
                self.raw_calls.append(
                    (task, deepcopy(inputs), deepcopy(schema_hint), instructions)
                )
            if task == "planning.review_plan":
                self.review_attempt += 1
                if self.review_attempt == 1:
                    raise RuntimeError("MODEL_REQUEST_FAILED:freeze-review")
                if self.review_attempt == 2:
                    return _review(_issue(field="procedure"))
                candidate_id = inputs.get("current_candidate_plan_id", "")
                return _review(
                    _issue(
                        field="procedure",
                        status="CLOSED",
                        resolution="procedure repaired",
                        evidence_artifact_ids=[candidate_id],
                    ),
                    verdict="ACCEPT",
                    closed=("PRI-control",),
                )
            if task == "planning.revise_from_review":
                self.revise_attempt += 1
                if self.revise_attempt == 1:
                    raise RuntimeError("MODEL_REQUEST_FAILED:freeze-revision")
                return {
                    **deepcopy(inputs["current_candidate"]),
                    "procedure": {"steps": ["frozen repair"]},
                    "fix_map": {"PRI-control": ["procedure"]},
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    source_root = Path(__file__).resolve().parents[2]
    copied_root = tmp_path / "frozen-prompt-skills"
    shutil.copytree(source_root / "skills", copied_root / "skills")
    llm = PromptFreezeLLM()
    repo, engine, run = _ready_engine(
        tmp_path / "run-store", llm, skill_loader=SkillLoader(copied_root)
    )
    with pytest.raises(RuntimeError, match="freeze-review"):
        engine.run_step(run.id, "research_plan")

    skill_path = copied_root / "skills" / "research-refine" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8") + "\nLIVE SKILL DRIFT",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        planner_module,
        "authoritative_plan_contract",
        lambda: {"live_drift": "must never enter the frozen prompt"},
    )
    monkeypatch.setattr(
        engine_module,
        "authoritative_plan_contract",
        lambda: {"live_drift": "must never enter the frozen prompt"},
    )
    monkeypatch.setattr(
        planner_module, "PLAN_REVIEW_FIXED_INSTRUCTIONS", "LIVE REVIEW DRIFT"
    )
    monkeypatch.setattr(
        planner_module, "PLAN_REVISION_FIXED_INSTRUCTIONS", "LIVE REVISION DRIFT"
    )
    monkeypatch.setattr(
        engine_module, "PLAN_REVIEW_FIXED_INSTRUCTIONS", "LIVE REVIEW DRIFT"
    )
    monkeypatch.setattr(
        engine_module, "PLAN_REVISION_FIXED_INSTRUCTIONS", "LIVE REVISION DRIFT"
    )

    with pytest.raises(RuntimeError, match="freeze-revision"):
        engine.run_step(run.id, "research_plan")
    completed = engine.run_step(run.id, "research_plan")
    assert is_plan_governance_accepted(completed.artifacts)

    review_calls = [row for row in llm.raw_calls if row[0] == "planning.review_plan"]
    revision_calls = [
        row for row in llm.raw_calls if row[0] == "planning.revise_from_review"
    ]
    assert review_calls[0] == review_calls[1]
    assert revision_calls[0] == revision_calls[1]
    assert "LIVE REVIEW DRIFT" not in review_calls[1][3]
    assert "LIVE REVISION DRIFT" not in revision_calls[1][3]
    assert "LIVE SKILL DRIFT" not in review_calls[1][3]
    assert "LIVE SKILL DRIFT" not in revision_calls[1][3]
    assert "live_drift" not in json.dumps(review_calls[1], ensure_ascii=False)
    assert "live_drift" not in json.dumps(revision_calls[1], ensure_ascii=False)


def test_dataset_identity_alias_is_canonicalized_through_ledger_diff_and_fix_map(
    tmp_path,
):
    class CanonicalFieldLLM(BoundedReviewLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "planning.revise_from_review":
                self.calls.append((task, deepcopy(inputs), instructions))
                self.revision_count += 1
                return {
                    **deepcopy(inputs["current_candidate"]),
                    "dataset": {"name": "canonical revised dataset"},
                    "fix_map": {"PRI-control": ["dataset_identity"]},
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    llm = CanonicalFieldLLM(
        [
            _review(_issue(field="dataset_identity")),
            _review(
                _issue(field="dataset_identity", status="CLOSED"),
                verdict="ACCEPT",
                closed=("PRI-control",),
            ),
        ]
    )
    repo, engine, run = _ready_engine(tmp_path, llm)
    completed = engine.run_step(run.id, "research_plan")
    ledgers = [
        item for item in completed.artifacts if item.type == "plan_review_issue_ledger"
    ]
    candidates = [
        item for item in completed.artifacts if item.type == "research_plan_candidate"
    ]
    assert ledgers[0].content["issues"][0]["contract_fields"] == ["dataset"]
    assert candidates[1].content["normalized_plan"]["fix_map"] == {
        "PRI-control": ["dataset"]
    }
    assert ledgers[-1].content["validated_open_blocker_ids"] == []
    assert is_plan_governance_accepted(completed.artifacts)


@pytest.mark.parametrize(
    "artifact_type", ["research_plan_candidate", "plan_review", "plan_review_issue_ledger"]
)
def test_corrupt_governance_child_fails_before_llm_or_artifact_side_effect(
    tmp_path, artifact_type
):
    llm = BoundedReviewLLM([_review(verdict="ACCEPT")])
    repo, engine, run = _ready_engine(tmp_path, llm)
    completed = engine.run_step(run.id, "research_plan")
    stored = repo.get_run(completed.id)
    target = next(item for item in stored.artifacts if item.type == artifact_type)
    target.content["policy_artifact_id"] = "art_corrupted_policy"
    repo.save_run(stored)
    calls_before = len(llm.calls)
    governance_before = len(
        [item for item in stored.artifacts if item.type.startswith("plan_review") or item.type == "research_plan_candidate"]
    )
    revisions_before = llm.revision_count

    with pytest.raises(PlanReviewPolicyIntegrityError):
        engine.run_step(stored.id, "research_plan")

    failed = repo.get_run(stored.id)
    governance_after = len(
        [item for item in failed.artifacts if item.type.startswith("plan_review") or item.type == "research_plan_candidate"]
    )
    assert len(llm.calls) == calls_before
    assert llm.revision_count == revisions_before
    assert governance_after == governance_before
    assert failed.status == "POLICY_INTEGRITY_REQUIRED"


def test_migration_payload_corruption_fails_closed_before_llm_side_effect(tmp_path):
    llm = BoundedReviewLLM([_review(verdict="ACCEPT")])
    repo, engine, run = _ready_engine(tmp_path, llm)
    repo.add_artifact(
        run.id,
        "plan",
        "Legacy Final Plan",
        {"objective": "legacy", "procedure": {"steps": ["one"]}},
        "research_plan",
        "legacy planner",
    )
    completed = engine.run_step(run.id, "research_plan")
    stored = repo.get_run(completed.id)
    migration = next(
        item for item in stored.artifacts if item.type == "plan_governance_migration"
    )
    migration.content["legacy_plan_hash"] = "corrupted"
    repo.save_run(stored)
    calls_before = len(llm.calls)
    artifacts_before = len(stored.artifacts)

    with pytest.raises(PlanReviewPolicyIntegrityError):
        engine.run_step(stored.id, "research_plan")

    failed = repo.get_run(stored.id)
    assert len(llm.calls) == calls_before
    assert len(failed.artifacts) == artifacts_before
    assert failed.status == "POLICY_INTEGRITY_REQUIRED"


@pytest.mark.parametrize(
    "missing_phase",
    [
        "CANDIDATE_CREATED",
        "REVIEW_CREATED",
        "LEDGER_COMMITTED",
        "REVISION_REQUESTED",
        "REVISION_CREATED",
        "ROUND_COMPLETE",
    ],
)
def test_all_six_missing_checkpoints_reconcile_idempotently(tmp_path, missing_phase):
    llm = BoundedReviewLLM(
        [
            _review(_issue()),
            _review(
                _issue(status="CLOSED"), verdict="ACCEPT", closed=("PRI-control",)
            ),
        ]
    )
    repo, engine, run = _ready_engine(tmp_path, llm)
    original_append = engine._append_plan_review_phase
    tripped = {"value": False}

    def crash_before_checkpoint(*args, **kwargs):
        phase = args[3]
        if phase == missing_phase and not tripped["value"]:
            tripped["value"] = True
            raise RuntimeError(f"checkpoint={missing_phase}")
        return original_append(*args, **kwargs)

    engine._append_plan_review_phase = crash_before_checkpoint
    with pytest.raises(RuntimeError, match=f"checkpoint={missing_phase}"):
        engine.run_step(run.id, "research_plan")
    engine._append_plan_review_phase = original_append

    completed = engine.run_step(run.id, "research_plan")
    phases_by_round = {}
    for item in completed.artifacts:
        if item.type == "plan_review_round_state":
            phases_by_round.setdefault(item.content["round_index"], []).append(
                item.content["phase"]
            )
    assert phases_by_round[1] == [
        "CANDIDATE_CREATED",
        "REVIEW_CREATED",
        "LEDGER_COMMITTED",
        "REVISION_REQUESTED",
        "REVISION_CREATED",
        "ROUND_COMPLETE",
    ]
    assert phases_by_round[2] == [
        "CANDIDATE_CREATED",
        "REVIEW_CREATED",
        "LEDGER_COMMITTED",
        "ROUND_COMPLETE",
    ]
    assert len([call for call in llm.calls if call[0] == "planning.review_plan"]) == 2
    assert len([call for call in llm.calls if call[0] == "planning.revise_from_review"]) == 1
    assert llm.revision_count == 1
    assert is_plan_governance_accepted(completed.artifacts)


@pytest.mark.parametrize(
    "crash_point",
    ["candidate", "review", "ledger", "revision_request", "revision_candidate"],
)
def test_round_checkpoint_resume_is_idempotent(tmp_path, crash_point):
    class CrashableLLM(BoundedReviewLLM):
        def __init__(self):
            super().__init__([
                _review(_issue()),
                _review(_issue(status="CLOSED"), verdict="ACCEPT"),
            ])
            self.fail_review_once = crash_point == "candidate"
            self.fail_revision_once = crash_point == "revision_request"

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "planning.review_plan" and self.fail_review_once:
                self.fail_review_once = False
                self.calls.append((task, deepcopy(inputs), instructions))
                raise RuntimeError("MODEL_REQUEST_FAILED:checkpoint=candidate")
            if task == "planning.revise_from_review" and self.fail_revision_once:
                self.fail_revision_once = False
                self.calls.append((task, deepcopy(inputs), instructions))
                raise RuntimeError("MODEL_REQUEST_FAILED:checkpoint=revision_request")
            return super().generate_json(task, inputs, schema_hint, instructions)

    llm = CrashableLLM()
    repo, engine, run = _ready_engine(tmp_path, llm)
    original_add = repo.add_artifact
    tripped = {"value": False}

    def crashing_add(*args, **kwargs):
        artifact_type = args[1]
        title = args[2]
        if crash_point == "review" and artifact_type == "plan_review_issue_ledger" and not tripped["value"]:
            tripped["value"] = True
            raise RuntimeError("checkpoint=review")
        if crash_point == "ledger" and artifact_type == "plan_review_revision_request" and not tripped["value"]:
            tripped["value"] = True
            raise RuntimeError("checkpoint=ledger")
        if crash_point == "revision_candidate" and artifact_type == "research_plan_candidate" and "Round 2" in title and not tripped["value"]:
            tripped["value"] = True
            artifact = original_add(*args, **kwargs)
            raise RuntimeError("checkpoint=revision_candidate")
        return original_add(*args, **kwargs)

    if crash_point in {"review", "ledger", "revision_candidate"}:
        repo.add_artifact = crashing_add
    with pytest.raises(RuntimeError):
        engine.run_step(run.id, "research_plan")
    repo.add_artifact = original_add
    completed = engine.run_step(run.id, "research_plan")
    assert any(item.type == "plan" for item in completed.artifacts)
    assert len([item for item in completed.artifacts if item.type == "research_plan_candidate"]) == 2
    assert len([item for item in completed.artifacts if item.type == "plan_review_issue_ledger"]) == 2
    assert len({(item.content["round_index"], item.content["plan_id"]) for item in completed.artifacts if item.type == "plan_review_issue_ledger"}) == 2
