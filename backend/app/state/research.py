from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.research.actions import ResearchAction
from backend.app.research.belief import BeliefState
from backend.app.research.budget import BudgetState
from backend.app.research.evidence import EvidenceUnit
from backend.app.research.experiment import ExperimentRecord
from backend.app.research.frontier import ResearchFrontier
from backend.app.research.profiles import BaselineProfile, ProblemProfile


class ResearchState(BaseModel):
    """Current scientific state. Historical artifact auditing remains external."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(default_factory=lambda: f"research_{uuid4().hex[:12]}")
    problem: ProblemProfile
    baseline: BaselineProfile | None = None
    frontier: ResearchFrontier = Field(default_factory=ResearchFrontier)
    beliefs: BeliefState = Field(default_factory=BeliefState)
    budget: BudgetState
    evidence: list[EvidenceUnit] = Field(default_factory=list)
    experiments: list[ExperimentRecord] = Field(default_factory=list)
    current_action: ResearchAction | None = None
    action_history: list[ResearchAction] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    best_branch_id: str | None = None
    iteration: int = Field(default=0, ge=0)
    ledger_ref: str | None = None
    stopped: bool = False
    stop_reason: str = ""
    language: str = "zh-CN"

    @model_validator(mode="after")
    def references_exist(self):
        branch_ids = {branch.id for branch in self.frontier.branches}
        if self.best_branch_id and self.best_branch_id not in branch_ids:
            raise ValueError("STATE_BEST_BRANCH_NOT_FOUND")
        if self.current_action and self.current_action.branch_id:
            if self.current_action.branch_id not in branch_ids:
                raise ValueError("STATE_ACTION_BRANCH_NOT_FOUND")
        experiment_ids = [item.experiment_id for item in self.experiments]
        if len(experiment_ids) != len(set(experiment_ids)):
            raise ValueError("STATE_DUPLICATE_EXPERIMENT_ID")
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("STATE_DUPLICATE_EVIDENCE_ID")
        return self
