from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Belief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    support_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    uncertainty: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    experiment_ids: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)


class BeliefState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    beliefs: list[Belief] = Field(default_factory=list)

    def get(self, branch_id: str) -> Belief | None:
        return next((item for item in self.beliefs if item.branch_id == branch_id), None)

    def upsert(self, belief: Belief) -> "BeliefState":
        retained = [item for item in self.beliefs if item.branch_id != belief.branch_id]
        return self.model_copy(update={"beliefs": [*retained, belief]})
