from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from backend.app.baseline import BaselineReproducer, BaselineReproductionRequest
from backend.app.controller import ResearchLoop
from backend.app.experiment import (
    GeneralRepositoryExperimentContract,
    GeneralRepositoryImplementationPlanner,
    PlannedRepositoryExperimentAdapter,
    RepositoryExperimentContract,
    WorkspaceExperimentAdapter,
)
from backend.app.research import (
    BranchStatus,
    BudgetCost,
    BudgetState,
    DatasetIdentity,
    Estimate,
    ExperimentProtocol,
    MetricDefinition,
    MetricDirection,
    PriorityComponents,
    ProblemProfile,
    ResearchBranch,
    ResearchFrontier,
    ResearchOperator,
    ResearchAction,
    SeedPolicy,
    TrainingBudget,
)
from backend.app.state import ResearchState
from backend.app.workspace import RepositoryWorkspace, WorktreeManager


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "v2_demo_repo"


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def make_repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "research-repo"
    shutil.copytree(FIXTURE, root)
    git(root, "init")
    git(root, "config", "user.name", "V2 E2E Fixture")
    git(root, "config", "user.email", "v2-e2e@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    return root, git(root, "rev-parse", "HEAD")


def protocol() -> ExperimentProtocol:
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
        seed_policy=SeedPolicy(
            seeds=[11, 22], aggregation="deterministic mean", minimum_repetitions=2
        ),
        training_controls={"runtime": "deterministic-stdlib"},
    )


def estimate(value: float) -> Estimate:
    return Estimate(
        value=value,
        known=True,
        estimation_method="e2e fixture prior",
        provenance=["e2e-fixture"],
    )


def branch(branch_id: str, information_gain: float) -> ResearchBranch:
    components = PriorityComponents(
        expected_information_gain=estimate(information_gain),
        scientific_potential=estimate(0.8),
        evidence_support=Estimate(
            value=None,
            known=False,
            estimation_method="awaiting repository experiment",
        ),
        novelty_potential=estimate(0.6),
        expected_improvement=estimate(0.7),
        expected_uncertainty_reduction=estimate(0.9),
        compute_cost=estimate(0.1),
        risk=estimate(0.1),
        redundancy=estimate(0.1),
    )
    return ResearchBranch(
        id=branch_id,
        research_gap="The decision threshold may be biased for small positive values.",
        hypothesis="Lowering the decision threshold improves accuracy on the locked data.",
        mechanism="The current threshold misclassifies a positive sample near zero.",
        proposed_change="Lower DEFAULT_THRESHOLD from 0.5 to 0.0.",
        expected_observation="Accuracy rises from 0.8 to 1.0.",
        falsification_condition="Protocol-compatible replication does not exceed 0.8.",
        minimal_experiment="Edit model.py and run the tracked evaluation twice.",
        closest_prior_work=["fixture source inspection"],
        novelty_risk="This is a platform fixture, not a publishable contribution.",
        priority_components=components,
        estimated_cost=BudgetCost(experiments=1, compute_minutes=1),
        risk="low",
    )


def test_real_repository_results_drive_two_scientific_iterations(tmp_path):
    root, base_commit = make_repo(tmp_path)
    active = protocol()
    baseline_workspace = RepositoryWorkspace(
        root, allowed_executables={Path(sys.executable).name}
    )
    baseline = BaselineReproducer(baseline_workspace).reproduce_and_validate(
        BaselineReproductionRequest(
            repository=str(root),
            commit=base_commit,
            task=active.task,
            entrypoint="train.py",
            protocol=active,
            command=[
                sys.executable,
                "train.py",
                "--output",
                "baseline-result.json",
                "--protocol-fingerprint",
                active.fingerprint().value,
                "--seeds",
                "11",
                "22",
            ],
            result_path="baseline-result.json",
            environment={"python": sys.version.split()[0], "device": "cpu"},
            reported_metrics={"accuracy": 0.8},
        )
    )
    problem = ProblemProfile(
        question="Can a threshold correction improve the fixture model?",
        task=active.task,
        repository=str(root),
        dataset=active.dataset,
        success_criteria=["two audited protocol-compatible iterations"],
    )
    state = ResearchState(
        problem=problem,
        baseline=baseline,
        frontier=ResearchFrontier(
            branches=[branch("h1", 0.95), branch("h2", 0.4)]
        ),
        budget=BudgetState(
            experiment_limit=4,
            compute_minutes_limit=10,
            model_call_limit=2,
        ),
    )
    manager = WorktreeManager(root, tmp_path / "worktrees")
    adapter = WorkspaceExperimentAdapter(manager)

    def execute(action, current):
        index = current.iteration + 1
        is_first = action.operator == ResearchOperator.RUN_EXPERIMENT
        experiment_base = (
            base_commit if is_first else current.experiments[-1].code_commit
        )
        implementation = (
            {
                "model.py": (
                    "DEFAULT_THRESHOLD = 0.0\n\n\n"
                    "def predict(value: float, threshold: float = DEFAULT_THRESHOLD) -> int:\n"
                    "    return int(value >= threshold)\n"
                )
            }
            if is_first
            else {}
        )
        output = f"variant-{index}-result.json"
        return adapter.execute(
            RepositoryExperimentContract(
                experiment_id=f"repo_exp_{index}",
                branch_id=action.branch_id,
                worktree_branch=f"v2exp/h1-{index}",
                purpose=f"{action.operator} selected from updated state",
                repository=str(root),
                base_commit=experiment_base,
                protocol=active,
                baseline_protocol=baseline.protocol,
                baseline_metrics=baseline.local_metrics,
                config={"threshold": 0.0, "operator": action.operator},
                implementation_files=implementation,
                static_commands=[
                    [sys.executable, "-m", "py_compile", "model.py", "train.py"]
                ],
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
                commit_message=f"experiment(h1): iteration {index}",
            )
        )

    iterations = ResearchLoop().run(state, execute, iterations=2)
    first, second = iterations
    final = second.state

    assert baseline.local_metrics == {"accuracy": 0.8}
    assert first.action.operator == ResearchOperator.RUN_EXPERIMENT
    assert first.experiment.metrics == {"accuracy": 1.0}, first.experiment.model_dump()
    assert first.experiment.code_commit and first.experiment.code_commit != base_commit
    assert first.state.frontier.get("h1").status == BranchStatus.PROMISING
    assert second.action.operator == ResearchOperator.RUN_REPLICATION
    assert second.experiment.metrics == {"accuracy": 1.0}
    assert second.experiment.code_commit == first.experiment.code_commit
    assert final.frontier.get("h1").status == BranchStatus.VALIDATED
    assert final.beliefs.get("h1").support_score == 0.7
    assert all(item.improvement_claim_allowed for item in final.experiments)
    assert all(item.comparison.compatible for item in final.experiments)
    assert final.experiments[0].protocol_fingerprint == active.fingerprint()


