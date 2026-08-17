from __future__ import annotations

from backend.app.controller import ResearchController, ResearchLoop
from backend.app.research import (
    BaselineProfile,
    BaselineReproductionStatus,
    BranchStatus,
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
    ResearchAction,
    ResearchFrontier,
    ResearchOperator,
    SeedPolicy,
    TrainingBudget,
)
from backend.app.state import ResearchState
from backend.app.services.v2_critic import CriticDecisionService, ScientificCritique


def protocol() -> ExperimentProtocol:
    return ExperimentProtocol(
        task="classification",
        dataset=DatasetIdentity(
            name="tiny-fixture",
            version="1",
            source="generated-test-fixture",
            fingerprint="fixture-dataset-v1",
        ),
        split={"train": [0, 79], "test": [80, 99]},
        preprocessing={"scale": "zero_to_one"},
        metrics=[
            MetricDefinition(
                name="accuracy",
                direction=MetricDirection.MAXIMIZE,
                definition="correct divided by total",
                aggregation="mean over seeds",
            )
        ],
        training_budget=TrainingBudget(epochs=2),
        evaluation_protocol={"checkpoint": "last", "test_passes": 1},
        seed_policy=SeedPolicy(
            seeds=[11, 22], aggregation="mean", minimum_repetitions=2
        ),
        training_controls={"optimizer": "sgd", "batch_size": 8},
    )


def estimate(value: float) -> Estimate:
    return Estimate(
        value=value,
        known=True,
        estimation_method="deterministic integration fixture",
        provenance=["fixture-contract"],
    )


def components(info: float, potential: float) -> PriorityComponents:
    return PriorityComponents(
        expected_information_gain=estimate(info),
        scientific_potential=estimate(potential),
        evidence_support=Estimate(
            value=None,
            known=False,
            estimation_method="not measured before first experiment",
        ),
        novelty_potential=estimate(0.7),
        expected_improvement=estimate(0.65),
        expected_uncertainty_reduction=estimate(0.8),
        compute_cost=estimate(0.2),
        risk=estimate(0.2),
        redundancy=estimate(0.1),
    )


def research_branch(branch_id: str, info: float, potential: float) -> ResearchBranch:
    return ResearchBranch(
        id=branch_id,
        research_gap=f"Uncertainty represented by {branch_id}",
        hypothesis=f"{branch_id} improves the primary metric",
        mechanism=f"{branch_id} changes a distinct causal mechanism",
        proposed_change=f"Apply the {branch_id} repository variant",
        expected_observation="accuracy changes under the locked protocol",
        falsification_condition="replicated accuracy does not exceed the local baseline",
        minimal_experiment="run the locked protocol on seeds 11 and 22",
        closest_prior_work=[f"paper-{branch_id}"],
        novelty_risk="closest work may already cover part of the mechanism",
        priority_components=components(info, potential),
        estimated_cost=BudgetCost(experiments=1, compute_minutes=2),
        risk="bounded fixture execution",
    )


def initial_state() -> ResearchState:
    active = protocol()
    problem = ProblemProfile(
        question="Which repository change improves generalization?",
        task=active.task,
        repository="fixture-repository",
        dataset=active.dataset,
        compute_constraints={"max_minutes": 10},
        success_criteria=["audited protocol-compatible improvement"],
        open_questions=["Which mechanism deserves replication?"],
    )
    baseline = BaselineProfile(
        repository=problem.repository,
        commit="base123",
        task=problem.task,
        dataset=problem.dataset,
        entrypoint="train.py",
        environment={"python": "3.12", "device": "cpu"},
        protocol=active,
        reported_metrics={"accuracy": 0.8},
        local_metrics={"accuracy": 0.78},
        seeds=[11, 22],
        reproduction_status=BaselineReproductionStatus.VALIDATED,
        validation_reason="Audited local run is the comparison denominator",
        audit_passed=True,
    )
    return ResearchState(
        problem=problem,
        baseline=baseline,
        frontier=ResearchFrontier(
            branches=[
                research_branch("h1", 0.95, 0.9),
                research_branch("h2", 0.7, 0.75),
                research_branch("h3", 0.55, 0.65),
            ]
        ),
        budget=BudgetState(
            experiment_limit=5,
            compute_minutes_limit=20,
            model_call_limit=5,
        ),
        open_questions=problem.open_questions,
    )


