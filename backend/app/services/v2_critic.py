from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from backend.app.models.gateway import ModelGateway
from backend.app.research.actions import ResearchOperator
from backend.app.research.budget import BudgetCost
from backend.app.research.experiment import ExperimentRecord
from backend.app.research.frontier import BranchStatus, ResearchBranch
from backend.app.state.research import ResearchState
from backend.app.experiment.parameter_sweep import ParameterResponseEvidence


class ScientificCritique(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_claims: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    possible_mechanisms: list[str] = Field(min_length=1)
    alternative_explanations: list[str] = Field(default_factory=list)
    methodological_issues: list[str] = Field(default_factory=list)
    open_information_gaps: list[str] = Field(default_factory=list)
    recommended_actions: list[ResearchOperator] = Field(min_length=1)


class ParameterSweepCritique(ScientificCritique):
    calibration_supported: bool
    stable_improvement_interval: list[float] = Field(max_length=2)
    threshold_0_2_uniquely_supported: bool
    mechanism_conclusion_boundary: str = Field(min_length=1)
    replication_still_needed: bool
    robustness_still_needed: bool


class ScientificCritic:
    """Produces structured advice; it never selects the session's action."""

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def review(
        self,
        state: ResearchState,
        branch: ResearchBranch,
        record: ExperimentRecord,
    ) -> ScientificCritique:
        return self.gateway.invoke_structured(
            "v2.critic.review_experiment",
            [
                {
                    "role": "user",
                    "content": (
                        "Critique what this experiment does and does not establish. "
                        "Recommend information-seeking follow-ups, without making the final decision. "
                        "When evidence is supportive but the proposed mechanism remains unisolated, "
                        "prefer a minimal RUN_ABLATION over routine replication. "
                        "Keep JSON keys, schema identifiers, enum values, code, file paths, Git commits, "
                        "model IDs, and metric keys unchanged. Write every human-readable string value "
                        "in Simplified Chinese by default."
                    ),
                }
            ],
            ScientificCritique,
            context={
                "problem": state.problem.model_dump(mode="json"),
                "branch": branch.model_dump(mode="json"),
                "baseline": (
                    state.baseline.model_dump(mode="json") if state.baseline else None
                ),
                "experiment": record.model_dump(mode="json"),
                "evidence": [
                    item.model_dump(mode="json")
                    for item in state.evidence
                    if item.experiment_id == record.experiment_id
                ],
                "protocol_compatibility": record.comparison.model_dump(mode="json"),
                "belief": (
                    state.beliefs.get(branch.id).model_dump(mode="json")
                    if state.beliefs.get(branch.id)
                    else None
                ),
                "remaining_budget": state.budget.model_dump(mode="json"),
            },
        )

    def review_parameter_sweep(
        self,
        state: ResearchState,
        branch: ResearchBranch,
        sweep: ParameterResponseEvidence,
    ) -> ParameterSweepCritique:
        return self.gateway.invoke_structured(
            "v2.critic.review_parameter_sweep",
            [
                {
                    "role": "user",
                    "content": (
                        "Critique the complete predeclared parameter sweep. Determine whether "
                        "calibration is supported on the locked fixture, whether a stable improving "
                        "interval exists, whether threshold 0.2 is uniquely supported, and the exact "
                        "boundary of the mechanism claim. State whether replication or robustness is "
                        "still needed. Recommend actions only; the Controller makes the final choice. "
                        "Keep JSON keys, schema identifiers, enum values, code, file paths, Git commits, "
                        "model IDs, and metric keys unchanged. Write every human-readable string value "
                        "in Simplified Chinese by default."
                    ),
                }
            ],
            ParameterSweepCritique,
            context={
                "problem": state.problem.model_dump(mode="json"),
                "branch": branch.model_dump(mode="json"),
                "baseline": state.baseline.model_dump(mode="json") if state.baseline else None,
                "parameter_response": sweep.model_dump(mode="json", exclude={"records"}),
                "experiment_records": [
                    record.model_dump(mode="json") for record in sweep.records
                ],
                "evidence": [
                    item.model_dump(mode="json")
                    for item in state.evidence
                    if item.experiment_id
                    in {record.experiment_id for record in sweep.records}
                ],
                "remaining_budget": state.budget.model_dump(mode="json"),
            },
        )


class CriticDecisionService:
    """Validates critic advice and exposes it to the policy-based controller."""

    ALLOWED: dict[BranchStatus, set[ResearchOperator]] = {
        BranchStatus.PROMISING: {
            ResearchOperator.RUN_ABLATION,
            ResearchOperator.RUN_REPLICATION,
            ResearchOperator.REFINE_HYPOTHESIS,
            ResearchOperator.CHALLENGE_HYPOTHESIS,
            ResearchOperator.RUN_ROBUSTNESS,
        },
        BranchStatus.INCONCLUSIVE: {
            ResearchOperator.INVESTIGATE_FAILURE,
            ResearchOperator.RUN_ABLATION,
            ResearchOperator.RUN_REPLICATION,
            ResearchOperator.REFINE_HYPOTHESIS,
        },
        BranchStatus.VALIDATED: {ResearchOperator.FINAL_VALIDATION},
        BranchStatus.REJECTED: {ResearchOperator.STOP_BRANCH},
    }
    NON_REPEATABLE_FOLLOW_UPS = {ResearchOperator.RUN_ABLATION}

    def apply(
        self,
        state: ResearchState,
        branch_id: str,
        critique: ScientificCritique | ParameterSweepCritique,
        *,
        consume_model_call: bool = True,
    ) -> ResearchState:
        branch = state.frontier.get(branch_id)
        allowed = self.ALLOWED.get(branch.status, set())
        completed = {
            action.operator
            for action in state.action_history
            if action.branch_id == branch_id
        }

        def eligible(action: ResearchOperator) -> bool:
            return action in allowed and not (
                action in self.NON_REPEATABLE_FOLLOW_UPS and action in completed
            )

        recommendations = list(
            dict.fromkeys(
                action
                for action in critique.recommended_actions
                if eligible(action)
            )
        )
        if not recommendations:
            recommendations = [
                action for action in branch.next_actions if eligible(action)
            ]
        if not recommendations:
            recommendations = [
                action
                for action in (
                    ResearchOperator.RUN_ROBUSTNESS,
                    ResearchOperator.RUN_REPLICATION,
                    ResearchOperator.REFINE_HYPOTHESIS,
                    ResearchOperator.INVESTIGATE_FAILURE,
                )
                if eligible(action)
            ][:1]
        updated_branch = branch.model_copy(update={"next_actions": recommendations})
        new_gaps = [
            gap
            for gap in critique.open_information_gaps
            if gap not in state.open_questions
        ]
        return state.model_copy(
            update={
                "frontier": state.frontier.replace(updated_branch),
                "open_questions": [*state.open_questions, *new_gaps],
                "budget": (
                    state.budget.consume(BudgetCost(model_calls=1))
                    if consume_model_call
                    else state.budget
                ),
            }
        )
