from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.experiment import (
    ParameterSweepRun,
    ParameterSweepRunner,
    RepositoryExperimentContract,
    WorkspaceExperimentAdapter,
)
from backend.app.research import (
    BudgetState,
    ClaimEvidenceGraph,
    ClaimStatus,
    DatasetIdentity,
    EvidenceRelation,
    ExperimentProtocol,
    MetricDefinition,
    MetricDirection,
    ProblemProfile,
    SeedPolicy,
    TrainingBudget,
)
from backend.app.state import ResearchState
from backend.app.workspace import WorktreeManager


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "v2_demo_repo"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def protocol() -> ExperimentProtocol:
    dataset = DatasetIdentity(
        name="tiny-threshold-data",
        version="1",
        source="tracked deterministic repository fixture",
        fingerprint="tiny-threshold-v1",
    )
    return ExperimentProtocol(
        task="binary classification",
        dataset=dataset,
        split={"train": "embedded locked examples", "test": "embedded locked examples"},
        preprocessing={"transform": "none"},
        metrics=[
            MetricDefinition(
                name="accuracy",
                direction=MetricDirection.MAXIMIZE,
                definition="correct predictions / five locked examples",
                aggregation="deterministic evaluation",
            )
        ],
        training_budget=TrainingBudget(max_steps=1),
        evaluation_protocol={"evaluation": "all five tracked examples"},
        seed_policy=SeedPolicy(
            seeds=[11, 22], aggregation="deterministic mean", minimum_repetitions=2
        ),
        training_controls={"runtime": "deterministic Python stdlib"},
    )


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "sweep-repo"
    shutil.copytree(FIXTURE, root)
    git(root, "init")
    git(root, "config", "user.name", "V2 Sweep Test")
    git(root, "config", "user.email", "v2-sweep@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    return root, git(root, "rev-parse", "HEAD")


def model_source(threshold: float) -> str:
    return (
        f"DEFAULT_THRESHOLD = {threshold!r}\n\n\n"
        "def predict(value: float, threshold: float = DEFAULT_THRESHOLD) -> int:\n"
        "    return int(value >= threshold)\n"
    )


def sweep_run(
    *, root: Path, base_commit: str, active: ExperimentProtocol, threshold: float
) -> ParameterSweepRun:
    slug = str(threshold).replace(".", "_")
    output = f"sweep-{slug}.json"
    return ParameterSweepRun(
        parameter_name="threshold",
        parameter_value=threshold,
        contract=RepositoryExperimentContract(
            experiment_id=f"sweep_{slug}",
            branch_id="threshold_branch",
            worktree_branch=f"v2sweep/threshold-{slug}",
            purpose=f"Evaluate threshold={threshold} without adaptive selection.",
            repository=str(root),
            base_commit=base_commit,
            protocol=active,
            baseline_protocol=active,
            baseline_metrics={"accuracy": 0.8},
            config={"threshold": threshold, "study": "parameter_sweep"},
            implementation_files=(
                {} if threshold == 0.5 else {"model.py": model_source(threshold)}
            ),
            static_commands=[[sys.executable, "-m", "py_compile", "model.py", "train.py"]],
            smoke_commands=[[sys.executable, "-m", "pytest", "-q", "test_model.py"]],
            formal_command=[
                sys.executable,
                "train.py",
                "--output",
                output,
                "--protocol-fingerprint",
                active.fingerprint().value,
                "--seeds",
                "11",
                "22",
            ],
            result_path=output,
            environment={"python": sys.version.split()[0], "device": "cpu"},
            commit_message=f"experiment(threshold): sweep {threshold}",
            cleanup_worktree=False,
        ),
    )


def test_parameter_sweep_runs_every_value_under_one_protocol(tmp_path):
    root, base_commit = make_repo(tmp_path)
    active = protocol()
    values = [0.1, 0.2, 0.3, 0.4, 0.5]
    result = ParameterSweepRunner(
        WorkspaceExperimentAdapter(WorktreeManager(root, tmp_path / "worktrees"))
    ).run(
        [
            sweep_run(
                root=root,
                base_commit=base_commit,
                active=active,
                threshold=value,
            )
            for value in values
        ]
    )

    assert [point.parameter_value for point in result.points] == values
    assert [point.metric_value for point in result.points] == [1.0, 1.0, 0.8, 0.8, 0.8]
    assert [point.baseline_delta for point in result.points] == pytest.approx(
        [0.2, 0.2, 0.0, 0.0, 0.0]
    )
    assert result.stable_improvement_intervals == [(0.1, 0.2)]
    assert all(point.protocol_compatible for point in result.points)
    assert all(point.audit_passed for point in result.points)
    assert {record.protocol_fingerprint.value for record in result.records} == {
        active.fingerprint().value
    }
    evidence = result.evidence_units(branch_id="threshold_branch")
    assert len(evidence) == 5
    assert all(item.verified for item in evidence)

    state = ResearchState(
        problem=ProblemProfile(
            question="Does threshold calibration improve the locked fixture?",
            task=active.task,
            repository=str(root),
            dataset=active.dataset,
            success_criteria=["audited parameter response"],
        ),
        budget=BudgetState(
            experiment_limit=10,
            compute_minutes_limit=10,
            model_call_limit=2,
        ),
        experiments=result.records,
        evidence=evidence,
    )
    graph = ClaimEvidenceGraph.from_parameter_sweep(
        state, result, branch_id="threshold_branch"
    )
    assert [claim.status for claim in graph.claims] == [
        ClaimStatus.SUPPORTED,
        ClaimStatus.PARTIALLY_SUPPORTED,
        ClaimStatus.NOT_SUPPORTED,
    ]
    assert graph.claims[0].supporting_experiments == ["sweep_0_1", "sweep_0_2"]
    uniqueness = graph.claims[2]
    tied_alternative = next(
        link for link in uniqueness.links if link.experiment_id == "sweep_0_1"
    )
    assert tied_alternative.relation == EvidenceRelation.CONTRADICT
    assert uniqueness.contradicting_evidence == [tied_alternative.evidence_id]
    assert graph.audit().exportable is True


def test_parameter_sweep_rejects_protocol_drift_before_execution(tmp_path):
    root, base_commit = make_repo(tmp_path)
    active = protocol()
    drifted = active.model_copy(
        update={
            "preprocessing": {"transform": "changed"},
        }
    )
    good = sweep_run(root=root, base_commit=base_commit, active=active, threshold=0.1)
    bad = sweep_run(root=root, base_commit=base_commit, active=drifted, threshold=0.2)
    runner = ParameterSweepRunner(
        WorkspaceExperimentAdapter(WorktreeManager(root, tmp_path / "worktrees"))
    )

    with pytest.raises(ValueError, match="PARAMETER_SWEEP_PROTOCOL_DRIFT"):
        runner.run([good, bad])
