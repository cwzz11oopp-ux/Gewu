from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backend.app.controller.research_controller import ResearchController
from backend.app.controller.state_updater import ResearchStateUpdater
from backend.app.research.actions import ResearchAction
from backend.app.research.experiment import ExperimentRecord
from backend.app.state.research import ResearchState


ExperimentExecutor = Callable[[ResearchAction, ResearchState], ExperimentRecord]


@dataclass(frozen=True)
class ResearchIteration:
    action: ResearchAction
    experiment: ExperimentRecord
    state: ResearchState


class ResearchLoop:
    """Small scientific loop; execution is injected and never fabricated here."""

    def __init__(
        self,
        controller: ResearchController | None = None,
        updater: ResearchStateUpdater | None = None,
    ) -> None:
        self.controller = controller or ResearchController()
        self.updater = updater or ResearchStateUpdater(
            frontier_policy=self.controller.frontier_policy,
            budget_policy=self.controller.budget_policy,
        )

    def run_iteration(
        self,
        state: ResearchState,
        executor: ExperimentExecutor,
    ) -> ResearchIteration:
        action = self.controller.next_action(state)
        active = state.model_copy(update={"current_action": action})
        experiment = executor(action, active)
        updated = self.updater.apply_experiment(active, action, experiment)
        return ResearchIteration(action=action, experiment=experiment, state=updated)

    def run(
        self,
        state: ResearchState,
        executor: ExperimentExecutor,
        *,
        iterations: int,
    ) -> list[ResearchIteration]:
        if iterations <= 0:
            raise ValueError("RESEARCH_ITERATIONS_MUST_BE_POSITIVE")
        results: list[ResearchIteration] = []
        current = state
        for _ in range(iterations):
            iteration = self.run_iteration(current, executor)
            results.append(iteration)
            current = iteration.state
        return results
