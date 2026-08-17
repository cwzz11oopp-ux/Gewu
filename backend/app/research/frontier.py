from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.research.actions import ResearchOperator
from backend.app.research.budget import BudgetCost


class BranchStatus(StrEnum):
    PROPOSED = "proposed"
    QUEUED = "queued"
    RUNNING = "running"
    PROMISING = "promising"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"
    VALIDATED = "validated"
    ARCHIVED = "archived"


ALLOWED_TRANSITIONS: dict[BranchStatus, set[BranchStatus]] = {
    BranchStatus.PROPOSED: {BranchStatus.QUEUED, BranchStatus.REJECTED, BranchStatus.ARCHIVED},
    BranchStatus.QUEUED: {BranchStatus.RUNNING, BranchStatus.REJECTED, BranchStatus.ARCHIVED},
    BranchStatus.RUNNING: {
        BranchStatus.PROMISING,
        BranchStatus.INCONCLUSIVE,
        BranchStatus.REJECTED,
        BranchStatus.QUEUED,
    },
    BranchStatus.PROMISING: {
        BranchStatus.RUNNING,
        BranchStatus.VALIDATED,
        BranchStatus.INCONCLUSIVE,
        BranchStatus.REJECTED,
        BranchStatus.ARCHIVED,
    },
    BranchStatus.INCONCLUSIVE: {
        BranchStatus.QUEUED,
        BranchStatus.RUNNING,
        BranchStatus.REJECTED,
        BranchStatus.ARCHIVED,
    },
    BranchStatus.REJECTED: {BranchStatus.ARCHIVED},
    BranchStatus.VALIDATED: {BranchStatus.ARCHIVED},
    BranchStatus.ARCHIVED: set(),
}


class Estimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: float | None = Field(default=None, ge=0.0, le=1.0)
    known: bool
    estimation_method: str = Field(min_length=1)
    provenance: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def known_matches_value(self):
        if self.known and self.value is None:
            raise ValueError("KNOWN_ESTIMATE_VALUE_REQUIRED")
        if not self.known and self.value is not None:
            raise ValueError("UNKNOWN_ESTIMATE_MUST_NOT_HAVE_VALUE")
        return self


class PriorityComponents(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_information_gain: Estimate
    scientific_potential: Estimate
    evidence_support: Estimate
    novelty_potential: Estimate
    expected_improvement: Estimate
    expected_uncertainty_reduction: Estimate
    compute_cost: Estimate
    risk: Estimate
    redundancy: Estimate


class ResearchBranch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"branch_{uuid4().hex[:12]}")
    parent_id: str | None = None
    research_gap: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    proposed_change: str = Field(min_length=1)
    expected_observation: str = Field(min_length=1)
    falsification_condition: str = Field(min_length=1)
    minimal_experiment: str = Field(min_length=1)
    closest_prior_work: list[str] = Field(default_factory=list)
    novelty_risk: str = Field(min_length=1)
    status: BranchStatus = BranchStatus.PROPOSED
    evidence_ids: list[str] = Field(default_factory=list)
    experiment_ids: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    base_commit: str | None = None
    code_commit: str | None = None
    priority: float | None = Field(default=None, ge=0.0, le=1.0)
    priority_components: PriorityComponents
    estimated_cost: BudgetCost = Field(default_factory=BudgetCost)
    risk: str = Field(min_length=1)
    next_actions: list[ResearchOperator] = Field(
        default_factory=lambda: [ResearchOperator.RUN_EXPERIMENT]
    )

    def transition(self, status: BranchStatus) -> "ResearchBranch":
        if status == self.status:
            return self
        if status not in ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"INVALID_BRANCH_TRANSITION:{self.status}->{status}")
        return self.model_copy(update={"status": status})


class ResearchFrontier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branches: list[ResearchBranch] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tree(self):
        ids = [branch.id for branch in self.branches]
        if len(ids) != len(set(ids)):
            raise ValueError("FRONTIER_DUPLICATE_BRANCH_ID")
        known = set(ids)
        for branch in self.branches:
            if branch.parent_id and branch.parent_id not in known:
                raise ValueError(f"FRONTIER_PARENT_NOT_FOUND:{branch.parent_id}")
            if branch.parent_id == branch.id:
                raise ValueError("FRONTIER_SELF_PARENT")
        return self

    def get(self, branch_id: str) -> ResearchBranch:
        for branch in self.branches:
            if branch.id == branch_id:
                return branch
        raise KeyError(branch_id)

    def replace(self, updated: ResearchBranch) -> "ResearchFrontier":
        if not any(item.id == updated.id for item in self.branches):
            raise KeyError(updated.id)
        return self.model_copy(
            update={
                "branches": [
                    updated if item.id == updated.id else item for item in self.branches
                ]
            }
        )

    def add(self, branch: ResearchBranch) -> "ResearchFrontier":
        return ResearchFrontier(branches=[*self.branches, branch])

    def selectable(self) -> list[ResearchBranch]:
        allowed = {
            BranchStatus.PROPOSED,
            BranchStatus.QUEUED,
            BranchStatus.PROMISING,
            BranchStatus.INCONCLUSIVE,
        }
        return [branch for branch in self.branches if branch.status in allowed]
