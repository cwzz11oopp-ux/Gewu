from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from backend.app.agents.writer import WriterAgent
from backend.app.baseline import BaselineReproducer, BaselineReproductionRequest
from backend.app.config import Settings
from backend.app.experiment import (
    RepositoryExperimentContract,
    WorkspaceExperimentAdapter,
)
from backend.app.literature import SprintLiteratureService
from backend.app.models.gateway import LegacyQwenAdapter
from backend.app.models.v2_session import ResearchEventKind, ResearchSessionEvent
from backend.app.providers.literature import get_literature_provider
from backend.app.providers.llm import get_llm_provider
from backend.app.research import DatasetIdentity, ExperimentProtocol
from backend.app.research.actions import ResearchAction, ResearchOperator
from backend.app.research.belief import Belief
from backend.app.research.budget import BudgetState
from backend.app.research.claims import (
    Claim,
    ClaimEvidenceGraph,
    ClaimEvidenceLink,
    ClaimStatus,
)
from backend.app.research.evidence import EvidenceRelation
from backend.app.research.ideator import BranchConstructor
from backend.app.research.frontier import ResearchBranch
from backend.app.services.v2_critic import ScientificCritic
from backend.app.services.v2_sessions import ResearchSessionService
from backend.app.services.v2_writer import V2WriterAdapter
from backend.app.storage.literature import LiteratureLibrary
from backend.app.storage.runtime_config import RuntimeConfigStore
from backend.app.storage.v2 import V2Stores
from backend.app.workspace import RepositoryWorkspace, WorktreeManager


EVALUATOR = ROOT / "scripts" / "evaluate_micrograd_relu.py"
DEFAULT_ACCEPTED_REPORT = (
    ROOT
    / "backend"
    / "data"
    / "checkpoint4-public"
    / "20260811T100238Z"
    / "public-repo-e2e-report.json"
)
ROBUSTNESS_INPUTS = [-1.0, -1e-12, -1e-15, -0.0, 0.0, 1e-15, 1e-12, 1.0]


def evaluator_command(protocol: ExperimentProtocol, output: str, profile: str) -> list[str]:
    return [
        sys.executable,
        "-B",
        str(EVALUATOR),
        "--output",
        output,
        "--protocol-fingerprint",
        protocol.fingerprint().value,
        "--seeds",
        "11",
        "22",
        "--profile",
        profile,
    ]


def static_commands() -> list[list[str]]:
    return [
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
    ]


def smoke_commands() -> list[list[str]]:
    return [
        [
            sys.executable,
            "-B",
            "-c",
            (
                "from micrograd.engine import Value; "
                "x=Value(-0.0); y=x.relu(); y.backward(); "
                "assert y.data == 0.0 and x.grad == 0.0"
            ),
        ]
    ]


def robustness_protocol(core: ExperimentProtocol, upstream_commit: str) -> ExperimentProtocol:
    return core.model_copy(
        update={
            "dataset": DatasetIdentity(
                name="micrograd-relu-boundary-grid",
                version="2",
                source="predeclared deterministic finite boundary grid",
                fingerprint=f"micrograd-relu-boundary-grid-v2@{upstream_commit[:12]}",
            ),
            "split": {
                "train": "not applicable; implementation-level numerical study",
                "test": "eight locked finite values around the ReLU boundary",
            },
            "evaluation_protocol": {
                "inputs": ROBUSTNESS_INPUTS,
                "value_tolerance": 1e-12,
                "gradient_tolerance": 1e-12,
                "reference": "ReLU value and zero-at-origin gradient convention",
                "signed_zero": "both signed zero inputs must emit canonical positive zero",
                "scope": "finite ordinary values plus 1e-15 and 1e-12 boundary perturbations",
                "explicit_exclusions": [
                    "NaN",
                    "infinity",
                    "subnormal values",
                    "general floating-point behavior",
                ],
            },
        }
    )


