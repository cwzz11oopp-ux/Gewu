from __future__ import annotations

import json

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
    return {
        "verdict": verdict,
        "issues": [] if verdict == "ACCEPT" else [{"description": "missing justified control", "reason": "control absent", "affected_plan_section": "control"}],
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

    def generate_json(self, task, inputs, schema_hint, instructions=""):
        if task == "planning.review_plan":
            return self.reviews.pop(0)
        if task == "planning.revise_from_review":
            return dict(inputs["current_plan"], additional_sections={"revision": "applied"})
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
    first = merge_issue_ledger([], _review("REVISE"), round_index=1)
    second = merge_issue_ledger(first, {**_review("REVISE"), "issues": [{"description": "new concern"}]}, round_index=2)
    assert any(item["status"] == "resolved" for item in second)
    assert next(item for item in second if item["description"] == "new concern")["blocking"] is False


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
    result = engine.run_step(run.id, "research_plan")
    assert result.status == "RECOVERABLE_PROVIDER_ERROR"
    assert any(item.type == "research_plan_candidate" for item in result.artifacts)
    assert any(item.type == "failure_record" for item in result.artifacts)