class PlannerGateway:
    def __init__(self) -> None:
        self.calls = []

    def invoke_structured(self, task_type, messages, output_schema, context=None):
        self.calls.append((task_type, context))
        if task_type == "v2.repository.inspect":
            assert "model.py" in context["tracked_files"]
            return output_schema.model_validate(
                {
                    "files": ["model.py"],
                    "rationale": "The hypothesis targets the decision threshold defined in model.py.",
                }
            )
        assert task_type == "v2.repository.implementation_plan"
        assert "DEFAULT_THRESHOLD = 0.5" in context["selected_sources"]["model.py"]
        return output_schema.model_validate(
            {
                "summary": "Lower only the default threshold to test the stated mechanism.",
                "edits": [
                    {
                        "path": "model.py",
                        "replacement_content": (
                            "DEFAULT_THRESHOLD = 0.0\n\n\n"
                            "def predict(value: float, threshold: float = DEFAULT_THRESHOLD) -> int:\n"
                            "    return int(value >= threshold)\n"
                        ),
                        "rationale": "This one-line behavioral change isolates threshold bias.",
                    }
                ],
                "expected_effect": "The near-zero positive example becomes correctly classified.",
                "risks": ["The fixture is deterministic and does not establish external validity."],
            }
        )


def test_general_planner_inspects_and_edits_repository_without_caller_file_contents(tmp_path):
    root, base_commit = make_repo(tmp_path)
    active = protocol()
    candidate = branch("planner_h1", 0.95)
    action = ResearchAction(
        operator=ResearchOperator.RUN_EXPERIMENT,
        branch_id=candidate.id,
        reason="Test the highest-information threshold mechanism.",
        target_information_gap=candidate.research_gap,
        expected_information_gain=0.95,
        estimated_cost=candidate.estimated_cost,
        prerequisites=["validated baseline"],
        completion_criteria=[candidate.expected_observation],
        decision_iteration=0,
    )
    gateway = PlannerGateway()
    adapter = PlannedRepositoryExperimentAdapter(
        GeneralRepositoryImplementationPlanner(gateway),
        WorkspaceExperimentAdapter(WorktreeManager(root, tmp_path / "planned-worktrees")),
    )
    output = "planned-result.json"
    result = adapter.execute(
        GeneralRepositoryExperimentContract(
            experiment_id="planned_repo_exp_1",
            action=action,
            branch=candidate,
            worktree_branch="v2exp/planner-h1",
            repository=str(root),
            base_commit=base_commit,
            protocol=active,
            baseline_protocol=active,
            baseline_metrics={"accuracy": 0.8},
            config={"operator": action.operator},
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
            commit_message="experiment(planner_h1): lower threshold",
        )
    )

    assert [call[0] for call in gateway.calls] == [
        "v2.repository.inspect",
        "v2.repository.implementation_plan",
    ]
    assert result.trace.inspected_files == ["model.py"]
    assert result.trace.edited_files == ["model.py"]
    assert result.trace.safety_validated is True
    assert result.record.metrics == {"accuracy": 1.0}
    assert result.record.audit_passed is True
    assert result.record.changed_files == ["model.py"]
    assert result.record.code_commit and result.record.code_commit != base_commit
