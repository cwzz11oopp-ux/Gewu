from __future__ import annotations

from backend.app.experiment import ExperimentContract, ExperimentExecutionAdapter
from backend.app.models.experiment import ExperimentBundle, ExperimentFile, ExperimentManifest
from backend.app.providers.experiment import MockExperimentProvider
from backend.app.research import (
    ComparisonDecision,
    DatasetIdentity,
    ExperimentProtocol,
    MetricDefinition,
    MetricDirection,
    SeedPolicy,
    TrainingBudget,
)


def protocol() -> ExperimentProtocol:
    return ExperimentProtocol(
        task="classification",
        dataset=DatasetIdentity(
            name="fixture-data",
            version="1",
            source="test",
            fingerprint="fixture-v1",
        ),
        split={"train": "train", "test": "test"},
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
        seed_policy=SeedPolicy(seeds=[7], aggregation="mean"),
        training_controls={"optimizer": "sgd"},
    )


def bundle() -> ExperimentBundle:
    return ExperimentBundle(
        manifest=ExperimentManifest(
            run_id="run_fixture",
            experiment_id="experiment_1",
            result_id="experiment_1_result",
            dataset="fixture-data",
            expected_metrics=["accuracy"],
            seeds=[7],
        ),
        files=[ExperimentFile(path="train.py", content="print('fixture')")],
    )


def contract() -> ExperimentContract:
    active = protocol()
    return ExperimentContract(
        branch_id="h1",
        purpose="exercise the adapter",
        repository="fixture-repo",
        base_commit="base123",
        protocol=active,
        baseline_protocol=active,
        baseline_metrics={"accuracy": 0.4},
        config={"learning_rate": 0.1},
        bundle=bundle(),
    )


class RealFixtureProvider(MockExperimentProvider):
    def run(self, task, code=None):
        result = super().run(task, code)
        result["provider"] = "fixture_real"
        result["is_real_experiment"] = True
        result["metrics"] = {"accuracy": 0.6}
        result["attempts"] = [{"status": "completed", "log_path": "fixture.log"}]
        return result


def test_adapter_reuses_legacy_execution_and_produces_audited_v2_record():
    record = ExperimentExecutionAdapter(RealFixtureProvider()).execute(contract())
    assert record.experiment_id == "experiment_1"
    assert record.branch_id == "h1"
    assert record.metrics == {"accuracy": 0.6}
    assert record.audit_passed is True
    assert record.comparison.decision == ComparisonDecision.ALLOWED
    assert record.improvement_claim_allowed is True
    assert record.logs == ["fixture.log"]
    assert record.changed_files == ["train.py"]


def test_mock_runtime_result_is_recorded_but_cannot_support_improvement_claim():
    record = ExperimentExecutionAdapter(MockExperimentProvider()).execute(contract())
    assert record.metrics == {"accuracy": 0.5}
    assert record.audit_passed is False
    assert record.comparison.decision == ComparisonDecision.NOT_ALLOWED
    assert record.improvement_claim_allowed is False
