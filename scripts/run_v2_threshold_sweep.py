from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from backend.app.agents.writer import WriterAgent
from backend.app.config import Settings
from backend.app.controller import ResearchController
from backend.app.experiment import (
    ParameterSweepRun,
    ParameterSweepRunner,
    RepositoryExperimentContract,
    WorkspaceExperimentAdapter,
)
from backend.app.models.gateway import LegacyQwenAdapter
from backend.app.models.v2_session import ResearchEventKind, ResearchSessionEvent
from backend.app.providers.llm import get_llm_provider
from backend.app.research import BudgetState, ClaimEvidenceGraph
from backend.app.services.v2_critic import CriticDecisionService, ScientificCritic
from backend.app.services.v2_writer import V2WriterAdapter
from backend.app.storage.runtime_config import RuntimeConfigStore
from backend.app.storage.v2 import V2Stores
from backend.app.workspace import WorktreeManager


VALUES = [0.1, 0.2, 0.3, 0.4, 0.5]


def replacement_source(source: str, threshold: float) -> str:
    value = repr(threshold)
    replaced, count = re.subn(
        r"(?m)^DEFAULT_THRESHOLD\s*=\s*[-+0-9.eE]+\s*$",
        f"DEFAULT_THRESHOLD = {value}",
        source,
        count=1,
    )
    if count != 1:
        raise RuntimeError("THRESHOLD_DECLARATION_NOT_FOUND")
    return replaced


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--checkpoint-report",
        default=str(
            ROOT
            / "backend"
            / "data"
            / "checkpoint3-live"
            / "20260811T085142Z"
            / "live-e2e-report.json"
        ),
    )
    parser.add_argument("--output-root", default="")
    args = parser.parse_args()

    checkpoint_path = Path(args.checkpoint_report).resolve()
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    session_id = str(checkpoint["session_id"])
    runtime_root = Path(checkpoint["runtime_root"])
    repository = runtime_root / "repository"
    session_dir = checkpoint_path.parent / "session"
    stores = V2Stores(str(session_dir))
    state = stores.states.get(session_id)
    if state.baseline is None or not state.baseline.can_be_comparison_denominator:
        raise RuntimeError("SWEEP_VALIDATED_BASELINE_REQUIRED")
    protocol = state.baseline.protocol
    baseline_metrics = state.baseline.local_metrics
    branch_id = checkpoint["first_action"]["branch_id"]
    base_commit = state.baseline.commit

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root
        else ROOT / "backend" / "data" / "checkpoint4-threshold" / stamp
    )
    output_root.mkdir(parents=True, exist_ok=False)

    from backend.app.workspace.git import GitRepository

    git_repository = GitRepository(repository)
    base_source = git_repository.read_text_at(base_commit, "model.py")
    worktree_root = (
        Path(tempfile.gettempdir()) / "ai-scientist-v2-sweep" / stamp / "worktrees"
    )
    worktree_root.mkdir(parents=True, exist_ok=False)
    manager = WorktreeManager(repository, worktree_root)
    runs: list[ParameterSweepRun] = []
    for threshold in VALUES:
        slug = str(threshold).replace(".", "_")
        result_path = f"threshold-sweep-{slug}.json"
        source = replacement_source(base_source, threshold)
        implementation = {} if source == base_source else {"model.py": source}
        runs.append(
            ParameterSweepRun(
                parameter_name="threshold",
                parameter_value=threshold,
                contract=RepositoryExperimentContract(
                    experiment_id=f"threshold_sweep_{slug}",
                    branch_id=branch_id,
                    worktree_branch=f"v2sweep/{stamp}-threshold-{slug}",
                    purpose=(
                        f"Predeclared parameter sweep at threshold={threshold}; "
                        "no adaptive parameter selection."
                    ),
                    repository=str(repository),
                    base_commit=base_commit,
                    protocol=protocol,
                    baseline_protocol=protocol,
                    baseline_metrics=baseline_metrics,
                    config={
                        "threshold": threshold,
                        "study": "predeclared_parameter_sweep",
                    },
                    implementation_files=implementation,
                    static_commands=[
                        [sys.executable, "-m", "py_compile", "model.py", "train.py"]
                    ],
                    smoke_commands=[
                        [sys.executable, "-m", "pytest", "-q", "test_model.py"]
                    ],
                    formal_command=[
                        sys.executable,
                        "train.py",
                        "--output",
                        result_path,
                        "--protocol-fingerprint",
                        protocol.fingerprint().value,
                        "--seeds",
                        *[str(seed) for seed in protocol.seed_policy.seeds],
                    ],
                    result_path=result_path,
                    environment={
                        "python": sys.version.split()[0],
                        "device": "cpu",
                        "provider": "local",
                        "live_model_validation": True,
                    },
                    commit_message=f"experiment(threshold): sweep {threshold}",
                    cleanup_worktree=False,
                ),
            )
        )

    sweep = ParameterSweepRunner(WorkspaceExperimentAdapter(manager)).run(runs)
    (output_root / "threshold-parameter-response.partial.json").write_text(
        json.dumps(sweep.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if len(sweep.records) != len(VALUES):
        raise RuntimeError("PARAMETER_SWEEP_INCOMPLETE")
    if any(not record.audit_passed for record in sweep.records):
        raise RuntimeError("PARAMETER_SWEEP_AUDIT_FAILED")

    sweep_evidence = sweep.evidence_units(branch_id=branch_id)
    old_budget = state.budget
    experiment_cost = len(sweep.records)
    updated_budget = BudgetState(
        experiment_limit=max(
            old_budget.experiment_limit,
            old_budget.experiments_used + experiment_cost + 2,
        ),
        compute_minutes_limit=max(
            old_budget.compute_minutes_limit,
            old_budget.compute_minutes_used + experiment_cost + 10,
        ),
        model_call_limit=max(
            old_budget.model_call_limit,
            old_budget.model_calls_used + 4,
        ),
        experiments_used=old_budget.experiments_used + experiment_cost,
        compute_minutes_used=old_budget.compute_minutes_used + experiment_cost,
        model_calls_used=old_budget.model_calls_used,
    )
    state = state.model_copy(
        update={
            "experiments": [*state.experiments, *sweep.records],
            "evidence": [*state.evidence, *sweep_evidence],
            "budget": updated_budget,
            "iteration": state.iteration + 1,
            "current_action": None,
        }
    )
    stores.persist(state)
    stores.events.append(
        ResearchSessionEvent(
            session_id=session_id,
            kind=ResearchEventKind.PARAMETER_SWEEP_RECORDED,
            iteration=state.iteration,
            payload=sweep.model_dump(mode="json", exclude={"records"}),
        )
    )

    load_dotenv(ROOT / ".env")
    base_settings = Settings.from_env()
    settings = RuntimeConfigStore(base_settings.data_dir).apply(base_settings)
    provider = get_llm_provider(settings)
    if settings.llm_provider != "qwen" or getattr(provider, "fallback", False):
        raise RuntimeError("LIVE_QWEN_CONFIGURATION_NOT_READY")
    gateway = LegacyQwenAdapter(provider)
    critic = ScientificCritic(gateway)
    branch = state.frontier.get(branch_id)
    critique = critic.review_parameter_sweep(state, branch, sweep)
    critic_metadata = provider.consume_call_metadata()
    state = CriticDecisionService().apply(state, branch_id, critique)
    next_action = ResearchController().next_action(state)
    state = state.model_copy(update={"current_action": next_action})
    stores.persist(state)
    stores.events.append(
        ResearchSessionEvent(
            session_id=session_id,
            kind=ResearchEventKind.CRITIQUE_RECORDED,
            iteration=state.iteration,
            payload={
                "study": "parameter_sweep",
                "branch_id": branch_id,
                **critique.model_dump(mode="json"),
            },
        )
    )
    stores.events.append(
        ResearchSessionEvent(
            session_id=session_id,
            kind=ResearchEventKind.ACTION_SELECTED,
            iteration=state.iteration,
            payload=next_action.model_dump(mode="json"),
        )
    )

    graph = ClaimEvidenceGraph.from_parameter_sweep(
        state, sweep, branch_id=branch_id
    )
    stores.events.append(
        ResearchSessionEvent(
            session_id=session_id,
            kind=ResearchEventKind.CLAIM_GRAPH_UPDATED,
            iteration=state.iteration,
            payload=graph.model_dump(mode="json"),
        )
    )

    writer_adapter = V2WriterAdapter(WriterAgent(provider))
    report = writer_adapter.build_report(
        state,
        graph,
        parameter_sweep=sweep,
        instructions="Produce a concise scientific report suitable for the V2 Beta demo.",
    )
    report_title = str(report.get("Report Title") or "V2 threshold calibration report")
    report_json = output_root / "v2-research-report.json"
    report_html = output_root / "v2-research-report.html"
    report_docx = output_root / "v2-research-report.docx"
    sweep_path = output_root / "threshold-parameter-response.json"
    graph_path = output_root / "claim-evidence-graph.json"
    critique_path = output_root / "qwen-sweep-critique.json"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_html.write_bytes(
        writer_adapter.html_bytes(report, session_id=session_id, title=report_title)
    )
    report_docx.write_bytes(
        writer_adapter.docx_bytes(report, session_id=session_id, title=report_title)
    )
    sweep_path.write_text(
        json.dumps(sweep.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    graph_path.write_text(
        json.dumps(graph.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    critique_path.write_text(
        json.dumps(
            {
                "critique": critique.model_dump(mode="json"),
                "model_metadata": critic_metadata,
                "controller_action": next_action.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    stores.events.append(
        ResearchSessionEvent(
            session_id=session_id,
            kind=ResearchEventKind.REPORT_EXPORTED,
            iteration=state.iteration,
            payload={
                "json": str(report_json),
                "html": str(report_html),
                "docx": str(report_docx),
                "numeric_source": "ExperimentRecord",
            },
        )
    )

    print(
        json.dumps(
            {
                "status": "validated",
                "session_id": session_id,
                "parameter_response": [
                    point.model_dump(mode="json") for point in sweep.points
                ],
                "stable_improvement_intervals": sweep.stable_improvement_intervals,
                "critic": critique.model_dump(mode="json"),
                "controller_action": next_action.model_dump(mode="json"),
                "claim_statuses": [
                    {"claim": claim.statement, "status": claim.status}
                    for claim in graph.claims
                ],
                "report_json": str(report_json),
                "report_html": str(report_html),
                "report_docx": str(report_docx),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
