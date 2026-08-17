from __future__ import annotations

from backend.app.controller.budget_policy import BudgetPolicy
from backend.app.controller.frontier_policy import FrontierPolicy
from backend.app.research.actions import ResearchAction, ResearchOperator
from backend.app.research.belief import Belief
from backend.app.research.evidence import (
    EvidenceRelation,
    EvidenceSourceType,
    EvidenceUnit,
)
from backend.app.research.experiment import ExperimentRecord, ExperimentResultStatus
from backend.app.research.frontier import (
    BranchStatus,
    Estimate,
    PriorityComponents,
    ResearchBranch,
)
from backend.app.research.protocol import ComparisonDecision, MetricDirection
from backend.app.state.research import ResearchState


class ResearchStateUpdater:
    def __init__(
        self,
        frontier_policy: FrontierPolicy | None = None,
        budget_policy: BudgetPolicy | None = None,
    ) -> None:
        self.frontier_policy = frontier_policy or FrontierPolicy()
        self.budget_policy = budget_policy or BudgetPolicy()

    def apply_experiment(
        self,
        state: ResearchState,
        action: ResearchAction,
        record: ExperimentRecord,
    ) -> ResearchState:
        if not action.branch_id or action.branch_id != record.branch_id:
            raise ValueError("EXPERIMENT_ACTION_BRANCH_MISMATCH")
        if record.experiment_id in {item.experiment_id for item in state.experiments}:
            raise ValueError("EXPERIMENT_ALREADY_RECORDED")
        branch = state.frontier.get(record.branch_id)
        evidence, outcome = self._evidence_and_outcome(state, branch, action, record)
        updated_branch = self._updated_branch(state, branch, record, evidence, outcome)
        frontier = state.frontier.replace(updated_branch)
        frontier = self.frontier_policy.rerank(frontier)
        beliefs = state.beliefs.upsert(
            self._updated_belief(state, updated_branch, record, evidence, outcome)
        )
        budget = self.budget_policy.consume(state.budget, action.estimated_cost)
        ranked_all = sorted(
            frontier.branches,
            key=lambda item: (-(item.priority or 0.0), item.id),
        )
        return state.model_copy(
            update={
                "frontier": frontier,
                "beliefs": beliefs,
                "budget": budget,
                "evidence": [*state.evidence, evidence],
                "experiments": [*state.experiments, record],
                "current_action": None,
                "action_history": [*state.action_history, action],
                "best_branch_id": ranked_all[0].id if ranked_all else None,
                "iteration": state.iteration + 1,
            }
        )

    def _evidence_and_outcome(
        self,
        state: ResearchState,
        branch: ResearchBranch,
        action: ResearchAction,
        record: ExperimentRecord,
    ) -> tuple[EvidenceUnit, str]:
        if (
            action.operator == ResearchOperator.RUN_ABLATION
            and record.result_status == ExperimentResultStatus.SUCCEEDED
            and record.improvement_claim_allowed
        ):
            prior = next(
                (
                    item
                    for item in reversed(state.experiments)
                    if item.branch_id == branch.id
                    and item.result_status == ExperimentResultStatus.SUCCEEDED
                    and item.improvement_claim_allowed
                ),
                None,
            )
            if prior is None:
                outcome = "failed"
                relation = EvidenceRelation.CONTEXT
                claim = "Ablation lacks a prior compatible variant result for comparison."
                strength = 0.25
            else:
                metric = record.protocol.metrics[0].name
                direction = self._primary_direction(record)
                prior_value = prior.metrics[metric]
                ablated_value = record.metrics[metric]
                degraded = (
                    ablated_value < prior_value
                    if direction == MetricDirection.MAXIMIZE
                    else ablated_value > prior_value
                )
                outcome = "mechanism_support" if degraded else "mechanism_contradict"
                relation = EvidenceRelation.SUPPORT if degraded else EvidenceRelation.CONTRADICT
                claim = (
                    f"Under a compatible audited ablation, {metric} changed from the prior "
                    f"variant value {prior_value} to {ablated_value}; "
                    + (
                        "removing the mechanism reduced performance."
                        if degraded
                        else "removing the mechanism did not reduce performance."
                    )
                )
                strength = min(1.0, 0.6 + 0.05 * len(record.seeds))
        elif record.result_status != ExperimentResultStatus.SUCCEEDED:
            outcome = "failed"
            relation = EvidenceRelation.CONTEXT
            claim = f"Experiment {record.experiment_id} failed and cannot test {branch.hypothesis}."
            strength = 0.2
        elif not record.improvement_claim_allowed:
            outcome = "incompatible"
            relation = EvidenceRelation.CONTEXT
            claim = (
                f"Experiment {record.experiment_id} produced an observation, but "
                "COMPARISON_NOT_ALLOWED prevents a direct baseline improvement claim."
            )
            strength = 0.35
        else:
            direction = self._primary_direction(record)
            metric = record.protocol.metrics[0].name
            delta = record.metrics[metric] - record.baseline_metrics[metric]
            improved = delta > 0 if direction == MetricDirection.MAXIMIZE else delta < 0
            if improved:
                outcome = "support"
                relation = EvidenceRelation.SUPPORT
                claim = (
                    f"Under a compatible audited protocol, {metric} changed from "
                    f"{record.baseline_metrics[metric]} to {record.metrics[metric]}."
                )
                strength = min(1.0, 0.55 + 0.05 * len(record.seeds))
            else:
                outcome = "contradict"
                relation = EvidenceRelation.CONTRADICT
                claim = (
                    f"Under a compatible audited protocol, {metric} did not improve "
                    f"over the local baseline ({record.metrics[metric]} vs "
                    f"{record.baseline_metrics[metric]})."
                )
                strength = min(1.0, 0.55 + 0.05 * len(record.seeds))
        return (
            EvidenceUnit(
                source_type=EvidenceSourceType.EXPERIMENT,
                experiment_id=record.experiment_id,
                claim=claim,
                relation=relation,
                strength=strength,
                verified=record.audit_passed,
                access_level="runtime",
                provenance={
                    "branch_id": record.branch_id,
                    "protocol_fingerprint": record.protocol_fingerprint.value,
                    "comparison_decision": record.comparison.decision,
                    "base_commit": record.base_commit,
                    "code_commit": record.code_commit,
                },
            ),
            outcome,
        )

    def _updated_branch(
        self,
        state: ResearchState,
        branch: ResearchBranch,
        record: ExperimentRecord,
        evidence: EvidenceUnit,
        outcome: str,
    ) -> ResearchBranch:
        prior_records = [item for item in state.experiments if item.branch_id == branch.id]
        running = self._as_running(branch)
        if outcome == "mechanism_support":
            status = BranchStatus.PROMISING
            next_actions = [
                ResearchOperator.RUN_REPLICATION,
                ResearchOperator.RUN_ROBUSTNESS,
            ]
        elif outcome == "mechanism_contradict":
            status = BranchStatus.INCONCLUSIVE
            next_actions = [
                ResearchOperator.REFINE_HYPOTHESIS,
                ResearchOperator.INVESTIGATE_FAILURE,
            ]
        elif outcome == "support":
            status = (
                BranchStatus.VALIDATED
                if branch.status == BranchStatus.PROMISING and prior_records
                else BranchStatus.PROMISING
            )
            next_actions = (
                [ResearchOperator.FINAL_VALIDATION]
                if status == BranchStatus.VALIDATED
                else [ResearchOperator.RUN_REPLICATION, ResearchOperator.RUN_ABLATION]
            )
        elif outcome == "contradict":
            status = BranchStatus.REJECTED if prior_records else BranchStatus.INCONCLUSIVE
            next_actions = (
                [ResearchOperator.STOP_BRANCH]
                if status == BranchStatus.REJECTED
                else [ResearchOperator.RUN_REPLICATION, ResearchOperator.RUN_ABLATION]
            )
        else:
            status = BranchStatus.INCONCLUSIVE
            next_actions = [ResearchOperator.INVESTIGATE_FAILURE]

        components = self._update_components(
            branch.priority_components,
            record.experiment_id,
            outcome,
        )
        transitioned = running.transition(
            BranchStatus.PROMISING if status == BranchStatus.VALIDATED else status
        )
        if status == BranchStatus.VALIDATED:
            transitioned = transitioned.transition(BranchStatus.VALIDATED)
        return transitioned.model_copy(
            update={
                "status": status,
                "evidence_ids": [*branch.evidence_ids, evidence.id],
                "experiment_ids": [*branch.experiment_ids, record.experiment_id],
                "observations": [*branch.observations, evidence.claim],
                "code_commit": record.code_commit or branch.code_commit,
                "priority_components": components,
                "next_actions": next_actions,
            }
        )

    @staticmethod
    def _as_running(branch: ResearchBranch) -> ResearchBranch:
        current = branch
        if current.status == BranchStatus.PROPOSED:
            current = current.transition(BranchStatus.QUEUED)
        if current.status in {
            BranchStatus.QUEUED,
            BranchStatus.PROMISING,
            BranchStatus.INCONCLUSIVE,
        }:
            current = current.transition(BranchStatus.RUNNING)
        if current.status != BranchStatus.RUNNING:
            raise ValueError(f"BRANCH_NOT_RUNNABLE:{current.status}")
        return current

    @staticmethod
    def _update_components(
        components: PriorityComponents,
        experiment_id: str,
        outcome: str,
    ) -> PriorityComponents:
        support = {
            "support": 0.85,
            "mechanism_support": 0.9,
            "contradict": 0.15,
            "mechanism_contradict": 0.25,
        }.get(outcome, 0.4)
        improvement = {
            "support": 0.8,
            "mechanism_support": 0.8,
            "contradict": 0.1,
            "mechanism_contradict": 0.25,
        }.get(outcome, 0.4)
        uncertainty = {
            "support": 0.65,
            "mechanism_support": 0.85,
            "contradict": 0.7,
            "mechanism_contradict": 0.65,
        }.get(outcome, 0.45)

        def measured(value: float, name: str) -> Estimate:
            return Estimate(
                value=value,
                known=True,
                estimation_method=f"deterministic_{name}_from_audited_experiment",
                provenance=[experiment_id],
            )

        return components.model_copy(
            update={
                "evidence_support": measured(support, "evidence_support"),
                "expected_improvement": measured(improvement, "expected_improvement"),
                "expected_uncertainty_reduction": measured(
                    uncertainty, "uncertainty_reduction"
                ),
            }
        )

    @staticmethod
    def _updated_belief(
        state: ResearchState,
        branch: ResearchBranch,
        record: ExperimentRecord,
        evidence: EvidenceUnit,
        outcome: str,
    ) -> Belief:
        current = state.beliefs.get(branch.id) or Belief(
            branch_id=branch.id,
            statement=branch.hypothesis,
        )
        adjustment = {
            "support": 0.35,
            "mechanism_support": 0.25,
            "contradict": -0.35,
            "mechanism_contradict": -0.2,
        }.get(outcome, 0.0)
        uncertainty_reduction = (
            0.25
            if outcome in {
                "support",
                "mechanism_support",
                "contradict",
                "mechanism_contradict",
            }
            else 0.05
        )
        return current.model_copy(
            update={
                "support_score": min(1.0, max(-1.0, current.support_score + adjustment)),
                "uncertainty": max(0.0, current.uncertainty - uncertainty_reduction),
                "evidence_ids": [*current.evidence_ids, evidence.id],
                "experiment_ids": [*current.experiment_ids, record.experiment_id],
                "rationale": [*current.rationale, evidence.claim],
            }
        )

    @staticmethod
    def _primary_direction(record: ExperimentRecord) -> MetricDirection:
        primary = record.protocol.metrics[0]
        if primary.name not in record.metrics or primary.name not in record.baseline_metrics:
            raise ValueError(f"PRIMARY_METRIC_MISSING:{primary.name}")
        return primary.direction
