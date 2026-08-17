from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from backend.app.baseline import BaselineReproducer, BaselineReproductionRequest
from backend.app.research import (
    BaselineReproductionStatus,
    DatasetIdentity,
    ExperimentProtocol,
    MetricDefinition,
    MetricDirection,
    SeedPolicy,
    TrainingBudget,
)
from backend.app.workspace import RepositoryWorkspace


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "v2_demo_repo"


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "baseline-repo"
    shutil.copytree(FIXTURE, root)
    git(root, "init")
    git(root, "config", "user.name", "V2 Fixture")
    git(root, "config", "user.email", "v2-fixture@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline fixture")
    return root, git(root, "rev-parse", "HEAD")


def active_protocol() -> ExperimentProtocol:
    return ExperimentProtocol(
        task="binary classification",
        dataset=DatasetIdentity(
            name="tiny-threshold-data",
            version="1",
            source="tracked demo fixture",
            fingerprint="tiny-threshold-v1",
        ),
        split={"train": "embedded fixture", "test": "embedded fixture"},
        preprocessing={"transform": "none"},
        metrics=[
            MetricDefinition(
                name="accuracy",
                direction=MetricDirection.MAXIMIZE,
                definition="correct / total",
                aggregation="deterministic evaluation",
            )
        ],
        training_budget=TrainingBudget(max_steps=1),
        evaluation_protocol={"evaluation": "all five tracked examples"},
        seed_policy=SeedPolicy(seeds=[11, 22], aggregation="deterministic mean", minimum_repetitions=2),
        training_controls={"runtime": "deterministic-stdlib"},
    )


def request(root: Path, commit: str, *, fingerprint: str | None = None):
    protocol = active_protocol()
    return BaselineReproductionRequest(
        repository=str(root),
        commit=commit,
        task=protocol.task,
        entrypoint="train.py",
        protocol=protocol,
        command=[
            sys.executable,
            "train.py",
            "--output",
            "baseline-result.json",
            "--protocol-fingerprint",
            fingerprint or protocol.fingerprint().value,
            "--seeds",
            "11",
            "22",
        ],
        result_path="baseline-result.json",
        environment={"python": sys.version.split()[0], "device": "cpu"},
        reported_metrics={"accuracy": 0.8},
        tolerance=1e-12,
    )


def test_baseline_reproduction_uses_real_local_metrics_as_denominator(tmp_path):
    root, commit = repository(tmp_path)
    workspace = RepositoryWorkspace(root, allowed_executables={Path(sys.executable).name})
    profile = BaselineReproducer(workspace).reproduce_and_validate(
        request(root, commit)
    )
    assert profile.reproduction_status == BaselineReproductionStatus.VALIDATED
    assert profile.local_metrics == {"accuracy": 0.8}
    assert profile.reported_metrics == {"accuracy": 0.8}
    assert profile.can_be_comparison_denominator is True
    assert "Local metrics are the comparison denominator" in profile.validation_reason


def test_baseline_reproduction_rejects_result_with_wrong_protocol_fingerprint(tmp_path):
    root, commit = repository(tmp_path)
    workspace = RepositoryWorkspace(root, allowed_executables={Path(sys.executable).name})
    profile = BaselineReproducer(workspace).reproduce_and_validate(
        request(root, commit, fingerprint="wrong-fingerprint")
    )
    assert profile.reproduction_status == BaselineReproductionStatus.PROTOCOL_MISMATCH
    assert profile.can_be_comparison_denominator is False
    assert profile.local_metrics == {"accuracy": 0.8}
