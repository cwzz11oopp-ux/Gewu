from __future__ import annotations

import json

import pytest

from backend.app.graph import ResearchGraph, ResearchGraphStatus
from backend.app.research import (
    BaselineProfile,
    BaselineReproductionStatus,
    BudgetCost,
    BudgetState,
    DatasetIdentity,
    Estimate,
    ExperimentProtocol,
    ExperimentRecord,
    ExperimentResultStatus,
    MetricDefinition,
    MetricDirection,
    PriorityComponents,
    ProblemProfile,
    ProtocolCompatibilityGate,
    ResearchBranch,
    ResearchFrontier,
    ResearchOperator,
    SeedPolicy,
    TrainingBudget,
)
from backend.app.state import ResearchState


def make_state() -> ResearchState:
    protocol = ExperimentProtocol(
        task="classification",
        dataset=DatasetIdentity(
            name="graph-fixture", version="1", source="test", fingerprint="graph-v1"
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
    estimate = lambda value: Estimate(
        value=value,
        known=True,
        estimation_method="graph fixture",
        provenance=["fixture"],
    )
    branch = ResearchBranch(
        id="h1",
        research_gap="The graph fixture has one unresolved mechanism.",
        hypothesis="The fixture variant improves accuracy.",
        mechanism="The variant corrects one systematic error.",
        proposed_change="Apply the fixture variant.",
        expected_observation="Accuracy exceeds 0.5.",
        falsification_condition="Replication fails to exceed 0.5.",
        minimal_experiment="Two deterministic seeds.",
        closest_prior_work=["fixture"],
        novelty_risk="Not a scientific novelty claim.",
        priority_components=PriorityComponents(
            expected_information_gain=estimate(0.9),
            scientific_potential=estimate(0.8),
            evidence_support=Estimate(
                value=None, known=False, estimation_method="awaiting experiment"
            ),
            novelty_potential=estimate(0.5),
            expected_improvement=estimate(0.7),
            expected_uncertainty_reduction=estimate(0.9),
            compute_cost=estimate(0.1),
            risk=estimate(0.1),
            redundancy=estimate(0.1),
        ),
        estimated_cost=BudgetCost(experiments=1, compute_minutes=1),
        risk="low",
    )
    problem = ProblemProfile(
        question="Exercise graph resume",
        task=protocol.task,
        repository="fixture",
        dataset=protocol.dataset,
        success_criteria=["two state-driven actions"],
    )
    baseline = BaselineProfile(
        repository="fixture",
        commit="base",
        task=protocol.task,
        dataset=protocol.dataset,
        entrypoint="train.py",
        environment={"kind": "test"},
        protocol=protocol,
        local_metrics={"accuracy": 0.5},
        seeds=[1, 2],
        reproduction_status=BaselineReproductionStatus.VALIDATED,
        validation_reason="fixture",
        audit_passed=True,
    )
    return ResearchState(
        session_id="graph_session",
        problem=problem,
        baseline=baseline,
        frontier=ResearchFrontier(branches=[branch]),
        budget=BudgetState(
            experiment_limit=3, compute_minutes_limit=5, model_call_limit=2
        ),
    )


class Executor:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, action, state):
        self.calls += 1
        protocol = state.baseline.protocol
        return ExperimentRecord(
            experiment_id=f"graph_exp_{self.calls}",
            branch_id=action.branch_id,
            purpose=str(action.operator),
            repository="fixture",
            base_commit="base",
            code_commit=f"commit_{self.calls}",
            protocol=protocol,
            protocol_fingerprint=protocol.fingerprint(),
            config={"iteration": self.calls},
            seeds=[1, 2],
            metrics={"accuracy": 0.6},
            baseline_metrics={"accuracy": 0.5},
            comparison=ProtocolCompatibilityGate.evaluate(
                protocol, protocol, audit_passed=True
            ),
            audit_passed=True,
            environment={"kind": "test"},
            result_status=ExperimentResultStatus.SUCCEEDED,
        )


def test_graph_cycles_and_resumes_from_persisted_state(tmp_path):
    executor = Executor()
    first_process = ResearchGraph(str(tmp_path))
    checkpoint = first_process.start(make_state())
    first = first_process.step("graph_session", executor)
    assert checkpoint.status == ResearchGraphStatus.READY
    assert first.action.operator == ResearchOperator.RUN_EXPERIMENT

    second_process = ResearchGraph(str(tmp_path))
    resumed = second_process.resume(
        "graph_session", executor, max_iterations=1
    )
    assert resumed[0].action.operator == ResearchOperator.RUN_REPLICATION
    final = second_process.stores.states.get("graph_session")
    assert final.iteration == 2
    assert len(second_process.stores.experiments.list("graph_session")) == 2
    assert len(second_process.stores.evidence.list("graph_session")) == 2

    raw_checkpoint = json.loads(
        (tmp_path / "v2-graph-checkpoints.json").read_text(encoding="utf-8")
    )["graph_session"]
    assert "experiments" not in raw_checkpoint
    assert "evidence" not in raw_checkpoint
    assert "metrics" not in raw_checkpoint


def test_pending_action_requires_explicit_runtime_recovery(tmp_path):
    graph = ResearchGraph(str(tmp_path))
    graph.start(make_state())

    def interrupted(action, state):
        raise RuntimeError("simulated process interruption")

    with pytest.raises(RuntimeError, match="simulated process interruption"):
        graph.step("graph_session", interrupted)
    assert graph.checkpoints.get("graph_session").status == ResearchGraphStatus.ACTION_PENDING

    with pytest.raises(RuntimeError, match="GRAPH_ACTION_RECOVERY_REQUIRED"):
        ResearchGraph(str(tmp_path)).resume(
            "graph_session", Executor(), max_iterations=1
        )

    recovered_record = Executor()
    resumed = ResearchGraph(str(tmp_path)).resume(
        "graph_session",
        Executor(),
        max_iterations=1,
        recover_pending=lambda action, state: recovered_record(action, state),
    )
    assert resumed[0].action.operator == ResearchOperator.RUN_EXPERIMENT
    assert resumed[0].state.iteration == 1
