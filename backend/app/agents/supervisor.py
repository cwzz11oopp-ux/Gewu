from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.app.agents.reviewer import ReviewRubric, ReviewerAgent, ValidationDecision
from backend.app.workflow.skills import SkillRegistry


_AGENT_NAMES = {
    "research": "Research Agent",
    "idea": "Idea Agent",
    "critic": "Critic Agent",
    "planning": "Planning Agent",
    "experiment": "Experiment Agent",
    "diagnostic": "Experiment Diagnostic Agent",
    "writer": "Writer Agent",
}

_REQUIRED_FIELDS = {
    "problem_understanding": ("problem_statement", "constraints", "knowledge_gaps", "literature_queries"),
    "knowledge_integration": ("references",),
    "hypothesis_generation": ("candidates",),
    "evidence_reasoning": ("candidate_assessments", "selection_required"),
    "research_plan": ("objective", "procedure"),
    "experiment_task": ("experiment_id", "manifest"),
    "experiment_run_analysis": (
        "experiment_id",
        "result_id",
        "metrics",
        "analysis",
        "audit",
    ),
    "experiment_diagnosis": (
        "category",
        "error_code",
        "root_cause",
        "repair_action",
        "user_message",
    ),
    "feedback_revision": (
        "verdict",
        "supported_claims",
        "unsupported_claims",
        "revisions",
        "next_action",
        "evidence_links",
    ),
    "report_export": (
        "Problem Statement",
        "Rationale",
        "Technical Details",
        "Datasets",
        "Source",
        "Target",
        "Paper Title",
        "Paper Abstract",
        "Methods",
        "Experiments",
        "Results",
        "References",
    ),
}

_SEMANTIC_RUBRICS = {
    "evidence_reasoning": ReviewRubric(
        "2", ("evidence traceability", "novelty risk", "candidate reasoning completeness")
    ),
    "research_plan": ReviewRubric(
        "1", ("falsifiability", "procedure completeness", "resource feasibility")
    ),
    "feedback_revision": ReviewRubric(
        "2",
        (
            "metric-to-claim traceability",
            "claim restraint",
            "honest partial or failed verdict acceptance",
            "next action",
        ),
    ),
    "report_export": ReviewRubric(
        "1", ("verified references", "audited results", "unsupported claim absence")
    ),
}


@dataclass(frozen=True)
class Delegation:
    step_id: str
    agent_id: str
    agent_name: str
    skill_ids: tuple[str, ...]
    tool_call: dict