class SyntheticVerifiedExecutor:
    """Test-only deterministic executor; never used by application runtime."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, action, state) -> ExperimentRecord:
        self.calls += 1
        active = state.baseline.protocol
        comparison = ProtocolCompatibilityGate.evaluate(
            state.baseline.protocol,
            active,
            audit_passed=True,
        )
        metric = 0.795 if self.calls == 1 else 0.792
        return ExperimentRecord(
            experiment_id=f"fixture_exp_{self.calls}",
            branch_id=action.branch_id,
            purpose=f"Fixture execution for {action.operator}",
            repository=state.problem.repository,
            base_commit="base123",
            code_commit=f"variant{self.calls}",
            changed_files=["model.py"],
            diff_summary="Fixture-only one-line mechanism change",
            protocol=active,
            protocol_fingerprint=active.fingerprint(),
            config={"variant": action.branch_id, "operator": action.operator},
            seeds=[11, 22],
            metrics={"accuracy": metric},
            baseline_metrics=state.baseline.local_metrics,
            comparison=comparison,
            audit_passed=True,
            environment={"kind": "deterministic-test-fixture"},
            logs=[f"fixture-{self.calls}.log"],
            result_status=ExperimentResultStatus.SUCCEEDED,
            analysis="Synthetic test fixture only; not a scientific result.",
        )


def test_updated_state_causes_a_different_second_research_action():
    loop = ResearchLoop()
    executor = SyntheticVerifiedExecutor()
    first = loop.run_iteration(initial_state(), executor)
    second_action = ResearchController().next_action(first.state)

    assert first.action.branch_id == "h1"
    assert first.action.operator == ResearchOperator.RUN_EXPERIMENT
    assert first.state.frontier.get("h1").status == BranchStatus.PROMISING
    assert first.state.beliefs.get("h1").support_score > 0
    assert second_action.branch_id == "h1"
    assert second_action.operator == ResearchOperator.RUN_REPLICATION
    assert second_action.operator != first.action.operator
    assert "compatible audited evidence" in second_action.reason


def test_two_complete_iterations_update_evidence_belief_budget_and_frontier():
    loop = ResearchLoop()
    executor = SyntheticVerifiedExecutor()
    iterations = loop.run(initial_state(), executor, iterations=2)
    final = iterations[-1].state

    assert [item.action.operator for item in iterations] == [
        ResearchOperator.RUN_EXPERIMENT,
        ResearchOperator.RUN_REPLICATION,
    ]
    assert final.iteration == 2
    assert len(final.experiments) == 2
    assert len(final.evidence) == 2
    assert final.budget.experiments_used == 2
    assert final.frontier.get("h1").status == BranchStatus.VALIDATED
    assert final.beliefs.get("h1").support_score == 0.7
    assert all(record.improvement_claim_allowed for record in final.experiments)

    third = ResearchController().next_action(final)
    assert third.branch_id == "h2"
    assert third.operator == ResearchOperator.RUN_EXPERIMENT


def test_incompatible_result_is_kept_as_observation_without_improvement_claim():
    state = initial_state()
    action = ResearchController().next_action(state)
    incompatible_protocol = state.baseline.protocol.model_copy(
        update={"split": {"train": [0, 89], "test": [90, 99]}}
    )
    comparison = ProtocolCompatibilityGate.evaluate(
        state.baseline.protocol,
        incompatible_protocol,
        audit_passed=True,
    )
    record = ExperimentRecord(
        experiment_id="fixture_incompatible",
        branch_id=action.branch_id,
        purpose="Verify comparison gate",
        repository=state.problem.repository,
        protocol=incompatible_protocol,
        protocol_fingerprint=incompatible_protocol.fingerprint(),
        config={"variant": "h1"},
        seeds=[11, 22],
        metrics={"accuracy": 0.99},
        baseline_metrics=state.baseline.local_metrics,
        comparison=comparison,
        audit_passed=True,
        environment={"kind": "test"},
        result_status=ExperimentResultStatus.SUCCEEDED,
    )
    updated = ResearchLoop().updater.apply_experiment(state, action, record)
    assert record.improvement_claim_allowed is False
    assert updated.frontier.get("h1").status == BranchStatus.INCONCLUSIVE
    assert "COMPARISON_NOT_ALLOWED" in updated.evidence[-1].claim


def test_controller_combines_critic_follow_up_with_belief_uncertainty():
    first = ResearchLoop().run_iteration(initial_state(), SyntheticVerifiedExecutor())
    branch = first.state.frontier.get("h1").model_copy(
        update={
            "status": BranchStatus.INCONCLUSIVE,
            "next_actions": [ResearchOperator.RUN_ABLATION],
        }
    )
    state = first.state.model_copy(
        update={"frontier": first.state.frontier.replace(branch)}
    )
    action = ResearchController().next_action(state)
    assert action.branch_id == "h1"
    assert action.operator == ResearchOperator.RUN_ABLATION


def test_ablation_drop_supports_mechanism_without_validating_whole_branch():
    loop = ResearchLoop()
    first = loop.run_iteration(initial_state(), SyntheticVerifiedExecutor())
    state = first.state
    action = ResearchAction(
        operator=ResearchOperator.RUN_ABLATION,
        branch_id="h1",
        reason="Remove the mechanism to test whether the gain disappears.",
        target_information_gap="Isolate the mechanism.",
        expected_information_gain=0.8,
        estimated_cost=BudgetCost(experiments=1, compute_minutes=2),
        prerequisites=["supportive variant"],
        completion_criteria=["Ablation changes the primary metric"],
        decision_iteration=state.iteration,
    )
    active = state.baseline.protocol
    record = ExperimentRecord(
        experiment_id="fixture_ablation",
        branch_id="h1",
        purpose="Remove h1 mechanism",
        repository=state.problem.repository,
        base_commit="variant1",
        code_commit="ablation1",
        changed_files=["model.py"],
        diff_summary="Remove the mechanism only",
        protocol=active,
        protocol_fingerprint=active.fingerprint(),
        config={"operator": ResearchOperator.RUN_ABLATION},
        seeds=[11, 22],
        metrics={"accuracy": 0.78},
        baseline_metrics=state.baseline.local_metrics,
        comparison=ProtocolCompatibilityGate.evaluate(active, active, audit_passed=True),
        audit_passed=True,
        environment={"kind": "deterministic-test-fixture"},
        result_status=ExperimentResultStatus.SUCCEEDED,
    )
    updated = loop.updater.apply_experiment(state, action, record)
    assert updated.frontier.get("h1").status == BranchStatus.PROMISING
    assert updated.frontier.get("h1").next_actions[0] == ResearchOperator.RUN_REPLICATION
    assert "removing the mechanism reduced performance" in updated.evidence[-1].claim
    assert updated.beliefs.get("h1").support_score == 0.6


def test_completed_ablation_is_not_selected_again_after_critic_recommends_robustness():
    state = initial_state()
    branch = state.frontier.get("h1").model_copy(
        update={
            "status": BranchStatus.PROMISING,
            "experiment_ids": ["main", "ablation"],
            "next_actions": [ResearchOperator.RUN_ABLATION],
        }
    )
    completed_ablation = ResearchAction(
        operator=ResearchOperator.RUN_ABLATION,
        branch_id="h1",
        reason="Completed mechanism ablation.",
        target_information_gap="Was the gain caused by the mechanism?",
        expected_information_gain=0.8,
        estimated_cost=BudgetCost(experiments=1, compute_minutes=1),
        completion_criteria=["Ablation result recorded"],
        decision_iteration=1,
    )
    state = state.model_copy(
        update={
            "frontier": state.frontier.replace(branch),
            "action_history": [completed_ablation],
        }
    )
    critique = ScientificCritique(
        supported_claims=["The locked result supports a calibration effect."],
        unsupported_claims=["External robustness is not established."],
        possible_mechanisms=["Decision-boundary shift."],
        open_information_gaps=["Does the result survive data perturbation?"],
        recommended_actions=[
            ResearchOperator.RUN_ABLATION,
            ResearchOperator.RUN_ROBUSTNESS,
        ],
    )

    updated = CriticDecisionService().apply(
        state, "h1", critique, consume_model_call=False
    )
    action = ResearchController().next_action(updated)

    assert updated.frontier.get("h1").next_actions == [
        ResearchOperator.RUN_ROBUSTNESS
    ]
    assert action.operator == ResearchOperator.RUN_ROBUSTNESS
