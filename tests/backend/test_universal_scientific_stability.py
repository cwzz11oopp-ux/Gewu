from __future__ import annotations

import json
import pytest

from backend.app.providers.experiment import MockExperimentProvider
from backend.app.providers.literature import MockLiteratureProvider
from backend.app.providers.llm import MockLLMProvider
from backend.app.storage.repository import Repository
from backend.app.workflow.engine import WorkflowEngine
from backend.app.workflow.scientific_stability import (
    annotate_dataset_semantics,
    hard_constraint_issues,
    infer_research_profile,
    merge_issue_ledger,
    next_research_stage,
    protocol_state,
    readiness_state,
    scientific_claim,
    selected_hypothesis_digest,
)


def _review(verdict: str):
    issue = {
        "issue_id": "PRI-control",
        "blocker_class": "MISSING_EXECUTABLE_COMPARATOR",
        "severity": "BLOCKER",
        "title": "Missing justified control",
        "contract_fields": ["additional_sections"],
        "evidence": ["The current candidate resolves or still exhibits the control issue."],
        "reason": "The control must be executable.",
        "required_fix": "Supply one supported control.",
        "status": "CLOSED" if verdict == "ACCEPT" else "OPEN",
        "resolution": "The revised candidate supplies the control." if verdict == "ACCEPT" else "",
    }
    return {
        "verdict": verdict,
        "issues": [issue],
        "closed_issue_ids": ["PRI-control"] if verdict == "ACCEPT" else [],
        "required_changes": [] if verdict == "ACCEPT" else ["supply a supported control"],
        "suggested_fixes": [] if verdict == "ACCEPT" else [{"problem": "control", "recommended_fix": "state source", "alternative_fix": "verify", "reason": "provenance"}],
        "revised_plan_guidance": [] if verdict == "ACCEPT" else ["address the open ledger issue"],
        "experiment_feasibility": "FEASIBLE" if verdict == "ACCEPT" else "FEASIBLE_AFTER_REVISION",
    }


class RevisingLLM(MockLLMProvider):
    fallback = False
    mode = "fixture"

    def __init__(self, reviews):
        self.reviews = list(reviews)
        self.revision_calls = 0

    def generate_json(self, task, inputs, schema_hint, instructions=""):
        if task == "planning.review_plan":
            result = dict(self.reviews.pop(0))
            result["issues"] = [dict(item) for item in result.get("issues") or []]
            if result.get("verdict") == "ACCEPT" and not inputs.get("previous_issue_ledger"):
                result["issues"] = []
            for issue in result["issues"]:
                if issue.get("status") == "CLOSED":
                    issue["evidence_artifact_ids"] = [inputs["current_candidate_plan_id"]]
            return result
        if task == "planning.revise_from_review":
            self.revision_calls += 1
            return dict(
                inputs["current_candidate"],
                additional_sections={"revision": f"applied-{self.revision_calls}"},
                fix_map={"PRI-control": ["additional_sections"]},
            )
        return super().generate_json(task, inputs, schema_hint, instructions)


def _ready_engine(tmp_path, llm):
    repo = Repository(str(tmp_path))
    engine = WorkflowEngine(repo, llm, MockLiteratureProvider(), MockExperimentProvider())
    run = repo.create_run("Study a generic intervention", "stability fixture")
    for step in ("problem_understanding", "knowledge_integration", "hypothesis_generation", "evidence_reasoning"):
        run = engine.run_step(run.id, step)
    return repo, engine, engine.select_hypothesis(run.id, 0)


def test_universal_stability_test_matrix_domain_neutral_routes():
    matrix = [
        ("empirical", "measure an intervention", True, "empirical_data"),
        ("regression", "estimate a continuous target", True, "empirical_data"),
        ("forecast", "forecast a sequence", True, "hybrid"),
        ("literature", "literature review of a mechanism", False, "literature_synthesis"),
        ("math", "prove a theorem", False, "mathematical"),
    ]
    for _, problem, has_dataset, expected in matrix:
        profile = infer_research_profile(problem, dataset_present=has_dataset)
        assert expected in profile["profile_types"]
        if expected == "mathematical":
            assert profile["applicability"]["dataset"] is False
            assert profile["applicability"]["training"] is False
    assert all(metric not in json.dumps([infer_research_profile(item[1], dataset_present=item[2]) for item in matrix]).casefold()
               for metric in ("accuracy", "rmse", "f1", "pfa", "pd", "map"))


def test_hard_constraints_need_real_provenance_and_unknown_is_stable():
    unsupported = scientific_claim(0.1, kind="success_threshold")
    assert unsupported["status"] == "provisional"
    assert hard_constraint_issues([unsupported])
    unknown = scientific_claim(None, kind="dataset_axis_semantics")
    repeated = scientific_claim(unknown["value"], kind=unknown["kind"], status=unknown["status"])
    assert repeated["status"] == "unknown"


def test_dataset_unknown_routes_to_verification_without_guessing():
    dataset = annotate_dataset_semantics({"contract_id": "D1", "files": [], "schemas": []})
    profile = infer_research_profile("generic measurement", dataset_present=True)
    protocol = protocol_state(objective="reduce an observed error", profile=profile, literature={}, dataset=dataset, code={}, stage="VERIFY")
    readiness = readiness_state(assessment={}, dataset=dataset, protocol=protocol, profile=profile)
    assert readiness["state"] == "needs_verification"
    assert readiness["next_route"] == "dataset_verification"
    assert next_research_stage(readiness, profile) == "VERIFY"


