from __future__ import annotations

from dataclasses import dataclass

from backend.app.research.frontier import Estimate, ResearchBranch, ResearchFrontier


@dataclass(frozen=True)
class FrontierScore:
    branch_id: str
    score: float
    positive_score: float
    penalty_score: float
    unknown_components: tuple[str, ...]


class FrontierPolicy:
    """Explainable best-first policy; unknown estimates stay explicit in state."""

    UNKNOWN_ESTIMATE = 0.5
    POSITIVE_COMPONENTS = (
        "expected_information_gain",
        "scientific_potential",
        "evidence_support",
        "novelty_potential",
        "expected_improvement",
        "expected_uncertainty_reduction",
    )
    PENALTY_COMPONENTS = ("compute_cost", "risk", "redundancy")

    @classmethod
    def _policy_value(cls, estimate: Estimate) -> float:
        return estimate.value if estimate.known else cls.UNKNOWN_ESTIMATE

    def score(self, branch: ResearchBranch) -> FrontierScore:
        components = branch.priority_components
        positives = [
            self._policy_value(getattr(components, name))
            for name in self.POSITIVE_COMPONENTS
        ]
        penalties = [
            self._policy_value(getattr(components, name))
            for name in self.PENALTY_COMPONENTS
        ]
        positive_score = sum(positives) / len(positives)
        penalty_score = sum(penalties) / len(penalties)
        score = min(1.0, max(0.0, 0.8 * positive_score + 0.2 * (1.0 - penalty_score)))
        unknown = tuple(
            name
            for name in (*self.POSITIVE_COMPONENTS, *self.PENALTY_COMPONENTS)
            if not getattr(components, name).known
        )
        return FrontierScore(
            branch_id=branch.id,
            score=round(score, 8),
            positive_score=round(positive_score, 8),
            penalty_score=round(penalty_score, 8),
            unknown_components=unknown,
        )

    def rank(self, frontier: ResearchFrontier) -> list[ResearchBranch]:
        scored = [(branch, self.score(branch)) for branch in frontier.selectable()]
        scored.sort(key=lambda item: (-item[1].score, item[0].id))
        return [
            branch.model_copy(update={"priority": score.score})
            for branch, score in scored
        ]

    def rerank(self, frontier: ResearchFrontier) -> ResearchFrontier:
        scores = {item.id: self.score(item).score for item in frontier.branches}
        return frontier.model_copy(
            update={
                "branches": [
                    item.model_copy(update={"priority": scores[item.id]})
                    for item in frontier.branches
                ]
            }
        )
