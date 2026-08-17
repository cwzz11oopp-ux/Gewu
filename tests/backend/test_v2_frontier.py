from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.controller.frontier_policy import FrontierPolicy
from backend.app.research import (
    BranchStatus,
    BudgetCost,
    Estimate,
    PriorityComponents,
    ResearchBranch,
    ResearchFrontier,
)


def estimate(value: float | None, *, known: bool = True) -> Estimate:
    return Estimate(
        value=value,
        known=known,
        estimation_method="fixture estimate" if known else "not yet measured",
        provenance=["fixture"] if known else [],
    )


def components(info: float, risk: float = 0.2) -> PriorityComponents:
    return PriorityComponents(
        expected_information_gain=estimate(info),
        scientific_potential=estimate(0.8),
        evidence_support=estimate(None, known=False),
        novelty_potential=estimate(0.7),
        expected_improvement=estimate(0.6),
        expected_uncertainty_reduction=estimate(0.8),
        compute_cost=estimate(0.2),
        risk=estimate(risk),
        redundancy=estimate(0.1),
    )


def branch(branch_id: str, info: float, parent_id: str | None = None) -> ResearchBranch:
    return ResearchBranch(
        id=branch_id,
        parent_id=parent_id,
        research_gap=f"gap for {branch_id}",
        hypothesis=f"hypothesis for {branch_id}",
        mechanism="testable mechanism",
        proposed_change="small repository change",
        expected_observation="primary metric changes",
        falsification_condition="primary metric fails to improve after replication",
        minimal_experiment="two-seed smoke-scale experiment",
        closest_prior_work=["paper-fixture"],
        novelty_risk="mechanism may overlap prior work",
        priority_components=components(info),
        estimated_cost=BudgetCost(experiments=1, compute_minutes=2),
        risk="low fixture risk",
    )


def test_estimate_keeps_unknown_distinct_from_numeric_zero():
    unknown = estimate(None, known=False)
    assert unknown.value is None
    with pytest.raises(ValidationError, match="UNKNOWN_ESTIMATE_MUST_NOT_HAVE_VALUE"):
        estimate(0.0, known=False)


def test_branch_enforces_legal_status_transitions():
    candidate = branch("h1", 0.9)
    running = candidate.transition(BranchStatus.QUEUED).transition(BranchStatus.RUNNING)
    assert running.transition(BranchStatus.PROMISING).status == BranchStatus.PROMISING
    with pytest.raises(ValueError, match="INVALID_BRANCH_TRANSITION"):
        candidate.transition(BranchStatus.VALIDATED)


def test_frontier_requires_existing_parent_and_best_first_is_explainable():
    h1 = branch("h1", 0.9)
    h2 = branch("h2", 0.5)
    frontier = ResearchFrontier(branches=[h1, h2])
    policy = FrontierPolicy()
    ranked = policy.rank(frontier)
    score = policy.score(h1)
    assert [item.id for item in ranked] == ["h1", "h2"]
    assert "evidence_support" in score.unknown_components
    assert 0 <= score.penalty_score <= 1
    assert ranked[0].priority == score.score

    with pytest.raises(ValidationError, match="FRONTIER_PARENT_NOT_FOUND"):
        ResearchFrontier(branches=[branch("orphan", 0.5, parent_id="missing")])