def test_evidence_insufficient_cannot_enter_main_and_is_not_system_failure():
    readiness = readiness_state(
        assessment={"status": "EVIDENCE_INSUFFICIENT", "mechanism_gate": "FAIL", "recommendation": "REVISE"},
        dataset={}, protocol={}, profile={"applicability": {"experiment": True}},
    )
    assert readiness["state"] == "needs_evidence"
    assert readiness["next_route"] == "targeted_literature"
    assert next_research_stage(readiness, {"applicability": {"experiment": True}}) != "MAIN"


def test_review_ledger_converges_and_qualified_new_issue_rule():
    policy = {
        "blocker_classes": ["MISSING_EXECUTABLE_COMPARATOR"],
        "reopen_rules": {"allowed_bases": ["regression"]},
        "new_blocker_rules": {
            "after_initial_round_allowed_bases": ["regression", "new_evidence"]
        },
    }
    first = merge_issue_ledger(
        [], _review("REVISE"), round_index=1, frozen_policy=policy
    )
    second = merge_issue_ledger(
        first,
        {**_review("REVISE"), "issues": [{"title": "new concern"}]},
        round_index=2,
        frozen_policy=policy,
    )
    assert next(item for item in second if item["issue_id"] == "PRI-control")["status"] == "OPEN"
    assert next(item for item in second if item["title"] == "new concern")["validated_blocker"] is False


def test_selected_hypothesis_digest_excludes_unselected_large_assessments():
    assessments = [{"candidate_index": index, "candidate_id": f"H{index}", "reasoning": "x" * 60_000} for index in range(4)]
    selection = {"selected": [{"candidate_id": "H2", "claim": "selected"}], "selected_indexes": [2]}
    digest, telemetry = selected_hypothesis_digest(selection, {"candidate_assessments": assessments})
    text = json.dumps(digest)
    assert "H0" not in text and "H1" not in text and "H3" not in text
    assert telemetry["budget_status"] in {"within_budget", "over_budget"}


def test_review_exhaustion_is_recoverable_and_preserves_append_only_lineage(tmp_path):
    repo, engine, run = _ready_engine(tmp_path, RevisingLLM([_review("REVISE"), _review("REVISE"), _review("REVISE")]))
    result = engine.run_step(run.id, "research_plan")
    assert result.status == "NEEDS_PLAN_REVISION"
    assert result.status != "FAILED_SYSTEM"
    plan_step = next(item for item in result.steps if item.id == "research_plan")
    assert plan_step.status == "interrupted"
    assert plan_step.error == {
        "code": "PLAN_REVISION_REQUIRED",
        "message": "DeepSeek did not produce an acceptable plan within the bounded revision limit.",
        "recoverable": True,
        "user_action_required": True,
    }
    candidates = [item for item in result.artifacts if item.type == "research_plan_candidate"]
    reviews = [item for item in result.artifacts if item.type == "plan_review"]
    assert len(candidates) == 3 and len(reviews) == 3
    assert all(item.content["plan_id"] == item.id for item in candidates)
    assert all(item.content["review_id"] == item.id for item in reviews)
    assert any(item.type == "plan_revision_required" for item in result.artifacts)


def test_provider_review_failure_is_recoverable_and_checkpoint_is_preserved(tmp_path):
    class BrokenReviewLLM(RevisingLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "planning.review_plan":
                raise RuntimeError("provider unavailable")
            return super().generate_json(task, inputs, schema_hint, instructions)
    repo, engine, run = _ready_engine(tmp_path, BrokenReviewLLM([]))
    with pytest.raises(RuntimeError, match="provider unavailable"):
        engine.run_step(run.id, "research_plan")
    result = repo.get_run(run.id)
    assert result.status == "created"
    assert next(step for step in result.steps if step.id == "research_plan").status == "interrupted"
    assert any(item.type == "research_plan_candidate" for item in result.artifacts)
    assert any(item.type == "failure_record" for item in result.artifacts)


def test_plan_review_resume_reuses_the_existing_candidate_before_acceptance(tmp_path):
    class FailThenAcceptLLM(RevisingLLM):
        def __init__(self):
            super().__init__([])
            self.build_plan_calls = 0
            self.review_calls = 0

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "planning.build_plan":
                self.build_plan_calls += 1
            if task == "planning.review_plan":
                self.review_calls += 1
                if self.review_calls == 1:
                    raise RuntimeError("MODEL_REQUEST_FAILED:provider=deepseek")
                return {**_review("ACCEPT"), "issues": []}
            return super().generate_json(task, inputs, schema_hint, instructions)

    llm = FailThenAcceptLLM()
    repo, engine, run = _ready_engine(tmp_path, llm)
    with pytest.raises(RuntimeError, match="provider=deepseek"):
        engine.run_step(run.id, "research_plan")

    interrupted = repo.get_run(run.id)
    candidates = [item for item in interrupted.artifacts if item.type == "research_plan_candidate"]
    assert len(candidates) == 1
    candidate_id = candidates[0].id
    assert llm.build_plan_calls == 1
    assert not any(item.type in {"plan", "baseline_profile", "fair_experiment_contract"}
                   for item in interrupted.artifacts)

    resumed = engine.run_step(run.id, "research_plan")
    resumed_candidates = [item for item in resumed.artifacts if item.type == "research_plan_candidate"]
    assert [item.id for item in resumed_candidates] == [candidate_id]
    assert llm.build_plan_calls == 1
    assert any(item.type == "plan" for item in resumed.artifacts)
    assert any(item.type == "baseline_profile" for item in resumed.artifacts)
    assert any(item.type == "fair_experiment_contract" for item in resumed.artifacts)
