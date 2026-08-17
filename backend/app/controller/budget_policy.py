from __future__ import annotations

from backend.app.research.budget import BudgetCost, BudgetState


class BudgetPolicy:
    def allows(self, budget: BudgetState, cost: BudgetCost) -> bool:
        return budget.can_afford(cost)

    def consume(self, budget: BudgetState, cost: BudgetCost) -> BudgetState:
        return budget.consume(cost)
