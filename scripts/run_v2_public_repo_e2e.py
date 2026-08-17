from __future__ import annotations

import argparse
import json
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


REPOSITORY_URL = "https://github.com/karpathy/micrograd.git"
EVALUATOR = ROOT / "scripts" / "evaluate_micrograd_relu.py"


def run_git(cwd: Path | None, *args: str, timeout: int = 120) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return completed.stdout.strip()


def protocol(commit: str) -> ExperimentProtocol:
    return ExperimentProtocol(
        task="numerical conformance of scalar automatic differentiation",
        dataset=DatasetIdentity(
            name="micrograd-relu-signed-zero-grid",
            version="1",
            source="predeclared deterministic scalar grid",
            fingerprint=f"micrograd-relu-signed-zero-grid-v1@{commit[:12]}",
        ),
        split={
            "train": "not applicable; implementation-level numerical study",
            "test": "eight locked values including negative zero",
        },
        preprocessing={"transform": "none", "dtype": "Python float"},
        metrics=[
            MetricDefinition(
                name="relu_conformance_score",
                direction=MetricDirection.MAXIMIZE,
                definition=(
                    "fraction of locked inputs matching ReLU value and gradient convention, "
                    "including canonical positive-zero output"
                ),
                aggregation="passed cases / eight locked cases",
            )
        ],
        training_budget=TrainingBudget(max_steps=1),
        evaluation_protocol={
            "inputs": [-1000.0, -10.0, -1.0, -0.0, 0.0, 1.0, 10.0, 1000.0],
            "value_tolerance": 1e-12,
            "gradient_tolerance": 1e-12,
            "reference": "ReLU value and zero-at-origin gradient convention",
            "signed_zero": "zero outputs must have positive sign",
        },
        seed_policy=SeedPolicy(
            seeds=[11, 22],
            aggregation="deterministic repeated evaluation",
            minimum_repetitions=2,
        ),
        training_controls={"network": "disabled", "device": "cpu"},
    )


