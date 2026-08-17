from __future__ import annotations

from backend.app.controller.budget_policy import BudgetPolicy
from backend.app.controller.frontier_policy import FrontierPolicy
from backend.app.controller.stop_policy import StopPolicy
from backend.app.research.actions import ResearchAction, ResearchOperator
from backend.app.research.budget import BudgetCost
from backend.app.research.frontier import BranchStatus, ResearchBranch
from backend.app.state.research import ResearchState


class ResearchController:
    """Policy engine selecting a branch and operator from current scientific state."""

    def __init__(
        self,
        frontier_policy: FrontierPolicy | None = None,
        budget_policy: BudgetPolicy | None = None,
        stop_policy: StopPolicy | None = None,
    ) -> None:
        self.frontier_policy = frontier_policy or FrontierPolicy()
        self.budget_policy = budget_policy or BudgetPolicy()
        self.stop_policy = stop_policy or StopPolicy()

    def next_action(self, state: ResearchState) -> ResearchAction:
        if state.baseline is None or not state.baseline.can_be_comparison_denominator:
            cost = BudgetCost(experiments=1, compute_minutes=1.0)
            self._require_budget(state, cost)
            return ResearchAction(
                operator=ResearchOperator.REPRODUCE_BASELINE,
                reason="A validated local baseline under the active protocol is required before variant comparison.",
                target_information_gap="Local baseline performance and reproducibility are unknown.",
                expected_information_gain=1.0,
                estimated_cost=cost,
                completion_criteria=["BaselineProfile contains audited local metrics"],
                decision_iteration=state.iteration,
            )

        stop = self.stop_policy.evaluate(state)
        if stop.should_stop:
            raise StopIteration(stop.reason)

        ranked = self._decision_rank(state)
        if not ranked:
            cost = BudgetCost(model_calls=1)
            self._require_budget(state, cost)
            return ResearchAction(
                operator=ResearchOperator.EXPLORE_NEW_MECHANISM,
                reason="No selectable branch exists; the frontier needs a falsifiable proposal.",
                target_information_gap="No active mechanism explains how to improve or challenge the baseline.",
                expected_information_gain=0.8,
                estimated_cost=cost,
                completion_criteria=["At least two complete branch proposals are added"],
                decision_iteration=state.iteration,
            )

        for branch in ranked:
            if not self.budget_policy.allows(state.budget, branch.estimated_cost):
                continue
            operator = self._operator_for(branch)
            score = self.frontier_policy.score(branch)
            info = branch.priority_components.expected_information_gain
            return ResearchAction(
                operator=operator,
                branch_id=branch.id,
                reason=self._reason(branch, operator, score.score),
                target_information_gap=branch.research_gap,
                expected_information_gain=(
                    info.value if info.known else self.frontier_policy.UNKNOWN_ESTIMATE
                ),
                estimated_cost=branch.estimated_cost,
                prerequisites=["validated local baseline", "complete branch contract"],
                completion_criteria=[
                    branch.expected_observation,
                    f"Falsify when: {branch.falsification_condition}",
                ],
                decision_iteration=state.iteration,
            )
        raise StopIteration("No selectable branch fits the remaining budget")

    def _decision_rank(self, state: ResearchState) -> list[ResearchBranch]:
        """Combine Frontier score, Belief uncertainty, and scientific follow-up value."""
        ranked = self.frontier_policy.rank(state.frontier)
        follow_ups = {
            ResearchOperator.RUN_ABLATION,
            ResearchOperator.REFINE_HYPOTHESIS,
            ResearchOperator.INVESTIGATE_FAILURE,
            ResearchOperator.CHALLENGE_HYPOTHESIS,
            ResearchOperator.RUN_ROBUSTNESS,
        }

        def decision_score(branch: ResearchBranch) -> tuple[float, str]:
            score = self.frontier_policy.score(branch).score
            operator = self._operator_for(branch)
            belief = state.beliefs.get(branch.id)
            if branch.experiment_ids and operator in follow_ups:
                uncertainty = belief.uncertainty if belief else 0.5
                score += 0.1 + 0.1 * uncertainty
            return score, branch.id

        return sorted(ranked, key=lambda branch: (-decision_score(branch)[0], branch.id))

    def _require_budget(self, state: ResearchState, cost: BudgetCost) -> None:
        if not self.budget_policy.allows(state.budget, cost):
            raise StopIteration("Required action exceeds remaining budget")

    @staticmethod
    def _operator_for(branch: ResearchBranch) -> ResearchOperator:
        if branch.status in {BranchStatus.PROPOSED, BranchStatus.QUEUED}:
            return ResearchOperator.RUN_EXPERIMENT
        if branch.next_actions:
            return branch.next_actions[0]
        if branch.status == BranchStatus.PROMISING:
            return ResearchOperator.RUN_REPLICATION
        if branch.status == BranchStatus.INCONCLUSIVE:
            return ResearchOperator.INVESTIGATE_FAILURE
        return ResearchOperator.RUN_EXPERIMENT

    @staticmethod
    def _reason(branch: ResearchBranch, operator: ResearchOperator, score: float) -> str:
        if operator == ResearchOperator.RUN_REPLICATION:
            return (
                f"Branch {branch.id} is promising after compatible audited evidence; "
                f"replication now has the highest best-first score ({score:.3f})."
            )
        if operator == ResearchOperator.RUN_ABLATION:
            return (
                f"Branch {branch.id} has supportive evidence but its mechanism remains "
                f"unisolated; ablation has best-first score {score:.3f}."
            )
        if operator == ResearchOperator.RUN_ROBUSTNESS:
            return (
                f"Branch {branch.id} is supported on the locked protocol, while robustness "
                f"outside the observed parameter interval remains unknown; robustness testing "
                f"has best-first score {score:.3f}."
            )
        return (
            f"Branch {branch.id} has the highest affordable best-first score "
            f"({score:.3f}) and has not yet received decisive experimental evidence."
        )
