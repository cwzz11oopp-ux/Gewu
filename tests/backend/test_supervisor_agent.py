import pytest

from backend.app.agents.supervisor import SupervisorAgent
from backend.app.workflow.skills import SkillRegistry


EXPECTED_ASSIGNMENTS = {
    "problem_understanding": ("research", ("problem-framing",)),
    "knowledge_integration": ("research", ("research-lit", "research-wiki")),
    "hypothesis_generation": ("idea", ("idea-creator", "hypothesis-evidence")),
    "evidence_reasoning": (
        "critic", ("evidence-recovery", "idea-selection", "novelty-check", "research-review")
    ),
    "research_plan": (
        "planning",
        ("research-refine", "hypothesis-experiment-gate", "experiment-plan"),
    ),
    "experiment_task": ("experiment", ("experiment-implementation",)),
    "experiment_run_analysis": (
        "experiment",
        ("run-experiment", "analyze-results", "experiment-audit"),
    ),
    "experiment_diagnosis": ("diagnostic", ("experiment-diagnosis",)),
    "feedback_revision": (
        "critic",
        ("experiment-iteration", "result-to-claim"),
    ),
    "report_export": ("writer", ("competition-report", "report-quality-audit")),
}


def _supervisor() -> SupervisorAgent:
    return SupervisorAgent(SkillRegistry())


@pytest.mark.parametrize("step_id", EXPECTED_ASSIGNMENTS)
def test_supervisor_statically_delegates_each_workflow_step(step_id):
    expected_agent, expected_skills = EXPECTED_ASSIGNMENTS[step_id]

    delegation = _supervisor().delegate(step_id)

    assert delegation.agent_id == expected_agent
    assert delegation.skill_ids == expected_skills
    assert delegation.tool_call["provider"] == "supervisor_agent"
    assert delegation.tool_call["method"] == "delegate"
    assert delegation.tool_call["routing_mode"] == "static"
    assert delegation.tool_call["agent_id"] == expected_agent
    assert delegation.tool_call["skills"] == list(expected_skills)
    assert delegation.tool_call["instruction_source"] == "skill_runtime"


def test_supervisor_rejects_unknown_steps():
    with pytest.raises(ValueError, match="UNKNOWN_WORKFLOW_STEP:not-a-step"):
        _supervisor().delegate("not-a-step")


def test_supervisor_routes_without_a_skill_loader_or_prompt_bundle():
    delegation = SupervisorAgent(SkillRegistry()).delegate("experiment_task")

    assert delegation.skill_ids == ("experiment-implementation",)
    assert not hasattr(delegation, "instructions")
    assert delegation.tool_call["instruction_source"] == "skill_runtime"


def test_removed_idea_selection_step_is_not_routable():
    with pytest.raises(ValueError, match="UNKNOWN_WORKFLOW_STEP:idea_selection"):
        _supervisor().delegate("idea_selection")


def test_supervisor_rejects_an_engine_branch_for_the_wrong_agent():
    delegation = _supervisor().delegate("research_plan")

    with pytest.raises(ValueError, match="SUPERVISOR_AGENT_MISMATCH"):
        _supervisor().require_agent(delegation, "experiment")


def test_registry_selects_only_declared_conditional_skills():
    registry = SkillRegistry()

    assert registry.conditional_skills_for(
        "feedback_revision", {"experiment_verdict": "partial"}
    ) == ("ablation-planner",)
    assert registry.conditional_skills_for(
        "experiment_run_analysis", {"monitoring_enabled": True}
    ) == ("monitor-experiment",)
    assert registry.conditional_skills_for(
        "feedback_revision", {"experiment_verdict": "supported"}
    ) == ()


def test_composite_and_conditional_skills_are_not_unconditional():
    registry = SkillRegistry()

    unconditional = {
        skill_id
        for step_id in EXPECTED_ASSIGNMENTS
        for skill_id in registry.skills_for(step_id)
    }

    assert "experiment-bridge" not in unconditional
    assert "paper-writing" not in unconditional
    assert "ablation-planner" not in unconditional
    assert "monitor-experiment" not in unconditional


def test_supervisor_rejects_evidence_without_reference_list():
    decision = _supervisor().validate("knowledge_integration", {"summary": "missing"})

    assert decision.accepted is False
    assert decision.issues == ("references must be a list",)


def test_supervisor_accepts_deterministically_valid_evidence():
    decision = _supervisor().validate("knowledge_integration", {"references": []})

    assert decision.accepted is True
    assert decision.issues == ()


def test_supervisor_requires_chinese_feedback_for_chinese_research():
    content = {
        "verdict": "failed",
        "supported_claims": [],
        "unsupported_claims": ["The threshold was not met."],
        "revisions": [],
        "next_action": "Document the negative result.",
        "evidence_links": [],
        "feedback": "The experiment did not support the hypothesis.",
        "required_revision": "None.",
        "overclaim_risks": ["Do not claim an improvement."],
        "result_analysis": {
            "measured_facts": ["Accuracy decreased."],
            "failed_criteria": ["Accuracy target failed."],
            "improved_metrics": [],
            "degraded_metrics": ["accuracy"],
            "uncertainties": ["Seed variance is missing."],
            "methodological_issues": [],
            "causal_hypotheses": ["The baseline is already strong."],
            "knowledge_gaps": [],
        },
    }

    decision = _supervisor().validate(
        "feedback_revision",
        content,
        review_context={"output_language": "zh-CN"},
    )

    assert decision.accepted is False
    assert "OUTPUT_LANGUAGE_INVALID:feedback:expected=zh-CN" in decision.issues
    assert "OUTPUT_LANGUAGE_INVALID:next_action:expected=zh-CN" in decision.issues


def test_supervisor_accepts_chinese_feedback_while_preserving_machine_enums():
    content = {
        "verdict": "failed",
        "supported_claims": [],
        "unsupported_claims": ["测试准确率未达到预设阈值。"],
        "revisions": [],
        "next_action": "记录负结果并停止当前假设。",
        "evidence_links": [],
        "feedback": "实验结果未支持当前假设。",
        "required_revision": "无需继续修改当前假设。",
        "overclaim_risks": ["不得宣称性能获得提升。"],
        "result_analysis": {
            "measured_facts": ["测试准确率下降。"],
            "failed_criteria": ["准确率提升目标未达到。"],
            "improved_metrics": [],
            "degraded_metrics": ["accuracy"],
            "uncertainties": ["尚未报告种子方差。"],
            "methodological_issues": [],
            "causal_hypotheses": ["基线性能可能已经较强。"],
            "knowledge_gaps": [],
        },
    }

    decision = _supervisor().validate(
        "feedback_revision",
        content,
        review_context={"output_language": "zh-CN"},
    )

    assert decision.accepted is True
    assert content["verdict"] == "failed"


def test_supervisor_stops_content_revision_after_two_attempts():
    with pytest.raises(ValueError, match="SUPERVISOR_REVISION_LIMIT"):
        _supervisor().require_revision("knowledge_integration", 3, ("bad evidence",))


def test_supervisor_stops_experiment_diagnosis_after_five_attempts():
    with pytest.raises(ValueError, match="SUPERVISOR_REVISION_LIMIT"):
        _supervisor().require_revision(
            "experiment_run_analysis", 6, ("runtime failed",), diagnosis=True
        )
