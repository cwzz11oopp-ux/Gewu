from backend.app.agents.hypothesis import HypothesisAgent
from backend.app.workflow.hypothesis_contract import (
    hypothesis_candidate_issues,
    normalize_hypothesis_content,
)


class TaskRecorder:
    mode = "qwen"
    fallback = False

    def __init__(self):
        self.schema_hints = []

    def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
        self.schema_hints.append(schema_hint)
        return {"candidates": []}


def test_hypothesis_agent_requests_plain_simplified_chinese_candidates_without_scores():
    llm = TaskRecorder()

    HypothesisAgent(llm).generate({"problem_statement": "训练紧凑 CNN"}, [])

    schema = llm.schema_hints[0]
    candidate_schema = schema["candidates"][0]
    schema_text = str(schema)
    assert schema["candidate_count_range"] == "必须生成 3 到 5 个技术路线不同的候选假设"
    assert "简体中文" in schema_text
    assert "不要评分" in schema_text
    assert "不要排序" in schema_text
    assert "score" not in candidate_schema
    assert "rank" not in candidate_schema
    assert "recommendation" not in candidate_schema


def test_normalize_hypothesis_accepts_qwen_hypothesis_field_as_claim():
    raw = {
        "candidates": [
            {
                "candidate_id": "H1",
                "hypothesis": "Adding dropout improves a compact CNN under a fixed-seed ablation.",
                "mechanism_and_causal_chain": "Dropout reduces overfitting.",
            }
        ],
        "provider_mode": "qwen",
        "fallback_used": False,
    }

    normalized = normalize_hypothesis_content(raw)

    assert normalized["candidates"][0]["claim"] == (
        "Adding dropout improves a compact CNN under a fixed-seed ablation."
    )
    assert "hypothesis" not in normalized["candidates"][0]


def test_single_candidate_is_not_a_valid_hypothesis_set():
    issues = hypothesis_candidate_issues({
        "candidates": [{"claim": "Only one candidate"}],
    })

    assert issues
    assert "3 to 5" in issues[0]