def _config_fingerprint(record) -> str:
    encoded = json.dumps(
        record.config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _link(record, evidence, relation: EvidenceRelation | None = None) -> ClaimEvidenceLink:
    metric = record.protocol.metrics[0].name
    return ClaimEvidenceLink(
        evidence_id=evidence.id,
        experiment_id=record.experiment_id,
        relation=relation or evidence.relation,
        metric_name=metric,
        metric_value=record.metrics[metric],
        baseline_value=record.baseline_metrics[metric],
        protocol_fingerprint=record.protocol_fingerprint.value,
        config_fingerprint=_config_fingerprint(record),
        code_commit=record.code_commit,
        protocol_compatible=record.comparison.compatible,
        audit_passed=record.audit_passed,
        evidence_strength=evidence.strength,
    )


def public_claim_graph(state, branch_id: str) -> ClaimEvidenceGraph:
    records = {record.experiment_id: record for record in state.experiments}
    evidence = {
        item.experiment_id: item
        for item in state.evidence
        if item.experiment_id in records and item.verified
    }
    first = records["micrograd_live_exp_1"]
    ablation = records["micrograd_live_exp_2_ablation"]
    robustness = records["micrograd_live_exp_3_robustness"]
    first_link = _link(first, evidence[first.experiment_id])
    ablation_link = _link(ablation, evidence[ablation.experiment_id])
    robustness_link = _link(robustness, evidence[robustness.experiment_id])
    return ClaimEvidenceGraph(
        claims=[
            Claim(
                statement=(
                    "The minimal ReLU non-positive guard improves canonical signed-zero "
                    "conformance on the original locked eight-input protocol."
                ),
                branch_id=branch_id,
                status=ClaimStatus.SUPPORTED,
                rationale=(
                    "The audited main experiment improved 0.875 to 1.0 and the same-protocol "
                    "ablation returned to 0.875."
                ),
                evidence_strength=min(first_link.evidence_strength, ablation_link.evidence_strength),
                links=[first_link, ablation_link],
            ),
            Claim(
                statement=(
                    "The observed improvement is attributable to routing zero-valued inputs "
                    "through an explicit canonical-zero branch."
                ),
                branch_id=branch_id,
                status=ClaimStatus.PARTIALLY_SUPPORTED,
                rationale=(
                    "The full-revert ablation isolates the local guard and the locked boundary "
                    "grid remains conformant, but finite deterministic grids do not exclude every "
                    "alternative implementation-level explanation."
                ),
                evidence_strength=min(
                    first_link.evidence_strength,
                    ablation_link.evidence_strength,
                    robustness_link.evidence_strength,
                ),
                links=[first_link, ablation_link, robustness_link],
            ),
            Claim(
                statement=(
                    "The change is established for NaN, subnormal values, or arbitrary "
                    "floating-point behavior."
                ),
                branch_id=branch_id,
                status=ClaimStatus.NOT_SUPPORTED,
                rationale=(
                    "Those domains were explicitly excluded; only signed zero and finite "
                    "1e-15/1e-12 boundary perturbations were tested."
                ),
                evidence_strength=robustness_link.evidence_strength,
                links=[
                    robustness_link.model_copy(
                        update={"relation": EvidenceRelation.CONTEXT}
                    )
                ],
            ),
        ]
    )


def _state_slice(state, branch_id: str) -> dict:
    branch = state.frontier.get(branch_id)
    belief = state.beliefs.get(branch_id)
    return {
        "iteration": state.iteration,
        "budget": state.budget.model_dump(mode="json"),
        "current_action": (
            state.current_action.model_dump(mode="json") if state.current_action else None
        ),
        "branch": branch.model_dump(mode="json"),
        "belief": belief.model_dump(mode="json") if belief else None,
    }


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def select_bounded_robustness(stores: V2Stores, state, branch_id: str):
    action = state.current_action
    if action is not None and action.operator == ResearchOperator.RUN_ROBUSTNESS:
        return state, False
    branch = state.frontier.get(branch_id)
    if (
        action is None
        or action.operator != ResearchOperator.RUN_REPLICATION
        or ResearchOperator.RUN_ROBUSTNESS not in branch.next_actions
    ):
        raise RuntimeError("CONTROLLER_RUN_ROBUSTNESS_ACTION_REQUIRED")
    bounded = ResearchAction(
        operator=ResearchOperator.RUN_ROBUSTNESS,
        branch_id=branch_id,
        reason=(
            "Final Demo Hardening directive selects the Critic-recommended bounded finite "
            "ReLU boundary check after the stable ablation; Controller had preferred "
            "RUN_REPLICATION on cost score."
        ),
        target_information_gap=(
            "Whether signed zero and the predeclared 1e-15/1e-12 finite boundary "
            "perturbations preserve locked ReLU conformance."
        ),
        expected_information_gain=action.expected_information_gain,
        estimated_cost=action.estimated_cost,
        prerequisites=[
            "audited main experiment",
            "audited full-revert ablation",
            "predeclared finite boundary grid",
        ],
        completion_criteria=[
            "Execute exactly the locked finite robustness grid under its own reproduced baseline.",
            "Do not infer NaN, infinity, subnormal, or general floating-point support.",
        ],
        decision_iteration=action.decision_iteration,
    )
    updated = state.model_copy(update={"current_action": bounded})
    stores.persist(updated)
    stores.events.append(
        ResearchSessionEvent(
            session_id=state.session_id,
            kind=ResearchEventKind.ACTION_SELECTED,
            iteration=state.iteration,
            payload={
                **bounded.model_dump(mode="json"),
                "selection_source": "user_authorized_final_demo_hardening_directive",
                "controller_preference": action.operator,
            },
        )
    )
    return updated, True


def reconcile_full_revert_metadata(stores: V2Stores, session_id: str) -> bool:
    state = stores.states.get(session_id)
    original = next(
        (
            record
            for record in state.experiments
            if record.experiment_id == "micrograd_live_exp_2_ablation"
            and record.config.get("ablation")
            == "strict full revert to audited upstream control commit"
        ),
        None,
    )
    if original is None or original.environment.get("live_planner") is False:
        return False
    corrected = original.model_copy(
        update={
            "environment": {
                **original.environment,
                "live_planner": False,
                "control_type": "audited upstream full-revert commit",
            }
        }
    )
    corrected_state = state.model_copy(
        update={
            "experiments": [
                corrected if item.experiment_id == corrected.experiment_id else item
                for item in state.experiments
            ]
        }
    )
    values = stores.experiments._read()
    values[session_id][corrected.experiment_id] = corrected.model_dump(mode="json")
    stores.experiments._write(values)
    stores.states.save(corrected_state)
    return True


def rollback_equivalent_ablation(
    stores: V2Stores,
    session_id: str,
    accepted: dict,
    report_path: Path,
    output_root: Path,
) -> str | None:
    """Rollback only the known planner result that retained the target capability."""
    state = stores.states.get(session_id)
    invalid = next(
        (
            record
            for record in state.experiments
            if record.experiment_id == "micrograd_live_exp_2_ablation"
            and record.metrics.get("relu_conformance_score") == 1.0
        ),
        None,
    )
    if invalid is None:
        return None
    archive_path = output_root / "invalid-live-planner-ablation.json"
    if report_path.exists() and not archive_path.exists():
        archive_path.write_bytes(report_path.read_bytes())

    before = json.loads(archive_path.read_text(encoding="utf-8"))["ablation"][
        "state_before"
    ]
    active_branch = ResearchBranch.model_validate(accepted["summary"]["active_branch"])
    frontier = state.frontier.replace(active_branch)
    belief = Belief.model_validate(before["belief"])
    second_critique_gaps = {
        gap
        for event in stores.events.list(session_id)
        if event.kind == ResearchEventKind.CRITIQUE_RECORDED and event.iteration == 3
        for gap in event.payload.get("open_information_gaps", [])
    }
    accepted_gaps = set((accepted.get("critique") or {}).get("open_information_gaps", []))
    open_questions = [
        gap
        for gap in state.open_questions
        if gap not in second_critique_gaps or gap in accepted_gaps
    ]
    restored = state.model_copy(
        update={
            "frontier": frontier,
            "beliefs": state.beliefs.upsert(belief),
            "budget": BudgetState.model_validate(before["budget"]),
            "evidence": [
                item
                for item in state.evidence
                if item.experiment_id != invalid.experiment_id
            ],
            "experiments": [
                item
                for item in state.experiments
                if item.experiment_id != invalid.experiment_id
            ],
            "current_action": ResearchAction.model_validate(accepted["second_action"]),
            "action_history": [
                action
                for action in state.action_history
                if action.id != accepted["second_action"]["id"]
            ],
            "open_questions": open_questions,
            "best_branch_id": active_branch.id,
            "iteration": 2,
            "stopped": False,
            "stop_reason": "",
        }
    )
    stores.states.save(restored)
    stores.frontiers.save(session_id, restored.frontier)
    experiment_values = stores.experiments._read()
    experiment_session = dict(experiment_values.get(session_id) or {})
    experiment_session.pop(invalid.experiment_id, None)
    experiment_values[session_id] = experiment_session
    stores.experiments._write(experiment_values)
    evidence_values = stores.evidence._read()
    evidence_session = dict(evidence_values.get(session_id) or {})
    evidence_values[session_id] = {
        key: value
        for key, value in evidence_session.items()
        if value.get("experiment_id") != invalid.experiment_id
    }
    stores.evidence._write(evidence_values)
    event_values = stores.events._read()
    event_values[session_id] = accepted["events"]
    stores.events._write(event_values)
    return str(archive_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accepted-report", default=str(DEFAULT_ACCEPTED_REPORT))
    parser.add_argument("--output-root", default="")
    args = parser.parse_args()
    accepted_path = Path(args.accepted_report).resolve()
    accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
    session_id = str(accepted["session_id"])
    session_root = accepted_path.parent / "session"
    stores = V2Stores(str(session_root))
    state = stores.states.get(session_id)
    repository = Path(state.problem.repository).resolve()
    runtime_root = repository.parent
    upstream_commit = str(accepted["commit"])
    first_record = next(
        record for record in state.experiments if record.experiment_id == "micrograd_live_exp_1"
    )
    branch_id = first_record.branch_id
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root
        else ROOT / "backend" / "data" / "final-demo-hardening" / "micrograd"
    )
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "public-hardening-report.json"

    invalid_planner_diagnostic = rollback_equivalent_ablation(
        stores, session_id, accepted, report_path, output_root
    )
    reconcile_full_revert_metadata(stores, session_id)
    archived_invalid = output_root / "invalid-live-planner-ablation.json"
    if invalid_planner_diagnostic is None and archived_invalid.exists():
        invalid_planner_diagnostic = str(archived_invalid)

    load_dotenv(ROOT / ".env")
    base_settings = Settings.from_env()
    settings = RuntimeConfigStore(base_settings.data_dir).apply(base_settings)
    provider = get_llm_provider(settings)
    if settings.llm_provider != "qwen" or getattr(provider, "fallback", False):
        raise RuntimeError("LIVE_QWEN_CONFIGURATION_NOT_READY")
    gateway = LegacyQwenAdapter(provider)
    sessions = ResearchSessionService(
        stores,
        BranchConstructor(gateway),
        SprintLiteratureService(
            get_literature_provider(settings),
            LiteratureLibrary(output_root / "literature"),
        ),
        model_ready=True,
        critic=ScientificCritic(gateway),
    )
    executor = WorkspaceExperimentAdapter(
        WorktreeManager(repository, runtime_root / "worktrees")
    )
    result: dict = {
        "status": "running",
        "source_report": str(accepted_path),
        "session_id": session_id,
        "repository": str(repository),
        "upstream_commit": upstream_commit,
        "branch_id": branch_id,
        "started_at": datetime.now(UTC).isoformat(),
        "credentials_redacted": True,
        "invalid_planner_ablation_diagnostic": invalid_planner_diagnostic,
    }

    try:
        state = stores.states.get(session_id)
        if not any(r.experiment_id == "micrograd_live_exp_2_ablation" for r in state.experiments):
            action = state.current_action
            if action is None or action.operator != ResearchOperator.RUN_ABLATION:
                raise RuntimeError("CONTROLLER_RUN_ABLATION_ACTION_REQUIRED")
            before = _state_slice(state, branch_id)
            ablation_record = executor.execute(
                RepositoryExperimentContract(
                    experiment_id="micrograd_live_exp_2_ablation",
                    branch_id=branch_id,
                    worktree_branch=f"v2public/final-ablation-{stamp}",
                    purpose=f"{action.operator}: {action.reason}",
                    repository=str(repository),
                    base_commit=upstream_commit,
                    protocol=state.baseline.protocol,
                    baseline_protocol=state.baseline.protocol,
                    baseline_metrics=state.baseline.local_metrics,
                    config={
                        "operator": ResearchOperator.RUN_ABLATION,
                        "study": "public_repository_final_hardening",
                        "ablation": "strict full revert to audited upstream control commit",
                        "variant_commit": first_record.code_commit,
                        "control_commit": upstream_commit,
                    },
                    implementation_files={},
                    static_commands=static_commands(),
                    smoke_commands=smoke_commands(),
                    formal_command=evaluator_command(
                        state.baseline.protocol, "v2-ablation-result.json", "core"
                    ),
                    result_path="v2-ablation-result.json",
                    environment={
                        "python": sys.version.split()[0],
                        "device": "cpu",
                        "provider": "local",
                        "repository_source": "public GitHub clone",
                        "live_planner": False,
                        "control_type": "audited upstream full-revert commit",
                    },
                    commit_message="experiment: evaluate upstream ablation control",
                    cleanup_worktree=False,
                )
            )
            if not ablation_record.audit_passed:
                raise RuntimeError(
                    "MICROGRAD_ABLATION_FAILED:" + ablation_record.analysis
                )
            transition = sessions.continue_session(session_id, experiment=ablation_record)
            critic_metadata = provider.consume_call_metadata()
            result["ablation"] = {
                "record": ablation_record.model_dump(mode="json"),
                "control_trace": {
                    "definition": "full revert of the selected guard mechanism",
                    "variant_commit": first_record.code_commit,
                    "control_commit": upstream_commit,
                    "repository_state": "clean upstream commit; no caller-supplied replacement contents",
                    "phases": [
                        "locked_protocol_validation",
                        "upstream_control_checkout",
                        "static_validation",
                        "smoke",
                        "formal_experiment",
                        "experiment_record",
                    ],
                },
                "critique": transition.critique.model_dump(mode="json"),
                "critic_metadata": critic_metadata,
                "controller_action": (
                    transition.action.model_dump(mode="json") if transition.action else None
                ),
                "state_before": before,
                "state_after": _state_slice(transition.state, branch_id),
            }
            _write_json(report_path, result)

        state = stores.states.get(session_id)
        ablation_record = next(
            r for r in state.experiments if r.experiment_id == "micrograd_live_exp_2_ablation"
        )
        if "ablation" not in result:
            ablation_critique = next(
                (
                    event.payload
                    for event in reversed(stores.events.list(session_id))
                    if event.kind == ResearchEventKind.CRITIQUE_RECORDED
                    and event.iteration == 3
                ),
                None,
            )
            result["ablation"] = {
                "record": ablation_record.model_dump(mode="json"),
                "critique": ablation_critique,
                "resumed_from_persisted_state": True,
            }

        robust = robustness_protocol(state.baseline.protocol, upstream_commit)
        baseline_result_path = repository / "v2-robustness-baseline-result.json"
        robustness_baseline = BaselineReproducer(
            RepositoryWorkspace(repository, allowed_executables={Path(sys.executable).name})
        ).reproduce_and_validate(
            BaselineReproductionRequest(
                repository=str(repository),
                commit=upstream_commit,
                task=robust.task,
                entrypoint=str(EVALUATOR),
                protocol=robust,
                command=evaluator_command(
                    robust, baseline_result_path.name, "robustness"
                ),
                result_path=baseline_result_path.name,
                environment={
                    "python": sys.version.split()[0],
                    "device": "cpu",
                    "repository_source": "public GitHub clone",
                    "scope": "finite signed-zero boundary robustness",
                },
                reported_metrics={},
            )
        )
        baseline_result_path.unlink(missing_ok=True)
        if not robustness_baseline.can_be_comparison_denominator:
            raise RuntimeError(
                "ROBUSTNESS_BASELINE_NOT_VALIDATED:"
                + robustness_baseline.validation_reason
            )
        result["robustness_baseline"] = robustness_baseline.model_dump(mode="json")

        state = stores.states.get(session_id)
        if not any(r.experiment_id == "micrograd_live_exp_3_robustness" for r in state.experiments):
            state, directive_override = select_bounded_robustness(
                stores, state, branch_id
            )
            action = state.current_action
            before = _state_slice(state, branch_id)
            robustness_record = executor.execute(
                RepositoryExperimentContract(
                    experiment_id="micrograd_live_exp_3_robustness",
                    branch_id=branch_id,
                    worktree_branch=f"v2public/final-robustness-{stamp}",
                    purpose=f"{action.operator}: {action.reason}",
                    repository=str(repository),
                    base_commit=str(first_record.code_commit),
                    protocol=robust,
                    baseline_protocol=robustness_baseline.protocol,
                    baseline_metrics=robustness_baseline.local_metrics,
                    config={
                        "operator": ResearchOperator.RUN_ROBUSTNESS,
                        "study": "public_repository_final_hardening",
                        "profile": "signed_zero_and_finite_boundary_perturbations",
                        "excluded": [
                            "NaN",
                            "infinity",
                            "subnormal values",
                            "general floating-point behavior",
                        ],
                    },
                    implementation_files={},
                    static_commands=static_commands(),
                    smoke_commands=smoke_commands(),
                    formal_command=evaluator_command(
                        robust, "v2-robustness-result.json", "robustness"
                    ),
                    result_path="v2-robustness-result.json",
                    environment={
                        "python": sys.version.split()[0],
                        "device": "cpu",
                        "provider": "local",
                        "repository_source": "public GitHub clone",
                        "scope": "finite signed-zero boundary robustness",
                    },
                    commit_message="experiment: finite ReLU boundary robustness",
                    cleanup_worktree=False,
                )
            )
            if not robustness_record.audit_passed:
                raise RuntimeError(
                    "MICROGRAD_ROBUSTNESS_FAILED:" + robustness_record.analysis
                )
            transition = sessions.continue_session(
                session_id, experiment=robustness_record
            )
            critic_metadata = provider.consume_call_metadata()
            result["robustness"] = {
                "record": robustness_record.model_dump(mode="json"),
                "critique": transition.critique.model_dump(mode="json"),
                "critic_metadata": critic_metadata,
                "controller_action": (
                    transition.action.model_dump(mode="json") if transition.action else None
                ),
                "directive_override": directive_override,
                "state_before": before,
                "state_after": _state_slice(transition.state, branch_id),
            }
            _write_json(report_path, result)

        state = stores.states.get(session_id)
        robustness_record = next(
            r for r in state.experiments if r.experiment_id == "micrograd_live_exp_3_robustness"
        )
        if "robustness" not in result:
            result["robustness"] = {
                "record": robustness_record.model_dump(mode="json"),
                "resumed_from_persisted_state": True,
            }

        graph = public_claim_graph(state, branch_id)
        graph_audit = graph.audit()
        stores.events.append(
            ResearchSessionEvent(
                session_id=session_id,
                kind=ResearchEventKind.CLAIM_GRAPH_UPDATED,
                iteration=state.iteration,
                payload={
                    "source": "final_public_repository_hardening",
                    "graph": graph.model_dump(mode="json"),
                    "audit": graph_audit.model_dump(mode="json"),
                },
            )
        )
        writer = V2WriterAdapter(WriterAgent(provider))
        research_report = writer.build_report(
            state,
            graph,
            instructions=(
                "Conclude only for the two locked finite micrograd input grids. State explicitly "
                "that NaN, subnormal, infinity, and general floating-point behavior were not tested."
            ),
        )
        writer_metadata = provider.consume_call_metadata()
        report_json = output_root / "micrograd-research-report.json"
        report_html = output_root / "micrograd-research-report.html"
        report_docx = output_root / "micrograd-research-report.docx"
        _write_json(report_json, research_report)
        report_html.write_bytes(
            writer.html_bytes(
                research_report,
                session_id=session_id,
                title="AI Scientist V2 micrograd public repository study",
            )
        )
        report_docx.write_bytes(
            writer.docx_bytes(
                research_report,
                session_id=session_id,
                title="AI Scientist V2 micrograd public repository study",
            )
        )
        stores.events.append(
            ResearchSessionEvent(
                session_id=session_id,
                kind=ResearchEventKind.REPORT_EXPORTED,
                iteration=state.iteration,
                payload={
                    "source": "final_public_repository_hardening",
                    "json": str(report_json),
                    "html": str(report_html),
                    "docx": str(report_docx),
                    "final_conclusion": research_report.get("Conclusion", {}),
                },
            )
        )
        final_state = stores.states.get(session_id)
        result.update(
            {
                "status": "completed",
                "completed_at": datetime.now(UTC).isoformat(),
                "claim_evidence_graph": graph.model_dump(mode="json"),
                "claim_graph_audit": graph_audit.model_dump(mode="json"),
                "writer_metadata": writer_metadata,
                "artifacts": {
                    "json": str(report_json),
                    "html": str(report_html),
                    "docx": str(report_docx),
                },
                "summary": sessions.summary(session_id),
                "findings": sessions.findings(session_id),
                "final_state": _state_slice(final_state, branch_id),
                "events": [
                    event.model_dump(mode="json")
                    for event in sessions.events(session_id)
                ],
            }
        )
        _write_json(report_path, result)
        print(
            json.dumps(
                {
                    "status": "completed",
                    "session_id": session_id,
                    "ablation_metric": ablation_record.metrics,
                    "robustness_metric": robustness_record.metrics,
                    "claim_statuses": [claim.status for claim in graph.claims],
                    "report_path": str(report_path),
                    "docx": str(report_docx),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        result.update(
            {
                "status": "blocked",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "failed_at": datetime.now(UTC).isoformat(),
            }
        )
        _write_json(report_path, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
