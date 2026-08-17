from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BudgetCost(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiments: int = Field(default=0, ge=0)
    compute_minutes: float = Field(default=0.0, ge=0.0)
    model_calls: int = Field(default=0, ge=0)


class BudgetState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    experiment_limit: int = Field(gt=0)
    compute_minutes_limit: float = Field(gt=0.0)
    model_call_limit: int = Field(gt=0)
    experiments_used: int = Field(default=0, ge=0)
    compute_minutes_used: float = Field(default=0.0, ge=0.0)
    model_calls_used: int = Field(default=0, ge=0)

    def can_afford(self, cost: BudgetCost) -> bool:
        return (
            self.experiments_used + cost.experiments <= self.experiment_limit
            and self.compute_minutes_used + cost.compute_minutes
            <= self.compute_minutes_limit
            and self.model_calls_used + cost.model_calls <= self.model_call_limit
        )

    def consume(self, cost: BudgetCost) -> "BudgetState":
        if not self.can_afford(cost):
            raise ValueError("RESEARCH_BUDGET_EXCEEDED")
        return self.model_copy(
            update={
                "experiments_used": self.experiments_used + cost.experiments,
                "compute_minutes_used": self.compute_minutes_used
                + cost.compute_minutes,
                "model_calls_used": self.model_calls_used + cost.model_calls,
            }
        )

    @property
    def exhausted(self) -> bool:
        return (
            self.experiments_used >= self.experiment_limit
            or self.compute_minutes_used >= self.compute_minutes_limit
            or self.model_calls_used >= self.model_call_limit
        )
