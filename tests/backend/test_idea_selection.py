import pytest

from backend.app.agents.idea_selection import IdeaSelectionAgent
from backend.app.workflow.idea_selection import normalize_idea_review, select_top_evaluation


def item(index, scores, decision="REVISE"):
    return {
        "candidate_index": index,
        "idea_card": {"claim": f"c{index}"},
        "evidence_ledger": [],
        "closest_prior_work": [],
        "gates": {"testability": "PASS"},
        "scores": scores,
        "mde": {},
        "risks": [],
        "decision": decision,
        "confidence": "medium",
        "unknowns": [],
    }


def test_server_weighted_selection_ignores_model_selected_index():
    weak = item(0, {"novelty": 5, "scientific_soundness": 1, "impact": 1, "testability": 1, "execution_feasibility": 1, "reproducibility_compliance": 1})
    strong = item(1, {"novelty": 3, "scientific_soundness": 5, "impact": 5, "testability": 5, "execution_feasibility": 5, "reproducibility_compliance": 5})
    candidates = [{"claim": "c0"}, {"claim": "c1"}]
    review = normalize_idea_review({"selected_index": 0, "evaluations": [weak, strong]}, candidates)

    assert "selected_index" not in review
    assert select_top_evaluation(review["evaluations"])["selected_index"] == 1


def test_server_breaks_score_ties_by_lowest_candidate_index():
    scores = {"novelty": 4, "scientific_soundness": 4, "impact": 4, "testability": 4, "execution_feasibility": 4, "reproducibility_compliance": 4}
    review = normalize_idea_review(
        {"evaluations": [item(1, scores), item(0, scores)]},
        [{"claim": "c0"}, {"claim": "c1"}],
    )

    assert select_top_evaluation(review["evaluations"])["selected_index"] == 0


def test_server_never_selects_a_candidate_that_failed_the_decision_gate():
    scores = {"novelty": 5, "scientific_soundness": 5, "impact": 5, "testability": 5, "execution_feasibility": 5, "reproducibility_compliance": 5}
    weak_scores = {key: 1 for key in scores}

    selected = select_top_evaluation([
        item(0, scores, "EVIDENCE_INSUFFICIENT"),
        item(1, weak_scores, "GO"),
    ])

    assert selected["selected_index"] == 1


def test_contract_rejects_legacy_targeted_retrieval_decision_literal():
    scores = {
        "novelty": 3,
        "scientific_soundness": 3,
        "impact": 3,
        "testability": 5,
        "execution_feasibility": 4,
        "reproducibility_compliance": 4,
    }

    with pytest.raises(ValueError, match="IDEA_SELECTION_OUTPUT_INVALID: decision is invalid"):
        normalize_idea_review(
            {"evaluations": [item(0, scores, "TARGETED_RETRIEVAL")]},
            [{"claim": "c0"}],
        )


def test_server_refuses_to_force_a_winner_when_all_candidates_fail_the_gate():
    scores = {"novelty": 5, "scientific_soundness": 5, "impact": 5, "testability": 5, "execution_feasibility": 5, "reproducibility_compliance": 5}

    with pytest.raises(ValueError, match="NO_VIABLE_HYPOTHESIS"):
        select_top_evaluation([
            item(0, scores, "STOP"),
            item(1, scores, "EVIDENCE_INSUFFICIENT"),
        ])


def test_contract_rejects_missing_candidate_review():
    scores = {"novelty": 1, "scientific_soundness": 1, "impact": 1, "testability": 1, "execution_feasibility": 1, "reproducibility_compliance": 1}
    with pytest.raises(ValueError, match="IDEA_SELECTION_OUTPUT_INVALID"):
        normalize_idea_review(
            {"evaluations": [item(0, scores)]},
            [{"claim": "c0"}, {"claim": "c1"}],
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_ledger", {}),
        ("closest_prior_work", {}),
        ("gates", []),
        ("mde", []),
        ("risks", {}),
        ("unknowns", {}),
    ],
)
def test_contract_rejects_invalid_audit_field_shapes(field, value):
    scores = {"novelty": 1, "scientific_soundness": 1, "impact": 1, "testability": 1, "execution_feasibility": 1, "reproducibility_compliance": 1}
    review = item(0, scores)
    review[field] = value

    with pytest.raises(ValueError, match="IDEA_SELECTION_OUTPUT_INVALID"):
        normalize_idea_review({"evaluations": [review]}, [{"claim": "c0"}])