def evaluator_command(active: ExperimentProtocol, output: str) -> list[str]:
    return [
        sys.executable,
        "-B",
        str(EVALUATOR),
        "--output",
        output,
        "--protocol-fingerprint",
        active.fingerprint().value,
        "--seeds",
        "11",
        "22",
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="")
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root
        else ROOT / "backend" / "data" / "checkpoint4-public" / stamp
    )
    output_root.mkdir(parents=True, exist_ok=False)
    runtime_root = Path(tempfile.gettempdir()) / "ai-scientist-v2-public" / stamp
    runtime_root.mkdir(parents=True, exist_ok=False)
    repository = runtime_root / "micrograd"
    partial_path = output_root / "public-repo-e2e-partial.json"

    try:
        run_git(None, "clone", "--depth", "1", REPOSITORY_URL, str(repository))
        run_git(repository, "config", "user.name", "AI Scientist V2 Public E2E")
        run_git(repository, "config", "user.email", "v2-public@example.invalid")
        commit = run_git(repository, "rev-parse", "HEAD")
        remote = run_git(repository, "remote", "get-url", "origin")
        inspection = RepositoryWorkspace(
            repository, allowed_executables={Path(sys.executable).name}
        ).inspect()
        partial_path.write_text(
            json.dumps(
                {
                    "stage": "repository_intake",
                    "repository_url": remote,
                    "commit": commit,
                    "inspection": inspection.model_dump(mode="json"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        active = protocol(commit)
        workspace = RepositoryWorkspace(
            repository, allowed_executables={Path(sys.executable).name}
        )
        baseline = BaselineReproducer(workspace).reproduce_and_validate(
            BaselineReproductionRequest(
                repository=str(repository),
                commit=commit,
                task=active.task,
                entrypoint=str(EVALUATOR),
                protocol=active,
                command=evaluator_command(active, "v2-baseline-result.json"),
                result_path="v2-baseline-result.json",
                environment={
                    "python": sys.version.split()[0],
                    "device": "cpu",
                    "repository_source": "public GitHub clone",
                },
                reported_metrics={},
            )
        )
        baseline_result = repository / "v2-baseline-result.json"
        baseline_result.unlink(missing_ok=True)
        if not baseline.can_be_comparison_denominator:
            raise RuntimeError(
                f"PUBLIC_REPOSITORY_BASELINE_NOT_VALIDATED:{baseline.validation_reason}"
            )

        load_dotenv(ROOT / ".env")
        base_settings = Settings.from_env()
        settings = RuntimeConfigStore(base_settings.data_dir).apply(base_settings)
        provider = get_llm_provider(settings)
        if settings.llm_provider != "qwen" or getattr(provider, "fallback", False):
            raise RuntimeError("LIVE_QWEN_CONFIGURATION_NOT_READY")
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
                "Can a minimal implementation change make micrograd ReLU emit canonical "
                "positive zero for negative-zero input while preserving locked ordinary-range "
                "values and gradients?"
            ),
            task=active.task,
            repository=str(repository),
            dataset=active.dataset,
            compute_constraints={"device": "cpu", "maximum_experiments": 2},
            research_constraints=[
                "This is an unfamiliar public repository cloned from its upstream GitHub URL.",
                "The locked evaluator checks ReLU values, gradients, and positive-zero canonicalization on eight fixed inputs.",
                "Do not edit the external evaluator, protocol, inputs, tolerances, seeds, or metric computation.",
                "Prefer a minimal change to existing repository source and preserve ordinary-range behavior.",
                "No performance gain is required; failure or no improvement is valid evidence.",
            ],
            success_criteria=[
                "Reproduce an audited baseline from the public commit",
                "Generate multiple live Qwen branches",
                "Produce one autonomous repository edit and audited experiment",
                "Obtain live Critic advice and a second Controller action",
            ],
            open_questions=[
                "Does the current ReLU preserve a negative-zero sign even though its numeric value equals zero?"
            ],
        )
        state = sessions.create(
            problem,
            BudgetState(
                experiment_limit=3,
                compute_minutes_limit=30,
                model_call_limit=8,
            ),
            baseline,
        )
        started = sessions.start(state.session_id)
        ideator_metadata = provider.consume_call_metadata()
        if started.action is None or not started.action.branch_id:
            raise RuntimeError("PUBLIC_REPOSITORY_IDEATION_DID_NOT_SELECT_BRANCH")

        branch = started.state.frontier.get(started.action.branch_id)
        planner = PlannedRepositoryExperimentAdapter(
            GeneralRepositoryImplementationPlanner(gateway),
            WorkspaceExperimentAdapter(
                WorktreeManager(repository, runtime_root / "worktrees")
            ),
        )
        experiment = planner.execute(
            GeneralRepositoryExperimentContract(
                experiment_id="micrograd_live_exp_1",
                action=started.action,
                branch=branch,
                worktree_branch=f"v2public/{stamp}-1",
                repository=str(repository),
                base_commit=commit,
                protocol=active,
                baseline_protocol=baseline.protocol,
                baseline_metrics=baseline.local_metrics,
                config={
                    "operator": started.action.operator,
                    "study": "public_repository_e2e",
                },
                static_commands=[
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        (
                            "from pathlib import Path; "
                            "[compile(Path(p).read_text(encoding='utf-8'), p, 'exec') "
                            "for p in ('micrograd/engine.py', 'micrograd/nn.py')]"
                        ),
                    ]
                ],
                smoke_commands=[
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        (
                            "from micrograd.engine import Value; "
                            "x=Value(2.0); y=(x*x).relu(); y.backward(); "
                            "assert y.data == y.data and x.grad == x.grad"
                        ),
                    ]
                ],
                formal_command=evaluator_command(active, "v2-variant-result.json"),
                result_path="v2-variant-result.json",
                environment={
                    "python": sys.version.split()[0],
                    "device": "cpu",
                    "provider": "local",
                    "repository_source": "public GitHub clone",
                    "live_model_validation": True,
                },
                commit_message=f"experiment({branch.id}): public repository study",
                cleanup_worktree=False,
            )
        )
        planner_metadata = provider.consume_call_metadata()
        partial = json.loads(partial_path.read_text(encoding="utf-8"))
        partial.update(
            {
                "stage": "experiment",
                "baseline": baseline.model_dump(mode="json"),
                "session_id": state.session_id,
                "branches": [
                    item.model_dump(mode="json")
                    for item in started.state.frontier.branches
                ],
                "ideator_metadata": ideator_metadata,
                "controller_action": started.action.model_dump(mode="json"),
                "planner_trace": experiment.trace.model_dump(mode="json"),
                "planner_metadata": planner_metadata,
                "experiment": experiment.record.model_dump(mode="json"),
            }
        )
        partial_path.write_text(
            json.dumps(partial, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        after = sessions.continue_session(
            state.session_id, experiment=experiment.record
        )
        critic_metadata = provider.consume_call_metadata()
        final = {
            **partial,
            "stage": "completed",
            "critique": after.critique.model_dump(mode="json") if after.critique else None,
            "critic_metadata": critic_metadata,
            "second_action": after.action.model_dump(mode="json") if after.action else None,
            "summary": sessions.summary(state.session_id),
            "findings": sessions.findings(state.session_id),
            "events": [
                event.model_dump(mode="json") for event in sessions.events(state.session_id)
            ],
        }
        report_path = output_root / "public-repo-e2e-report.json"
        report_path.write_text(
            json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "status": "completed",
                    "repository": remote,
                    "commit": commit,
                    "baseline": baseline.local_metrics,
                    "branch_count": len(started.state.frontier.branches),
                    "first_action": started.action.operator,
                    "experiment_status": experiment.record.result_status,
                    "experiment_metrics": experiment.record.metrics,
                    "changed_files": experiment.record.changed_files,
                    "second_action": after.action.operator if after.action else None,
                    "report_path": str(report_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        diagnostic = {
            "status": "blocked",
            "stage": (
                json.loads(partial_path.read_text(encoding="utf-8")).get("stage")
                if partial_path.exists()
                else "clone"
            ),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "repository_url": REPOSITORY_URL,
            "runtime_root": str(runtime_root),
        }
        (output_root / "public-repo-e2e-diagnostic.json").write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(diagnostic, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
