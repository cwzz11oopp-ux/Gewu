from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from backend.app.baseline import BaselineReproducer, BaselineReproductionRequest
from backend.app.config import Settings
from backend.app.experiment import (
    GeneralRepositoryExperimentContract,
    GeneralRepositoryImplementationPlanner,
    PlannedRepositoryExperimentAdapter,
    WorkspaceExperimentAdapter,
)
from backend.app.literature import SprintLiteratureService
from backend.app.models.gateway import LegacyQwenAdapter
from backend.app.providers.literature import get_literature_provider
from backend.app.providers.llm import get_llm_provider
from backend.app.research import (
    BudgetState,
    DatasetIdentity,
    ExperimentProtocol,
    MetricDefinition,
    MetricDirection,
    ProblemProfile,
    SeedPolicy,
    TrainingBudget,
)
from backend.app.research.ideator import BranchConstructor
from backend.app.services.v2_critic import ScientificCritic
from backend.app.services.v2_sessions import ResearchSessionService
from backend.app.storage.literature import LiteratureLibrary
from backend.app.storage.runtime_config import RuntimeConfigStore
from backend.app.storage.v2 import V2Stores
from backend.app.workspace import RepositoryWorkspace, WorktreeManager


FIXTURE = ROOT / "tests" / "fixtures" / "v2_demo_repo"


