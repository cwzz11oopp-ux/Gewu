from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.literature.paper_card import PaperCard
from backend.app.models.gateway import ModelGateway
from backend.app.research.budget import BudgetCost, BudgetState
from backend.app.research.frontier import (
    Estimate,
    PriorityComponents,
    ResearchBranch,
    ResearchFrontier,
)
from backend.app.research.profiles import BaselineProfile, ProblemProfile


class BranchProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_gap: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    mechanism: str = Field(min_length=1)
    proposed_change: str = Field(min_length=1)
    expected_observation: str = Field(min_length=1)
    falsification_condition: str = Field(min_length=1)
    minimal_experiment: str = Field(min_length=1)
    closest_prior_work: list[str] = Field(default_factory=list)
    novelty_risk: str = Field(min_length=1)
    information_gain: Literal["low", "medium", "high"]
    scientific_potential: Literal["low", "medium", "high"]
    estimated_compute_minutes: float = Field(gt=0)
    risk: Literal["low", "medium", "high"]
    initially_runnable: bool
    required_prior_evidence: list[str]


class BranchProposalBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[BranchProposal] = Field(min_length=3, max_length=8)


class BranchGateRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis: str
    reason: str


class BranchConstructionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: list[ResearchBranch]
    rejected: list[BranchGateRejection]


class BranchConstructor:
    QUALITATIVE = {"low": 0.25, "medium": 0.5, "high": 0.75}

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def construct(
        self,
        problem: ProblemProfile,
        baseline: BaselineProfile,
        papers: list[PaperCard],
        frontier: ResearchFrontier,
        known_failures: list[str],
        budget: BudgetState,
    ) -> list[ResearchBranch]:
        result = self.construct_with_gate(
            problem, baseline, papers, frontier, known_failures, budget
        )
        return result.accepted

    def construct_with_gate(
        self,
        problem: ProblemProfile,
        baseline: BaselineProfile,
        papers: list[PaperCard],
        frontier: ResearchFrontier,
        known_failures: list[str],
        budget: BudgetState,
    ) -> BranchConstructionResult:
        batch = self.gateway.invoke_structured(
            "v2.ideator.construct_branches",
            [
                {
                    "role": "user",
                    "content": (
                        "Construct three to five distinct, falsifiable research branches. "
                        "Do not claim novelty beyond the supplied literature access level. "
                        "Every initial branch must be directly runnable against the validated baseline. "
                        "Do not propose an ablation or replication that assumes an unobserved variant gain; "
                        "those are follow-ups selected only after experimental evidence exists. "
                        "Keep JSON keys, schema identifiers, enum values, code, file paths, Git commits, "
                        "model IDs, and metric keys unchanged. Write every human-readable string value "
                        "in Simplified Chinese by default."
                    ),
                }
            ],
            BranchProposalBatch,
            context={
                "problem": problem.model_dump(mode="json"),
                "baseline": baseline.model_dump(mode="json"),
                "literature": [item.model_dump(mode="json") for item in papers],
                "frontier": frontier.model_dump(mode="json"),
                "known_failures": known_failures,
                "budget": budget.model_dump(mode="json"),
            },
        )
        accepted: list[ResearchBranch] = []
        rejected: list[BranchGateRejection] = []
        for index, proposal in enumerate(batch.proposals, 1):
            reasons: list[str] = []
            if not proposal.initially_runnable:
                reasons.append("proposal is not directly runnable against the current baseline")
            if proposal.required_prior_evidence:
                reasons.append(
                    "requires prior evidence: " + "; ".join(proposal.required_prior_evidence)
                )
            if reasons:
                rejected.append(
                    BranchGateRejection(
                        hypothesis=proposal.hypothesis,
                        reason="; ".join(reasons),
                    )
                )
            else:
                accepted.append(self._branch(proposal, index))
        return BranchConstructionResult(accepted=accepted, rejected=rejected)

    def _branch(self, proposal: BranchProposal, index: int) -> ResearchBranch:
        def heuristic(level: str, name: str) -> Estimate:
            return Estimate(
                value=self.QUALITATIVE[level],
                known=True,
                estimation_method="model qualitative heuristic; not a calibrated probability",
                provenance=[f"branch_proposal_{index}:{name}"],
            )

        unknown = lambda name: Estimate(
            value=None,
            known=False,
            estimation_method=f"{name} awaits empirical evidence",
        )
        return ResearchBranch(
            research_gap=proposal.research_gap,
            hypothesis=proposal.hypothesis,
            mechanism=proposal.mechanism,
            proposed_change=proposal.proposed_change,
            expected_observation=proposal.expected_observation,
            falsification_condition=proposal.falsification_condition,
            minimal_experiment=proposal.minimal_experiment,
            closest_prior_work=proposal.closest_prior_work,
            novelty_risk=proposal.novelty_risk,
            priority_components=PriorityComponents(
                expected_information_gain=heuristic(proposal.information_gain, "information_gain"),
                scientific_potential=heuristic(proposal.scientific_potential, "scientific_potential"),
                evidence_support=unknown("evidence support"),
                novelty_potential=unknown("novelty potential"),
                expected_improvement=unknown("expected improvement"),
                expected_uncertainty_reduction=heuristic(proposal.information_gain, "uncertainty_reduction"),
                compute_cost=Estimate(
                    value=min(1.0, proposal.estimated_compute_minutes / 240.0),
                    known=True,
                    estimation_method="proposal compute estimate normalized to four hours",
                    provenance=[f"branch_proposal_{index}:estimated_compute_minutes"],
                ),
                risk=heuristic(proposal.risk, "risk"),
                redundancy=unknown("redundancy"),
            ),
            estimated_cost=BudgetCost(
                experiments=1,
                compute_minutes=proposal.estimated_compute_minutes,
                model_calls=0,
            ),
            risk=proposal.risk,
        )
