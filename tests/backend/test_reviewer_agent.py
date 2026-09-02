import json

import pytest

from backend.app.agents.reviewer import ReviewRubric, ReviewerAgent


class RecordingReviewerLLM:
    mode = "qwen"
    fallback = False

    def __init__(self, response=None):
        self.response = response or {"accepted": True, "issues": []}
        self.calls = []

    def generate_json(self, task, inputs, schema_hint, instructions=""):
        self.calls.append(
            {
                "task": task,
                "inputs": inputs,
                "schema_hint": schema_hint,
                "instructions": instructions,
            }
        )
        return self.response


def test_reviewer_uses_raw_artifact_and_wiki_in_fresh_qwen_request(tmp_path):
    artifact_path = tmp_path / "candidate.json"
    artifact_path.write_text(json.dumps({"objective": "measure robustness"}), encoding="utf-8")
    wiki_path = tmp_path / "query_pack.md"
    wiki_path.write_text("# Evidence\nverified source", encoding="utf-8")
    llm = RecordingReviewerLLM()
    reviewer = ReviewerAgent(llm)

    decision = reviewer.review(
        "research_plan",
        artifact_path,
        (wiki_path,),
        ReviewRubric(version="1", criteria=("falsifiable", "feasible")),
    )

    assert decision.accepted is True
    call = llm.calls[0]
    assert call["task"] == "reviewer.semantic"
    assert call["inputs"]["artifact"]["objective"] == "measure robustness"
    assert call["inputs"]["wiki_files"][0]["content"] == "# Evidence\nverified source"
    assert call["inputs"]["rubric"]["criteria"] == ["falsifiable", "feasible"]
    assert "supervisor_summary" not in call["inputs"]


def test_report_reviewer_receives_writer_evidence_even_without_wiki(tmp_path):
    evidence = {"deterministic_result_evidence": {"paired_t_test": {"p_value": .06378557524}},
                "verified_references": [{"paper_id": "PAPER-abc", "abstract": "Source passage"}]}
    artifact_path = tmp_path / "candidate.json"
    artifact_path.write_text(json.dumps({"Report Evidence": evidence}), encoding="utf-8")
    llm = RecordingReviewerLLM()
    ReviewerAgent(llm).review("report_export", artifact_path, (), ReviewRubric("1", ("evidence",)))
    call = llm.calls[0]
    assert call["inputs"]["artifact"]["Report Evidence"] == evidence
    assert "Do not declare evidence absent" in call["instructions"]
    assert "source passage must also support the claim" in call["instructions"]


@pytest.mark.parametrize(
    "step_id",
    ["evidence_reasoning", "research_plan", "feedback_revision", "report_export"],
)
def test_reviewer_returns_structured_semantic_rejection(tmp_path, step_id):
    artifact_path = tmp_path / "candidate.json"
    artifact_path.write_text("{}", encoding="utf-8")
    reviewer = ReviewerAgent(
        RecordingReviewerLLM({"accepted": False, "issues": ["claim is not supported"]})
    )

    decision = reviewer.review(
        step_id,
        artifact_path,
        (),
        ReviewRubric(version="1", criteria=("evidence",)),
    )

    assert decision.accepted is False
    assert decision.issues == ("claim is not supported",)


def test_feedback_reviewer_acceptance_policy_does_not_require_future_experiments(tmp_path):
    artifact_path = tmp_path / "candidate.json"
    artifact_path.write_text(
        json.dumps({"verdict": "partial", "next_action": "run an ablation"}),
        encoding="utf-8",
    )
    llm = RecordingReviewerLLM()

    ReviewerAgent(llm).review(
        "feedback_revision",
        artifact_path,
        (),
        ReviewRubric(version="2", criteria=("claim restraint", "next action")),
        review_context={"experiment_result": {"metrics": {"accuracy": 0.8}}},
    )

    call = llm.calls[0]
    assert call["inputs"]["review_context"]["experiment_result"]["metrics"] == {
        "accuracy": 0.8
    }
    assert "Do not reject it merely because" in call["instructions"]


def test_evidence_reviewer_accepts_honest_negative_scientific_outcomes(tmp_path):
    artifact_path = tmp_path / "reasoning.json"
    artifact_path.write_text(
        json.dumps({"active_hypothesis": {"claim": "narrowed"}}),
        encoding="utf-8",
    )
    llm = RecordingReviewerLLM()

    ReviewerAgent(llm).review(
        "evidence_reasoning",
        artifact_path,
        (),
        ReviewRubric(version="1", criteria=("evidence traceability",)),
    )

    instructions = llm.calls[0]["instructions"]
    assert "evidence-insufficient scientific hypothesis is a valid outcome" in instructions
    assert "never put strengths, confirmations, or praise in issues" in instructions