class SupervisorAgent:
    name = "Supervisor Agent"

    def __init__(
        self,
        skill_registry: SkillRegistry,
        reviewer: ReviewerAgent | None = None,
    ) -> None:
        self.skill_registry = skill_registry
        self.reviewer = reviewer

    def delegate(self, step_id: str, state: dict | None = None) -> Delegation:
        assignment = self.skill_registry.assignment_for(step_id)
        skill_ids = assignment.skill_ids + self.skill_registry.conditional_skills_for(
            step_id, state or {}
        )
        if not skill_ids:
            raise ValueError(f"STATIC_SKILL_ROUTE_MISSING:{step_id}")
        agent_id = assignment.agent_id
        agent_name = _AGENT_NAMES[agent_id]
        tool_call = {
            "provider": "supervisor_agent",
            "method": "delegate",
            "routing_mode": "static",
            "step_id": step_id,
            "agent_id": agent_id,
            "agent": agent_name,
            "skills": list(skill_ids),
            "instruction_source": "skill_runtime",
        }
        return Delegation(
            step_id=step_id,
            agent_id=agent_id,
            agent_name=agent_name,
            skill_ids=skill_ids,
            tool_call=tool_call,
        )

    @staticmethod
    def require_agent(delegation: Delegation, expected_agent_id: str) -> None:
        if delegation.agent_id != expected_agent_id:
            raise ValueError(
                "SUPERVISOR_AGENT_MISMATCH:"
                f"{delegation.step_id}:expected={expected_agent_id}:assigned={delegation.agent_id}"
            )

    def validate(
        self,
        step_id: str,
        content: dict,
        artifact_path: Path | None = None,
        wiki_paths: tuple[Path, ...] = (),
        review_context: dict | None = None,
    ) -> ValidationDecision:
        if step_id not in _REQUIRED_FIELDS:
            raise ValueError(f"UNKNOWN_WORKFLOW_STEP:{step_id}")

        issues: list[str] = []
        for field in _REQUIRED_FIELDS[step_id]:
            if field not in content:
                if step_id == "knowledge_integration" and field == "references":
                    issues.append("references must be a list")
                else:
                    issues.append(f"{field} is required")
        if "references" in content and not isinstance(content["references"], list):
            issues.append("references must be a list")
        if step_id == "evidence_reasoning":
            assessments = content.get("candidate_assessments")
            if not isinstance(assessments, list) or not assessments:
                issues.append("candidate_assessments must be a non-empty list")
            elif any(
                not isinstance(item, dict)
                or not isinstance(item.get("candidate_index"), int)
                or not isinstance(item.get("evaluation"), dict)
                for item in assessments
            ):
                issues.append(
                    "each candidate assessment must contain candidate_index and evaluation"
                )
            if content.get("selection_required") is not True:
                issues.append("selection_required must be true")
        if (
            step_id == "feedback_revision"
            and (review_context or {}).get("output_language") == "zh-CN"
        ):
            issues.extend(_chinese_feedback_issues(content))
        if issues:
            return ValidationDecision(False, tuple(dict.fromkeys(issues)))

        # This stage already consists of one comprehensive model review over every
        # candidate. A second model-as-reviewer pass adds latency without adding a
        # decision, because the human explicitly owns the final selection.
        if step_id == "evidence_reasoning":
            return ValidationDecision(True)

        rubric = _SEMANTIC_RUBRICS.get(step_id)
        if self.reviewer is None or rubric is None:
            return ValidationDecision(True)
        if artifact_path is None:
            return ValidationDecision(False, ("semantic review artifact path is required",))
        return self.reviewer.review(
            step_id,
            artifact_path,
            wiki_paths,
            rubric,
            review_context=review_context,
        )

    @staticmethod
    def require_revision(
        step_id: str,
        attempt: int,
        issues: tuple[str, ...],
        diagnosis: bool = False,
    ) -> dict:
        limit = SupervisorAgent.revision_limit(step_id, diagnosis=diagnosis)
        if attempt > limit:
            detail = " | ".join(str(issue) for issue in issues)[:320]
            raise ValueError(
                f"SUPERVISOR_REVISION_LIMIT:{step_id}:{limit}:last_issues={detail}"
            )
        return {
            "step_id": step_id,
            "attempt": attempt,
            "limit": limit,
            "diagnosis": diagnosis,
            "issues": list(issues),
            "status": "revision_requested",
        }

    @staticmethod
    def revision_limit(step_id: str, *, diagnosis: bool = False) -> int:
        if step_id in {"evidence_reasoning", "experiment_task"}:
            return 5
        if diagnosis and step_id == "experiment_run_analysis":
            return 5
        return 3 if diagnosis else 2

    @staticmethod
    def commit_wiki_changes(change_set, wiki):
        return wiki.commit_changes(change_set, actor="supervisor")


def _chinese_feedback_issues(content: dict) -> list[str]:
    fields = (
        "feedback",
        "required_revision",
        "next_action",
        "supported_claims",
        "unsupported_claims",
        "revisions",
        "overclaim_risks",
    )
    issues = []
    for field in fields:
        if not _value_has_required_chinese(content.get(field)):
            issues.append(f"OUTPUT_LANGUAGE_INVALID:{field}:expected=zh-CN")
    analysis = content.get("result_analysis")
    if isinstance(analysis, dict):
        for field in (
            "measured_facts",
            "failed_criteria",
            "uncertainties",
            "methodological_issues",
            "causal_hypotheses",
            "knowledge_gaps",
        ):
            if not _value_has_required_chinese(analysis.get(field)):
                issues.append(
                    f"OUTPUT_LANGUAGE_INVALID:result_analysis.{field}:expected=zh-CN"
                )
    return issues


def _value_has_required_chinese(value) -> bool:
    if value in (None, "", []):
        return True
    if isinstance(value, list):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value)
    return any("\u4e00" <= char <= "\u9fff" for char in text)
