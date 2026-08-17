from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.research import (
    BaselineProfile,
    BaselineReproductionStatus,
    ComparisonDecision,
    DatasetIdentity,
    ExperimentProtocol,
    MetricDefinition,
    MetricDirection,
    ProtocolCompatibilityGate,
    SeedPolicy,
    TrainingBudget,
)


def protocol(**updates) -> ExperimentProtocol:
    values = {
        "task": "classification",
        "dataset": DatasetIdentity(
            name="demo-dataset",
            version="1",
            source="fixture",
            fingerprint="dataset-sha256",
        ),
        "split": {"train": "train-v1", "test": "test-v1"},
        "preprocessing": {"normalize": [0.5, 0.5]},
        "metrics": [
            MetricDefinition(
                name="accuracy",
                direction=MetricDirection.MAXIMIZE,
                definition="correct predictions divided by examples",
                aggregation="mean over seeds",
            )
        ],
        "training_budget": TrainingBudget(epochs=2),
        "evaluation_protocol": {"checkpoint": "best-validation", "test_runs": 1},
        "seed_policy": SeedPolicy(seeds=[1, 2], aggregation="mean", minimum_repetitions=2),
        "training_controls": {"optimizer": "sgd", "batch_size": 8},
    }
    values.update(updates)
    return ExperimentProtocol(**values)


def test_protocol_fingerprint_is_deterministic_for_canonical_content():
    first = protocol(
        split={"train": "train-v1", "test": "test-v1"},
        training_controls={"optimizer": "sgd", "batch_size": 8},
    )
    second = protocol(
        split={"test": "test-v1", "train": "train-v1"},
        training_controls={"batch_size": 8, "optimizer": "sgd"},
    )
    assert first.fingerprint() == second.fingerprint()
    assert len(first.fingerprint().value) == 64


def test_protocol_gate_rejects_split_mismatch_and_preserves_observation_status():
    baseline = protocol()
    variant = protocol(split={"train": "train-v2", "test": "test-v1"})
    result = ProtocolCompatibilityGate.evaluate(
        baseline, variant, audit_passed=True
    )
    assert result.compatible is False
    assert result.decision == ComparisonDecision.NOT_ALLOWED
    assert result.mismatches == ["split"]
    assert result.improvement_claim_allowed is False


def test_protocol_gate_requires_compatibility_and_audit_for_claim():
    active = protocol()
    failed_audit = ProtocolCompatibilityGate.evaluate(
        active, active, audit_passed=False
    )
    allowed = ProtocolCompatibilityGate.evaluate(active, active, audit_passed=True)
    assert failed_audit.compatible is True
    assert failed_audit.decision == ComparisonDecision.NOT_ALLOWED
    assert allowed.decision == ComparisonDecision.ALLOWED
    assert allowed.improvement_claim_allowed is True


def test_validated_baseline_requires_audited_local_metrics_and_seeds():
    active = protocol()
    with pytest.raises(ValidationError, match="VALIDATED_BASELINE_LOCAL_METRICS_REQUIRED"):
        BaselineProfile(
            repository="fixture-repo",
            commit="abc123",
            task=active.task,
            dataset=active.dataset,
            entrypoint="train.py",
            environment={"python": "3.12"},
            protocol=active,
            reproduction_status=BaselineReproductionStatus.VALIDATED,
            audit_passed=True,
        )

    baseline = BaselineProfile(
        repository="fixture-repo",
        commit="abc123",
        task=active.task,
        dataset=active.dataset,
        entrypoint="train.py",
        environment={"python": "3.12"},
        protocol=active,
        reported_metrics={"accuracy": 0.8},
        local_metrics={"accuracy": 0.78},
        seeds=[1, 2],
        reproduction_status=BaselineReproductionStatus.VALIDATED,
        validation_reason="Local baseline passed the protocol and audit gates",
        audit_passed=True,
    )
    assert baseline.can_be_comparison_denominator is True
    assert baseline.local_metrics["accuracy"] == 0.78
