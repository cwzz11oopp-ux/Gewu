from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.controller import ResearchController, ResearchStateUpdater
from backend.app.literature import SprintLiteratureService
from backend.app.research.actions import ResearchAction, ResearchOperator
from backend.app.research.budget import BudgetCost, BudgetState
from backend.app.research.experiment import ExperimentRecord
from backend.app.research.ideator import BranchConstructor
from backend.app.research.profiles import BaselineProfile, ProblemProfile
from backend.app.state.research import ResearchState
from backend.app.storage.v2 import V2Stores
from backend.app.models.v2_session import (
    ResearchEventKind,
    ResearchSessionEvent,
)
from backend.app.research.protocol import MetricDirection
from backend.app.services.v2_critic import (
    CriticDecisionService,
    ScientificCritic,
    ScientificCritique,
)


class SessionConflict(RuntimeError):
    pass


class ModelUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SessionTransition:
    state: ResearchState
    action: ResearchAction | None
    critique: ScientificCritique | None = None


class ResearchSessionService:
    """Durable lifecycle around the already validated V2 scientific core."""

    def __init__(
        self,
        stores: V2Stores,
        branch_constructor: BranchConstructor,
        literature: SprintLiteratureService,
        *,
        model_ready: bool,
        controller: ResearchController | None = None,
        updater: ResearchStateUpdater | None = None,
        critic: ScientificCritic | None = None,
        critic_decisions: CriticDecisionService | None = None,
    ) -> None:
        self.stores = stores
        self.branch_constructor = branch_constructor
        self.literature = literature
        self.model_ready = model_ready
        self.controller = controller or ResearchController()
        self.updater = updater or ResearchStateUpdater()
        self.critic = critic
        self.critic_decisions = critic_decisions or CriticDecisionService()

    def create(
        self,
        problem: ProblemProfile,
        budget: BudgetState,
        baseline: BaselineProfile | None = None,
        *,
        session_id: str | None = None,
    ) -> ResearchState:
        state = ResearchState(
            **({"session_id": session_id} if session_id else {}),
            problem=problem,
            baseline=baseline,
            budget=budget,
            open_questions=list(problem.open_questions),
        )
        self.stores.persist(state)
        self.stores.events.append(
            self._event(
                state,
                ResearchEventKind.SESSION_CREATED,
                {
                    "question": state.problem.question,
                    "repository": state.problem.repository,
                    "model_live_validation": (
                        "ready" if self.model_ready else "pending"
                    ),
                },
            )
        )
        return state

    def get(self, session_id: str) -> ResearchState:
        return self.stores.states.get(session_id)

    def start(self, session_id: str) -> SessionTransition:
        state = self.get(session_id)
        if state.stopped:
            raise SessionConflict("RESEARCH_SESSION_STOPPED")
        if state.current_action is not None:
            return SessionTransition(state=state, action=state.current_action)
        state = self._ensure_frontier(state)
        return self._select_and_persist(state)

    def continue_session(
        self,
        session_id: str,
        *,
        baseline: BaselineProfile | None = None,
        experiment: ExperimentRecord | None = None,
    ) -> SessionTransition:
        state = self.get(session_id)
        if state.stopped:
            raise SessionConflict("RESEARCH_SESSION_STOPPED")
        action = state.current_action
        if action is None:
            raise SessionConflict("RESEARCH_SESSION_HAS_NO_PENDING_ACTION")

        critique = None
        pending_events: list[ResearchSessionEvent] = []
        if action.operator == ResearchOperator.REPRODUCE_BASELINE:
            if baseline is None or experiment is not None:
                raise SessionConflict("VALIDATED_BASELINE_REQUIRED")
            if not baseline.can_be_comparison_denominator:
                raise SessionConflict("BASELINE_NOT_VALIDATED")
            if baseline.repository != state.problem.repository:
                raise SessionConflict("BASELINE_REPOSITORY_MISMATCH")
            if baseline.task != state.problem.task or baseline.dataset != state.problem.dataset:
                raise SessionConflict("BASELINE_PROBLEM_MISMATCH")
            state = state.model_copy(
                update={
                    "baseline": baseline,
                    "budget": state.budget.consume(action.estimated_cost),
                    "current_action": None,
                    "action_history": [*state.action_history, action],
                    "iteration": state.iteration + 1,
                }
            )
            pending_events.append(
                self._event(
                    state,
                    ResearchEventKind.BASELINE_ACCEPTED,
                    {
                        "commit": baseline.commit,
                        "status": baseline.reproduction_status,
                        "metrics": baseline.local_metrics,
                        "audit_passed": baseline.audit_passed,
                    },
                )
            )
        else:
            if experiment is None or baseline is not None:
                raise SessionConflict("EXPERIMENT_RECORD_REQUIRED")
            state = self.updater.apply_experiment(state, action, experiment)
            if self.critic is None:
                raise ModelUnavailable("SCIENTIFIC_CRITIC_MODEL_UNAVAILABLE")
            if not state.budget.can_afford(BudgetCost(model_calls=1)):
                raise SessionConflict("SCIENTIFIC_CRITIC_BUDGET_EXCEEDED")
            updated_branch = state.frontier.get(experiment.branch_id)
            pending_events.append(
                self._event(
                    state,
                    ResearchEventKind.EXPERIMENT_RECORDED,
                    {
                        "experiment_id": experiment.experiment_id,
                        "operator": action.operator,
                        "branch_id": experiment.branch_id,
                        "result_status": experiment.result_status,
                        "base_commit": experiment.base_commit,
                        "code_commit": experiment.code_commit,
                        "protocol_compatible": experiment.comparison.compatible,
                        "metrics": experiment.metrics,
                        "baseline_metrics": experiment.baseline_metrics,
                        "audit_passed": experiment.audit_passed,
                    },
                )
            )
            critique = self.critic.review(state, updated_branch, experiment)
            state = self.critic_decisions.apply(
                state, experiment.branch_id, critique
            )
            pending_events.append(
                self._event(
                    state,
                    ResearchEventKind.CRITIQUE_RECORDED,
                    {
                        "branch_id": experiment.branch_id,
                        **critique.model_dump(mode="json"),
                    },
                )
            )

        state = self._ensure_frontier(state)
        return self._select_and_persist(
            state, critique=critique, pending_events=pending_events
        )

    def stop(self, session_id: str, reason: str) -> ResearchState:
        state = self.get(session_id)
        if state.stopped:
            return state
        state = state.model_copy(
            update={
                "stopped": True,
                "stop_reason": reason,
                "current_action": None,
            }
        )
        self.stores.persist(state)
        self.stores.events.append(
            self._event(
                state,
                ResearchEventKind.SESSION_STOPPED,
                {"reason": reason},
            )
        )
        return state

    def events(self, session_id: str) -> list[ResearchSessionEvent]:
        self.get(session_id)
        return self.stores.events.list(session_id)

    def summary(self, session_id: str) -> dict[str, Any]:
        state = self.get(session_id)
        best_result = self._best_result(state)
        action = state.current_action
        active_branch = (
            state.frontier.get(action.branch_id).model_dump(mode="json")
            if action and action.branch_id
            else None
        )
        budget = state.budget
        return {
            "session_id": state.session_id,
            "status": "stopped" if state.stopped else ("waiting" if action else "created"),
            "model_live_validation": "ready" if self.model_ready else "pending",
            "baseline": state.baseline.model_dump(mode="json") if state.baseline else None,
            "best_result": best_result,
            "active_branch": active_branch,
            "current_decision": action.model_dump(mode="json") if action else None,
            "iterations": state.iteration,
            "experiment_count": len(state.experiments),
            "remaining_budget": {
                "experiments": budget.experiment_limit - budget.experiments_used,
                "compute_minutes": budget.compute_minutes_limit - budget.compute_minutes_used,
                "model_calls": budget.model_call_limit - budget.model_calls_used,
            },
            "stop_reason": state.stop_reason,
        }

    def findings(self, session_id: str) -> dict[str, Any]:
        state = self.get(session_id)
        critiques = [
            event
            for event in self.stores.events.list(session_id)
            if event.kind == ResearchEventKind.CRITIQUE_RECORDED
        ]
        latest = critiques[-1].payload if critiques else {}
        action = state.current_action
        return {
            "supported_claims": latest.get("supported_claims", []),
            "unsupported_claims": latest.get("unsupported_claims", []),
            "possible_mechanisms": latest.get("possible_mechanisms", []),
            "alternative_explanations": latest.get("alternative_explanations", []),
            "methodological_issues": latest.get("methodological_issues", []),
            "open_information_gaps": latest.get(
                "open_information_gaps", state.open_questions
            ),
            "recommended_actions": latest.get("recommended_actions", []),
            "next_evidence_requested": action.target_information_gap if action else "",
        }

    def _ensure_frontier(self, state: ResearchState) -> ResearchState:
        if state.baseline is None or not state.baseline.can_be_comparison_denominator:
            return state
        if state.frontier.branches:
            return state
        if not self.model_ready:
            raise ModelUnavailable("QWEN_API_KEY_MISSING")

        ideation_action = self.controller.next_action(state)
        if ideation_action.operator != ResearchOperator.EXPLORE_NEW_MECHANISM:
            raise SessionConflict("FRONTIER_IDEATION_ACTION_EXPECTED")
        literature = self.literature.research(
            [state.problem.question, f"{state.problem.task} {state.problem.question}"],
            limit_per_query=5,
        )
        try:
            construction = self.branch_constructor.construct_with_gate(
                state.problem,
                state.baseline,
                literature.papers,
                state.frontier,
                known_failures=[],
                budget=state.budget,
            )
        except Exception as exc:
            self.stores.events.append(
                self._event(
                    state,
                    ResearchEventKind.BRANCH_GATE,
                    {
                        "accepted": [],
                        "rejected": [
                            {
                                "reason": f"schema_or_scientific_gate:{type(exc).__name__}"
                            }
                        ],
                    },
                )
            )
            raise
        branches = construction.accepted
        if len(branches) < 2:
            raise SessionConflict("IDEATOR_MULTIPLE_BRANCHES_REQUIRED")
        frontier = state.frontier
        for branch in branches:
            # Pydantic construction is the schema gate; these explicit checks make
            # the scientific gate visible at the service boundary.
            if not branch.falsification_condition.strip():
                raise SessionConflict("BRANCH_FALSIFIABILITY_REQUIRED")
            if not branch.minimal_experiment.strip():
                raise SessionConflict("BRANCH_MINIMAL_EXPERIMENT_REQUIRED")
            frontier = frontier.add(branch)
        state = state.model_copy(
            update={
                "frontier": self.controller.frontier_policy.rerank(frontier),
                "budget": state.budget.consume(ideation_action.estimated_cost),
                "evidence": [*state.evidence, *literature.evidence_units],
                "open_questions": [
                    *state.open_questions,
                    *[gap for gap in literature.gaps if gap not in state.open_questions],
                ],
                "action_history": [*state.action_history, ideation_action],
                "iteration": state.iteration + 1,
            }
        )
        self.stores.events.append(
            self._event(
                state,
                ResearchEventKind.BRANCH_GATE,
                {
                    "accepted": [
                        {
                            "branch_id": branch.id,
                            "gate_reason": "schema, falsifiability, and minimal experiment passed",
                        }
                        for branch in branches
                    ],
                    "rejected": [
                        item.model_dump(mode="json") for item in construction.rejected
                    ],
                },
            )
        )
        return state

    def _select_and_persist(
        self,
        state: ResearchState,
        *,
        critique: ScientificCritique | None = None,
        pending_events: list[ResearchSessionEvent] | None = None,
    ) -> SessionTransition:
        try:
            action = self.controller.next_action(state)
        except StopIteration as exc:
            state = state.model_copy(
                update={
                    "stopped": True,
                    "stop_reason": str(exc),
                    "current_action": None,
                }
            )
            action = None
        else:
            state = state.model_copy(update={"current_action": action})
        self.stores.persist(state)
        for event in pending_events or []:
            self.stores.events.append(event)
        if action is not None:
            self.stores.events.append(
                self._event(
                    state,
                    ResearchEventKind.ACTION_SELECTED,
                    action.model_dump(mode="json"),
                )
            )
        return SessionTransition(state=state, action=action, critique=critique)

    @staticmethod
    def _event(
        state: ResearchState,
        kind: ResearchEventKind,
        payload: dict[str, Any],
    ) -> ResearchSessionEvent:
        return ResearchSessionEvent(
            session_id=state.session_id,
            kind=kind,
            iteration=state.iteration,
            payload=payload,
        )

    @staticmethod
    def _best_result(state: ResearchState) -> dict[str, Any] | None:
        if state.baseline is None:
            return None
        metric = state.baseline.protocol.metrics[0]
        candidates = [
            record
            for record in state.experiments
            if record.audit_passed
            and record.comparison.compatible
            and metric.name in record.metrics
        ]
        if not candidates:
            return None
        reverse = metric.direction == MetricDirection.MAXIMIZE
        best = sorted(
            candidates,
            key=lambda record: record.metrics[metric.name],
            reverse=reverse,
        )[0]
        baseline_value = best.baseline_metrics.get(metric.name)
        value = best.metrics[metric.name]
        return {
            "experiment_id": best.experiment_id,
            "branch_id": best.branch_id,
            "metric": metric.name,
            "value": value,
            "baseline": baseline_value,
            "delta": value - baseline_value if baseline_value is not None else None,
            "audit_passed": best.audit_passed,
            "protocol_compatible": best.comparison.compatible,
        }
