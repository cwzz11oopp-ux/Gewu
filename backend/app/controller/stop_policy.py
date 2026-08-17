from __future__ import annotations

from dataclasses import dataclass

from backend.app.research.frontier import BranchStatus
from backend.app.state.research import ResearchState


@dataclass(frozen=True)
class StopDecision:
    should_stop: bool
    reason: str = ""


class StopPolicy:
    def evaluate(self, state: ResearchState) -> StopDecision:
        if state.stopped:
            return StopDecision(True, state.stop_reason or "Research was stopped")
        if state.budget.exhausted:
            return StopDecision(True, "Research budget exhausted")
        if state.frontier.branches and all(
            branch.status in {
                BranchStatus.REJECTED,
                BranchStatus.VALIDATED,
                BranchStatus.ARCHIVED,
            }
            for branch in state.frontier.branches
        ):
            return StopDecision(True, "No expandable research branches remain")
        return StopDecision(False)
