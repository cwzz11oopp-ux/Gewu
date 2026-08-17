from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.app.research.budget import BudgetCost


class ResearchOperator(StrEnum):
    SEARCH_LITERATURE = "SEARCH_LITERATURE"
    REPRODUCE_BASELINE = "REPRODUCE_BASELINE"
    EXPLORE_NEW_MECHANISM = "EXPLORE_NEW_MECHANISM"
    EXPAND_BRANCH = "EXPAND_BRANCH"
    REFINE_HYPOTHESIS = "REFINE_HYPOTHESIS"
    RUN_EXPERIMENT = "RUN_EXPERIMENT"
    RUN_REPLICATION = "RUN_REPLICATION"
    RUN_ABLATION = "RUN_ABLATION"
    INVESTIGATE_FAILURE = "INVESTIGATE_FAILURE"
    CHALLENGE_HYPOTHESIS = "CHALLENGE_HYPOTHESIS"
    RUN_ROBUSTNESS = "RUN_ROBUSTNESS"
    STOP_BRANCH = "STOP_BRANCH"
    FINAL_VALIDATION = "FINAL_VALIDATION"
    WRITE_PAPER = "WRITE_PAPER"


class ResearchAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: f"action_{uuid4().hex[:12]}")
    operator: ResearchOperator
    branch_id: str | None = None
    reason: str = Field(min_length=1)
    target_information_gap: str = Field(min_length=1)
    expected_information_gain: float = Field(ge=0.0, le=1.0)
    estimated_cost: BudgetCost = Field(default_factory=BudgetCost)
    prerequisites: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(min_length=1)
    decision_iteration: int = Field(ge=0)