def git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def protocol() -> ExperimentProtocol:
    return ExperimentProtocol(
        task="binary classification",
        dataset=DatasetIdentity(
            name="tiny-threshold-data",
            version="1",
            source="tracked deterministic repository fixture",
            fingerprint="tiny-threshold-v1",
        ),
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


def experiment_contract(
    *,
    output_root: Path,
    repository: Path,
    action,
    branch,
    active_protocol: ExperimentProtocol,
    baseline,
    base_commit: str,
    index: int,
) -> GeneralRepositoryExperimentContract:
    result_path = f"live-result-{index}.json"
    return GeneralRepositoryExperimentContract(
        experiment_id=f"live_exp_{index}",
        action=action,
        branch=branch,
        worktree_branch=f"v2live/{output_root.name}-{index}",
        repository=str(repository),
        base_commit=base_commit,
        protocol=active_protocol,
        baseline_protocol=baseline.protocol,
        baseline_metrics=baseline.local_metrics,
        config={"operator": action.operator, "live_qwen_planner": True},
        static_commands=[
            [sys.executable, "-m", "py_compile", "model.py", "train.py"]
        ],
        smoke_commands=[[sys.executable, "-m", "pytest", "-q", "test_model.py"]],
        formal_command=[
            sys.executable,
            "train.py",
            "--output",
            result_path,
            "--protocol-fingerprint",
            active_protocol.fingerprint().value,
            "--seeds",
            "11",
            "22",
        ],
        result_path=result_path,
        environment={
            "python": sys.version.split()[0],
            "device": "cpu",
            "live_model_validation": True,
        },
        commit_message=f"experiment({branch.id}): live {action.operator} iteration {index}",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    base_settings = Settings.from_env()
    settings = RuntimeConfigStore(base_settings.data_dir).apply(base_settings)
    if settings.llm_provider != "qwen" or not settings.qwen_api_key:
        raise RuntimeError("LIVE_QWEN_CONFIGURATION_NOT_READY")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_root = Path(args.output_root).resolve() if args.output_root else (
        ROOT / "backend" / "data" / "checkpoint3-live" / stamp
    )
    output_root.mkdir(parents=True, exist_ok=False)
    runtime_root = Path(tempfile.gettempdir()) / "ai-scientist-v2-live" / stamp
    runtime_root.mkdir(parents=True, exist_ok=False)
    repository = runtime_root / "repository"
    shutil.copytree(FIXTURE, repository)
    git(repository, "init")
    git(repository, "config", "user.name", "AI Scientist V2 Live E2E")
    git(repository, "config", "user.email", "v2-live@example.invalid")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "baseline fixture")
    base_commit = git(repository, "rev-parse", "HEAD")

    active_protocol = protocol()
    workspace = RepositoryWorkspace(
        repository, allowed_executables={Path(sys.executable).name}
    )
    baseline = BaselineReproducer(workspace).reproduce_and_validate(
        BaselineReproductionRequest(
            repository=str(repository),
            commit=base_commit,
            task=active_protocol.task,
            entrypoint="train.py",
            protocol=active_protocol,
            command=[
                sys.executable,
                "train.py",
                "--output",
                "baseline-result.json",
                "--protocol-fingerprint",
                active_protocol.fingerprint().value,
                "--seeds",
                "11",
                "22",
            ],
            result_path="baseline-result.json",
            environment={"python": sys.version.split()[0], "device": "cpu"},
            reported_metrics={"accuracy": 0.8},
        )
    )
    if not baseline.can_be_comparison_denominator:
        raise RuntimeError(f"LIVE_BASELINE_NOT_VALIDATED:{baseline.validation_reason}")

    os.environ["QWEN_DIAGNOSTIC_LOG"] = str(output_root / "qwen-diagnostics.jsonl")
    provider = get_llm_provider(settings)
    gateway = LegacyQwenAdapter(provider)
    literature = SprintLiteratureService(
        get_literature_provider(settings),
        LiteratureLibrary(output_root / "literature"),
    )
    sessions = ResearchSessionService(
        V2Stores(str(output_root / "session")),
        BranchConstructor(gateway),
        literature,
        model_ready=True,
        critic=ScientificCritic(gateway),
    )
    problem = ProblemProfile(
        question=(
            "Can a minimal decision-rule calibration improve accuracy on the locked "
            "threshold dataset, and can an ablation distinguish calibration from protocol drift?"
        ),
        task=active_protocol.task,
        repository=str(repository),
        dataset=active_protocol.dataset,
        compute_constraints={"device": "cpu", "maximum_experiments": 2},
        research_constraints=[
            "The repository contains model.py, train.py, and test_model.py.",
            "The baseline threshold is 0.5 and one positive value at 0.2 is misclassified.",
            "Do not modify tests, evaluation data, metric computation, seeds, or protocol.",
            "Treat this as a live autonomy platform test, not a publishable novelty claim.",
        ],
        success_criteria=[
            "An audited protocol-compatible first experiment",
            "A Critic-informed non-fixed second action",
            "A second audited experiment with updated evidence",
        ],
        open_questions=["Is any observed gain caused by the proposed mechanism?"],
    )
    state = sessions.create(
        problem,
        BudgetState(
            experiment_limit=4,
            compute_minutes_limit=500,
            model_call_limit=12,
        ),
        baseline,
    )
    started = sessions.start(state.session_id)
    ideator_metadata = provider.consume_call_metadata()
    if started.action is None or not started.action.branch_id:
        raise RuntimeError("LIVE_IDEATOR_DID_NOT_SELECT_BRANCH")

    planner = PlannedRepositoryExperimentAdapter(
        GeneralRepositoryImplementationPlanner(gateway),
        WorkspaceExperimentAdapter(
            WorktreeManager(repository, runtime_root / "worktrees")
        ),
    )
    first_branch = started.state.frontier.get(started.action.branch_id)
    first = planner.execute(
        experiment_contract(
            output_root=output_root,
            repository=repository,
            action=started.action,
            branch=first_branch,
            active_protocol=active_protocol,
            baseline=baseline,
            base_commit=base_commit,
            index=1,
        )
    )
    first_planner_metadata = provider.consume_call_metadata()
    (output_root / "live-e2e-partial.json").write_text(
        json.dumps(
            {
                "stage": "first_experiment",
                "session_id": state.session_id,
                "ideator_metadata": ideator_metadata,
                "branches": [
                    item.model_dump(mode="json")
                    for item in started.state.frontier.branches
                ],
                "first_action": started.action.model_dump(mode="json"),
                "first_planner": first.trace.model_dump(mode="json"),
                "first_planner_metadata": first_planner_metadata,
                "first_experiment": first.record.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if not first.record.audit_passed:
        raise RuntimeError(f"LIVE_FIRST_EXPERIMENT_FAILED:{first.record.analysis}")

    after_first = sessions.continue_session(
        state.session_id, experiment=first.record
    )
    first_critic_metadata = provider.consume_call_metadata()
    partial = json.loads(
        (output_root / "live-e2e-partial.json").read_text(encoding="utf-8")
    )
    partial.update(
        {
            "stage": "first_critic_controller",
            "first_critique": (
                after_first.critique.model_dump(mode="json")
                if after_first.critique
                else None
            ),
            "first_critic_metadata": first_critic_metadata,
            "second_action": after_first.action.model_dump(mode="json"),
        }
    )
    (output_root / "live-e2e-partial.json").write_text(
        json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if after_first.action is None or not after_first.action.branch_id:
        raise RuntimeError("LIVE_CRITIC_CONTROLLER_DID_NOT_SELECT_FOLLOW_UP")

    second_branch = after_first.state.frontier.get(after_first.action.branch_id)
    second_base = first.record.code_commit or base_commit
    second = planner.execute(
        experiment_contract(
            output_root=output_root,
            repository=repository,
            action=after_first.action,
            branch=second_branch,
            active_protocol=active_protocol,
            baseline=baseline,
            base_commit=second_base,
            index=2,
        )
    )
    second_planner_metadata = provider.consume_call_metadata()
    if not second.record.audit_passed:
        raise RuntimeError(f"LIVE_SECOND_EXPERIMENT_FAILED:{second.record.analysis}")

    after_second = sessions.continue_session(
        state.session_id, experiment=second.record
    )
    second_critic_metadata = provider.consume_call_metadata()
    final_state = after_second.state
    report = {
        "live_model_validation": "validated",
        "session_id": state.session_id,
        "runtime_root": str(runtime_root),
        "research_question": problem.question,
        "baseline": baseline.model_dump(mode="json"),
        "ideator_metadata": ideator_metadata,
        "branches": [
            branch.model_dump(mode="json") for branch in started.state.frontier.branches
        ],
        "branch_gate_events": [
            event.model_dump(mode="json")
            for event in sessions.events(state.session_id)
            if event.kind == "BRANCH_GATE"
        ],
        "first_action": started.action.model_dump(mode="json"),
        "first_planner": first.trace.model_dump(mode="json"),
        "first_planner_metadata": first_planner_metadata,
        "first_experiment": first.record.model_dump(mode="json"),
        "first_critique": (
            after_first.critique.model_dump(mode="json")
            if after_first.critique
            else None
        ),
        "first_critic_metadata": first_critic_metadata,
        "second_action": after_first.action.model_dump(mode="json"),
        "second_planner": second.trace.model_dump(mode="json"),
        "second_planner_metadata": second_planner_metadata,
        "second_experiment": second.record.model_dump(mode="json"),
        "second_critique": (
            after_second.critique.model_dump(mode="json")
            if after_second.critique
            else None
        ),
        "second_critic_metadata": second_critic_metadata,
        "next_action": (
            after_second.action.model_dump(mode="json")
            if after_second.action
            else None
        ),
        "summary": sessions.summary(state.session_id),
        "findings": sessions.findings(state.session_id),
        "events": [
            event.model_dump(mode="json") for event in sessions.events(state.session_id)
        ],
        "final_frontier": final_state.frontier.model_dump(mode="json"),
    }
    report_path = output_root / "live-e2e-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "validated",
                "report_path": str(report_path),
                "session_id": state.session_id,
                "branch_count": len(started.state.frontier.branches),
                "first_action": started.action.operator,
                "first_metrics": first.record.metrics,
                "second_action": after_first.action.operator,
                "second_metrics": second.record.metrics,
                "next_action": after_second.action.operator if after_second.action else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
