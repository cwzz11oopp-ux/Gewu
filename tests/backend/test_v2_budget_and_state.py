from __future__ import annotations

import pytest

from backend.app.research import BudgetCost, BudgetState


def test_budget_consumption_is_explicit_and_cannot_overspend():
    budget = BudgetState(
        experiment_limit=2,
        compute_minutes_limit=5,
        model_call_limit=3,
    )
    cost = BudgetCost(experiments=1, compute_minutes=2, model_calls=1)
    updated = budget.consume(cost)
    assert budget.experiments_used == 0
    assert updated.experiments_used == 1
    assert updated.compute_minutes_used == 2
    with pytest.raises(ValueError, match="RESEARCH_BUDGET_EXCEEDED"):
        updated.consume(BudgetCost(experiments=2))
