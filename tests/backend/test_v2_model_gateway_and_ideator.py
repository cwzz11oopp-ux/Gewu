from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.models.gateway import LegacyQwenAdapter
from backend.app.research import (
    BaselineProfile,
    BaselineReproductionStatus,
    BudgetState,
    DatasetIdentity,
    ExperimentProtocol,
    MetricDefinition,
    MetricDirection,
    ProblemProfile,
    ResearchFrontier,
    SeedPolicy,
    TrainingBudget,
)
from backend.app.research.ideator import BranchConstructor


class ProposalProvider:
    mode = "fixture"
    fallback = False

    def __init__(self, *, incomplete: bool = False) -> None:
        self.incomplete = incomplete
        self.calls = []

    def generate_json(self, task, inputs, schema_hint, instructions=""):
        self.calls.append((task, inputs, schema_hint, instructions))
        proposal = {
            "research_gap": "The baseline lacks adaptive calibration.",
            "hypothesis": "Adaptive calibration improves the primary metric.",
            "mechanism": "Calibration corrects systematic score bias.",
            "proposed_change": "Add a train-only calibration parameter.",
            "expected_observation": "Accuracy increases under the locked test protocol.",
            "falsification_condition": "Two seeds fail to improve accuracy.",
            "minimal_experiment": "Run two locked seeds with calibration only.",
            "closest_prior_work": ["paper_fixture"],
            "novelty_risk": "Prior calibration work may be close.",
            "information_gain": "high",
            "scientific_potential": "medium",
            "estimated_compute_minutes": 2,
            "risk": "low",
            "initially_runnable": True,
            "required_prior_evidence": [],
        }
        second = dict(proposal)
        second.update(
            {
                "research_gap": "The baseline representation may discard margin information.",
                "hypothesis": "A margin feature improves generalization.",
                "mechanism": "The margin exposes distance from the decision boundary.",
                "proposed_change": "Append one normalized margin feature.",
                "closest_prior_work": ["paper_fixture_2"],
                "information_gain": "medium",
            }
        )
        if self.incomplete:
            proposal.pop("falsification_condition")
        third = dict(proposal)
        third.update(
            {
                "research_gap": "The baseline may be sensitive to one boundary example.",
                "hypothesis": "A train-only robust threshold improves the locked metric.",
                "mechanism": "Robust calibration reduces boundary sensitivity.",
                "proposed_change": "Estimate one robust threshold from training values.",
                "closest_prior_work": ["paper_fixture_3"],
                "information_gain": "medium",
            }
        )
        return {
            "proposals": [proposal, second, third],
            "provider_mode": "qwen",
            "fallback_used": False,
            "model_used": "qwen-test",
            "model_route": "reasoning",
            "model_fallback_used": False,
            "model_fallback_reason": "",
            "thinking_enabled": True,
            "json_repaired": False,
            "shape_normalized": False,
        }


def protocol():
    return ExperimentProtocol(
        task="classification",
        dataset=DatasetIdentity(
            name="fixture", version="1", source="test", fingerprint="fixture-v1"
        ),
        split={"train": "a", "test": "b"},
        preprocessing={"scale": "unit"},
        metrics=[
            MetricDefinition(
                name="accuracy",
                direction=MetricDirection.MAXIMIZE,
                definition="correct / total",
                aggregation="mean",
            )
        ],
        training_budget=TrainingBudget(epochs=1),
        evaluation_protocol={"checkpoint": "last"},
        seed_policy=SeedPolicy(seeds=[1, 2], aggregation="mean", minimum_repetitions=2),
        training_controls={"optimizer": "sgd"},
    )


def inputs():
    active = protocol()
    problem = ProblemProfile(
        question="Improve the fixture baseline",
        task=active.task,
        repository="fixture-repo",
        dataset=active.dataset,
        success_criteria=["audited improvement"],
    )
    baseline = BaselineProfile(
        repository=problem.repository,
        commit="base",
        task=active.task,
        dataset=active.dataset,
        entrypoint="train.py",
        environment={"python": "3.12"},
        protocol=active,
        local_metrics={"accuracy": 0.8},
        seeds=[1, 2],
        reproduction_status=BaselineReproductionStatus.VALIDATED,
        validation_reason="fixture",
        audit_passed=True,
    )
    budget = BudgetState(
        experiment_limit=4, compute_minutes_limit=10, model_call_limit=4
    )
    return problem, baseline, budget


def test_provider_neutral_gateway_builds_multiple_complete_branches():
    problem, baseline, budget = inputs()
    provider = ProposalProvider()
    branches = BranchConstructor(LegacyQwenAdapter(provider)).construct(
        problem,
        baseline,
        papers=[],
        frontier=ResearchFrontier(),
        known_failures=[],
        budget=budget,
    )
    assert len(branches) == 3
    assert branches[0].falsification_condition
    assert branches[0].priority_components.expected_information_gain.value == 0.75
    assert "not a calibrated probability" in (
        branches[0].priority_components.expected_information_gain.estimation_method
    )
    assert provider.calls[0][0] == "v2.ideator.construct_branches"
    prompt = provider.calls[0][1]["messages"][0]["content"]
    assert "Simplified Chinese" in prompt
    assert "Keep JSON keys" in prompt


def test_incomplete_idea_is_rejected_before_it_reaches_frontier():
    problem, baseline, budget = inputs()
    constructor = BranchConstructor(LegacyQwenAdapter(ProposalProvider(incomplete=True)))
    with pytest.raises(ValidationError, match="falsification_condition"):
        constructor.construct(
            problem,
            baseline,
            papers=[],
            frontier=ResearchFrontier(),
            known_failures=[],
            budget=budget,
        )