@pytest.mark.parametrize(
    "scores,decision",
    [
        ({"novelty": 1, "scientific_soundness": 1, "impact": 1, "testability": 1, "execution_feasibility": 1}, "REVISE"),
        ({"novelty": 1, "scientific_soundness": 1, "impact": 1, "testability": 1, "execution_feasibility": 1, "reproducibility_compliance": 5.1}, "REVISE"),
        ({"novelty": 1, "scientific_soundness": 1, "impact": 1, "testability": 1, "execution_feasibility": 1, "reproducibility_compliance": 1}, "APPROVE"),
    ],
)
def test_contract_rejects_invalid_scores_and_decisions(scores, decision):
    with pytest.raises(ValueError, match="IDEA_SELECTION_OUTPUT_INVALID"):
        normalize_idea_review(
            {"evaluations": [item(0, scores, decision)]},
            [{"claim": "c0"}],
        )


def test_contract_rejects_unexpected_score_key():
    scores = {
        "novelty": 1,
        "scientific_soundness": 1,
        "impact": 1,
        "testability": 1,
        "execution_feasibility": 1,
        "reproducibility_compliance": 1,
        "model_total": 5,
    }

    with pytest.raises(ValueError, match="IDEA_SELECTION_OUTPUT_INVALID"):
        normalize_idea_review(
            {"evaluations": [item(0, scores)]},
            [{"claim": "c0"}],
        )


def test_contract_replaces_invalid_idea_card_with_matching_normalized_candidate():
    scores = {"novelty": 1, "scientific_soundness": 1, "impact": 1, "testability": 1, "execution_feasibility": 1, "reproducibility_compliance": 1}
    evaluation = item(0, scores)
    evaluation["idea_card"] = "provider returned the wrong shape"
    candidate = {
        "claim": "candidate claim",
        "candidate_id": "CAND-001",
        "verifiability": "fixed-seed comparison",
        "novelty_basis": ["verified reference"],
        "risks": [],
        "rank": 1,
    }

    review = normalize_idea_review({"evaluations": [evaluation]}, [candidate])

    assert review["evaluations"][0]["idea_card"] == {
        "claim": "candidate claim",
        "candidate_id": "CAND-001",
        "verifiability": "fixed-seed comparison",
        "novelty_basis": ["verified reference"],
        "risks": [],
    }


def test_contract_still_rejects_invalid_non_idea_card_audit_shapes():
    scores = {"novelty": 1, "scientific_soundness": 1, "impact": 1, "testability": 1, "execution_feasibility": 1, "reproducibility_compliance": 1}
    evaluation = item(0, scores)
    evaluation["evidence_ledger"] = {}

    with pytest.raises(ValueError, match="IDEA_SELECTION_OUTPUT_INVALID"):
        normalize_idea_review({"evaluations": [evaluation]}, [{"claim": "c0"}])


class TaskRecorder:
    mode = "qwen"
    fallback = False

    def __init__(self):
        self.calls = []

    def generate_json(self, task, inputs, schema_hint, instructions=""):
        self.calls.append((task, inputs, schema_hint, instructions))
        return {"evaluations": []}


def test_agent_requests_idea_evaluations_from_llm_provider():
    llm = TaskRecorder()
    result = IdeaSelectionAgent(llm).review(
        {"problem_statement": "p"},
        "fixed compute budget",
        [{"id": "E1"}],
        [{"claim": "candidate"}],
        instructions="Follow the idea-selection skill.",
    )

    assert result == {"evaluations": []}
    task, inputs, schema, instructions = llm.calls[0]
    assert task == "idea_selection.review"
    assert inputs == {
        "problem": {"problem_statement": "p"},
        "constraints": "fixed compute budget",
            "evidence": [{"id": "E1"}],
            "candidates": [{"claim": "candidate"}],
            "evidence_audit": {},
        }
    evaluation_schema = schema["evaluations"][0]
    assert schema["evaluation_count"] == "exactly one evaluation per candidate"
    assert evaluation_schema["evidence_ledger"] == ["object"]
    assert evaluation_schema["scores"] == {
        "novelty": "number 0..5",
        "scientific_soundness": "number 0..5",
        "impact": "number 0..5",
        "testability": "number 0..5",
        "execution_feasibility": "number 0..5",
        "reproducibility_compliance": "number 0..5",
    }
    assert evaluation_schema["decision"] == "GO|REVISE|PIVOT|STOP|EVIDENCE_INSUFFICIENT"
    assert instructions == "Follow the idea-selection skill."
