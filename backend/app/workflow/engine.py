from __future__ import annotations

import inspect
import hashlib
import json
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock
from uuid import uuid4

from backend.app.agents.critic import CriticAgent
from backend.app.agents.diagnostic import ExperimentDiagnosticAgent
from backend.app.agents.experiment import ExperimentAgent, ExperimentBundleCandidateError
from backend.app.agents.idea import IdeaAgent
from backend.app.agents.idea_selection import IdeaSelectionAgent
from backend.app.agents.planner import (
    PLAN_REVIEW_FIXED_INSTRUCTIONS,
    PLAN_REVIEW_PROMPT_SCHEMA_VERSION,
    PLAN_REVISION_FIXED_INSTRUCTIONS,
    PlanningAgent,
    build_plan_review_runtime_contract,
    build_plan_revision_runtime_contract,
    plan_review_schema_snapshot,
    plan_revision_patch_schema,
    plan_revision_schema_snapshot,
)
from backend.app.agents.reviewer import ValidationDecision
from backend.app.agents.research import ResearchAgent
from backend.app.agents.supervisor import Delegation, SupervisorAgent
from backend.app.agents.writer import ReportFactAuditError, WriterAgent
from backend.app.models.experiment import ExperimentBundle
from backend.app.models.provider import EvidenceCard
from backend.app.providers.experiment import ExperimentProvider
from backend.app.providers.experiment import validate_local_gpu_preflight
from backend.app.providers.literature import LiteratureProvider
from backend.app.providers.llm import LLMProvider, LLMRequestCancelled
from backend.app.storage.repository import Repository
from backend.app.storage.literature import LiteratureLibrary
from backend.app.storage.research_wiki import ResearchWikiStore
from backend.app.workflow.hypothesis_contract import (
    MAX_HYPOTHESIS_CANDIDATES,
    hypothesis_candidate_issues,
    normalize_candidate,
    normalize_candidates,
    normalize_hypothesis_content,
)
from backend.app.workflow.idea_selection import (
    AUTO_SELECT_THRESHOLD,
    WEIGHTS,
    composite_score,
    normalize_idea_review,
    weighted_score,
)
from backend.app.workflow.dataset_catalog import (
    dataset_card,
    dataset_display_name,
    normalize_dataset_name,
)
from backend.app.workflow.dataset_inspection import (
    contract_canonical_name,
    dataset_option,
    inspect_dataset_directory,
    resolve_local_dataset_directory,
)
from backend.app.workflow.research_constraints import normalize_constraints
from backend.app.workflow.phase2_evidence import (
    baseline_profile,
    dataset_profile as phase2_dataset_profile,
    fair_experiment_contract,
    paired_seed_metrics,
    progressive_protocol,
    result_evidence,
    route_result,
)
from backend.app.workflow.evidence_audit import (
    build_evidence_audit,
)
from backend.app.workflow.evidence_pipeline import (
    analyze_research_gaps,
    candidate_evidence_map,
    extract_claim_evidence,
    targeted_queries as candidate_targeted_queries,
)
from backend.app.workflow.artifact_lineage import experiment_bundle_ids
from backend.app.workflow.experiment_code import (
    experiment_validation_issues,
    smoke_data_reduction_issues,
)
from backend.app.workflow.experiment_harness import compile_bundle_runtime_contract
from backend.app.workflow.scientific_integrity import (
    compile_scientific_contract,
    scientific_feedback,
    validate_coverage,
    validate_split_contract,
)
from backend.app.workflow.scientific_evolution import (
    build_working_hypothesis,
    detect_disagreement,
    evolution_decision,
    normalize_scientific_analysis,
    synthesize_scientific_conclusion,
    unavailable_secondary_review,
)
from backend.app.workflow.plan_contract import (
    CANONICAL_PLAN_CONTRACT_FIELDS,
    FIELD_ALIAS_TO_CANONICAL,
    authoritative_plan_contract,
    canonical_training_epochs,
    canonical_contract_field,
    execution_training_budget,
    merge_plan_patch,
    normalize_plan,
)
from backend.app.workflow.plan_review_governance import (
    GOVERNANCE_IMPLEMENTATION_SEMANTIC_VERSION,
    PlanReviewPolicyIntegrityError,
    adjudicate_review,
    canonical_sha256,
    changed_contract_fields,
    deterministic_fix_map,
    fix_map_issues,
    freeze_plan_review_recovery,
    freeze_plan_governance_migration,
    freeze_review_policy,
    is_plan_governance_accepted,
    normalize_skill_content,
    validate_frozen_review_policy,
    validate_plan_review_recovery,
    validate_plan_governance_migration,
)
from backend.app.workflow.policies import (
    competition_export_allowed,
    feedback_requires_follow_up,
    normalize_feedback_decision,
    normalize_feedback_verdict,
)
from backend.app.workflow.research_state import build_research_state
from backend.app.workflow.serial_iteration import (
    build_iteration_memory, continuation_stop, direction_issues,
    freeze_iteration_policy, implementation_base, prompt_memory, trial_signature,
)
from backend.app.workflow.knowledge import (
    KnowledgeIntegrationService,
)
from backend.app.workflow.prompt_context import (
    PromptContextBudget,
    budget_instructions,
    build_hypothesis_context,
    compact_problem,
    filter_and_dedupe_hypothesis_cards,
    literature_card,
    select_units,
    select_units_bounded,
)
from backend.app.workflow.research_synthesis import (
    build_research_synthesis,
    build_gap_processing_pipeline,
    candidate_synthesis_provenance_issues,
    candidate_code_evidence_provenance_issues,
    normalize_candidate_code_evidence_provenance,
    normalize_candidate_synthesis_provenance,
    synthesis_prompt_context,
)
from backend.app.workflow.github_source import GitHubSourceInspector
from backend.app.workflow.scientific_stability import (
    annotate_dataset_semantics,
    build_world_state,
    context_telemetry,
    infer_research_profile,
    next_research_stage,
    protocol_state,
    readiness_state,
    selected_hypothesis_digest,
    EvidenceInsufficientGapsError,
    failure_state_for,
)
from backend.app.workflow.skill_runtime import RuntimePackage, SkillRuntime, ToolRegistry
from backend.app.workflow.skills import (
    EXCLUDED_CATALOG_DIRECTORIES,
    SkillCatalog,
    SkillLoader,
    SkillRegistry,
)
from backend.app.workflow.steps import ORDER


_STEP_REQUIRED_INPUTS = {
    "knowledge_integration": ("problem",),
    "hypothesis_generation": ("problem", "evidence", "research_synthesis"),
    "evidence_reasoning": ("problem", "evidence", "hypothesis"),
    "experiment_task": ("plan",),
    "experiment_run_analysis": ("plan", "experiment_task"),
    "feedback_revision": ("plan", "experiment_result"),
}

EVIDENCE_REASONING_PROMPT_VERSION = "v2-claim-evidence-recovery"
MAX_TARGETED_RETRIEVAL_ROUNDS = 2


class WorkflowEngine:
    universal_scientific_stability = True
    def __init__(
        self,
        repository: Repository,
        llm_provider: LLMProvider,
        literature_provider: LiteratureProvider,
        experiment_provider: ExperimentProvider,
        skill_loader: SkillLoader | None = None,
        skill_registry: SkillRegistry | None = None,
        skill_catalog: SkillCatalog | None = None,
        supervisor_agent: SupervisorAgent | None = None,
        skill_runtime: SkillRuntime | None = None,
        knowledge_service: KnowledgeIntegrationService | None = None,
        competition_mode: bool = False,
        max_feedback_iterations: int = 4,
        max_deepseek_plan_revision: int = 2,
        github_source_inspector: GitHubSourceInspector | None = None,
    ) -> None:
        self.repository = repository
        self.llm_provider = llm_provider
        self.literature_provider = literature_provider
        self.experiment_provider = experiment_provider
        self.competition_mode = competition_mode
        self.max_feedback_iterations = max(1, int(max_feedback_iterations))
        self.max_deepseek_plan_revision = max(0, int(max_deepseek_plan_revision))
        self.github_source_inspector = github_source_inspector or GitHubSourceInspector()
        self._run_locks: dict[str, RLock] = {}
        self._run_locks_guard = Lock()
        data_root = Path(self.repository.store.data_dir)
        self.knowledge_service = knowledge_service or KnowledgeIntegrationService(
            ResearchWikiStore(data_root / "research-wiki"),
            LiteratureLibrary(data_root / "literature"),
            literature_provider,
        )
        self._skill_loader = skill_loader or SkillLoader(Path(__file__).resolve().parents[3])
        self.skill_registry = skill_registry or SkillRegistry()
        self.skill_catalog = skill_catalog or SkillCatalog(self._skill_loader)
        self.research_agent = ResearchAgent(llm_provider)
        self.hypothesis_agent = IdeaAgent(llm_provider)
        self.idea_selection_agent = IdeaSelectionAgent(llm_provider)
        self.planning_agent = PlanningAgent(llm_provider)
        self.experiment_agent = ExperimentAgent(experiment_provider, llm_provider)
        self.diagnostic_agent = ExperimentDiagnosticAgent(llm_provider)
        self.critic_agent = CriticAgent(llm_provider)
        self.writer_agent = WriterAgent(llm_provider)
        self.tool_registry = self._build_tool_registry()
        self.configured_tools = self.tool_registry.names()
        self.skill_runtime = skill_runtime or SkillRuntime(
            self._skill_loader,
            self.skill_registry,
            self.tool_registry,
        )
        self.supervisor_agent = supervisor_agent or SupervisorAgent(self.skill_registry)

    @property
    def skill_loader(self) -> SkillLoader:
        return self._skill_loader

    @skill_loader.setter
    def skill_loader(self, loader: SkillLoader) -> None:
        self._skill_loader = loader
        self.skill_catalog = SkillCatalog(loader)
        self.skill_runtime = SkillRuntime(loader, self.skill_registry, self.tool_registry)
        reviewer = getattr(self.supervisor_agent, "reviewer", None)
        self.supervisor_agent = SupervisorAgent(self.skill_registry, reviewer)

    def _run_lock(self, run_id: str) -> RLock:
        with self._run_locks_guard:
            return self._run_locks.setdefault(run_id, RLock())

    def run_step(self, run_id: str, step_id: str, *, force: bool = False):
        with self._run_lock(run_id):
            if step_id not in ORDER:
                raise ValueError(f"UNKNOWN_WORKFLOW_STEP:{step_id}")
            if step_id == "feedback_revision":
                existing = self.repository.get_run(run_id)
                latest = self._latest_by_type(existing.artifacts)
                if self._result_already_reviewed(
                    existing.artifacts, latest.get("experiment_result")
                ):
                    return existing
            begin_run = getattr(self.llm_provider, "begin_run", None)
            end_run = getattr(self.llm_provider, "end_run", None)
            if callable(begin_run):
                begin_run(run_id)
            try:
                self.repository.update_step_state(run_id, step_id, "running")
                try:
                    if self.repository.get_run(run_id).stop_requested:
                        raise LLMRequestCancelled()
                    self._run_step(run_id, step_id, force=force)
                except LLMRequestCancelled as exc:
                    self.repository.update_step_state(
                        run_id,
                        step_id,
                        "interrupted",
                        error={"code": "PIPELINE_STOPPED", "message": str(exc)},
                    )
                    raise
                except Exception as exc:
                    state = failure_state_for(exc)
                    recoverable = state in {
                        "RECOVERABLE_PROVIDER_ERROR",
                        "POLICY_INTEGRITY_REQUIRED",
                        "EVIDENCE_RETRY_REQUIRED",
                    }
                    self.repository.update_step_state(
                        run_id,
                        step_id,
                        "interrupted" if recoverable else "failed",
                        error={
                            "code": str(exc).split(":", 1)[0],
                            "message": str(exc),
                            "recoverable": recoverable,
                            "user_action_required": state == "POLICY_INTEGRITY_REQUIRED",
                        },
                    )
                    if state == "POLICY_INTEGRITY_REQUIRED":
                        self.repository.update_workflow_state(
                            run_id,
                            status=state,
                            current_step="research_plan",
                            automatic=False,
                            stop_requested=False,
                        )
                    raise
                current = self.repository.get_run(run_id)
                current_step = next(item for item in current.steps if item.id == step_id)
                if current_step.status == "running":
                    self.repository.update_step_state(run_id, step_id, "completed")
                return self.repository.get_run(run_id)
            finally:
                if callable(end_run):
                    end_run(run_id)

    def cancel(self, run_id: str) -> bool:
        cancel_run = getattr(self.llm_provider, "cancel_run", None)
        return bool(cancel_run(run_id)) if callable(cancel_run) else False

    def preflight_run(self, run_id: str) -> dict:
        """Persist a secret-free admission result without running a workflow step."""
        run = self.repository.get_run(run_id)
        checks = []
        def add(name, ok, code="", detail=""):
            checks.append({"name": name, "ok": bool(ok), "code": code, "detail": detail})
        settings = getattr(self.experiment_provider, "settings", None)
        mode = getattr(self.llm_provider, "mode", "")
        def provider_check(provider_id: str, model: str | None = None):
            name = provider_id if model is None else f"{provider_id}:{model}"
            if mode == "mock":
                add(name, True, "MOCK_MODE", "Development mock provider; no external request was made.")
                return
            try:
                if model is not None and callable(getattr(self.llm_provider, "preflight_model", None)):
                    result = self.llm_provider.preflight_model(provider_id, model)
                else:
                    result = self.llm_provider.preflight(provider_id)
                add(name, True, "AVAILABLE", f"model={result.get('model', 'configured')}; structured_request=passed")
            except Exception as exc:
                detail = str(exc)
                for secret in (getattr(settings, "qwen_api_key", ""), getattr(settings, "deepseek_api_key", "")):
                    if secret:
                        detail = detail.replace(str(secret), "[REDACTED]")
                add(name, False, detail.split(":", 1)[0], detail[:500])
        configured = getattr(self.llm_provider, "configured_provider_models", None)
        pairs = configured() if callable(configured) else []
        if pairs:
            for provider_id, model in pairs:
                provider_check(provider_id, model)
        elif callable(configured):
            # Role-routed provider with zero configured roles. Admission must
            # fail explicitly; there is no default model to substitute.
            add(
                "model_roles",
                False,
                "MODEL_ROLE_NOT_CONFIGURED",
                "No model role is configured. Automatic scientific execution requires "
                "an explicit role -> {provider_id, model} assignment; no qwen/deepseek "
                "default will be used.",
            )
        else:
            # Non-role-routed provider (mock/dev). Never synthesize default
            # qwen/deepseek pairs; use the provider's own admission check.
            add(
                "provider_roles",
                True,
                "ROLE_ROUTING_NA",
                "Provider does not expose role pairs; no default model fallback applied.",
            )
            preflight_fn = getattr(self.llm_provider, "preflight", None)
            if callable(preflight_fn):
                try:
                    preflight_fn("provider")
                except Exception as exc:
                    add("provider_roles", False, str(exc).split(":", 1)[0], str(exc)[:500])
        try:
            profile = self._inspect_configured_local_dataset(run)
            add("dataset", True, detail=(profile or {}).get("contract_id", "configured"))
        except Exception as exc:
            add("dataset", False, str(exc).split(":", 1)[0], str(exc))
        status = getattr(settings, "provider_status", lambda: {})()
        experiment = status.get("experiment", {}) if isinstance(status, dict) else {}
        if getattr(settings, "experiment_provider", "") == "local_gpu":
            runtime_check = validate_local_gpu_preflight(settings)
            add("experiment_environment", bool(runtime_check.get("ok")), str(runtime_check.get("code") or ""), str(runtime_check.get("message") or ""))
        else:
            add("experiment_environment", bool(experiment.get("ready", True)), str(experiment.get("code") or ""))
        if run.github_repository_url:
            inspected = self.github_source_inspector.inspect(run.github_repository_url)
            add("repository", inspected.github_source_status == "parsed", inspected.github_source_status, "; ".join(inspected.warnings[:3]))
        else:
            add("repository", True, "NOT_PROVIDED")
        payload = {"schema_version": 1, "run_id": run_id, "blocking": not all(item["ok"] for item in checks), "checks": checks}
        self.repository.add_artifact(run_id, "run_preflight", "Run Preflight", payload, "preflight", "Foundation Preflight")
        return payload

    def _ensure_research_constraints(self, run_id: str):
        run = self.repository.get_run(run_id)
        if run.research_constraints_artifact_id:
            return self.repository.get_artifact(run_id, run.research_constraints_artifact_id)
        artifact = self.repository.add_artifact(run_id, "research_constraints", "Frozen Research Constraints", normalize_constraints(run.research_constraints, run.constraints), "preflight", "Foundation Preflight")
        run = self.repository.get_run(run_id)
        run.research_constraints_artifact_id = artifact.id
        self.repository.save_run(run)
        return artifact

    def _persist_scientific_world_state(self, run_id: str, run, profile: dict, dataset: dict,
                                        protocol: dict, readiness: dict, stage: str,
                                        issue_ledger: list[dict]) -> None:
        world = build_world_state(run=run, profile=profile, dataset=dataset, protocol=protocol,
                                  readiness=readiness, stage=stage, issue_ledger=issue_ledger)
        current = self.repository.get_run(run_id)
        current.scientific_world_state = world
        self.repository.save_run(current)
        self.repository.add_artifact(run_id, "scientific_world_state", "Scientific World State", world,
                                     "research_plan", "Scientific Stability Engine")

    def _run_step(self, run_id: str, step_id: str, *, force: bool = False):
        if step_id not in ORDER:
            raise ValueError(f"UNKNOWN_WORKFLOW_STEP:{step_id}")
        self.skill_registry.assignment_for(step_id)
        run = self.repository.get_run(run_id)
        if self._has_locked_output(run.artifacts, step_id):
            self.repository.append_event(
                run_id,
                step_id,
                self.supervisor_agent.name,
                "Skipped step because locked artifacts already exist.",
                provider_mode=self.llm_provider.mode,
                fallback_used=self.llm_provider.fallback,
                fallback_reason="Mock LLM development fallback." if self.llm_provider.fallback else "",
            )
            return self.repository.get_run(run_id)

        latest = self._latest_by_type(run.artifacts)
        if step_id == "feedback_revision" and self._result_already_reviewed(
            run.artifacts, latest.get("experiment_result")
        ):
            return run
        if step_id == "report_export":
            if latest.get("report") is not None and not force:
                return run
            self._require_report_readiness(run.artifacts, latest)
        self._require_step_inputs(step_id, latest)
        state = self._skill_state(step_id, latest)
        delegation = self.supervisor_agent.delegate(step_id, state)
        frozen_policy_artifacts = (
            [item for item in run.artifacts if item.type == "plan_review_policy"]
            if step_id == "research_plan"
            else []
        )
        if step_id == "research_plan" and not frozen_policy_artifacts and any(
            item.type in {
                "plan_review_issue_ledger",
                "plan_review_round_state",
                "plan_review_revision_request",
                "plan_review_change_request",
                "plan_refinement_proposal",
                "plan_revision_required",
                "plan_governance_migration",
            }
            or (
                item.type in {"research_plan_candidate", "plan_review", "plan"}
                and bool((item.content or {}).get("policy_artifact_id"))
            )
            for item in run.artifacts
        ):
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:policy_missing_with_governance_history"
            )
        if frozen_policy_artifacts:
            if len(frozen_policy_artifacts) != 1:
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:multiple_policy_artifacts"
                )
            frozen_runtime = validate_frozen_review_policy(
                frozen_policy_artifacts[0].content or {}
            )
            package = RuntimePackage(
                step_id=step_id,
                agent_id=delegation.agent_id,
                skill_ids=tuple(frozen_runtime["active_skill_ids"]),
                instructions=str(frozen_runtime["runtime_instructions"]),
                authorized_tools=(),
                omitted_sections=(),
                audit={
                    "skill_hashes": deepcopy(frozen_runtime["instruction_hashes"]),
                    "instruction_sha256": frozen_runtime["runtime_instructions_sha256"],
                    "declared_tools": [],
                    "authorized_tools": [],
                    "denied_tools": [],
                    "omitted_sections": [],
                    "skill_invocations": [],
                },
            )
        else:
            package = self.skill_runtime.prepare(
                step_id,
                delegation.agent_id,
                self.configured_tools,
                state,
            )
        instructions = package.instructions
        skill_calls = [delegation.tool_call, self._runtime_call(package)]
        if step_id == "problem_understanding":
            self.supervisor_agent.require_agent(delegation, "research")
            dataset_profile_artifact = latest.get("dataset_profile")
            dataset_profile = (
                dataset_profile_artifact.content
                if dataset_profile_artifact is not None
                else self._inspect_configured_local_dataset(run)
            )
            if dataset_profile is not None and dataset_profile.get("dataset_profile_version") != 2:
                dataset_profile = phase2_dataset_profile(
                    dataset_profile, normalize_constraints(run.research_constraints, run.constraints)
                )
            if dataset_profile is not None and dataset_profile_artifact is None:
                dataset_profile_artifact = self.repository.add_artifact(
                    run_id,
                    "dataset_profile",
                    "Verified Local Dataset Profile",
                    dataset_profile,
                    "dataset_inspection",
                    "Dataset Inspector",
                )
                self.repository.lock_artifact(
                    run_id, dataset_profile_artifact.id, True
                )
                self.repository.append_event(
                    run_id,
                    "dataset_inspection",
                    "Dataset Inspector",
                    "Inspected and locked the configured local dataset before hypothesis generation.",
                    data={
                        "contract_id": dataset_profile["contract_id"],
                        "root": dataset_profile["root"],
                        "file_count": dataset_profile["file_count"],
                        "content_fingerprint": dataset_profile["content_fingerprint"],
                    },
                    output_summary={
                        "inspection_status": dataset_profile["inspection_status"],
                        "schemas": dataset_profile["schemas"],
                    },
                )
            dataset_instruction = ""
            if dataset_profile is not None:
                dataset_instruction = (
                    "\n\n## Authoritative local dataset\n"
                    "The dataset_profile included with the structured problem was produced from "
                    "the user's selected local directory. Treat it as immutable ground truth. "
                    "Do not replace it with a public, synthetic, image, or fallback dataset."
                )
            content = self._produce_validated(
                run_id,
                step_id,
                lambda revision: self.research_agent.structure_problem(
                    run.problem_input,
                    instructions=self._with_revision(
                        f"{instructions}{dataset_instruction}", revision
                    ),
                ),
            )
            if dataset_profile is not None:
                content["dataset_profile"] = dataset_profile
            self.repository.add_artifact(
                run_id, "problem", "Structured Problem", content, step_id, self.research_agent.name
            )
            if not any(a.type == "iteration_policy" for a in run.artifacts):
                iteration_policy_artifact = self.repository.add_artifact(
                    run_id, "iteration_policy", "Frozen Iteration Policy",
                    freeze_iteration_policy(content, run.problem_input, self.max_feedback_iterations),
                    "iteration_policy", "Workflow Engine",
                )
                self.repository.lock_artifact(run_id, iteration_policy_artifact.id, True)
            self._trace(
                run_id,
                step_id,
                self.research_agent.name,
                "Structured problem input.",
                {"problem_input": run.problem_input},
                content,
                skill_calls=skill_calls,
            )
        elif step_id == "knowledge_integration":
            self.supervisor_agent.require_agent(delegation, "research")
            self._require_tools(
                package,
                "query_wiki",
                "search_local_literature",
                "literature_search",
            )
            problem = latest["problem"].content
            result_box = {}

            def collect_evidence(_revision):
                result_box["result"] = self.knowledge_service.collect(
                    run_id,
                    problem,
                    knowledge_base_id=run.knowledge_base_id,
                )
                return result_box["result"].model_dump()

            output = self._produce_validated(run_id, step_id, collect_evidence)
            result = result_box["result"]
            self.repository.add_artifact(
                run_id, "evidence", "Verified Evidence", output, step_id, self.research_agent.name
            )
            # `references` is the complete exportable collection.  Older/mock
            # providers may expose only core cards, so retain that historical
            # compatibility without applying a new positional truncation.
            synthesis_sources = list(result.references or result.core_references)
            synthesis = build_research_synthesis(synthesis_sources)
            synthesis["literature_coverage"] = deepcopy(result.literature_coverage)
            github_source = None
            if run.github_repository_url:
                github_source = self.github_source_inspector.inspect(run.github_repository_url)
                github_content = github_source.model_dump()
                self.repository.add_artifact(
                    run_id, "github_source", "GitHub Source Inspection", github_content,
                    step_id, "GitHub Source Inspector",
                )
                if github_source.github_source_status == "parsed":
                    synthesis["code_evidence"] = github_source.code_evidence
                    self.repository.add_artifact(
                        run_id, "code_evidence", "GitHub Code Evidence",
                        {"repository_url": github_source.repository_url, "repository_commit": github_source.repository_commit,
                         "items": github_source.code_evidence},
                        step_id, "GitHub Source Inspector",
                    )
            synthesis["hypothesis_gap_processing"] = build_gap_processing_pipeline(synthesis)
            self.repository.add_artifact(
                run_id,
                "research_synthesis",
                "Research Synthesis",
                synthesis,
                step_id,
                self.research_agent.name,
            )
            gap_count = len(synthesis.get("research_gaps") or [])
            if gap_count == 0:
                # Zero research gaps makes hypothesis grounding structurally
                # impossible (every candidate must cite a real gap).  Stop here
                # with a recoverable, diagnosed error instead of burning the
                # supervisor revision loop downstream.  pipeline/start re-runs
                # this step (fresh evidence draw, bounded external retry).
                coverage = result.literature_coverage or {}
                external_degraded = bool((result.sources or {}).get("external_degraded"))
                diagnosis = (
                    f"EVIDENCE_INSUFFICIENT_GAPS: research_gaps=0 "
                    f"external_degraded={external_degraded} "
                    f"limitations_coverage={coverage.get('limitations_coverage')} "
                    f"future_work_coverage={coverage.get('future_work_coverage')} "
                    f"sufficient={coverage.get('sufficient')}"
                )
                reasons = coverage.get("insufficient_reasons") or []
                if reasons and not external_degraded:
                    diagnosis += " reasons=" + ",".join(str(item) for item in reasons)
                raise EvidenceInsufficientGapsError(diagnosis)
            if result.wiki_changes.papers or result.wiki_changes.gaps or result.wiki_changes.edges:
                self.supervisor_agent.commit_wiki_changes(
                    result.wiki_changes, self.knowledge_service.wiki
                )
            self._trace(
                run_id,
                step_id,
                self.research_agent.name,
                f"Collected {len(result.references)} verified references.",
                {"queries": problem.get("literature_queries") or [run.problem_input]},
                {
                    **output,
                    "research_synthesis": {
                        "paper_count": synthesis["source_collection"]["paper_count"],
                        "theme_count": len(synthesis["themes"]),
                        "gap_count": len(synthesis["research_gaps"]),
                        "future_work_count": len(synthesis["future_work"]),
                        "github_source_status": github_source.github_source_status if github_source else "not_provided",
                        "code_evidence_count": len(synthesis.get("code_evidence") or []),
                    },
                },
                tool_calls=[
                    {
                        "provider": call["source"],
                        "method": "search" if call["source"] != "wiki" else "query",
                        "query": call["query"],
                    }
                    for call in result.sources["calls"]
                ],
                skill_calls=skill_calls,
            )
        elif step_id == "hypothesis_generation":
            self.supervisor_agent.require_agent(delegation, "idea")
            problem = latest["problem"].content
            dataset_profile = (
                latest["dataset_profile"].content
                if latest.get("dataset_profile")
                else problem.get("dataset_profile")
            )
            evidence_content = latest["evidence"].content
            synthesis = latest["research_synthesis"].content
            source_cards = list(
                evidence_content.get("references")
                or evidence_content.get("core_references")
                or []
            )
            # Every valid paper participates in hypothesis formation: only
            # clearly-irrelevant and duplicate cards are removed, and each
            # survivor is compacted into a fixed-size Compact Paper Card.  There
            # is no representative sampling and no character-budget truncation.
            # The relevance filter is question-driven: it derives English terms
            # from the current research question + dataset name (no hardcoded
            # domain vocabulary) and removes only cards that share none of them
            # AND have low existing retrieval relevance.
            question_text = str((problem or {}).get("problem_statement") or "")
            if isinstance(dataset_profile, dict):
                dataset_name = str(
                    dataset_profile.get("canonical_name")
                    or dataset_profile.get("name") or ""
                ).strip()
                if dataset_name.casefold() not in {"data", "dataset", "datasets"}:
                    question_text = f"{question_text} {dataset_name}".strip()
            hypothesis_card_pipeline = filter_and_dedupe_hypothesis_cards(
                source_cards, research_question=question_text
            )
            valid_cards = hypothesis_card_pipeline["cards"]
            evidence = [
                build_hypothesis_context(card, index)
                for index, card in enumerate(valid_cards)
            ]
            context_budget = PromptContextBudget()
            synthesis_context = synthesis_prompt_context(synthesis)
            round_metadata = self._next_hypothesis_round(run, latest)
            revision_context = self._hypothesis_revision_context(run, round_metadata)
            if revision_context:
                synthesis_context["revision_context"] = revision_context
            raw_box = {}
            hypothesis_instructions = budget_instructions(instructions, context_budget)
            if dataset_profile:
                hypothesis_instructions = (
                    f"{instructions}\n\n## Mandatory dataset binding\n"
                    f"Generate hypotheses only for dataset contract "
                    f"{dataset_profile['contract_id']} at {dataset_profile['root']}. "
                    "Every candidate must be compatible with the observed file schemas and must "
                    "not propose another dataset or synthetic replacement."
                )

            def generate_hypothesis(revision):
                raw = self.hypothesis_agent.generate(
                    compact_problem(problem),
                    evidence,
                    research_synthesis=synthesis_context,
                    instructions=self._with_revision(
                        hypothesis_instructions, revision
                    ),
                )
                raw_box["raw"] = raw
                normalized = self._normalize_nonempty_hypothesis(raw)
                code_provenance_issues = candidate_code_evidence_provenance_issues(
                    normalized["candidates"], synthesis
                )
                normalized["candidates"] = [
                    normalize_candidate_code_evidence_provenance(
                        normalize_candidate_synthesis_provenance(candidate, synthesis), synthesis
                    ) for candidate in normalized["candidates"]
                ]
                provenance_issues = candidate_synthesis_provenance_issues(
                    normalized["candidates"], synthesis
                ) + code_provenance_issues
                if provenance_issues:
                    normalized["_validation_issues"] = provenance_issues
                return normalized

            try:
                hypothesis = self._produce_validated(run_id, step_id, generate_hypothesis)
            except ValueError:
                if "raw" in raw_box:
                    self.repository.append_event(
                        run_id,
                        step_id,
                        self.hypothesis_agent.name,
                        "Hypothesis generation returned no usable candidates.",
                        data={
                            "raw_output": raw_box["raw"],
                            "valid_evidence_count": len(evidence),
                            "hypothesis_card_pipeline": hypothesis_card_pipeline["counts"],
                        },
                        output_summary={"accepted": False},
                        provider_mode=self.llm_provider.mode,
                        fallback_used=self.llm_provider.fallback,
                        fallback_reason=(
                            "Mock LLM development fallback." if self.llm_provider.fallback else ""
                        ),
                    )
                raise
            hypothesis["hypothesis_round"] = {
                **round_metadata,
                "created_candidate_ids": [
                    str(candidate.get("candidate_id") or "")
                    for candidate in hypothesis["candidates"]
                ],
            }
            hypothesis_artifact = self.repository.add_artifact(
                run_id,
                "hypothesis",
                f"Candidate Hypothesis · Round {round_metadata['round_index']}",
                hypothesis,
                step_id,
                self.hypothesis_agent.name,
            )
            self._trace(
                run_id,
                step_id,
                self.hypothesis_agent.name,
                "Generated verifiable hypothesis.",
                {
                    "synthesis_paper_count": (synthesis.get("source_collection") or {}).get("paper_count", 0),
                    "synthesis_theme_count": len(synthesis.get("themes") or []),
                    "synthesis_gap_count": len(synthesis.get("research_gaps") or []),
                    "gap_processing": synthesis_context.get("gap_processing") or {},
                    "round": hypothesis["hypothesis_round"],
                    "valid_evidence_count": len(evidence),
                    "hypothesis_card_pipeline": hypothesis_card_pipeline["counts"],
                },
                hypothesis,
                skill_calls=skill_calls,
            )
        elif step_id == "evidence_reasoning":
            self.supervisor_agent.require_agent(delegation, "critic")
            problem = latest["problem"].content
            candidates = normalize_candidates(
                latest["hypothesis"].content.get("candidates") or []
            )
            if not candidates:
                raise ValueError("HYPOTHESIS_CANDIDATES_EMPTY")
            all_evidence = list(latest["evidence"].content["references"])
            focused_evidence = self._focused_evidence_for_candidates(
                all_evidence, candidates
            )
            context_budget = PromptContextBudget()
            evidence = select_units(
                [literature_card(card) for card in focused_evidence],
                context_budget.max_reference_chars,
            )
            compact_scientific_problem = compact_problem(problem)
            review_box = {}
            evidence_set_hash = self._stable_hash(evidence)
            candidate_ids = [self._stable_hash(candidate)[:16] for candidate in candidates]

            def reason_about_evidence(revision):
                evidence_audit = build_evidence_audit(evidence, candidates)
                evidence_audit["policy"]["enforced"] = not self.llm_provider.fallback
                registry_ids = [
                    item["evidence_id"] for item in evidence_audit["registry"]
                ]
                candidate_audits = {
                    item["candidate_index"]: {
                        **item,
                        "registry_evidence_ids": registry_ids,
                    }
                    for item in evidence_audit["candidate_audits"]
                }
                review_audit = {
                    "policy": deepcopy(evidence_audit["policy"]),
                    "registry": [
                        {
                            key: deepcopy(entry[key])
                            for key in ("evidence_id", "title", "verified")
                        }
                        for entry in evidence_audit["registry"]
                    ],
                    "candidate_audits": deepcopy(evidence_audit["candidate_audits"]),
                }
                revised_instructions = budget_instructions(
                    self._with_revision(instructions, revision), context_budget
                )
                review = self._load_idea_review_checkpoint(
                    run_id, candidate_ids, evidence_set_hash, candidates
                )
                raw_review = None
                issues: list[str] = []
                for format_attempt in range(3 if review is None else 0):
                    format_feedback = ""
                    if issues:
                        format_feedback = (
                            "\n\n## Mandatory JSON shape correction\n"
                            + "\n".join(f"- {issue}" for issue in issues)
                            + f"\nReturn a top-level evaluations array with exactly "
                            f"{len(candidates)} entries indexed 0..{len(candidates) - 1}. "
                            "Do not wrap it in idea_selection_review. Do not return an "
                            "active_hypothesis, recommendations, or a selected index."
                        )
                    raw_review = self.idea_selection_agent.review(
                        compact_scientific_problem,
                        run.constraints,
                        evidence,
                        candidates,
                        review_audit,
                        instructions=f"{revised_instructions}{format_feedback}",
                    )
                    try:
                        review = normalize_idea_review(raw_review, candidates)
                        self.repository.add_artifact(
                            run_id,
                            "idea_review_checkpoint",
                            "Validated Idea Review Checkpoint",
                            {
                                "prompt_version": EVIDENCE_REASONING_PROMPT_VERSION,
                                "evidence_set_hash": evidence_set_hash,
                                "candidate_ids": candidate_ids,
                                "review": review,
                            },
                            step_id,
                            self.idea_selection_agent.name,
                        )
                        break
                    except ValueError as exc:
                        if not str(exc).startswith("IDEA_SELECTION_OUTPUT_INVALID"):
                            raise
                        issues = [
                            str(exc),
                            (
                                f"Return exactly {len(candidates)} evaluations, one for each "
                                "candidate_index, and make evidence_ledger/closest_prior_work/"
                                "risks/unknowns arrays, gates/scores/mde objects."
                            ),
                        ]
                        self.repository.append_event(
                            run_id,
                            step_id,
                            self.idea_selection_agent.name,
                            "Idea selection review returned invalid output.",
                            data={
                                "raw_review": raw_review,
                                "candidate_count": len(candidates),
                                "format_attempt": format_attempt + 1,
                                "format_attempt_limit": 3,
                            },
                            output_summary={
                                "accepted": False,
                                "issues": issues,
                                "retry_scope": "format",
                            },
                            provider_mode=self.llm_provider.mode,
                            fallback_used=self.llm_provider.fallback,
                            fallback_reason=(
                                "Mock LLM development fallback."
                                if self.llm_provider.fallback
                                else ""
                            ),
                        )
                if review is None:
                    return {
                        "_validation_issues": issues,
                        "raw_review": raw_review,
                    }
                assessments = []
                revision_issues: list[str] = []
                for index, hypothesis in enumerate(candidates):
                    candidate_issue_count = len(revision_issues)
                    evaluation = review["evaluations"][index]
                    decision = str(evaluation.get("decision") or "")
                    candidate_audit = deepcopy(candidate_audits.get(index) or {})
                    candidate_audit["registry"] = deepcopy(
                        evidence_audit["registry"]
                    )
                    candidate_audit["candidate_audit"] = {
                        key: deepcopy(value)
                        for key, value in candidate_audit.items()
                        if key != "registry"
                    }
                    revision_required = decision in {"REVISE", "PIVOT"}
                    critic_instruction = (
                        f"{revised_instructions}\n\n## Mandatory decision completion\n"
                        f"The idea review decision for this candidate is {decision}. "
                    )
                    if revision_required:
                        critic_instruction += (
                            "Return a fully rewritten, directly selectable revised_hypothesis. "
                            "It must materially change the claim and include non-empty claim, "
                            "verifiability, novelty_basis, and risks. Apply every necessary "
                            "change now; do not return recommendations for a future edit."
                        )
                    else:
                        critic_instruction += (
                            "Return the complete selectable hypothesis when it is verified. "
                            "Do not leave unapplied editing recommendations."
                        )
                    checkpoint = self._load_candidate_checkpoint(
                        run_id,
                        index,
                        candidate_ids[index],
                        evidence_set_hash,
                        hypothesis,
                        evaluation,
                    )
                    if checkpoint is not None:
                        assessments.append(checkpoint)
                        self.repository.append_event(
                            run_id, step_id, self.critic_agent.name,
                            f"CAND-{index + 1:03d} checkpoint reused.",
                            data={"candidate_index": index, "status": "checkpoint_reused"},
                        )
                        continue
                    candidate_scope = self._focused_evidence_for_candidate(
                        all_evidence, hypothesis
                    )
                    candidate_evidence = select_units(
                        [literature_card(card) for card in candidate_scope],
                        context_budget.max_reference_chars,
                    )
                    candidate_scope_hash = self._stable_hash(candidate_evidence)
                    self.repository.append_event(
                        run_id, step_id, self.critic_agent.name,
                        f"CAND-{index + 1:03d} started.",
                        data={"candidate_index": index, "status": "started",
                              "evidence_scope_count": len(candidate_evidence)},
                    )
                    try:
                        critic_reasoning = self.critic_agent.evidence_reasoning(
                            hypothesis,
                            candidate_evidence,
                            evidence_audit=candidate_audit,
                            evaluation=evaluation,
                            instructions=critic_instruction,
                        )
                    except Exception as exc:
                        if failure_state_for(exc) == "RECOVERABLE_PROVIDER_ERROR":
                            self.repository.append_event(
                                run_id, step_id, self.critic_agent.name,
                                f"CAND-{index + 1:03d} interrupted; resume is available.",
                                data={
                                    "status": "interrupted", "candidate_index": index,
                                    "candidate_id": f"CAND-{index + 1:03d}",
                                    "completed_count": len(assessments), "recoverable": True,
                                    "error_code": str(exc).split(":", 1)[0],
                                },
                                output_summary={"recoverable": True},
                            )
                        raise
                    assessment = self._candidate_assessment(
                        index,
                        hypothesis,
                        evaluation,
                        critic_reasoning,
                        candidate_audit,
                        enforce_evidence_gate=not self.llm_provider.fallback,
                    )
                    assessment["recommendation"] = decision
                    if revision_required:
                        revised = assessment["revised_hypothesis"]
                        missing_fields = []
                        if not str(revised.get("claim") or "").strip():
                            missing_fields.append("claim")
                        if not str(revised.get("verifiability") or "").strip():
                            missing_fields.append("verifiability")
                        for field in ("novelty_basis", "risks"):
                            if not isinstance(revised.get(field), list):
                                missing_fields.append(field)
                        if not assessment["was_revised"]:
                            revision_issues.append(
                                f"HYPOTHESIS_REVISION_REQUIRED:candidate={index}"
                            )
                        elif missing_fields:
                            revision_issues.append(
                                "HYPOTHESIS_REVISION_INCOMPLETE:"
                                f"candidate={index}:missing={','.join(missing_fields)}"
                            )
                        elif not assessment["revision_reason"].strip():
                            revision_issues.append(
                                f"HYPOTHESIS_REVISION_REASON_REQUIRED:candidate={index}"
                            )
                    if len(revision_issues) == candidate_issue_count:
                        self.repository.add_artifact(
                            run_id,
                            "candidate_reasoning_checkpoint",
                            f"Candidate Reasoning Checkpoint {index + 1}",
                            {
                                "candidate_index": index,
                                "candidate_id": candidate_ids[index],
                                "candidate_label": f"CAND-{index + 1:03d}",
                                "prompt_version": EVIDENCE_REASONING_PROMPT_VERSION,
                                "evidence_set_hash": evidence_set_hash,
                                "evidence_scope_count": len(candidate_evidence),
                                "evidence_scope_hash": candidate_scope_hash,
                                "evidence_source_ids": [self._stable_hash(item)[:16] for item in candidate_evidence],
                                "assessment": assessment,
                            },
                            step_id,
                            self.critic_agent.name,
                        )
                    assessments.append(assessment)
                    self.repository.append_event(
                        run_id, step_id, self.critic_agent.name,
                        f"CAND-{index + 1:03d} completed.",
                        data={"candidate_index": index, "status": "completed",
                              "evidence_scope_count": len(candidate_evidence)},
                    )
                if revision_issues:
                    return {
                        "_validation_issues": revision_issues,
                        "candidate_assessments": assessments,
                    }
                recovery = self._recover_candidate_evidence(
                    run_id=run_id,
                    candidates=candidates,
                    assessments=assessments,
                    evidence_cards=focused_evidence,
                    instructions=revised_instructions,
                )
                assessments = recovery["assessments"]
                recovered_cards = recovery["evidence_cards"]
                claim_registry = extract_claim_evidence(recovered_cards)
                research_gaps = analyze_research_gaps(claim_registry)
                for index, assessment in enumerate(assessments):
                    candidate = candidates[index]
                    assessment["candidate_evidence_map"] = candidate_evidence_map(
                        candidate, claim_registry, research_gaps
                    )
                    assessment["critic_decision"] = self._critic_decision(assessment)
                    if assessment["critic_decision"] == "TARGETED_RETRIEVAL":
                        assessment["recommendation"] = "TARGETED_RETRIEVAL"
                review_box["review"] = review
                return {
                    "literature_registry": recovered_cards,
                    "evidence_registry": claim_registry,
                    "research_gaps": research_gaps,
                    "evidence_policy": evidence_audit["policy"],
                    "targeted_retrieval": recovery["summary"],
                    "candidate_evidence_maps": [
                        assessment["candidate_evidence_map"] for assessment in assessments
                    ],
                    "unverified_citations": [
                        {
                            "candidate_id": item["candidate_evidence_map"]["candidate_id"],
                            "citations": item["candidate_evidence_map"]["unverified_claims"],
                        }
                        for item in assessments
                        if item["candidate_evidence_map"]["unverified_claims"]
                    ],
                    "candidate_assessments": assessments,
                    "selection_required": True,
                    "selection_status": "awaiting_selection",
                    "selection_guidance": (
                        "Evidence reasoning is complete. A human must choose one candidate "
                        "before research planning can continue."
                    ),
                }

            reasoning = self._produce_validated(run_id, step_id, reason_about_evidence)
            hypothesis_round = deepcopy((latest["hypothesis"].content.get("hypothesis_round") or {}))
            reasoning["hypothesis_round"] = hypothesis_round
            review = review_box["review"]
            review_content = {"evaluations": review["evaluations"], "weights": WEIGHTS}
            review_content["hypothesis_round"] = hypothesis_round
            self.repository.add_artifact(
                run_id,
                "idea_review",
                "Evidence-Reasoned Idea Review",
                review_content,
                step_id,
                self.idea_selection_agent.name,
            )
            self.repository.add_artifact(
                run_id,
                "reasoning",
                "Evidence Reasoning",
                reasoning,
                step_id,
                self.critic_agent.name,
                parent_artifact_id=latest["hypothesis"].id,
            )
            self._trace(
                run_id,
                step_id,
                self.critic_agent.name,
                "Reviewed every candidate and paused for human hypothesis selection.",
                {"candidate_count": len(candidates), "evidence_count": len(evidence)},
                reasoning,
                skill_calls=skill_calls,
            )
        elif step_id == "research_plan":
            self.supervisor_agent.require_agent(delegation, "planning")
            existing_final_plan = latest.get("plan")
            selection = self._require_evidence_reasoned_hypothesis_selection(latest)
            # A failed core claim starts a PIVOT branch.  The branch's working
            # hypothesis becomes the only planning authority for its pending
            # refinement; the original selection remains historical evidence.
            pending_pivot = self._pending_pivot_lineage(latest)
            if pending_pivot:
                selection = deepcopy(selection)
                pivot_hypothesis = deepcopy(pending_pivot["working_hypothesis"])
                pivot_hypothesis.setdefault(
                    "candidate_id",
                    str(((selection.get("selected") or [{}])[0]).get("candidate_id") or ""),
                )
                selection["selected"] = [pivot_hypothesis]
                selection["pivot_lineage"] = deepcopy(pending_pivot)
            constraints_artifact = self._ensure_research_constraints(run_id)
            execution_seed_artifact = self._ensure_backend_execution_seed_contract(
                run_id,
                constraints=constraints_artifact.content,
            )
            execution_seeds = list(execution_seed_artifact.content["seeds"])
            run = self.repository.get_run(run_id)
            latest = self._latest_by_type(run.artifacts)
            reasoning_content = latest.get("reasoning").content if latest.get("reasoning") else {}
            selected_index = (selection.get("selected_indexes") or [None])[0]
            selected_id = str(((selection.get("selected") or [{}])[0]).get("candidate_id") or "")
            selected_assessment = next(
                (item for item in (reasoning_content.get("candidate_assessments") or [])
                 if isinstance(item, dict) and (item.get("candidate_index") == selected_index or item.get("candidate_id") == selected_id)),
                {},
            )
            dataset_profile = (
                latest["dataset_profile"].content
                if latest.get("dataset_profile")
                else None
            )
            profile = infer_research_profile(
                run.problem_input, dataset_present=dataset_profile is not None,
                evidence=latest.get("evidence").content if latest.get("evidence") else None,
            )
            dataset_state = annotate_dataset_semantics(dataset_profile) if dataset_profile else {}
            protocol = protocol_state(
                objective=str(((selection.get("selected") or [{}])[0]).get("claim") or run.problem_input),
                profile=profile,
                literature=latest.get("evidence").content if latest.get("evidence") else {},
                dataset=dataset_state,
                code=latest.get("code_evidence").content if latest.get("code_evidence") else {},
                stage="VERIFY",
            )
            readiness = readiness_state(assessment=selected_assessment, dataset=dataset_state, protocol=protocol, profile=profile)
            stage = next_research_stage(readiness, profile)
            if readiness["state"] in {"needs_evidence", "scientifically_infeasible"}:
                requirement = {
                    "readiness": readiness, "research_profile": profile, "protocol_state": protocol,
                    "current_research_stage": stage, "selected_hypothesis": selection.get("selected") or [],
                }
                self.repository.add_artifact(run_id, "hypothesis_readiness", "Hypothesis Readiness Gate", requirement,
                                             step_id, "Scientific Stability Gate", parent_artifact_id=latest["hypothesis_selection"].id)
                self.repository.update_workflow_state(
                    run_id,
                    status="NEEDS_EVIDENCE" if readiness["state"] == "needs_evidence" else "HYPOTHESIS_REJECTED",
                    current_step="research_plan", automatic=False, stop_requested=False,
                )
                self._persist_scientific_world_state(run_id, run, profile, dataset_state, protocol, readiness, stage, [])
                return
            dataset_options = (
                [dataset_option(dataset_profile)]
                if dataset_profile
                else self._dataset_options()
            )
            constraints_reference = {
                "artifact_id": run.research_constraints_artifact_id or "",
                "schema_version": 1,
            }
            policy_artifact, frozen_policy = self._ensure_plan_review_policy(
                run_id,
                package=package,
                run=run,
                latest=latest,
                selection=selection,
                constraints_artifact=constraints_artifact,
            )
            # Existing Runs always use the immutable prompt snapshot stored in
            # their policy artifact; current disk Skills cannot alter semantics.
            instructions = str(frozen_policy["runtime_instructions"])

            def build_plan(revision):
                pending = latest.get("research_plan_candidate")
                pending_plan = (
                    deepcopy((pending.content or {}).get("normalized_plan") or {})
                    if pending and (pending.content or {}).get("status") == "review_pending"
                    and ((pending.content or {}).get("research_constraints_reference") or {}).get("artifact_id") == (run.research_constraints_artifact_id or "")
                    and (pending.content or {}).get("policy_artifact_id") == policy_artifact.id
                    and not any(
                        artifact.type == "plan_review"
                        and artifact.parent_artifact_id == pending.id
                        for artifact in run.artifacts
                    )
                    else {}
                )
                raw_plan = pending_plan or self.planning_agent.build_plan(
                        selection,
                        instructions=self._with_revision(instructions, revision),
                        dataset_options=dataset_options,
                        authoritative_contract_snapshot=frozen_policy[
                            "authoritative_plan_contract_snapshot"
                        ],
                        plan_context={
                            "authoritative_plan_contract": deepcopy(
                                frozen_policy[
                                    "authoritative_plan_contract_snapshot"
                                ]
                            ),
                            "dataset_profile": dataset_profile or {},
                            "run_constraints": run.constraints,
                            "execution_seed_contract": deepcopy(
                                execution_seed_artifact.content
                            ),
                            "research_profile": profile,
                            "protocol_state": protocol,
                            "readiness_state": readiness,
                            "current_research_stage": stage,
                            "available_split_information": {
                                "dataset_card": (dataset_options[0].get("card") if dataset_options else {}),
                                "existing_split_contract": {},
                            },
                        },
                    )
                candidate = normalize_plan(
                    raw_plan,
                    selection,
                    provider_mode=self.llm_provider.mode,
                    fallback_used=self.llm_provider.fallback,
                )
                if execution_training_budget(candidate) is None:
                    candidate.setdefault("_validation_issues", []).append(
                        "MODEL_PLANNED_TRAINING_EPOCHS_REQUIRED"
                    )
                # Seed values are backend-owned preregistration state.  The model
                # may describe their statistical role, but it cannot choose or
                # change the concrete values reviewed by plan governance.
                candidate["seeds"] = list(execution_seeds)
                if dataset_profile:
                    candidate = self._bind_plan_to_dataset(
                        candidate, dataset_profile
                    )
                candidate["scientific_contract"] = compile_scientific_contract(
                    run.problem_input,
                    selection.get("selected") or [],
                    candidate,
                )
                candidate["scientific_integrity_issues"] = [
                    *validate_coverage(candidate["scientific_contract"]),
                    *validate_split_contract(candidate.get("split_contract") or (candidate.get("dataset") or {}).get("split_contract") or {}),
                ]
                candidate["research_profile"] = profile
                candidate["protocol_state"] = protocol
                candidate["readiness_state"] = readiness
                candidate["research_stage"] = stage
                issues = self._plan_dataset_issues(candidate, dataset_options)
                if issues:
                    return {**candidate, "_validation_issues": issues}
                return self._attach_dataset_card(candidate, dataset_options)

            def produce_initial_plan() -> dict:
                migration_id = str(
                    (frozen_policy.get("source_artifact_lineage") or {}).get(
                        "migration_artifact_id"
                    )
                    or ""
                )
                migrated_candidate = (
                    normalize_plan(
                        deepcopy(existing_final_plan.content or {}),
                        selection,
                        provider_mode=self.llm_provider.mode,
                        fallback_used=self.llm_provider.fallback,
                    )
                    if existing_final_plan is not None and migration_id
                    else None
                )
                # A legacy final plan predating the executable training-budget
                # contract remains provenance, not an executable candidate. Ask
                # the planner for a current contract instead of inventing epochs
                # or failing before governance can migrate the run.
                if (
                    migrated_candidate is not None
                    and execution_training_budget(migrated_candidate) is not None
                ):
                    candidate = migrated_candidate
                elif migrated_candidate is not None:
                    candidate = normalize_plan(
                        merge_plan_patch(
                            build_plan(None),
                            deepcopy(existing_final_plan.content or {}),
                        ),
                        selection,
                        provider_mode=self.llm_provider.mode,
                        fallback_used=self.llm_provider.fallback,
                    )
                else:
                    candidate = build_plan(None)
                candidate["seeds"] = list(execution_seeds)
                structural_issues = list(candidate.pop("_validation_issues", []) or [])
                if execution_training_budget(candidate) is None:
                    structural_issues.append("MODEL_PLANNED_TRAINING_EPOCHS_REQUIRED")
                structural_decision = self.supervisor_agent.validate(step_id, candidate)
                structural_issues.extend(structural_decision.issues)
                if structural_issues:
                    raise ValueError(
                        "MODEL_OUTPUT_VALIDATION_FAILURE:"
                        + ";".join(dict.fromkeys(str(item) for item in structural_issues))
                    )
                candidate["research_constraints_artifact_id"] = constraints_reference["artifact_id"]
                candidate["research_constraints_reference"] = constraints_reference
                return candidate

            governance_result = self._execute_plan_review_governance(
                run_id=run_id,
                step_id=step_id,
                run=run,
                latest=latest,
                selection=selection,
                dataset_options=dataset_options,
                dataset_profile=dataset_profile,
                policy_artifact=policy_artifact,
                frozen_policy=frozen_policy,
                instructions=instructions,
                constraints_reference=constraints_reference,
                stage=stage,
                profile=profile,
                protocol=protocol,
                readiness=readiness,
                dataset_state=dataset_state,
                produce_initial_plan=produce_initial_plan,
                execution_seeds=execution_seeds,
            )
            if governance_result is None:
                return
            plan, plan_candidate, issue_ledger = governance_result
            if plan.get("seeds") != execution_seeds:
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:execution_seed_contract"
                )
            if (
                existing_final_plan is not None
                and existing_final_plan.parent_artifact_id == plan_candidate.id
                and (existing_final_plan.content or {}).get("plan_candidate_id")
                == plan_candidate.id
                and is_plan_governance_accepted(
                    self.repository.get_run(run_id).artifacts
                )
            ):
                return self.repository.get_run(run_id)
            plan["scientific_contract"] = compile_scientific_contract(
                run.problem_input, selection.get("selected") or [], plan
            )
            plan["scientific_integrity_issues"] = [
                *validate_coverage(plan["scientific_contract"]),
                *validate_split_contract(plan.get("split_contract") or (plan.get("dataset") or {}).get("split_contract") or {}),
            ]
            plan["plan_candidate_id"] = plan_candidate.id
            plan["policy_artifact_id"] = policy_artifact.id
            plan["policy_payload_sha256"] = frozen_policy["policy_payload_sha256"]
            plan["accepted_candidate_payload_sha256"] = canonical_sha256(
                (plan_candidate.content or {}).get("normalized_plan") or {}
            )
            frozen_training_budget = self._frozen_model_training_budget(run_id)
            if (
                frozen_training_budget is not None
                and execution_training_budget(plan) != frozen_training_budget
            ):
                raise ValueError("MODEL_TRAINING_BUDGET_CHANGED_DURING_ITERATION")
            plan_artifact = self.repository.add_artifact(
                run_id, "plan", "Research Plan", plan, step_id,
                self.planning_agent.name, parent_artifact_id=plan_candidate.id,
            )
            self._freeze_model_training_budget(
                run_id,
                plan,
                plan_artifact_id=plan_artifact.id,
            )
            self.repository.add_artifact(
                run_id, "scientific_contract", "Scientific Coverage and Split Contract",
                plan.get("scientific_contract") or {}, step_id, self.planning_agent.name,
            )
            # Phase 2 keeps the baseline and all comparison controls durable
            # and independent from a model-generated experiment description.
            phase2_constraints = normalize_constraints(run.research_constraints, run.constraints)
            baseline = baseline_profile(
                phase2_constraints, plan, dataset_profile or {}, run.github_repository_url
            )
            baseline_artifact = self.repository.add_artifact(
                run_id, "baseline_profile", "Baseline Profile", baseline, step_id,
                "Phase 2 Baseline Contract",
            )
            fair_contract = fair_experiment_contract(
                dataset_profile or {}, baseline, phase2_constraints, plan
            )
            self.repository.add_artifact(
                run_id, "fair_experiment_contract", "Frozen Fair Experiment Contract",
                fair_contract, step_id, "Phase 2 Experiment Contract",
                parent_artifact_id=baseline_artifact.id,
            )
            self._trace(
                run_id,
                step_id,
                self.planning_agent.name,
                "Built experiment plan.",
                {"hypothesis_selection": latest["hypothesis_selection"].content},
                plan,
                skill_calls=skill_calls,
            )
            self._persist_scientific_world_state(run_id, run, profile, dataset_state, protocol, readiness, stage, issue_ledger)
        elif step_id == "experiment_task":
            if not is_plan_governance_accepted(
                self.repository.get_run(run_id).artifacts
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:governance_acceptance_required"
                )
            self.supervisor_agent.require_agent(delegation, "experiment")
            self._require_tools(package, "build_experiment_bundle")
            formal_validation = self._formal_validation_pending(run.artifacts)
            reusable_bundle_artifact = next(
                (
                    artifact
                    for artifact in reversed(run.artifacts)
                    if artifact.type == "experiment_bundle"
                    and (artifact.content or {}).get("runtime_contract", {}).get("stage")
                    == "small_scale"
                ),
                None,
            )
            experiment_id = self.repository.next_experiment_id(run_id)
            result_id = f"{experiment_id}_result"
            python_command = self.experiment_provider.python_command()
            # A plan-review repair is allowed to correct ``evaluations``. Keep
            # the feedback contract derived from those final metrics; otherwise
            # generation and validation demand different metric names.
            experiment_plan = self._synchronize_iteration_contract(
                deepcopy(latest["plan"].content)
            )
            training_budget_contract = self._freeze_model_training_budget(
                run_id,
                experiment_plan,
                plan_artifact_id=latest["plan"].id,
            )
            if (training_budget_contract.content or {}).get("mode") == "epochs":
                experiment_plan = self._with_frozen_training_epochs(
                    experiment_plan,
                    int(training_budget_contract.content["epochs"]),
                )
            # Reuse the preregistered backend seed contract that plan governance
            # already reviewed.  Never resample seeds at execution time.
            execution_seed_artifact = self._ensure_backend_execution_seed_contract(
                run_id,
                constraints=normalize_constraints(run.research_constraints, run.constraints),
                plan=experiment_plan,
            )
            execution_seeds = list(execution_seed_artifact.content["seeds"])
            if experiment_plan.get("seeds") not in ([], execution_seeds):
                raise ValueError("EXECUTION_SEED_CONTRACT_MISMATCH")
            experiment_plan["seeds"] = execution_seeds
            base_task = {
                **self.experiment_agent.build_task(experiment_plan),
                "run_id": run_id,
                "experiment_id": experiment_id,
                "result_id": result_id,
                "research_constraints_artifact_id": run.research_constraints_artifact_id or "",
                "research_constraints_reference": {"artifact_id": run.research_constraints_artifact_id or "", "schema_version": 1},
            }
            fair_contract = latest.get("fair_experiment_contract")
            if fair_contract:
                base_task["phase2_protocol"] = progressive_protocol(
                    fair_contract.content,
                    "formal_validation",
                )
                base_task["phase2_protocol"]["seeds"] = list(experiment_plan["seeds"])
            base_task["scientific_contract"] = compile_scientific_contract(
                run.problem_input,
                (latest.get("hypothesis_selection").content.get("selected") if latest.get("hypothesis_selection") else []),
                experiment_plan,
                base_task,
            )
            task_box = {}
            bundle_box = {}
            previous_bundle: ExperimentBundle | None = None
            previous_candidate: dict | None = None
            frozen_contract: dict | None = None
            repair_history: list[dict] = []
            pivot_base = self._pivot_implementation_base(
                run.artifacts, experiment_plan
            )
            serial_reference = (experiment_plan.get("iteration_contract") or {}).get("implementation_reference")
            pending_proposal = latest.get("plan_refinement_proposal")
            if pending_proposal:
                expected_reference = ((pending_proposal.content.get("normalized_plan") or {}).get("iteration_contract") or {}).get("implementation_reference")
                if expected_reference and serial_reference != expected_reference:
                    raise ValueError("ITERATION_BASE_REFERENCE_CHANGED_DURING_REVIEW")
            if serial_reference:
                pivot_base = implementation_base(run.artifacts, serial_reference)
                base_task["implementation_base_reference"] = deepcopy(serial_reference)

            def build_experiment(revision):
                nonlocal previous_bundle, previous_candidate, frozen_contract
                task = dict(base_task)
                capture: dict = {}
                origin = "generate" if previous_bundle is None and previous_candidate is None else "repair"

                def attempt_evidence(bundle: ExperimentBundle | None = None) -> dict:
                    normalized = capture.get("normalized_bundle")
                    if normalized is None and bundle is not None:
                        normalized = bundle.model_dump()
                    normalized = deepcopy(normalized) if isinstance(normalized, dict) else None
                    return {
                        "candidate_origin": origin,
                        "raw_model_output": deepcopy(capture.get("raw_model_output") or previous_candidate or {}),
                        "normalized_bundle": normalized,
                        "manifest": (normalized or {}).get("manifest") or {},
                        "files": (normalized or {}).get("files") or [],
                        "requirements": (normalized or {}).get("requirements") or [],
                        "repair_history": deepcopy(repair_history),
                        "skill_hash": package.audit.get("instruction_sha256", ""),
                        "skill_invocations": deepcopy(package.audit.get("skill_invocations") or []),
                        "plan_artifact_id": latest["plan"].id,
                        "research_constraints_reference": task.get("research_constraints_reference"),
                        "dataset_contract_reference": {
                            "contract_id": str((latest["plan"].content.get("dataset") or {}).get("contract_id") or ""),
                            "content_fingerprint": str((latest["plan"].content.get("dataset") or {}).get("content_fingerprint") or ""),
                            "root": str((latest["plan"].content.get("dataset") or {}).get("root") or ""),
                        },
                    }
                try:
                    if (
                        previous_bundle is None
                        and previous_candidate is None
                        and formal_validation
                        and reusable_bundle_artifact is not None
                        and not serial_reference
                    ):
                        reused = ExperimentBundle.model_validate(
                            reusable_bundle_artifact.content
                        )
                        manifest = reused.manifest.model_copy(
                            update={
                                "run_id": run_id,
                                "experiment_id": experiment_id,
                                "result_id": result_id,
                                "python_args": [
                                    "--run-id",
                                    run_id,
                                    "--experiment-id",
                                    experiment_id,
                                    "--result-id",
                                    result_id,
                                    "--output",
                                    f"results/{result_id}.json",
                                ],
                            }
                        )
                        bundle = compile_bundle_runtime_contract(
                            experiment_plan,
                            task,
                            reused.model_copy(
                                update={"manifest": manifest, "runtime_contract": None}
                            ),
                        )
                        task["implementation_reused_from"] = reusable_bundle_artifact.id
                        capture["raw_model_output"] = bundle.model_dump()
                        capture["normalized_bundle"] = bundle.model_dump()
                    elif previous_bundle is None and previous_candidate is None:
                        bundle = self.experiment_agent.generate_bundle(
                            run_id,
                            experiment_id,
                            experiment_plan,
                            task,
                            self._with_revision(instructions, revision),
                            python_command,
                            require_smoke_test=True,
                            validate=False,
                            capture=capture,
                            implementation_base=pivot_base,
                        )
                    else:
                        feedback = list((revision or {}).get("issues") or [])
                        bundle = self.experiment_agent.repair_bundle(
                            experiment_plan,
                            task,
                            previous_bundle,
                            {
                                "stage": "experiment_bundle_preflight",
                                "validation_issues": feedback,
                            },
                            self._with_revision(instructions, revision),
                            validation_feedback=feedback,
                            repair_history=repair_history,
                            validate=False,
                            previous_candidate=previous_candidate,
                            frozen_contract=frozen_contract,
                            capture=capture,
                        )
                    previous_bundle = bundle
                    previous_candidate = None
                    if frozen_contract is None:
                        frozen_contract = bundle.manifest.model_dump()
                    if not self.llm_provider.fallback:
                        self.experiment_agent.validate_bundle(
                            experiment_plan,
                            bundle,
                            require_smoke_test=True,
                            task=task,
                        )
                except ExperimentBundleCandidateError as exc:
                    failed_candidate = dict(exc.candidate)
                    # A malformed repair is audit evidence, not a replacement
                    # for the last complete Bundle.  Only an initial malformed
                    # generation needs its raw candidate as the next repair base.
                    if previous_bundle is None and not str(exc).startswith("EXPERIMENT_CODE_ITERATION_PATCH_INVALID"):
                        previous_candidate = failed_candidate
                        if frozen_contract is None:
                            frozen_contract = self.experiment_agent.frozen_contract_from_candidate(
                                experiment_plan,
                                task,
                                previous_candidate,
                            )
                    issues = experiment_validation_issues(exc)
                    if issues:
                        repair_history.append(
                            {
                                "attempt": len(repair_history) + 1,
                                "issues": list(issues),
                            }
                        )
                        return {
                            **task,
                            "_validation_issues": issues,
                            "_repair_history": deepcopy(repair_history),
                            "_candidate_attempt": attempt_evidence(),
                        }
                    raise
                except ValueError as exc:
                    issues = experiment_validation_issues(exc)
                    if issues:
                        repair_history.append(
                            {
                                "attempt": len(repair_history) + 1,
                                "issues": list(issues),
                            }
                        )
                        return {
                            **task,
                            "_validation_issues": issues,
                            "_repair_history": deepcopy(repair_history),
                            "_candidate_attempt": attempt_evidence(bundle if "bundle" in locals() else None),
                        }
                    raise
                task = {
                    **task,
                    "manifest": bundle.manifest.model_dump(),
                    "repair_history": deepcopy(repair_history),
                    "_candidate_attempt": attempt_evidence(bundle),
                }
                task_box["task"] = task
                bundle_box["bundle"] = bundle
                return task

            task = self._produce_validated(run_id, step_id, build_experiment)
            bundle = bundle_box["bundle"]
            task_artifact = self.repository.add_artifact(
                run_id,
                "experiment_task",
                f"Experiment Task {experiment_id}",
                task,
                step_id,
                self.experiment_agent.name,
                parent_artifact_id=latest["plan"].id,
            )
            bundle_artifact = self.repository.add_artifact(
                run_id,
                "experiment_bundle",
                f"Experiment Bundle {experiment_id}",
                bundle.model_dump(),
                step_id,
                self.experiment_agent.name,
                parent_artifact_id=task_artifact.id,
            )
            self._trace(
                run_id,
                step_id,
                self.experiment_agent.name,
                "Built executable experiment task.",
                {"plan": experiment_plan},
                {
                    "task": task,
                    "bundle": {
                        "artifact_id": bundle_artifact.id,
                        "experiment_id": experiment_id,
                        "result_id": result_id,
                        "entrypoint": bundle.manifest.entrypoint,
                        "files": [item.path for item in bundle.files],
                        "requirements": bundle.requirements,
                    },
                },
                tool_calls=[
                    {"provider": "experiment", "method": "plan"},
                    {"provider": "experiment", "method": "generate_bundle"},
                    *(
                        [{"provider": "experiment", "method": "repair_bundle"}]
                        if repair_history
                        else []
                    ),
                ],
                skill_calls=skill_calls,
            )
        elif step_id == "experiment_run_analysis":
            self.supervisor_agent.require_agent(delegation, "experiment")
            execution_tool = (
                "ssh_run"
                if type(self.experiment_provider).__name__ == "RemoteGpuExperimentProvider"
                else "local_process_run"
            )
            self._require_tools(
                package,
                execution_tool,
                "read_experiment_result",
                "audit_result",
            )
            bundle_artifact = latest.get("experiment_bundle")
            bundle = (
                ExperimentBundle.model_validate(bundle_artifact.content)
                if bundle_artifact
                else None
            )
            if bundle is None:
                raise ValueError(
                    "EXPERIMENT_BUNDLE_REQUIRED:RERUN_EXPERIMENT_TASK"
                )
            previous_result = latest.get("experiment_result")
            previous_attempts = []
            experiment_id = latest["experiment_task"].content.get("experiment_id")
            if previous_result and previous_result.content.get("experiment_id") == experiment_id:
                previous_attempts = list(previous_result.content.get("attempts") or [])
            current_attempts = []
            active_task = dict(latest["experiment_task"].content)
            active_plan = self._synchronize_iteration_contract(
                deepcopy(active_task.get("plan") or latest["plan"].content)
            )
            runtime_candidate: dict | None = None
            analysis_instructions = self.skill_runtime.instructions_for(
                package, "analyze-results"
            )
            audit_instructions = self.skill_runtime.instructions_for(
                package, "experiment-audit"
            )

            def run_experiment(_revision):
                nonlocal runtime_candidate
                if runtime_candidate is None:
                    smoke_issues = [
                        issue
                        for item in bundle.files
                        for issue in smoke_data_reduction_issues(item.content)
                    ]
                    if smoke_issues:
                        raise RuntimeError(";".join(smoke_issues))
                    attempt_started_at = datetime.now(timezone.utc).isoformat()
                    recover = getattr(
                        self.experiment_provider, "recover_completed_result", None
                    )
                    force_new = experiment_id in run.force_new_attempt_experiment_ids
                    raw_result = (
                        recover(active_task, bundle)
                        if callable(recover) and not force_new
                        else None
                    )
                    if raw_result is None:
                        raw_result = self.experiment_agent.run(active_task, bundle)
                    current_attempts.append(
                        {
                            "attempt": len(previous_attempts) + len(current_attempts) + 1,
                            "start_time": raw_result.get("start_time", attempt_started_at),
                            "end_time": raw_result.get("end_time"),
                            "status": "completed",
                            "error_code": "",
                            "log_path": raw_result.get("log_path", ""),
                            "recovered": bool(
                                raw_result.get("recovered_from_completed_attempt")
                            ),
                        }
                    )
                    runtime_candidate = {
                        **raw_result,
                        "attempts": [*previous_attempts, *current_attempts],
                    }
                candidate = deepcopy(runtime_candidate)
                analysis = self.experiment_agent.analyze_result(
                    active_plan,
                    active_task,
                    candidate,
                    instructions=analysis_instructions,
                )
                audit = self.experiment_agent.audit_result(
                    bundle,
                    candidate,
                    instructions=audit_instructions,
                )
                if audit["integrity_status"] != "passed" or (
                    self.competition_mode and not audit["is_real_experiment"]
                ):
                    # An audit rejection is a validation failure, not a result
                    # that may be passed downstream as merely non-real.  The
                    # bounded diagnosis loop below retains the failed attempt
                    # and repairs the same scientific Bundle lineage.
                    raise RuntimeError(
                        "EXPERIMENT_AUDIT_FAILED:"
                        + " | ".join(str(item) for item in audit["issues"])
                    )
                return {
                    **candidate,
                    "analysis": analysis,
                    "audit": audit,
                    # The audit, not the provider's process-success flag, is
                    # authoritative for downstream claim/export eligibility.
                    "is_real_experiment": audit["is_real_experiment"],
                }

            result = None
            final_error: RuntimeError | ValueError | None = None
            latest_diagnosis: dict | None = None
            max_auto_repairs = 5
            repair_history: list[str] = []
            for repair_index in range(max_auto_repairs + 1):
                failure_started_at = datetime.now(timezone.utc).isoformat()
                had_runtime_result = runtime_candidate is not None
                try:
                    result = self._produce_validated(
                        run_id,
                        step_id,
                        run_experiment,
                        diagnosis=True,
                    )
                    break
                except (RuntimeError, ValueError) as exc:
                    final_error = exc
                    repair_history.append(str(exc))
                    error_code = str(exc).split(":", 1)[0]
                    if not had_runtime_result and runtime_candidate is None:
                        current_attempts.append(
                            {
                                "attempt": len(previous_attempts) + len(current_attempts) + 1,
                                "start_time": failure_started_at,
                                "end_time": datetime.now(timezone.utc).isoformat(),
                                "status": "failed",
                                "error_code": error_code,
                                "log_path": "",
                            }
                        )

                    diagnosis_delegation = self.supervisor_agent.delegate(
                        "experiment_diagnosis"
                    )
                    self.supervisor_agent.require_agent(
                        diagnosis_delegation, "diagnostic"
                    )
                    diagnosis_package = self.skill_runtime.prepare(
                        "experiment_diagnosis",
                        diagnosis_delegation.agent_id,
                        self.configured_tools,
                    )
                    diagnosis_calls = [
                        diagnosis_delegation.tool_call,
                        self._runtime_call(diagnosis_package),
                    ]
                    skill_calls.extend(diagnosis_calls)
                    diagnosis = self.diagnostic_agent.diagnose(
                        exc,
                        task=active_task,
                        bundle=bundle.model_dump(),
                        attempts=[*previous_attempts, *current_attempts],
                        instructions=diagnosis_package.instructions,
                    )
                    diagnosis["repair_attempt"] = repair_index + 1
                    diagnosis["max_auto_repairs"] = max_auto_repairs
                    can_repair = (
                        diagnosis.get("auto_repairable") is True
                        and repair_index < max_auto_repairs
                    )
                    repair_result = {
                        "status": "not_attempted",
                        "action": diagnosis.get("repair_action") or "none",
                    }
                    regenerated_bundle = None
                    if can_repair:
                        action = diagnosis.get("repair_action")
                        try:
                            if action == "quarantine_corrupt_dataset_download":
                                self._require_tools(
                                    diagnosis_package,
                                    "repair_dataset_cache",
                                    "retry_experiment",
                                )
                                repair = getattr(
                                    self.experiment_provider,
                                    "quarantine_failed_dataset_download",
                                    None,
                                )
                                if not callable(repair):
                                    raise RuntimeError(
                                        "EXPERIMENT_DATASET_AUTO_REPAIR_UNAVAILABLE"
                                    )
                                repair_result = repair(bundle.manifest.dataset)
                            elif action == "retry_stage":
                                self._require_tools(
                                    diagnosis_package, "retry_experiment"
                                )
                                repair_result = {
                                    "status": "completed",
                                    "action": action,
                                    "reason": "retrying unchanged stage",
                                }
                            elif action == "repair_experiment_code":
                                self._require_tools(
                                    diagnosis_package,
                                    "build_experiment_bundle",
                                    "retry_experiment",
                                )
                                implementation_delegation = (
                                    self.supervisor_agent.delegate("experiment_task")
                                )
                                implementation_package = self.skill_runtime.prepare(
                                    "experiment_task",
                                    implementation_delegation.agent_id,
                                    self.configured_tools,
                                )
                                repair_instructions = self.skill_runtime.instructions_for(
                                    implementation_package,
                                    "experiment-implementation",
                                )
                                validation_feedback: list[str] = []
                                repair_errors: list[str] = []
                                runtime_parent_attempt_id = self._runtime_repair_parent_attempt_id(
                                    run.artifacts,
                                    experiment_id=experiment_id,
                                    task_artifact=latest["experiment_task"],
                                    fallback_id=(
                                        bundle_artifact.id if bundle_artifact else None
                                    ),
                                )

                                def persist_runtime_candidate(capture: dict, *, accepted: bool, issues: list[str]) -> str:
                                    nonlocal runtime_parent_attempt_id
                                    normalized = capture.get("normalized_bundle")
                                    normalized = deepcopy(normalized) if isinstance(normalized, dict) else None
                                    payload = {
                                        "candidate_origin": "runtime_repair",
                                        # A candidate can be rejected before it can be
                                        # normalized into a Bundle (for example, after an
                                        # interrupted model response).  Keep its ownership
                                        # independent from the optional normalized payload so
                                        # a persisted checkpoint can always rebuild lineage.
                                        "experiment_id": experiment_id,
                                        "task_artifact_id": latest["experiment_task"].id,
                                        "normalization_status": (
                                            "normalized"
                                            if normalized is not None
                                            else "unavailable"
                                        ),
                                        "raw_model_output": deepcopy(capture.get("raw_model_output") or {}),
                                        "normalized_bundle": normalized,
                                        "manifest": (normalized or {}).get("manifest") or {},
                                        "files": (normalized or {}).get("files") or [],
                                        "requirements": (normalized or {}).get("requirements") or [],
                                        "repair_history": deepcopy(repair_history),
                                        "skill_hash": implementation_package.audit.get("instruction_sha256", ""),
                                        "skill_invocations": deepcopy(implementation_package.audit.get("skill_invocations") or []),
                                        "plan_artifact_id": latest["plan"].id,
                                        "dataset_contract_reference": {
                                            "contract_id": str((latest["plan"].content.get("dataset") or {}).get("contract_id") or ""),
                                            "content_fingerprint": str((latest["plan"].content.get("dataset") or {}).get("content_fingerprint") or ""),
                                            "root": str((latest["plan"].content.get("dataset") or {}).get("root") or ""),
                                        },
                                        "attempt_id": "",
                                        "parent_attempt_id": runtime_parent_attempt_id or "",
                                        "attempt_number": candidate_index + 1,
                                        "accepted": accepted,
                                        "validation_issues": list(issues),
                                    }
                                    artifact = self.repository.add_artifact(
                                        run_id,
                                        "experiment_candidate_attempt",
                                        f"Experiment Runtime Repair Candidate {candidate_index + 1}",
                                        payload,
                                        step_id,
                                        self.experiment_agent.name,
                                        parent_artifact_id=runtime_parent_attempt_id,
                                    )
                                    payload["attempt_id"] = artifact.id
                                    stored_run = self.repository.get_run(run_id)
                                    for index, stored in enumerate(stored_run.artifacts):
                                        if stored.id == artifact.id:
                                            stored_run.artifacts[index] = stored.model_copy(update={"content": payload})
                                            self.repository.save_run(stored_run)
                                            break
                                    runtime_parent_attempt_id = artifact.id
                                    return artifact.id

                                for candidate_index in range(5):
                                    capture: dict = {}
                                    try:
                                        regenerated_bundle = self.experiment_agent.repair_bundle(
                                            active_plan,
                                            active_task,
                                            bundle,
                                            diagnosis,
                                            repair_instructions,
                                            validation_feedback,
                                            repair_history=list(
                                                dict.fromkeys(repair_history)
                                            ),
                                            capture=capture,
                                        )
                                        persist_runtime_candidate(capture, accepted=True, issues=[])
                                        repair_result = {
                                            "status": "completed",
                                            "action": action,
                                            "candidate_attempts": candidate_index + 1,
                                            "files": [
                                                item.path for item in regenerated_bundle.files
                                            ],
                                            "scientific_contract_preserved": True,
                                        }
                                        break
                                    except (RuntimeError, ValueError) as candidate_error:
                                        message = str(candidate_error)
                                        issues = experiment_validation_issues(candidate_error) or [message]
                                        persist_runtime_candidate(capture, accepted=False, issues=issues)
                                        repair_errors.append(message)
                                        validation_feedback.extend(issues)
                                if regenerated_bundle is None:
                                    raise RuntimeError(
                                        "EXPERIMENT_CODE_REPAIR_CANDIDATES_REJECTED:"
                                        + " | ".join(repair_errors)
                                    )
                            elif action == "regenerate_experiment_bundle":
                                self._require_tools(
                                    diagnosis_package,
                                    "build_experiment_bundle",
                                    "retry_experiment",
                                )
                                implementation_delegation = (
                                    self.supervisor_agent.delegate("experiment_task")
                                )
                                implementation_package = self.skill_runtime.prepare(
                                    "experiment_task",
                                    implementation_delegation.agent_id,
                                    self.configured_tools,
                                )
                                repair_instructions = self.skill_runtime.instructions_for(
                                    implementation_package,
                                    "experiment-implementation",
                                )
                                repair_request = (
                                    "## Diagnostic Repair Request\n"
                                    + diagnosis["root_cause"]
                                    + "\nCorrect these concrete errors:\n"
                                    + "\n".join(
                                        f"- {item}" for item in diagnosis["evidence"]
                                    )
                                )
                                regenerated_bundle = self.experiment_agent.generate_bundle(
                                    run_id,
                                    experiment_id,
                                    active_plan,
                                    active_task,
                                    "\n\n".join(
                                        (repair_instructions, repair_request)
                                    ),
                                    self.experiment_provider.python_command(),
                                    require_smoke_test=True,
                                )
                                repair_result = {
                                    "status": "completed",
                                    "action": action,
                                    "files": [
                                        item.path for item in regenerated_bundle.files
                                    ],
                                }
                            else:
                                repair_result = {
                                    "status": "not_allowed",
                                    "action": action or "none",
                                }
                        except (RuntimeError, ValueError) as repair_error:
                            repair_result = {
                                "status": "failed",
                                "action": action or "none",
                                "error": str(repair_error),
                            }
                            can_repair = False

                    diagnosis["repair_result"] = repair_result
                    diagnosis["resolved"] = False
                    latest_diagnosis = diagnosis
                    diagnosis_artifact = self.repository.add_artifact(
                        run_id,
                        "experiment_diagnosis",
                        f"Experiment Diagnosis {repair_index + 1}",
                        diagnosis,
                        step_id,
                        self.diagnostic_agent.name,
                        parent_artifact_id=(
                            bundle_artifact.id if bundle_artifact else None
                        ),
                    )
                    if regenerated_bundle is not None:
                        bundle = regenerated_bundle
                        runtime_candidate = None
                        active_task = {
                            **active_task,
                            "manifest": bundle.manifest.model_dump(),
                        }
                        bundle_artifact = self.repository.add_artifact(
                            run_id,
                            "experiment_bundle",
                            f"Repaired Experiment Bundle {experiment_id}",
                            bundle.model_dump(),
                            step_id,
                            self.diagnostic_agent.name,
                            parent_artifact_id=diagnosis_artifact.id,
                        )
                    self.repository.append_event(
                        run_id,
                        step_id,
                        self.diagnostic_agent.name,
                        (
                            "Diagnosed failure and applied a bounded repair."
                            if can_repair
                            else "Diagnosed failure; automatic repair is unavailable."
                        ),
                        data=diagnosis,
                        output_summary=diagnosis,
                        tool_calls=diagnosis_calls,
                        provider_mode=self.llm_provider.mode,
                        fallback_used=self.llm_provider.fallback,
                    )
                    if not can_repair:
                        break

            if result is None:
                exc = final_error or RuntimeError("UNKNOWN_EXPERIMENT_FAILURE")
                error_code = str(exc).split(":", 1)[0]
                provider_class = type(self.experiment_provider).__name__
                provider_name = {
                    "LocalGpuExperimentProvider": "local_gpu",
                    "RemoteGpuExperimentProvider": "remote_gpu",
                    "MockExperimentProvider": "mock",
                }.get(provider_class, provider_class)
                failure = {
                    "run_id": run_id,
                    "experiment_id": experiment_id,
                    "result_id": latest["experiment_task"].content.get("result_id"),
                    "provider": provider_name,
                    "is_real_experiment": False,
                    "metrics": {},
                    "parameters": bundle.manifest.parameters if bundle else {},
                    "seeds": bundle.manifest.seeds if bundle else [],
                    "environment": {},
                    "attempts": [*previous_attempts, *current_attempts],
                    "status": "failed",
                    "verdict": "failed",
                    "error": str(exc),
                    "diagnosis": latest_diagnosis or {},
                }
                self.repository.add_artifact(
                    run_id,
                    "experiment_result",
                    "Experiment Result (Failed)",
                    failure,
                    step_id,
                    self.experiment_agent.name,
                    parent_artifact_id=bundle_artifact.id if bundle_artifact else None,
                )
                self._clear_forced_experiment_attempt(run_id, experiment_id)
                self._trace(
                    run_id,
                    step_id,
                    self.experiment_agent.name,
                    "Recorded failed experiment attempt.",
                    {"task": active_task},
                    failure,
                    tool_calls=[
                        {"provider": provider_name, "method": "run_and_analyze", "error": error_code}
                    ],
                    skill_calls=skill_calls,
                )
                return self.repository.get_run(run_id)
            if latest_diagnosis is not None:
                latest_diagnosis = {
                    **latest_diagnosis,
                    "resolved": True,
                    "user_message": (
                        latest_diagnosis.get("user_message", "")
                        + " 修复后重试已成功。"
                    ).strip(),
                }
                result["diagnosis"] = latest_diagnosis
                self.repository.add_artifact(
                    run_id,
                    "experiment_diagnosis",
                    "Experiment Diagnosis Resolved",
                    latest_diagnosis,
                    step_id,
                    self.diagnostic_agent.name,
                    parent_artifact_id=(
                        bundle_artifact.id if bundle_artifact else None
                    ),
                )
                self.repository.append_event(
                    run_id,
                    step_id,
                    self.diagnostic_agent.name,
                    "Verified automatic repair with a successful retry.",
                    data=latest_diagnosis,
                    output_summary=latest_diagnosis,
                    tool_calls=[],
                    provider_mode=self.llm_provider.mode,
                    fallback_used=self.llm_provider.fallback,
                )
            result_artifact = self.repository.add_artifact(
                run_id,
                "experiment_result",
                "Experiment Result",
                result,
                step_id,
                self.experiment_agent.name,
                parent_artifact_id=bundle_artifact.id if bundle_artifact else None,
            )
            # A result is only promoted to Phase 2 evidence when it supplies
            # paired per-seed baseline and idea measurements.  Missing pairs
            # remain explicit rather than being interpreted by an LLM.
            fair_contract = latest.get("fair_experiment_contract")
            primary = str((fair_contract.content if fair_contract else {}).get("primary_metric") or "accuracy")
            result_bundle = latest.get("experiment_bundle")
            expected_metrics = (
                ((result_bundle.content or {}).get("manifest") or {}).get("expected_metrics")
                if result_bundle else None
            )
            baseline_by_seed, idea_by_seed = paired_seed_metrics(
                result,
                primary,
                expected_metrics,
                comparisons=list(
                    ((active_task.get("plan") or {}).get("comparisons") or [])
                ),
            )
            direction = str(
                (fair_contract.content if fair_contract else {}).get("primary_metric_direction")
                or ""
            )
            evidence = result_evidence(
                baseline_by_seed, idea_by_seed, primary, direction=direction,
            )
            evidence["stage"] = str(
                (result.get("runtime") or {}).get("stage")
                or (active_task.get("phase2_protocol") or {}).get("stage")
                or "small_scale"
            )
            evidence["route"] = route_result(evidence, anomalies=list(result.get("anomalies") or []))
            self.repository.add_artifact(
                run_id, "result_evidence", "Deterministic Result Evidence", evidence,
                step_id, "Phase 2 Result Analyzer", parent_artifact_id=result_artifact.id,
            )
            self._clear_forced_experiment_attempt(run_id, experiment_id)
            self._trace(
                run_id,
                step_id,
                self.experiment_agent.name,
                "Recorded experiment result.",
                {"task": latest["experiment_task"].content},
                result,
                tool_calls=[{"provider": result.get("provider"), "method": "run_and_analyze"}],
                skill_calls=skill_calls,
            )
        elif step_id == "feedback_revision":
            if not is_plan_governance_accepted(
                self.repository.get_run(run_id).artifacts
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:governance_acceptance_required"
                )
            feedback_policy_artifacts = [
                item for item in run.artifacts if item.type == "plan_review_policy"
            ]
            if len(feedback_policy_artifacts) != 1:
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:feedback_policy"
                )
            feedback_policy_artifact = feedback_policy_artifacts[0]
            feedback_frozen_policy = validate_frozen_review_policy(
                feedback_policy_artifact.content or {}
            )
            self.supervisor_agent.require_agent(delegation, "critic")
            result_artifact = latest["experiment_result"]
            result = result_artifact.content
            if self._is_engineering_failure(result):
                return self.repository.get_run(run_id)
            historical_iteration = max(
                [
                    int(artifact.content.get("iteration") or 0)
                    for artifact in run.artifacts
                    if artifact.type == "revision"
                ]
                or [0]
            )
            iteration = max(run.feedback_iteration, historical_iteration) + 1
            active_hypothesis = self._feedback_hypothesis(latest)
            current_plan = latest["plan"].content
            iteration_memory = build_iteration_memory(run.artifacts)
            optimizing = iteration_memory.get("enabled") is True
            retained_best = iteration_memory.get("best") if optimizing else None
            research_context = prompt_memory(iteration_memory) if optimizing else None
            optimization_stop = continuation_stop(iteration_memory, iteration, self.max_feedback_iterations) if optimizing else ""
            deterministic_evidence = (
                deepcopy(latest["result_evidence"].content)
                if latest.get("result_evidence") else {}
            )
            result_analysis = {
                **(result.get("analysis") or {}),
                "deterministic_metric_evidence": deterministic_evidence,
            }
            result_for_feedback = {**result, "analysis": result_analysis}
            output_language = self._output_language(run)

            def route_to_report(candidate: dict, reason: str) -> None:
                """Make terminal routing and user-facing actions agree."""
                candidate["decision"] = "REPORT"
                candidate["route_reason"] = reason
                candidate["requires_follow_up"] = False
                candidate["required_revision"] = ""
                candidate["revisions"] = []
                candidate["next_action"] = (
                    "保留当前实验结论并生成报告。"
                    if output_language == "zh-CN"
                    else "Preserve the current result and generate the report."
                )
                candidate.pop("revised_plan", None)

            claim_instructions = self.skill_runtime.instructions_for(
                package, "experiment-iteration", "result-to-claim"
            )
            claim_instructions = self._with_output_language(
                claim_instructions, output_language
            )
            if optimizing:
                claim_instructions += (
                    "\nThe frozen goal is optimization. Keep the honest hypothesis verdict separate "
                    "from whether another experiment is useful. supported does not by itself end "
                    "this goal. Never require extra rounds merely to satisfy a round count. Use the "
                    "bounded research_context history and preserve negative findings."
                )

            def review_result(revision):
                candidate = self.critic_agent.review_result(
                    active_hypothesis,
                    result_for_feedback,
                    plan=current_plan,
                    analysis=result_analysis,
                    audit=result.get("audit") or {},
                    **({"research_context": research_context} if optimizing else {}),
                    instructions=self._with_revision(claim_instructions, revision),
                )
                candidate.setdefault(
                    "verdict",
                    result.get("verdict") or result.get("status") or "partial",
                )
                verdict = normalize_feedback_verdict(candidate.get("verdict"))
                candidate["verdict"] = verdict
                candidate["iteration"] = iteration
                candidate.setdefault("feedback", "")
                candidate.setdefault("required_revision", "")
                candidate.setdefault("supported_claims", [])
                candidate.setdefault("unsupported_claims", [])
                candidate.setdefault("revisions", [])
                candidate.setdefault(
                    "next_action",
                    candidate.get("required_revision") or candidate.get("feedback") or "",
                )
                candidate.setdefault("evidence_links", [])
                candidate.setdefault("overclaim_risks", [])
                raw_decision = str(candidate.get("decision") or "").strip().upper()
                decision = normalize_feedback_decision(raw_decision)
                route_reason = (
                    "MODEL_DECISION"
                    if raw_decision
                    in {"REPORT", "STOP", "TERMINATE", "FINALIZE", "REVISE", "PIVOT"}
                    else "MISSING_OR_INVALID_DECISION"
                )
                if verdict == "supported" and not optimizing:
                    decision = "REPORT"
                    route_reason = "VERDICT_SUPPORTED"
                elif iteration >= self.max_feedback_iterations:
                    decision = "REPORT"
                    route_reason = "ITERATION_LIMIT_REACHED"
                elif decision in {"REVISE", "PIVOT"} and not str(
                    candidate.get("required_revision") or ""
                ).strip():
                    decision = "REPORT"
                    route_reason = "MISSING_EXECUTABLE_REVISION"
                if decision == "REPORT":
                    route_to_report(candidate, route_reason)
                else:
                    candidate["decision"] = decision
                    candidate["route_reason"] = route_reason
                    candidate["requires_follow_up"] = True
                candidate["result_analysis"] = self._normalize_iteration_analysis(
                    candidate.get("result_analysis"),
                    result,
                )
                candidate["literature_queries"] = self._normalize_iteration_queries(
                    candidate.get("literature_queries")
                )
                if (
                    candidate["requires_follow_up"]
                    and candidate["result_analysis"]["knowledge_gaps"]
                    and not candidate["literature_queries"]
                ):
                    candidate["_validation_issues"] = [
                        "ITERATION_LITERATURE_QUERIES_REQUIRED"
                    ]
                return candidate

            review_context = {
                "hypothesis": active_hypothesis,
                "plan": current_plan,
                "experiment_result": result_for_feedback,
                "analysis": result_analysis,
                "audit": result.get("audit") or {},
                "output_language": output_language,
                "research_constraints_reference": {"artifact_id": run.research_constraints_artifact_id or "", "schema_version": 1},
                **({"research_context": research_context} if optimizing else {}),
            }
            feedback = self._produce_validated(
                run_id,
                step_id,
                review_result,
                validation_context=review_context,
            )
            # Step 6: interpret a validated result through two independent model
            # calls.  The secondary receives only shared evidence/result inputs,
            # never Qwen reasoning.  All reconciliation below is deterministic.
            literature_evidence = list((latest.get("evidence").content.get("core_references") or latest.get("evidence").content.get("references") or [])) if latest.get("evidence") else []
            try:
                qwen_analysis = normalize_scientific_analysis(
                    self.critic_agent.scientific_result_analysis(
                        active_hypothesis, result_for_feedback, plan=current_plan,
                        evidence=literature_evidence, provider_id="qwen",
                        instructions=claim_instructions,
                    ),
                    provider_id="qwen",
                )
            except ValueError as exc:
                if str(exc) != "SCIENTIFIC_ANALYSIS_STATUS_INVALID:EMPTY":
                    raise
                fallback_status = {
                    "supported": "SUPPORTED",
                    "failed": "CONTRADICTED",
                    "partial": "INCONCLUSIVE",
                }.get(str(feedback.get("verdict") or "").lower(), "INCONCLUSIVE")
                qwen_analysis = normalize_scientific_analysis(
                    {
                        "hypothesis_status": fallback_status,
                        "supported_findings": list(feedback.get("supported_claims") or []),
                        "contradicting_findings": list(feedback.get("unsupported_claims") or []),
                        "alternative_explanations": [],
                        "confounders": list((feedback.get("result_analysis") or {}).get("methodological_issues") or []),
                        "evidence_gaps": list((feedback.get("result_analysis") or {}).get("knowledge_gaps") or []),
                        "interpretation": str(feedback.get("feedback") or feedback.get("next_action") or "Primary scientific analysis returned an empty status."),
                        "recommended_action": str(feedback.get("next_action") or ""),
                        "proposed_hypothesis": None,
                        "confidence": 0.0,
                    },
                    provider_id="qwen_fallback",
                )
            qwen_analysis_artifact = self.repository.add_artifact(
                run_id, "qwen_scientific_analysis", f"Qwen Scientific Analysis {iteration}",
                qwen_analysis, step_id, "Qwen Primary Scientific Analyst",
                parent_artifact_id=result_artifact.id,
            )
            try:
                deepseek_analysis = normalize_scientific_analysis(
                    self.critic_agent.scientific_result_analysis(
                        active_hypothesis, result_for_feedback, plan=current_plan,
                        evidence=literature_evidence, provider_id="deepseek",
                        instructions=claim_instructions,
                    ),
                    provider_id="deepseek",
                )
            except (RuntimeError, ValueError) as exc:
                if not any(token in str(exc) for token in (
                    "DEEPSEEK", "SECONDARY_REVIEW_UNAVAILABLE", "MODEL_REQUEST_",
                    "MODEL_PROVIDER_CONFIG_ERROR", "JSONDecodeError",
                    "SCIENTIFIC_ANALYSIS_", "MODEL_EMPTY_OUTPUT",
                )):
                    raise
                deepseek_analysis = unavailable_secondary_review(str(exc))
            deepseek_analysis_artifact = self.repository.add_artifact(
                run_id, "deepseek_scientific_review", f"DeepSeek Independent Scientific Review {iteration}",
                deepseek_analysis, step_id, "DeepSeek Independent Scientific Critic",
                parent_artifact_id=result_artifact.id,
            )
            # Phase 3 exposes the same independent review under a durable,
            # explicit diagnosis Artifact for append-only Idea lineage.
            scientific_diagnosis_artifact = self.repository.add_artifact(
                run_id, "scientific_diagnosis", f"Scientific Diagnosis v{iteration}",
                {**deepseek_analysis, "result_evidence_id": (latest.get("result_evidence").id if latest.get("result_evidence") else ""),
                 "research_constraints_artifact_id": run.research_constraints_artifact_id or "", "diagnosis_provider": "deepseek"},
                step_id, "DeepSeek Independent Scientific Critic", parent_artifact_id=deepseek_analysis_artifact.id,
            )
            disagreement = detect_disagreement(qwen_analysis, deepseek_analysis)
            disagreement_artifact = self.repository.add_artifact(
                run_id, "scientific_disagreement", f"Scientific Disagreement {iteration}",
                disagreement, step_id, "Deterministic Scientific Disagreement Detector",
                parent_artifact_id=qwen_analysis_artifact.id,
            )
            synthesis = synthesize_scientific_conclusion(qwen_analysis, deepseek_analysis, disagreement)
            synthesis_artifact = self.repository.add_artifact(
                run_id, "scientific_synthesis", f"Scientific Synthesis {iteration}",
                synthesis, step_id, "Workflow Engine",
                parent_artifact_id=disagreement_artifact.id,
            )
            conclusion = {
                "research_question_id": (latest.get("problem").id if latest.get("problem") else ""),
                "hypothesis_id": (latest.get("hypothesis_selection").id if latest.get("hypothesis_selection") else ""),
                "claim": active_hypothesis.get("claim") or "",
                "evidence_for": synthesis["supported_claims"], "evidence_against": synthesis["unsupported_claims"],
                "limitations": synthesis["remaining_uncertainties"], "confounders": synthesis["confounders"],
                "unresolved_questions": synthesis["remaining_uncertainties"], "confidence": synthesis["confidence"],
                "derived_from": [result_artifact.id, qwen_analysis_artifact.id, deepseek_analysis_artifact.id, disagreement_artifact.id, synthesis_artifact.id],
                "hypothesis_status": synthesis["hypothesis_status"], "current_conclusion": synthesis["current_conclusion"],
            }
            conclusion_artifact = self.repository.add_artifact(
                run_id, "scientific_conclusion", f"Scientific Conclusion {iteration}", conclusion,
                step_id, "Workflow Engine", parent_artifact_id=synthesis_artifact.id,
            )
            evolution = evolution_decision(synthesis, iteration=iteration, max_iterations=self.max_feedback_iterations)
            evolution_artifact = self.repository.add_artifact(
                run_id, "hypothesis_evolution_decision", f"Hypothesis Evolution Decision {iteration}", evolution,
                step_id, "Workflow Engine", parent_artifact_id=conclusion_artifact.id,
            )
            feedback["scientific_conclusion_id"] = conclusion_artifact.id
            feedback["hypothesis_evolution_decision_id"] = evolution_artifact.id
            if optimizing:
                feedback["research_context"] = research_context
                feedback["scientific_synthesis"] = deepcopy(synthesis)
                feedback["scientific_disagreement"] = deepcopy(disagreement)
                if optimization_stop:
                    route_to_report(feedback, optimization_stop)
                else:
                    # Direction selection, below, is the final continuation
                    # decision and sees both independent scientific analyses.
                    # This provisional flag is never saved as a final decision.
                    feedback["requires_follow_up"] = True
            working_hypothesis = None
            if feedback["requires_follow_up"] and evolution["create_working_hypothesis"]:
                working_hypothesis = build_working_hypothesis(
                    parent_hypothesis_id=conclusion["hypothesis_id"],
                    parent_claim=conclusion["claim"], proposal=synthesis["proposed_hypothesis"],
                    derived_from=conclusion["derived_from"], reason=evolution["reason"], revision=iteration,
                )
            iteration_analysis_artifact = self.repository.add_artifact(
                run_id,
                "iteration_analysis",
                f"Iteration Analysis {iteration}",
                feedback["result_analysis"],
                step_id,
                self.critic_agent.name,
                parent_artifact_id=result_artifact.id,
            )
            verdict_package = package
            if feedback["requires_follow_up"]:
                verdict_state = {
                    **state,
                    "experiment_verdict": feedback["verdict"],
                    "plan_refinement_enabled": True,
                }
                verdict_delegation = self.supervisor_agent.delegate(
                    step_id, verdict_state
                )
                verdict_package = self.skill_runtime.prepare(
                    step_id,
                    verdict_delegation.agent_id,
                    self.configured_tools,
                    verdict_state,
                )
                if verdict_package.skill_ids != package.skill_ids:
                    skill_calls.extend(
                        [verdict_delegation.tool_call, self._runtime_call(verdict_package)]
                    )

            if feedback["requires_follow_up"]:
                query_specs = feedback["literature_queries"]
                self._require_tools(
                    verdict_package,
                    "query_wiki",
                    "search_local_literature",
                    "literature_search",
                )
                iteration_evidence = self._collect_iteration_evidence(
                    run_id,
                    query_specs,
                )
                iteration_evidence_artifact = self.repository.add_artifact(
                    run_id,
                    "iteration_evidence",
                    f"Iteration Evidence {iteration}",
                    iteration_evidence,
                    step_id,
                    self.critic_agent.name,
                    parent_artifact_id=iteration_analysis_artifact.id,
                )
                self.repository.add_artifact(
                    run_id, "targeted_literature_update", f"Targeted Literature Update {iteration}",
                    {**iteration_evidence, "scientific_diagnosis_id": scientific_diagnosis_artifact.id},
                    step_id, self.critic_agent.name, parent_artifact_id=iteration_evidence_artifact.id,
                )
                direction_instructions = self._with_output_language(
                    self.skill_runtime.instructions_for(
                        verdict_package, "experiment-iteration"
                    ),
                    output_language,
                )
                if optimizing:
                    direction_instructions += (
                        "\nDecide whether the frozen optimization goal has a useful next experiment "
                        "AFTER reading feedback.scientific_synthesis and research_context. Do not "
                        "copy the provisional review decision. supported is not a stop condition. "
                        "REPORT is valid at any round. A continuation must cite exact source_result_ids, "
                        "name the measured result_basis, and be one of the compared candidates. "
                        "Never claim a high AUC alone proves leakage, or a drop after changing splits "
                        "proves its cause. Treat those as hypotheses to test."
                    )
                direction = {}
                for direction_attempt in range(3):
                    direction = self._normalize_iteration_direction(
                        self.critic_agent.select_iteration_direction(
                            active_hypothesis,
                            current_plan,
                            result,
                            feedback,
                            iteration_evidence,
                            instructions=direction_instructions,
                        )
                    )
                    direction_issues = self._iteration_direction_issues(
                        direction, output_language
                    )
                    if optimizing:
                        direction_issues.extend(self._serial_direction_issues(direction, iteration_memory))
                    if not direction_issues:
                        break
                    direction_instructions += (
                        "\n\n## 必须修正的方向决策输出\n"
                        + "\n".join(f"- {issue}" for issue in direction_issues)
                    )
                else:
                    direction = {
                        "decision": "REPORT",
                        "evidence_sufficiency": "EVIDENCE_INSUFFICIENT",
                        "evidence_assessment": [],
                        "optimization_candidates": [],
                        "selected_direction": {},
                        "selection_reason": "方向输出未通过格式校验，停止自动追加实验。",
                        "next_action": "保留当前实验结论并生成报告。",
                    }
                self.repository.add_artifact(
                    run_id,
                    "iteration_decision",
                    f"Iteration Decision {iteration}",
                    direction,
                    step_id,
                    self.critic_agent.name,
                    parent_artifact_id=iteration_evidence_artifact.id,
                )
                feedback["evidence_sufficiency"] = direction[
                    "evidence_sufficiency"
                ]
                feedback["evidence_assessment"] = direction[
                    "evidence_assessment"
                ]
                feedback["optimization_candidates"] = direction[
                    "optimization_candidates"
                ]
                feedback["selected_direction"] = direction[
                    "selected_direction"
                ]
                feedback["selection_reason"] = direction["selection_reason"]
                direction_decision = normalize_feedback_decision(
                    direction.get("decision")
                )
                if direction_decision == "REPORT" or not direction["selected_direction"]:
                    route_to_report(
                        feedback,
                        "DIRECTION_REPORT"
                        if direction_decision == "REPORT"
                        else "NO_EXECUTABLE_ITERATION_DIRECTION",
                    )
                else:
                    feedback["decision"] = direction_decision
                    feedback["route_reason"] = "EVIDENCE_GROUNDED_DIRECTION"
                    feedback["requires_follow_up"] = True
                    if optimizing:
                        feedback["required_revision"] = (
                            str(direction["selected_direction"].get("problem_addressed") or "")
                            + ": " + str(direction["selected_direction"].get("changed_variable") or "")
                        )
                        proposal = direction.get("proposed_hypothesis") or {}
                        if direction_decision == "PIVOT" and proposal.get("claim") and not working_hypothesis:
                            working_hypothesis = build_working_hypothesis(
                                parent_hypothesis_id=conclusion["hypothesis_id"],
                                parent_claim=conclusion["claim"], proposal=proposal,
                                derived_from=conclusion["derived_from"],
                                reason=direction["selection_reason"], revision=iteration,
                            )
                if feedback["requires_follow_up"] and direction["next_action"] and (
                    query_specs or not feedback.get("next_action")
                ):
                    feedback["next_action"] = direction["next_action"]
                if (
                    feedback["requires_follow_up"]
                    and direction["evidence_sufficiency"]
                    == "EVIDENCE_INSUFFICIENT"
                    and not direction["selected_direction"]
                ):
                    feedback["next_action"] = (
                        "当前实验未能支持实验前的 idea；基于失败标准、未支持主张"
                        "和已冻结的研究问题生成下一轮修订实验。"
                        if output_language == "zh-CN"
                        else (
                            "The experiment did not support the pre-experiment idea; "
                            "revise the idea and experiment within the frozen research question."
                        )
                    )
                pivot_lineage = {}
                if feedback["requires_follow_up"] and feedback["decision"] == "PIVOT":
                    pivot_lineage = self._pivot_lineage_payload(
                        working_hypothesis=working_hypothesis,
                        working_hypothesis_id=str(
                            feedback.get("working_hypothesis_id") or ""
                        ),
                        parent_claim=str(active_hypothesis.get("claim") or ""),
                        derived_from=list(
                            (working_hypothesis or {}).get("derived_from") or []
                        ),
                        base_plan_artifact_id=retained_best["plan_id"] if retained_best else latest["plan"].id,
                        base_experiment_task_id=(
                            retained_best["task_id"] if retained_best else latest.get("experiment_task").id
                            if latest.get("experiment_task")
                            else ""
                        ),
                        base_bundle_id=(
                            retained_best["bundle_id"] if retained_best else latest.get("experiment_bundle").id
                            if latest.get("experiment_bundle")
                            else ""
                        ),
                    )
                    if not pivot_lineage:
                        route_to_report(feedback, "PIVOT_HYPOTHESIS_LINEAGE_MISSING")
                if feedback["requires_follow_up"]:
                    if feedback["decision"] != "PIVOT":
                        working_hypothesis = None
                    selection = deepcopy(
                        self._require_evidence_reasoned_hypothesis_selection(latest)
                    )
                    selection["selected"] = [
                        deepcopy(working_hypothesis or active_hypothesis)
                    ]
                    dataset_profile = (
                        latest["dataset_profile"].content
                        if latest.get("dataset_profile")
                        else None
                    )
                    dataset_options = (
                        [dataset_option(dataset_profile)]
                        if dataset_profile
                        else self._dataset_options()
                    )
                    refinement_skill_ids = [
                        "experiment-iteration",
                        "research-refine",
                        "experiment-plan",
                    ]
                    if "ablation-planner" in verdict_package.skill_ids:
                        refinement_skill_ids.append("ablation-planner")
                    refinement_instructions = self.skill_runtime.instructions_for(
                        verdict_package, *refinement_skill_ids
                    )
                    refinement_instructions = self._with_output_language(
                        refinement_instructions, output_language
                    )
                    refinement_base = current_plan
                    best = retained_best
                    if best:
                        refinement_base = next(a.content for a in run.artifacts if a.id == best["plan_id"])
                        feedback["implementation_reference"] = {
                            key: best[key] for key in
                            ("result_id", "plan_id", "task_id", "bundle_id", "bundle_sha256", "protocol_key")
                        }
                        feedback["latest_trial_plan"] = current_plan
                        refinement_instructions += (
                            "\ncurrent_plan is the retained best implementation, not necessarily the "
                            "latest trial. experiment_result is the latest observation; never attribute "
                            "its metrics to the base. Inherit current_plan and implement only the selected "
                            "change. Use research_context and scientific_synthesis in feedback."
                        )
                    revised_plan = normalize_plan(
                        self.planning_agent.refine_plan(
                            selection,
                            refinement_base,
                            result,
                            feedback,
                            instructions=refinement_instructions,
                            dataset_options=dataset_options,
                            authoritative_contract_snapshot=feedback_frozen_policy[
                                "authoritative_plan_contract_snapshot"
                            ],
                        ),
                        selection,
                        provider_mode=self.llm_provider.mode,
                        fallback_used=self.llm_provider.fallback,
                    )
                    frozen_training_budget = self._frozen_model_training_budget(run_id)
                    if frozen_training_budget is None:
                        raise ValueError("MODEL_TRAINING_BUDGET_CONTRACT_MISSING")
                    if frozen_training_budget.get("mode") == "epochs":
                        revised_plan = self._with_frozen_training_epochs(
                            revised_plan,
                            int(frozen_training_budget["epochs"]),
                        )
                    revised_plan["seeds"] = list(
                        self._ensure_backend_execution_seed_contract(
                            run_id,
                            constraints=normalize_constraints(
                                run.research_constraints, run.constraints
                            ),
                            plan=current_plan,
                        ).content["seeds"]
                    )
                    if dataset_profile:
                        revised_plan = self._bind_plan_to_dataset(
                            revised_plan, dataset_profile
                        )
                    if pivot_lineage:
                        revised_plan = self._apply_pivot_claim_contract(
                            revised_plan, pivot_lineage
                        )
                    revised_plan["iteration_contract"] = self._build_iteration_contract(
                        iteration,
                        refinement_base,
                        revised_plan,
                        feedback,
                    )
                    if optimizing and best:
                        revised_plan["iteration_contract"]["implementation_reference"] = deepcopy(feedback["implementation_reference"])
                    if optimizing:
                        revised_plan["iteration_contract"]["source_result_ids"] = list(
                            feedback["selected_direction"]["source_result_ids"]
                        )
                    if pivot_lineage:
                        revised_plan["iteration_contract"]["hypothesis_lineage"] = pivot_lineage
                    revised_plan = self._attach_dataset_card(
                        revised_plan, dataset_options
                    )
                    material_changes = set(
                        revised_plan["iteration_contract"].get("changed_fields")
                        or []
                    )
                    if optimizing and trial_signature(revised_plan) in {
                        row.get("trial_signature") for row in iteration_memory.get("history", [])
                    }:
                        material_changes = set()
                        feedback["duplicate_trial_detected"] = True
                    if material_changes:
                        if working_hypothesis:
                            working_artifact = self.repository.add_artifact(
                                run_id,
                                "working_hypothesis",
                                f"Working Hypothesis v{iteration + 1}",
                                working_hypothesis,
                                step_id,
                                "Workflow Engine",
                                parent_artifact_id=conclusion_artifact.id,
                            )
                            feedback["working_hypothesis_id"] = working_artifact.id
                            self.repository.add_artifact(
                                run_id,
                                "idea_revision",
                                f"Idea Revision v{iteration + 1}",
                                {
                                    **working_hypothesis,
                                    "scientific_diagnosis_id": (
                                        scientific_diagnosis_artifact.id
                                    ),
                                    "research_constraints_artifact_id": (
                                        run.research_constraints_artifact_id or ""
                                    ),
                                },
                                step_id,
                                "Phase 3 Idea Evolution",
                                parent_artifact_id=working_artifact.id,
                            )
                            if pivot_lineage:
                                pivot_lineage["working_hypothesis_id"] = (
                                    working_artifact.id
                                )
                                revised_plan["iteration_contract"][
                                    "hypothesis_lineage"
                                ] = pivot_lineage
                        feedback["revised_plan"] = revised_plan
                    else:
                        route_to_report(feedback, "NO_MATERIAL_PLAN_CHANGE")
            revision_artifact = self.repository.add_artifact(
                run_id,
                "revision",
                "Feedback Revision",
                feedback,
                step_id,
                self.critic_agent.name,
                parent_artifact_id=result_artifact.id,
            )
            integrity_contract = compile_scientific_contract(
                run.problem_input,
                (latest.get("hypothesis_selection").content.get("selected") if latest.get("hypothesis_selection") else []),
                current_plan,
                latest.get("experiment_task").content if latest.get("experiment_task") else {},
            )
            scientific_verdict = {
                "supported": "supported", "failed": "unsupported", "partial": "inconclusive",
            }.get(str(feedback.get("verdict") or ""), "inconclusive")
            self.repository.add_artifact(
                run_id,
                "scientific_feedback",
                "Scientific Experiment Feedback",
                scientific_feedback(integrity_contract, result, scientific_verdict),
                step_id,
                self.critic_agent.name,
                parent_artifact_id=revision_artifact.id,
            )
            iteration_run = self.repository.get_run(run_id)
            iteration_run.feedback_iteration = max(
                iteration_run.feedback_iteration, iteration
            )
            self.repository.save_run(iteration_run)
            if feedback["requires_follow_up"]:
                proposal_payload = {
                    "schema_version": 1,
                    "policy_artifact_id": feedback_policy_artifact.id,
                    "policy_payload_sha256": feedback_frozen_policy[
                        "policy_payload_sha256"
                    ],
                    "base_plan_artifact_id": latest["plan"].id,
                    "base_candidate_id": str(
                        (latest["plan"].content or {}).get("plan_candidate_id") or ""
                    ),
                    "feedback_revision_id": revision_artifact.id,
                    "iteration": iteration,
                    "normalized_plan": deepcopy(feedback["revised_plan"]),
                }
                self.repository.add_artifact(
                    run_id,
                    "plan_refinement_proposal",
                    f"Research Plan Refinement Proposal (Feedback {iteration})",
                    {
                        **proposal_payload,
                        "proposal_payload_sha256": canonical_sha256(proposal_payload),
                    },
                    "research_plan",
                    self.planning_agent.name,
                    parent_artifact_id=revision_artifact.id,
                )
            state_run = self.repository.get_run(run_id)
            if optimizing:
                self.repository.add_artifact(
                    run_id, "optimization_state", f"Serial Optimization State {iteration}",
                    build_iteration_memory(state_run.artifacts), step_id, "Workflow Engine",
                    parent_artifact_id=revision_artifact.id,
                )
                state_run = self.repository.get_run(run_id)
            research_state = build_research_state(state_run.artifacts)
            self.repository.add_artifact(
                run_id,
                "research_state",
                f"Research State {iteration}",
                research_state,
                step_id,
                self.critic_agent.name,
                parent_artifact_id=revision_artifact.id,
            )
            self._trace(
                run_id,
                step_id,
                self.critic_agent.name,
                (
                    "Created feedback-based revision and refined the research plan."
                    if feedback["requires_follow_up"]
                    else "Created feedback-based revision without another plan."
                ),
                {"result": result},
                feedback,
                skill_calls=skill_calls,
            )
        elif step_id == "report_export":
            self.supervisor_agent.require_agent(delegation, "writer")
            self._require_tools(package, "render_report")
            current_state = build_research_state(run.artifacts)
            previous_state = next(
                (
                    artifact
                    for artifact in reversed(run.artifacts)
                    if artifact.type == "research_state"
                ),
                None,
            )
            if previous_state is None or previous_state.content != current_state:
                self.repository.add_artifact(
                    run_id,
                    "research_state",
                    "Final Research State",
                    current_state,
                    step_id,
                    self.writer_agent.name,
                    parent_artifact_id=(
                        run.artifacts[-1].id if run.artifacts else None
                    ),
                )
                run = self.repository.get_run(run_id)
            try:
                report = self._produce_validated(
                    run_id,
                    step_id,
                    lambda revision: self.writer_agent.build_report(
                        run.artifacts,
                        instructions=self._with_revision(instructions, revision),
                    ),
                )
            except ReportFactAuditError as exc:
                draft_artifact = self.repository.add_artifact(
                    run_id,
                    "report_draft",
                    "Report Draft Requiring Fact Repair",
                    exc.draft,
                    step_id,
                    self.writer_agent.name,
                )
                audit_artifact = self.repository.add_artifact(
                    run_id,
                    "report_audit",
                    "Internal Report Fact Audit",
                    exc.audit,
                    step_id,
                    self.writer_agent.name,
                    parent_artifact_id=draft_artifact.id,
                )
                failed_report_run = self.repository.get_run(run_id)
                self.repository.add_artifact(
                    run_id,
                    "research_state",
                    "Research State After Report Audit",
                    build_research_state(failed_report_run.artifacts),
                    step_id,
                    self.writer_agent.name,
                    parent_artifact_id=audit_artifact.id,
                )
                raise
            if self.competition_mode:
                allowed, reason = competition_export_allowed(report)
                if not allowed:
                    raise ValueError(f"COMPETITION_REPORT_BLOCKED:{reason}")
            report_artifact = self.repository.add_artifact(
                run_id,
                "report",
                "Competition Report",
                report,
                step_id,
                self.writer_agent.name,
            )
            completed_report_run = self.repository.get_run(run_id)
            self.repository.add_artifact(
                run_id,
                "research_state",
                "Research State After Report Export",
                build_research_state(completed_report_run.artifacts),
                step_id,
                self.writer_agent.name,
                parent_artifact_id=report_artifact.id,
            )
            self._trace(
                run_id,
                step_id,
                self.writer_agent.name,
                "Created report artifact.",
                {"artifact_count": len(run.artifacts)},
                report,
                skill_calls=skill_calls,
            )
        else:
            raise ValueError(f"Unknown workflow step: {step_id}")
        return self.repository.get_run(run_id)

    def rerun_from(self, run_id: str, step_id: str):
        # A no-selectable-candidate outcome is a scientific revision, not an
        # invalidation of the previous scientific record.  It must append a new
        # hypothesis/evidence round instead of routing through destructive
        # ``_rerun_from`` cleanup.
        if (
            step_id == "hypothesis_generation"
            and self.repository.get_run(run_id).status == "hypothesis_revision_required"
        ):
            self.repository.update_workflow_state(
                run_id,
                status="running",
                current_step=step_id,
                automatic=False,
                stop_requested=False,
            )
            try:
                with self._run_lock(run_id):
                    self._append_hypothesis_revision_round(run_id)
            except LLMRequestCancelled:
                self.repository.update_workflow_state(
                    run_id, status="paused", automatic=False, stop_requested=True
                )
                raise
            except Exception as exc:
                self.repository.update_workflow_state(run_id, status=failure_state_for(exc), automatic=False)
                raise
            return self.repository.update_workflow_state(
                run_id, status="paused", current_step="evidence_reasoning",
                automatic=False, stop_requested=False,
            )
        self.repository.update_workflow_state(
            run_id,
            status="running",
            current_step=step_id,
            automatic=False,
            stop_requested=False,
        )
        try:
            with self._run_lock(run_id):
                self._rerun_from(run_id, step_id)
        except LLMRequestCancelled:
            self.repository.update_workflow_state(
                run_id,
                status="paused",
                automatic=False,
                stop_requested=True,
            )
            raise
        except Exception as exc:
            self.repository.update_workflow_state(
                run_id,
                status=failure_state_for(exc),
                automatic=False,
            )
            raise
        return self.repository.update_workflow_state(
            run_id,
            status="paused",
            automatic=False,
            stop_requested=False,
        )

    def _rerun_from(self, run_id: str, step_id: str):
        run = self.repository.get_run(run_id)
        start = ORDER.index(step_id)
        preserve_requested_step = step_id == "research_plan" or any(
            artifact.locked and artifact.source_step == step_id
            for artifact in run.artifacts
        )
        if step_id == "experiment_run_analysis":
            # An experiment retry is another attempt of the current iteration.
            # Keep the revision -> refined plan -> task -> bundle lineage intact;
            # otherwise recursive cleanup silently falls back to the first task.
            affected_steps = {"report_export"}
        else:
            affected_steps = set(
                ORDER[start + 1 :] if preserve_requested_step else ORDER[start:]
            )
        removed_ids = {
            artifact.id
            for artifact in run.artifacts
            if artifact.source_step in affected_steps
        }
        changed = True
        while changed:
            changed = False
            for artifact in run.artifacts:
                if (
                    artifact.id not in removed_ids
                    and artifact.parent_artifact_id in removed_ids
                ):
                    removed_ids.add(artifact.id)
                    changed = True
        if step_id == "experiment_run_analysis":
            latest = self._latest_by_type(run.artifacts)
            task = latest.get("experiment_task")
            bundle = latest.get("experiment_bundle")
            previous_result = latest.get("experiment_result")
            experiment_id = str(task.content.get("experiment_id") or "") if task else ""
            if experiment_id and experiment_id not in run.force_new_attempt_experiment_ids:
                run.force_new_attempt_experiment_ids.append(experiment_id)
            self.repository.save_run(run)
            self.repository.append_event(
                run_id,
                step_id,
                "Workflow Engine",
                "Retrying the current experiment iteration with a new attempt.",
                data={
                    "retry_mode": "new_attempt_same_iteration",
                    "experiment_id": experiment_id,
                    "task_artifact_id": task.id if task else None,
                    "bundle_artifact_id": bundle.id if bundle else None,
                    "previous_result_artifact_id": (
                        previous_result.id if previous_result else None
                    ),
                },
                output_summary={
                    "lineage_preserved": True,
                    "report_invalidated": True,
                },
            )
        else:
            self.repository.save_run(run)
        if removed_ids:
            # Save any branch/attempt state first.  append_event reloads the
            # durable run, so this event cannot be overwritten by a stale run.
            self.repository.append_event(
                run_id,
                step_id,
                "Workflow Engine",
                "Superseded artifacts retained for append-only rerun.",
                data={"superseded_artifact_ids": sorted(removed_ids), "mode": "append_only"},
            )
        return self.run_step(run_id, step_id, force=step_id == "report_export")

    def _append_hypothesis_revision_round(self, run_id: str):
        """Run the next hypothesis/evidence pair without removing prior rounds."""
        before = self.repository.get_run(run_id)
        previous_hypotheses = [
            artifact.id for artifact in before.artifacts if artifact.type == "hypothesis"
        ]
        previous_reasoning = [
            artifact.id for artifact in before.artifacts if artifact.type == "reasoning"
        ]
        self.repository.append_event(
            run_id,
            "hypothesis_generation",
            "Workflow Engine",
            "Starting append-only hypothesis revision round.",
            data={
                "mode": "append_only_hypothesis_revision",
                "preserved_hypothesis_artifact_ids": previous_hypotheses,
                "preserved_reasoning_artifact_ids": previous_reasoning,
            },
            output_summary={"historical_artifacts_removed": 0},
        )
        self.run_step(run_id, "hypothesis_generation")
        return self.run_step(run_id, "evidence_reasoning")

    def _clear_forced_experiment_attempt(
        self, run_id: str, experiment_id: str | None
    ) -> None:
        if not experiment_id:
            return
        refreshed = self.repository.get_run(run_id)
        if experiment_id not in refreshed.force_new_attempt_experiment_ids:
            return
        refreshed.force_new_attempt_experiment_ids = [
            item
            for item in refreshed.force_new_attempt_experiment_ids
            if item != experiment_id
        ]
        self.repository.save_run(refreshed)

    def add_user_hypothesis(self, run_id: str, claim: str, replacement_index: int | None = None):
        with self._run_lock(run_id):
            # This endpoint is a recovery action: it persists a user-supplied
            # candidate and immediately re-runs evidence reasoning.  A run that
            # was intentionally paused still carries its cancellation marker;
            # clear that marker before the nested step starts, otherwise
            # ``run_step`` correctly (but incorrectly for this recovery path)
            # cancels the new work before it can inspect the candidate.
            if self.repository.get_run(run_id).stop_requested:
                self.repository.update_workflow_state(run_id, stop_requested=False)
            return self._add_user_hypothesis(run_id, claim, replacement_index)

    def regenerate_hypotheses(self, run_id: str):
        """Re-read the existing literature and synthesis for a new hypothesis round.

        This does not re-run literature search.  It reuses the already retrieved
        Evidence and Research Synthesis, re-runs Hypothesis Generation (creating an
        append-only new round), then re-runs Evidence Reasoning to recompute scores.
        Selection is left to the orchestrator's deterministic auto-select rule.
        """
        with self._run_lock(run_id):
            run = self.repository.get_run(run_id)
            latest = self._latest_by_type(run.artifacts)
            if "evidence" not in latest or "research_synthesis" not in latest:
                raise ValueError("HYPOTHESIS_REGENERATION_REQUIRES_LITERATURE")
            if run.stop_requested:
                self.repository.update_workflow_state(run_id, stop_requested=False)
            # A new hypothesis round supersedes the prior selection and any
            # downstream plan/experiment lineage, mirroring a manual re-selection.
            downstream_steps = set(ORDER[ORDER.index("research_plan"):])
            run.artifacts = [
                artifact
                for artifact in run.artifacts
                if (
                    artifact.locked
                    or (
                        artifact.type != "hypothesis_selection"
                        and artifact.source_step not in downstream_steps
                    )
                )
            ]
            self.repository.save_run(run)
            self.run_step(run_id, "hypothesis_generation")
            self.run_step(run_id, "evidence_reasoning")
            return self.repository.get_run(run_id)

    def select_hypothesis(self, run_id: str, candidate_index: int):
        with self._run_lock(run_id):
            run = self.repository.get_run(run_id)
            latest = self._latest_by_type(run.artifacts)
            reasoning_artifact = latest.get("reasoning")
            if (
                reasoning_artifact is None
                or reasoning_artifact.source_step != "evidence_reasoning"
            ):
                raise ValueError("HYPOTHESIS_REASONING_REQUIRED")
            assessments = reasoning_artifact.content.get("candidate_assessments")
            if not isinstance(assessments, list):
                raise ValueError("HYPOTHESIS_REASONING_REQUIRED")
            assessment = next(
                (
                    item
                    for item in assessments
                    if isinstance(item, dict)
                    and item.get("candidate_index") == candidate_index
                ),
                None,
            )
            if assessment is None:
                raise ValueError("HYPOTHESIS_SELECTION_INDEX_INVALID")
            selected = assessment.get("revised_hypothesis") or assessment.get(
                "original_hypothesis"
            )
            candidates = normalize_candidates(
                latest["hypothesis"].content.get("candidates") or []
            )
            original_candidate = (
                candidates[candidate_index]
                if candidate_index < len(candidates)
                else None
            )
            # A user-selected hypothesis is a problem-definition anchor.  The
            # critic may add an evidence assessment, but may not silently swap
            # its claim for a different, model-authored hypothesis.
            if isinstance(original_candidate, dict) and original_candidate.get("source") == "user":
                selected = deepcopy(original_candidate)
            if not isinstance(selected, dict) or not str(selected.get("claim") or "").strip():
                raise ValueError("HYPOTHESIS_SELECTION_CANDIDATE_INVALID")

            downstream_steps = set(ORDER[ORDER.index("research_plan"):])
            run.artifacts = [
                artifact
                for artifact in run.artifacts
                if (
                    artifact.locked
                    or (
                        artifact.type != "hypothesis_selection"
                        and artifact.source_step not in downstream_steps
                    )
                )
            ]
            self.repository.save_run(run)
            selection_content = {
                "selected": [deepcopy(selected)],
                "selected_indexes": [candidate_index],
                "selection_mode": "user_selected_after_evidence_reasoning",
                "selection_reason": "Selected by the user after reviewing model reasoning.",
                "assessment_status": assessment.get("status") or "",
                "evaluation": deepcopy(assessment.get("evaluation") or {}),
            }
            if "composite_score" in assessment:
                selection_content["composite_score"] = assessment["composite_score"]
            else:
                assessment_scores = (assessment.get("evaluation") or {}).get("scores")
                if isinstance(assessment_scores, dict) and set(assessment_scores) == set(WEIGHTS):
                    selection_content["composite_score"] = composite_score(assessment_scores)
            self.repository.add_artifact(
                run_id,
                "hypothesis_selection",
                "User-Selected Hypothesis",
                selection_content,
                "evidence_reasoning",
                "Human Researcher",
                parent_artifact_id=reasoning_artifact.id,
            )
            self.repository.append_event(
                run_id,
                "evidence_reasoning",
                "Human Researcher",
                "Selected a hypothesis after reviewing evidence reasoning.",
                data={
                    "candidate_index": candidate_index,
                    "reasoning_artifact_id": reasoning_artifact.id,
                },
                input_summary={"candidate_count": len(assessments)},
                output_summary={
                    "selected_index": candidate_index,
                    "claim": selected.get("claim") or "",
                },
                provider_mode=self.llm_provider.mode,
                fallback_used=False,
            )
            return self.repository.get_run(run_id)

    def auto_select_hypothesis(self, run_id: str):
        with self._run_lock(run_id):
            run = self.repository.get_run(run_id)
            latest = self._latest_by_type(run.artifacts)
            reasoning = latest.get("reasoning")
            assessments = (
                reasoning.content.get("candidate_assessments")
                if reasoning is not None
                else None
            )
            if not isinstance(assessments, list):
                raise ValueError("HYPOTHESIS_REASONING_REQUIRED")
            candidate_count = len(
                normalize_candidates(latest["hypothesis"].content.get("candidates") or [])
            )
            completed = {
                item.get("candidate_index")
                for item in assessments
                if isinstance(item, dict)
            }
            if completed != set(range(candidate_count)):
                raise ValueError(
                    f"HYPOTHESIS_CANDIDATE_REVIEWS_INCOMPLETE:{len(completed)}/{candidate_count}"
                )
            viable = [item for item in assessments if self._assessment_selectable(item)]
            if not viable:
                revision_required = {
                    "code": "NO_SELECTABLE_HYPOTHESIS",
                    "message": "All reviewed hypotheses are rejected or evidence-insufficient.",
                    "required_candidates": candidate_count,
                    "completed_valid_candidates": len(completed),
                    "reviewed_candidate_ids": [self._assessment_id(item) for item in assessments],
                    "selectable_candidate_ids": [],
                    "next_action": "hypothesis_revision_required",
                    "candidate_assessments": deepcopy(assessments),
                }
                existing = [
                    artifact for artifact in run.artifacts
                    if artifact.type == "hypothesis_revision_required"
                    and artifact.parent_artifact_id == reasoning.id
                ]
                if not existing:
                    self.repository.add_artifact(
                        run_id,
                        "hypothesis_revision_required",
                        "Hypothesis Revision Required",
                        revision_required,
                        "evidence_reasoning",
                        "Workflow Engine",
                        parent_artifact_id=reasoning.id,
                    )
                    self.repository.append_event(
                        run_id,
                        "evidence_reasoning",
                        "Workflow Engine",
                        "Evidence reasoning completed; hypothesis revision is required before selection.",
                        data={"code": "NO_SELECTABLE_HYPOTHESIS", "status": "hypothesis_revision_required"},
                        input_summary={"candidate_count": candidate_count},
                        output_summary={"reviewed_candidates": len(completed), "selectable_candidates": 0, "recoverable": True},
                        provider_mode=self.llm_provider.mode,
                    )
                return self.repository.update_workflow_state(
                    run_id,
                    status="hypothesis_revision_required",
                    current_step="evidence_reasoning",
                    automatic=False,
                    stop_requested=False,
                )

            ranked = [
                item for item in viable
                if isinstance((item.get("evaluation") or {}).get("scores"), dict)
                and set((item.get("evaluation") or {})["scores"]) == set(WEIGHTS)
            ]
            winner = None
            winner_score = 0.0
            if ranked:
                winner = min(
                    ranked,
                    key=lambda item: (
                        -weighted_score(item["evaluation"]["scores"]),
                        int(item["candidate_index"]),
                    ),
                )
                winner_score = composite_score(winner["evaluation"]["scores"])
            if winner is None or winner_score <= AUTO_SELECT_THRESHOLD:
                # No candidate strictly exceeds the composite threshold: keep the
                # decision human.  The run pauses at hypothesis selection; the
                # user clicks a candidate or regenerates hypotheses.
                self.repository.update_workflow_state(
                    run_id,
                    status="paused",
                    current_step="evidence_reasoning",
                    automatic=False,
                    stop_requested=False,
                )
                self.repository.append_event(
                    run_id, "evidence_reasoning", "Workflow Engine",
                    "No candidate exceeded the auto-select threshold; manual selection is required.",
                    data={
                        "status": "awaiting_user_selection",
                        "auto_select_threshold": AUTO_SELECT_THRESHOLD,
                        "best_composite_score": winner_score if winner is not None else None,
                    },
                    output_summary={"recoverable": True, "user_action_required": True},
                    provider_mode=self.llm_provider.mode,
                )
                return self.repository.get_run(run_id)
            mode = "automatic"
            reason = (
                "Highest server-computed weighted score among valid selectable "
                "candidates and above the auto-select threshold."
            )
            selected = winner.get("revised_hypothesis") or winner.get("original_hypothesis")
            candidates = normalize_candidates(latest["hypothesis"].content.get("candidates") or [])
            candidate_index = int(winner["candidate_index"])
            original_candidate = (
                candidates[candidate_index]
                if candidate_index < len(candidates)
                else None
            )
            if isinstance(original_candidate, dict) and original_candidate.get("source") == "user":
                selected = deepcopy(original_candidate)
            available_ids = [self._assessment_id(item) for item in viable]
            selected_id = self._assessment_id(winner)
            content = {
                "selected": [deepcopy(selected)],
                "selected_indexes": [winner["candidate_index"]],
                "selection_mode": mode,
                "selection_reason": reason,
                "selected_hypothesis_id": selected_id,
                "available_hypothesis_ids": available_ids,
                "assessment_status": winner.get("status") or "",
                "evaluation": deepcopy(winner.get("evaluation") or {}),
                "composite_score": winner_score,
                "auto_select_threshold": AUTO_SELECT_THRESHOLD,
            }
            self.repository.add_artifact(
                run_id, "hypothesis_selection", "Automatically Selected Hypothesis",
                content, "evidence_reasoning", "Workflow Engine",
                parent_artifact_id=reasoning.id,
            )
            self.repository.append_event(
                run_id, "evidence_reasoning", "Workflow Engine",
                "Automatically selected a fully reviewed hypothesis.",
                data={
                    "selection_mode": mode,
                    "selection_reason": reason,
                    "selected_hypothesis_id": selected_id,
                    "available_hypothesis_ids": available_ids,
                    "composite_score": winner_score,
                },
                output_summary={"selected_index": winner["candidate_index"]},
                provider_mode=self.llm_provider.mode,
            )
            return self.repository.get_run(run_id)

    @staticmethod
    def _assessment_selectable(assessment: dict) -> bool:
        selected = assessment.get("revised_hypothesis") or assessment.get("original_hypothesis")
        return (
            isinstance(selected, dict)
            and bool(str(selected.get("claim") or "").strip())
            and assessment.get("status") in {"verified", "revised"}
            and assessment.get("recommendation") in {"GO", "REVISE"}
        )

    @staticmethod
    def _assessment_id(assessment: dict) -> str:
        return f"hypothesis_{int(assessment.get('candidate_index', 0)) + 1}"

    def _add_user_hypothesis(self, run_id: str, claim: str, replacement_index: int | None = None):
        run = self.repository.get_run(run_id)
        latest = self._latest_by_type(run.artifacts)
        problem = latest["problem"].content
        evidence_cards = latest["evidence"].content["references"]
        evidence = [EvidenceCard.model_validate(card) for card in evidence_cards]
        delegation = self.supervisor_agent.delegate("hypothesis_generation")
        self.supervisor_agent.require_agent(delegation, "idea")
        package = self.skill_runtime.prepare(
            "hypothesis_generation",
            delegation.agent_id,
            self.configured_tools,
        )
        instructions = package.instructions
        skill_calls = [delegation.tool_call, self._runtime_call(package)]
        analyzed = self.hypothesis_agent.analyze_user_hypothesis(
            claim,
            problem,
            evidence,
            instructions=instructions,
        )
        # The user-provided claim defines the controlled research question.
        # Analysis may enrich its method/evidence fields but must not replace
        # that question with a model-generated alternative.
        analyzed["claim"] = claim
        analyzed["source"] = "user"
        analyzed = normalize_candidate(analyzed)
        current = latest.get("hypothesis")
        existing = []
        if current and isinstance(current.content.get("candidates"), list):
            existing = normalize_candidates(current.content["candidates"])
        if len(existing) >= MAX_HYPOTHESIS_CANDIDATES:
            if replacement_index is None:
                raise ValueError("HYPOTHESIS_REPLACEMENT_REQUIRED")
            if replacement_index < 0 or replacement_index >= len(existing):
                raise ValueError("HYPOTHESIS_REPLACEMENT_INDEX_INVALID")
            candidates = list(existing)
            candidates[replacement_index] = analyzed
        else:
            if replacement_index is not None:
                if replacement_index < 0 or replacement_index >= MAX_HYPOTHESIS_CANDIDATES:
                    raise ValueError("HYPOTHESIS_REPLACEMENT_INDEX_INVALID")
                if replacement_index < len(existing):
                    candidates = list(existing)
                    candidates[replacement_index] = analyzed
                else:
                    candidates = existing + [analyzed]
            else:
                candidates = existing + [analyzed]
        content = {
            "candidates": candidates[:MAX_HYPOTHESIS_CANDIDATES],
            "user_hypothesis": analyzed,
        }
        round_metadata = self._next_hypothesis_round(run, latest)
        content["hypothesis_round"] = {
            **round_metadata,
            "created_candidate_ids": [
                str(candidate.get("candidate_id") or "")
                for candidate in content["candidates"]
            ],
        }
        self.repository.add_artifact(
            run_id,
            "hypothesis",
            f"Candidate Hypothesis with User Input · Round {round_metadata['round_index']}",
            content,
            "hypothesis_generation",
            self.hypothesis_agent.name,
        )
        self._trace(
            run_id,
            "hypothesis_generation",
            self.hypothesis_agent.name,
            "Analyzed user-supplied hypothesis against verified evidence.",
            {"claim": claim, "evidence_count": len(evidence)},
            analyzed,
            skill_calls=skill_calls,
        )
        # A user supplied revision is also an append-only scientific round.
        # Earlier reasoning/evidence may be superseded for selection, but they
        # remain immutable provenance records and must stay inspectable.
        self.repository.append_event(
            run_id,
            "hypothesis_generation",
            "Workflow Engine",
            "Appended user-supplied hypothesis round without deleting prior evidence reasoning.",
            data={"round_id": content["hypothesis_round"]["round_id"]},
            output_summary={"historical_artifacts_removed": 0},
        )
        return self.run_step(run_id, "evidence_reasoning")

    def _latest_by_type(self, artifacts):
        latest = {}
        for artifact in artifacts:
            latest[artifact.type] = artifact
        return latest

    def _next_hypothesis_round(self, run, latest: dict) -> dict:
        rounds: list[dict] = []
        for artifact in run.artifacts:
            if artifact.type != "hypothesis":
                continue
            value = artifact.content.get("hypothesis_round")
            if isinstance(value, dict):
                rounds.append(value)
        prior = max(rounds, key=lambda item: int(item.get("round_index") or 0), default={})
        round_index = int(prior.get("round_index") or 0) + 1
        revision = latest.get("hypothesis_revision_required")
        revision_content = revision.content if revision is not None else {}
        reasoning = latest.get("reasoning")
        assessments = (
            reasoning.content.get("candidate_assessments")
            if reasoning is not None and isinstance(reasoning.content.get("candidate_assessments"), list)
            else []
        )
        feedback = [
            {
                "candidate_id": str(item.get("candidate_id") or self._assessment_id(item)),
                "status": str(item.get("status") or item.get("verdict") or ""),
                "reason": str(item.get("reasoning") or item.get("reason") or ""),
            }
            for item in assessments if isinstance(item, dict)
        ]
        return {
            "round_id": f"HYPOTHESIS-ROUND-{round_index:03d}",
            "round_index": round_index,
            "parent_round_id": str(prior.get("round_id") or ""),
            "revision_reason": str(
                revision_content.get("code") or revision_content.get("message") or "initial_hypothesis_generation"
            ),
            "scientific_feedback": feedback,
        }

    def _hypothesis_revision_context(self, run, round_metadata: dict) -> dict:
        if int(round_metadata.get("round_index") or 1) <= 1:
            return {}
        prior_hypothesis = next(
            (artifact for artifact in reversed(run.artifacts) if artifact.type == "hypothesis"),
            None,
        )
        if prior_hypothesis is None:
            return {}
        prior_round = prior_hypothesis.content.get("hypothesis_round")
        return {
            "parent_round_id": round_metadata.get("parent_round_id") or "",
            "revision_reason": round_metadata.get("revision_reason") or "",
            "scientific_feedback": deepcopy(round_metadata.get("scientific_feedback") or []),
            "prior_candidates": [
                {
                    "candidate_id": str(candidate.get("candidate_id") or ""),
                    "claim": str(candidate.get("claim") or ""),
                    "source_gap_ids": list(candidate.get("source_gap_ids") or []),
                }
                for candidate in prior_hypothesis.content.get("candidates") or []
                if isinstance(candidate, dict)
            ],
            "instruction": (
                "Do not repeat the prior candidates. Address the recorded evidence gaps, "
                "support/contradiction/missing-evidence feedback, and cite valid GAP IDs."
            ),
            "prior_round": deepcopy(prior_round) if isinstance(prior_round, dict) else {"legacy_artifact_id": prior_hypothesis.id},
        }

    @staticmethod
    def _runtime_repair_parent_attempt_id(
        artifacts,
        *,
        experiment_id: str,
        task_artifact,
        fallback_id: str | None,
    ) -> str | None:
        """Resolve a repair-candidate parent across durable checkpoint versions.

        ``normalized_bundle`` is intentionally nullable: failed candidates are
        audit evidence even when the model response could not be normalized.
        New candidate records carry task/experiment ownership independently;
        the plan-artifact fallback preserves recovery for checkpoints written
        before those fields were introduced.
        """
        task_id = task_artifact.id
        plan_id = task_artifact.parent_artifact_id
        for artifact in reversed(artifacts):
            if artifact.type != "experiment_candidate_attempt":
                continue
            content = artifact.content
            normalized = content.get("normalized_bundle")
            manifest = (
                normalized.get("manifest")
                if isinstance(normalized, dict)
                else {}
            )
            candidate_experiment_id = str(
                content.get("experiment_id")
                or manifest.get("experiment_id")
                or (content.get("manifest") or {}).get("experiment_id")
                or ""
            )
            if candidate_experiment_id == experiment_id:
                return artifact.id
            if content.get("task_artifact_id") == task_id:
                return artifact.id
            # Older checkpoints did not persist task/experiment ownership on
            # runtime repair candidates.  Their durable plan reference is the
            # authoritative lineage key; do not infer ownership from an
            # optional normalized Bundle.
            if (
                not candidate_experiment_id
                and plan_id
                and content.get("plan_artifact_id") == plan_id
            ):
                return artifact.id
        return fallback_id

    def _build_tool_registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register("read_run", self.repository.get_run)
        registry.register("read_artifact", self._read_artifact)
        registry.register("literature_search", self.literature_provider.search)
        registry.register("query_wiki", self.knowledge_service.wiki.query)
        registry.register("search_local_literature", self.knowledge_service.library.search)
        registry.register("propose_wiki_changes", self._identity_tool)
        registry.register("build_experiment_bundle", self.experiment_agent.generate_bundle)
        registry.register("read_experiment_result", self._read_experiment_result)
        registry.register("audit_evidence", self._identity_tool)
        registry.register("audit_result", self._identity_tool)
        repair_dataset_cache = getattr(
            self.experiment_provider, "quarantine_failed_dataset_download", self._identity_tool
        )
        registry.register("repair_dataset_cache", repair_dataset_cache)
        registry.register("retry_experiment", self.experiment_provider.run)
        registry.register("render_report", self.writer_agent.build_report)
        provider_name = type(self.experiment_provider).__name__
        execution_tool = "ssh_run" if provider_name == "RemoteGpuExperimentProvider" else "local_process_run"
        registry.register(execution_tool, self.experiment_provider.run)
        return registry

    def _read_artifact(self, run_id: str, artifact_id: str):
        run = self.repository.get_run(run_id)
        return next(
            artifact for artifact in run.artifacts if artifact.id == artifact_id
        )

    def _read_experiment_result(self, run_id: str):
        latest = self._latest_by_type(self.repository.get_run(run_id).artifacts)
        return latest.get("experiment_result")

    @staticmethod
    def _identity_tool(value):
        return value

    @staticmethod
    def _normalize_nonempty_hypothesis(raw: dict) -> dict:
        hypothesis = normalize_hypothesis_content(raw)
        if not hypothesis["candidates"]:
            raise ValueError("HYPOTHESIS_CANDIDATES_EMPTY")
        issues = hypothesis_candidate_issues(hypothesis)
        if issues:
            hypothesis["_validation_issues"] = issues
        return hypothesis

    @staticmethod
    def _focused_evidence_for_candidates(
        evidence: list[dict], candidates: list[dict], limit: int = 12
    ) -> list[dict]:
        referenced_titles: set[str] = set()
        referenced_urls: set[str] = set()
        for candidate in candidates:
            for basis in candidate.get("evidence_basis") or []:
                if not isinstance(basis, dict):
                    continue
                title = str(basis.get("source_title") or "").strip().casefold()
                url = str(basis.get("source_url") or "").strip().casefold()
                if title:
                    referenced_titles.add(title)
                if url:
                    referenced_urls.add(url)

        verified = [
            item
            for item in evidence
            if isinstance(item, dict) and item.get("verified") is True
        ]
        scoped = [
            item
            for item in verified
            if str(item.get("title") or "").strip().casefold() in referenced_titles
            or str(item.get("url") or "").strip().casefold() in referenced_urls
        ]
        remaining = [item for item in verified if item not in scoped]
        remaining.sort(
            key=lambda item: (
                -float(item.get("relevance") or 0.0),
                -float(item.get("reliability") or 0.0),
                str(item.get("title") or ""),
            )
        )
        focused = scoped + remaining
        return focused[: max(len(scoped), max(1, limit))]

    @staticmethod
    def _focused_evidence_for_candidate(
        evidence: list[dict], candidate: dict, limit: int = 12
    ) -> list[dict]:
        """Choose a bounded provenance-first scope for one critic call."""
        return WorkflowEngine._focused_evidence_for_candidates(
            evidence, [candidate], limit
        )[:limit]

    @staticmethod
    def _stable_hash(value) -> str:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_idea_review_checkpoint(
        self, run_id: str, candidate_ids: list[str], evidence_set_hash: str,
        candidates: list[dict],
    ) -> dict | None:
        run = self.repository.get_run(run_id)
        for artifact in reversed(run.artifacts):
            if artifact.type != "idea_review_checkpoint":
                continue
            content = artifact.content
            if (
                content.get("prompt_version") != EVIDENCE_REASONING_PROMPT_VERSION
                or content.get("evidence_set_hash") != evidence_set_hash
                or content.get("candidate_ids") != candidate_ids
            ):
                continue
            try:
                return normalize_idea_review(content.get("review") or {}, candidates)
            except ValueError:
                continue
        return None

    def _load_candidate_checkpoint(
        self, run_id: str, candidate_index: int, candidate_id: str,
        evidence_set_hash: str, hypothesis: dict, evaluation: dict,
    ) -> dict | None:
        run = self.repository.get_run(run_id)
        for artifact in reversed(run.artifacts):
            if artifact.type != "candidate_reasoning_checkpoint":
                continue
            content = artifact.content
            if (
                content.get("candidate_index") != candidate_index
                or content.get("candidate_id") != candidate_id
                or content.get("prompt_version") != EVIDENCE_REASONING_PROMPT_VERSION
                or content.get("evidence_set_hash") != evidence_set_hash
            ):
                continue
            assessment = content.get("assessment")
            if not isinstance(assessment, dict):
                continue
            try:
                normalized = self._candidate_assessment(
                    candidate_index,
                    hypothesis,
                    evaluation,
                    assessment.get("critic_reasoning") or {},
                    assessment.get("evidence_audit") or {},
                    enforce_evidence_gate=not self.llm_provider.fallback,
                )
            except (KeyError, TypeError, ValueError):
                continue
            normalized["recommendation"] = str(evaluation.get("decision") or "")
            if normalized.get("status") != assessment.get("status"):
                continue
            return assessment
        return None

    @staticmethod
    def _targeted_retrieval_summary(retrieval: dict) -> dict:
        return {
            "attempted": bool(retrieval.get("attempted")),
            "queries": list(retrieval.get("queries") or []),
            "original_evidence_count": int(
                retrieval.get("original_evidence_count") or 0
            ),
            "retrieved_count": int(retrieval.get("retrieved_count") or 0),
            "new_evidence_count": int(retrieval.get("new_evidence_count") or 0),
            "round_limit": MAX_TARGETED_RETRIEVAL_ROUNDS,
        }

    def _recover_candidate_evidence(
        self,
        *,
        run_id: str,
        candidates: list[dict],
        assessments: list[dict],
        evidence_cards: list[dict],
        instructions: str,
    ) -> dict:
        """Perform bounded candidate-specific retrieval before declaring failure.

        A failed first evidence pass is a recovery signal, not a terminal decision.
        We only invoke the loop when no candidate is currently selectable, preserving
        useful human review results and avoiding unnecessary external searches.
        """
        cards = [deepcopy(item) for item in evidence_cards]
        history: list[dict] = []
        initial_count = len(cards)
        if any(self._assessment_selectable(item) for item in assessments):
            return {
                "assessments": assessments,
                "evidence_cards": cards,
                "summary": {
                    "attempted": False, "queries": [], "original_evidence_count": initial_count,
                    "retrieved_count": 0, "new_evidence_count": 0,
                    "round_limit": MAX_TARGETED_RETRIEVAL_ROUNDS, "history": history,
                },
            }

        all_queries: list[str] = []
        retrieved_count = 0
        for round_number in range(1, MAX_TARGETED_RETRIEVAL_ROUNDS + 1):
            pending = [
                item for item in assessments
                if item.get("status") in {"evidence_insufficient", "revised"}
                and not self._assessment_selectable(item)
            ]
            if not pending:
                break
            query_by_index: dict[int, list[str]] = {}
            for assessment in pending:
                index = int(assessment["candidate_index"])
                evidence_map = candidate_evidence_map(
                    candidates[index], extract_claim_evidence(cards), analyze_research_gaps(extract_claim_evidence(cards))
                )
                queries = candidate_targeted_queries(
                    candidates[index], evidence_map, assessment.get("critic_reasoning") or {}
                )
                if queries:
                    query_by_index[index] = queries
                    all_queries.extend(queries)
            queries = list(dict.fromkeys(all_queries))[-12:]
            if not queries:
                break
            collected = self.knowledge_service.collect_queries(
                run_id,
                queries,
                knowledge_base_id=self.repository.get_run(run_id).knowledge_base_id,
            )
            additions = [card.model_dump() for card in collected.references]
            prior_keys = {
                self._stable_hash({
                    "title": card.get("title"), "url": card.get("url"),
                    "identifiers": card.get("identifiers") or {},
                })
                for card in cards
            }
            new_cards = [
                card for card in additions
                if self._stable_hash({
                    "title": card.get("title"), "url": card.get("url"),
                    "identifiers": card.get("identifiers") or {},
                }) not in prior_keys
            ]
            cards.extend(new_cards)
            retrieved_count += len(additions)
            if (
                collected.wiki_changes.papers
                or collected.wiki_changes.gaps
                or collected.wiki_changes.edges
            ):
                self.supervisor_agent.commit_wiki_changes(
                    collected.wiki_changes, self.knowledge_service.wiki
                )
            self.repository.add_artifact(
                run_id,
                "targeted_retrieval",
                f"Targeted Retrieval Round {round_number}",
                {
                    "round": round_number,
                    "queries": query_by_index,
                    "new_papers": new_cards,
                    "warnings": list(collected.warnings),
                    "sources": deepcopy(collected.sources),
                },
                "evidence_reasoning",
                self.research_agent.name,
            )

            evidence_audit = build_evidence_audit(cards, candidates)
            registry_ids = [entry["evidence_id"] for entry in evidence_audit["registry"]]
            for assessment in pending:
                index = int(assessment["candidate_index"])
                audit = deepcopy(evidence_audit["candidate_audits"][index])
                audit["registry"] = deepcopy(evidence_audit["registry"])
                audit["registry_evidence_ids"] = registry_ids
                candidate_scope = self._focused_evidence_for_candidate(
                    cards, candidates[index]
                )
                critic = self.critic_agent.evidence_reasoning(
                    candidates[index],
                    [literature_card(card) for card in candidate_scope],
                    evidence_audit=audit,
                    evaluation=assessment.get("evaluation") or {},
                    instructions=(
                        f"{instructions}\n\nThis is targeted retrieval round {round_number}. "
                        "Decide GO, REVISE, TARGETED_RETRIEVAL, or REJECT. Do not require a paper "
                        "to have already proved the novel final hypothesis; require evidence for motivation, "
                        "component mechanism, and research gap."
                    ),
                )
                refreshed = self._candidate_assessment(
                    index,
                    candidates[index],
                    assessment.get("evaluation") or {},
                    critic,
                    audit,
                    enforce_evidence_gate=not self.llm_provider.fallback,
                )
                refreshed["critic_decision"] = self._critic_decision(refreshed)
                decision = refreshed["critic_decision"]
                if decision == "REJECT":
                    refreshed["status"] = "rejected"
                    refreshed["recommendation"] = "REJECT"
                elif refreshed["status"] in {"verified", "revised"}:
                    refreshed["recommendation"] = "GO" if decision == "GO" else "REVISE" if decision == "REVISE" else "GO"
                else:
                    refreshed["recommendation"] = "TARGETED_RETRIEVAL"
                assessments[index] = refreshed
            history.append({
                "round": round_number,
                "queries": query_by_index,
                "new_papers": len(new_cards),
                "new_evidence": len(extract_claim_evidence(new_cards)),
                "candidate_statuses": [item.get("status") for item in assessments],
            })
            if any(self._assessment_selectable(item) for item in assessments):
                break

        for assessment in assessments:
            if assessment.get("status") == "evidence_insufficient" and not self._assessment_selectable(assessment):
                assessment["recommendation"] = "REJECTED_EVIDENCE_UNAVAILABLE"
        return {
            "assessments": assessments,
            "evidence_cards": cards,
            "summary": {
                "attempted": bool(history), "queries": list(dict.fromkeys(all_queries)),
                "original_evidence_count": initial_count, "retrieved_count": retrieved_count,
                "new_evidence_count": len(extract_claim_evidence(cards)) - len(extract_claim_evidence(evidence_cards)),
                "round_limit": MAX_TARGETED_RETRIEVAL_ROUNDS, "history": history,
            },
        }

    @staticmethod
    def _critic_decision(assessment: dict) -> str:
        """Normalize legacy and current Critic outputs at one production boundary."""
        critic = assessment.get("critic_reasoning") or {}
        raw_decision = str(critic.get("decision") or "").strip().upper()
        if raw_decision in {"GO", "REVISE", "TARGETED_RETRIEVAL", "REJECT"}:
            return raw_decision
        if raw_decision in {"EVIDENCE_INSUFFICIENT", "INSUFFICIENT", "NEEDS_MORE_EVIDENCE", "RETRIEVE_MORE"}:
            return "TARGETED_RETRIEVAL"
        if assessment.get("status") in {"verified", "revised"}:
            return "GO" if assessment.get("recommendation") == "GO" else "REVISE"
        if assessment.get("status") == "rejected":
            return "REJECT"
        return "TARGETED_RETRIEVAL"

    @staticmethod
    def _candidate_assessment(
        candidate_index: int,
        original_hypothesis: dict,
        evaluation: dict,
        critic_reasoning: dict,
        evidence_audit: dict | None = None,
        enforce_evidence_gate: bool = True,
    ) -> dict:
        if not isinstance(critic_reasoning, dict):
            raise ValueError("EVIDENCE_REASONING_OUTPUT_INVALID")

        original = normalize_candidate(original_hypothesis)
        revised = deepcopy(original)
        was_revised = False
        for field in ("revised_hypothesis", "active_hypothesis", "selected"):
            candidate = critic_reasoning.get(field)
            if (
                isinstance(candidate, dict)
                and isinstance(candidate.get("claim"), str)
                and candidate["claim"].strip()
                and candidate["claim"] != original.get("claim")
            ):
                revised = normalize_candidate(candidate)
                was_revised = True
                break

        explicit_status = critic_reasoning.get("status")
        explicit_decision = str(critic_reasoning.get("decision") or "").strip().upper()
        if was_revised:
            status = "revised"
        elif explicit_decision == "REJECT":
            status = "rejected"
        elif explicit_decision in {
            "TARGETED_RETRIEVAL",
            "EVIDENCE_INSUFFICIENT",
            "INSUFFICIENT",
            "NEEDS_MORE_EVIDENCE",
            "RETRIEVE_MORE",
        }:
            status = "evidence_insufficient"
        elif explicit_status in {"verified", "evidence_insufficient", "rejected"}:
            status = explicit_status
        elif evaluation["decision"] == "GO":
            status = "verified"
        elif evaluation["decision"] == "STOP":
            status = "rejected"
        else:
            status = "evidence_insufficient"
        evidence_gate = str((evidence_audit or {}).get("gate") or "UNKNOWN")
        claim_evidence_issues = []
        if enforce_evidence_gate and evidence_gate in {"PASS", "FAIL"}:
            claim_evidence_map = critic_reasoning.get("claim_evidence_map")
            allowed_ids = set(
                (evidence_audit or {}).get("registry_evidence_ids")
                or (evidence_audit or {}).get("matched_evidence_ids")
                or []
            )
            if not isinstance(claim_evidence_map, list) or not claim_evidence_map:
                claim_evidence_issues.append("CLAIM_EVIDENCE_MAP_MISSING")
            else:
                mapped_items = [
                    item for item in claim_evidence_map if isinstance(item, dict)
                ]
                used_ids = {
                    str(item.get("evidence_id") or "") for item in mapped_items
                }
                invalid_ids = sorted(
                    evidence_id
                    for evidence_id in used_ids
                    if not evidence_id or evidence_id not in allowed_ids
                )
                if invalid_ids:
                    claim_evidence_issues.append(
                        f"CLAIM_EVIDENCE_ID_INVALID:{','.join(invalid_ids)}"
                    )
                substantive_support = [
                    item
                    for item in mapped_items
                    if item.get("stance") == "support"
                    and item.get("relation") in {"DIRECT", "INDIRECT"}
                    and str(item.get("evidence_id") or "") in allowed_ids
                ]
                if not substantive_support:
                    claim_evidence_issues.append(
                        "DIRECT_OR_INDIRECT_SUPPORT_MISSING"
                    )
            if claim_evidence_issues:
                status = "evidence_insufficient"

        revision_reason = critic_reasoning.get("revision_reason")
        if not isinstance(revision_reason, str):
            revision_reason = ""
        if was_revised and not revision_reason:
            revision_reason = "Critic evidence reasoning revised the candidate claim."

        scores = evaluation.get("scores") if isinstance(evaluation, dict) else None
        assessment = {
            "candidate_index": candidate_index,
            "status": status,
            "original_hypothesis": original,
            "revised_hypothesis": revised,
            "was_revised": was_revised,
            "revision_reason": revision_reason,
            "evaluation": deepcopy(evaluation),
            "critic_reasoning": deepcopy(critic_reasoning),
            "evidence_audit": deepcopy(evidence_audit or {}),
            "claim_evidence_issues": claim_evidence_issues,
        }
        if isinstance(scores, dict) and set(scores) == set(WEIGHTS):
            assessment["composite_score"] = composite_score(scores)
        return assessment

    @staticmethod
    def _require_evidence_reasoned_hypothesis_selection(latest: dict) -> dict:
        artifact = latest.get("hypothesis_selection")
        if artifact is None or artifact.source_step != "evidence_reasoning":
            raise ValueError("HYPOTHESIS_SELECTION_REQUIRED")
        selection = artifact.content
        selected = selection.get("selected") if isinstance(selection, dict) else None
        if (
            selection.get("selection_mode")
            not in {
                "user_selected_after_evidence_reasoning",
                "evidence_reasoned_weighted_review",
                "automatic",
                "automatic_fallback",
            }
            or not isinstance(selected, list)
            or len(selected) != 1
        ):
            raise ValueError("HYPOTHESIS_SELECTION_REQUIRED")
        return selection

    @classmethod
    def _feedback_hypothesis(cls, latest: dict) -> dict:
        reasoning = latest.get("reasoning")
        active = reasoning.content.get("active_hypothesis") if reasoning else None
        if isinstance(active, dict) and active:
            hypothesis = deepcopy(active)
        else:
            selection = cls._require_evidence_reasoned_hypothesis_selection(latest)
            hypothesis = deepcopy(selection["selected"][0])
        plan = latest.get("plan")
        objective = (
            str(plan.content.get("objective") or "").strip()
            if (
                plan is not None
                and isinstance(plan.content, dict)
                # A Round 7 plan may be parented by its immutable candidate.
                # Only feedback-refined plans may supersede the active claim.
                and bool(plan.content.get("iteration_contract"))
            )
            else ""
        )
        if objective:
            hypothesis["original_claim"] = hypothesis.get("claim") or ""
            hypothesis["claim"] = objective
            hypothesis["status"] = "active"
        return hypothesis

    @staticmethod
    def _pivot_lineage_payload(
        *,
        working_hypothesis: dict | None,
        working_hypothesis_id: str,
        parent_claim: str,
        derived_from: list[str],
        base_plan_artifact_id: str,
        base_experiment_task_id: str,
        base_bundle_id: str,
    ) -> dict:
        """Build the immutable hand-off from a contradicted claim to a PIVOT."""
        working = deepcopy(working_hypothesis or {})
        claim = str(working.get("claim") or "").strip()
        if not claim or claim == parent_claim.strip():
            return {}
        return {
            "kind": "PIVOT",
            "working_hypothesis_id": working_hypothesis_id,
            "working_hypothesis": working,
            "parent_hypothesis_id": str(working.get("parent_hypothesis_id") or ""),
            "parent_claim": parent_claim,
            "claim": claim,
            "derived_from": list(dict.fromkeys(str(item) for item in derived_from if item)),
            "base_plan_artifact_id": base_plan_artifact_id,
            "base_experiment_task_id": base_experiment_task_id,
            "base_bundle_id": base_bundle_id,
            "change_set": {
                "allowed_files": ["train.py"],
                "required": ["Implement only the intervention required by the PIVOT claim."],
                "preserve": [
                    "verified dataset loader and split",
                    "baseline architecture and fixed controls",
                    "metric calculation and result serialization",
                    "runtime scaffold and backend-owned seeds",
                ],
            },
        }

    @staticmethod
    def _apply_pivot_claim_contract(plan: dict, lineage: dict) -> dict:
        """Keep all claim-bearing Plan fields on the new branch in lockstep."""
        value = deepcopy(plan)
        claim = str(lineage.get("claim") or "").strip()
        if not claim:
            return value
        value["objective"] = claim
        value["hypotheses"] = [claim]
        value["primary_claim"] = claim
        value["original_question_link"] = claim
        revised = dict(value.get("revised_hypothesis") or {})
        value["revised_hypothesis"] = {
            **revised,
            "claim": claim,
            "preserves_user_claim": True,
        }
        for row in value.get("traceability") or []:
            if isinstance(row, dict) and str(row.get("claim") or "") == str(lineage.get("parent_claim") or ""):
                row["claim"] = claim
        return value

    @staticmethod
    def _single_claim_alignment_blocker(
        open_blockers: list[dict], current_plan: dict, revised_plan: dict
    ) -> str:
        """Return the one safe stale-hypothesis repair, otherwise do nothing."""
        blockers = [
            item
            for item in open_blockers
            if "hypotheses" in set(item.get("contract_fields") or [])
        ]
        primary = str(current_plan.get("primary_claim") or "").strip()
        old_hypothesis = str(((current_plan.get("hypotheses") or [""])[0]) or "").strip()
        candidate_hypothesis = str(
            ((revised_plan.get("hypotheses") or [""])[0]) or ""
        ).strip()
        if (
            len(blockers) == 1
            and primary
            and old_hypothesis
            and old_hypothesis != primary
            and candidate_hypothesis != primary
            and str(revised_plan.get("primary_claim") or "").strip() == primary
            and str(revised_plan.get("objective") or "").strip() == str(current_plan.get("objective") or "").strip()
        ):
            return str(blockers[0].get("issue_id") or "")
        return ""

    @staticmethod
    def _pending_pivot_lineage(latest: dict) -> dict:
        proposal = latest.get("plan_refinement_proposal")
        working_artifact = latest.get("working_hypothesis")
        if proposal is None or working_artifact is None:
            return {}
        contract = (proposal.content or {}).get("normalized_plan", {}).get("iteration_contract") or {}
        lineage = contract.get("hypothesis_lineage")
        if not isinstance(lineage, dict):
            return {}
        if str(lineage.get("working_hypothesis_id") or "") != working_artifact.id:
            return {}
        return deepcopy(lineage)

    @staticmethod
    def _pivot_implementation_base(artifacts, plan: dict) -> dict | None:
        contract = plan.get("iteration_contract") or {}
        lineage = contract.get("hypothesis_lineage") if isinstance(contract, dict) else None
        if not isinstance(lineage, dict) or lineage.get("kind") != "PIVOT":
            return None
        bundle_id = str(lineage.get("base_bundle_id") or "")
        bundle_artifact = next(
            (item for item in artifacts if item.id == bundle_id and item.type == "experiment_bundle"),
            None,
        )
        if bundle_artifact is None:
            return None
        content = bundle_artifact.content or {}
        return {
            "bundle_artifact_id": bundle_artifact.id,
            "files": deepcopy(content.get("files") or []),
            "requirements": deepcopy(content.get("requirements") or []),
            "change_set": deepcopy(lineage.get("change_set") or {}),
        }

    @staticmethod
    def _result_already_reviewed(artifacts, result_artifact) -> bool:
        if result_artifact is None:
            return False
        revision = next(
            (
                artifact
                for artifact in reversed(artifacts)
                if artifact.type == "revision"
                and artifact.parent_artifact_id == result_artifact.id
            ),
            None,
        )
        if revision is None:
            return False
        if not feedback_requires_follow_up(revision.content):
            return True
        return any(
            artifact.type in {"plan", "plan_refinement_proposal"}
            and artifact.parent_artifact_id == revision.id
            for artifact in artifacts
        )

    @staticmethod
    def _reviewed_result_count(artifacts) -> int:
        revisions = [artifact for artifact in artifacts if artifact.type == "revision"]
        reviewed_ids = {
            artifact.parent_artifact_id
            for artifact in revisions
            if artifact.parent_artifact_id is not None
        }
        legacy_rounds = sum(
            artifact.parent_artifact_id is None for artifact in revisions
        )
        return len(reviewed_ids) + legacy_rounds

    @staticmethod
    def _build_iteration_contract(
        iteration: int,
        previous_plan: dict,
        revised_plan: dict,
        feedback: dict,
    ) -> dict:
        tracked_fields = (
            "method", "dataset", "comparisons", "evaluations", "procedure",
            "parameters", "seeds", "success_criteria", "failure_criteria",
            "stop_conditions", "optional_ablations", "primary_experiment",
            "baseline_and_controls", "split_contract", "staged_gates",
            "progressive_experiment",
        )
        changed_fields = [
            field for field in tracked_fields
            if previous_plan.get(field) != revised_plan.get(field)
        ]
        required_changes = []
        for value in (
            feedback.get("required_revision"),
            feedback.get("next_action"),
            feedback.get("feedback"),
        ):
            if isinstance(value, str) and value.strip() and value.strip() not in required_changes:
                required_changes.append(value.strip())
        for item in feedback.get("revisions") or []:
            value = str(item).strip()
            if value and value not in required_changes:
                required_changes.append(value)
        metrics = [
            str(item.get("metric") or "").strip()
            for item in revised_plan.get("evaluations") or []
            if isinstance(item, dict) and str(item.get("metric") or "").strip()
        ]
        comparisons = []
        for item in revised_plan.get("comparisons") or []:
            if not isinstance(item, dict):
                continue
            baseline = str(item.get("baseline") or "").strip()
            variant = str(item.get("variant") or "").strip()
            label = " vs ".join(value for value in (baseline, variant) if value)
            if label:
                comparisons.append(label)
        return {
            "feedback_iteration": iteration,
            "required_changes": required_changes,
            "required_metrics": list(dict.fromkeys(metrics)),
            "required_comparisons": list(dict.fromkeys(comparisons)),
            "changed_fields": changed_fields,
            "contract_status": "changed" if changed_fields else "needs_attention",
        }

    @staticmethod
    def _synchronize_iteration_contract(plan: dict) -> dict:
        """Make feedback metric validation follow the final plan evaluations."""
        contract = plan.get("iteration_contract")
        if not isinstance(contract, dict):
            return plan
        metrics = [
            str(item.get("metric") or "").strip()
            for item in plan.get("evaluations") or []
            if isinstance(item, dict) and str(item.get("metric") or "").strip()
        ]
        if metrics:
            plan["iteration_contract"] = {
                **contract,
                "required_metrics": list(dict.fromkeys(metrics)),
            }
        return plan

    @staticmethod
    def _with_frozen_training_epochs(plan: dict, epochs: int) -> dict:
        """Keep the first accepted model budget stable across follow-up plans."""
        frozen = deepcopy(plan)
        parameters = dict(frozen.get("parameters") or {})
        parameters["epochs"] = int(epochs)
        parameters.pop("max_epochs", None)
        parameters.pop("epochs_limit", None)
        frozen["parameters"] = parameters
        return frozen

    def _frozen_model_training_epochs(self, run_id: str) -> int | None:
        budget = self._frozen_model_training_budget(run_id)
        return int(budget["epochs"]) if budget and budget.get("mode") == "epochs" else None

    def _frozen_model_training_budget(self, run_id: str) -> dict | None:
        contracts = [
            artifact
            for artifact in self.repository.get_run(run_id).artifacts
            if artifact.type == "model_training_budget_contract"
        ]
        if len(contracts) > 1:
            raise ValueError("MODEL_TRAINING_BUDGET_CONTRACT_DUPLICATE")
        if not contracts:
            return None
        budget = dict(contracts[0].content or {})
        if budget.get("mode") == "single_fit":
            if int(budget.get("fit_count") or 0) != 1 or int(budget.get("max_iter") or 0) < 1:
                raise ValueError("MODEL_TRAINING_BUDGET_CONTRACT_INVALID")
            return {
                "mode": "single_fit",
                "fit_count": 1,
                "max_iter": int(budget["max_iter"]),
                "runtime_passes": 1,
            }
        raw = budget.get("epochs")
        epochs = canonical_training_epochs({"parameters": {"epochs": raw}})
        if epochs is None:
            raise ValueError("MODEL_TRAINING_BUDGET_CONTRACT_INVALID")
        return {"mode": "epochs", "epochs": epochs}

    def _freeze_model_training_budget(
        self,
        run_id: str,
        plan: dict,
        *,
        plan_artifact_id: str,
    ):
        """Persist the accepted model choice once; later plans may only inherit it."""
        chosen = execution_training_budget(plan)
        if chosen is None:
            raise ValueError("MODEL_PLANNED_TRAINING_EPOCHS_REQUIRED")
        existing = self._frozen_model_training_budget(run_id)
        if existing is not None:
            if chosen != existing:
                raise ValueError("MODEL_TRAINING_BUDGET_CHANGED_DURING_ITERATION")
            return next(
                artifact
                for artifact in self.repository.get_run(run_id).artifacts
                if artifact.type == "model_training_budget_contract"
            )
        return self.repository.add_artifact(
            run_id,
            "model_training_budget_contract",
            "Model-Planned Training Budget",
            {
                "schema_version": 1,
                **chosen,
                "source": "accepted_model_plan",
                "source_plan_artifact_id": plan_artifact_id,
                "frozen_for_follow_up_experiments": True,
            },
            "research_plan",
            self.planning_agent.name,
            parent_artifact_id=plan_artifact_id,
        )

    @staticmethod
    def _valid_execution_seeds(value: object) -> list[int]:
        if not isinstance(value, (list, tuple)):
            return []
        seeds: list[int] = []
        for item in value:
            if isinstance(item, bool):
                return []
            try:
                seed = int(item)
            except (TypeError, ValueError):
                return []
            if seed < 1 or seed >= 2**31 or seed in seeds:
                return []
            seeds.append(seed)
        return seeds[:5]

    @classmethod
    def _requested_execution_seed_count(
        cls,
        constraints: dict | None,
        plan: dict | None,
    ) -> int:
        policy = (
            (constraints or {}).get("seed_policy")
            if isinstance((constraints or {}).get("seed_policy"), dict)
            else {}
        )
        raw_count = policy.get("count")
        if raw_count in (None, ""):
            description = str(policy.get("description") or "")
            digits = "".join(
                char if char.isdigit() else " " for char in description
            ).split()
            raw_count = digits[0] if digits else None
        if raw_count in (None, ""):
            procedure = (plan or {}).get("procedure") or {}
            raw_count = procedure.get("repetitions") or 3
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            count = 3
        return max(1, min(count, 5))

    def _ensure_backend_execution_seed_contract(
        self,
        run_id: str,
        *,
        constraints: dict | None = None,
        plan: dict | None = None,
    ):
        """Create one durable backend-owned seed set before plan review.

        Models may explain how seeds index paired comparisons, but concrete seed
        values are generated once by the backend, persisted append-only, and
        reused by every plan revision, experiment task, and feedback iteration.
        """
        run = self.repository.get_run(run_id)
        existing = next(
            (
                artifact
                for artifact in run.artifacts
                if artifact.type == "execution_seed_contract"
            ),
            None,
        )
        if existing is not None:
            seeds = self._valid_execution_seeds(
                (existing.content or {}).get("seeds")
            )
            if not seeds or seeds != list((existing.content or {}).get("seeds") or []):
                raise ValueError("EXECUTION_SEED_CONTRACT_INVALID")
            return existing

        policy = (
            (constraints or {}).get("seed_policy")
            if isinstance((constraints or {}).get("seed_policy"), dict)
            else {}
        )
        requested = self._valid_execution_seeds(
            policy.get("seeds") or policy.get("values")
        )
        accepted_plan_seeds = self._valid_execution_seeds(
            (plan or {}).get("seeds")
        )
        if requested:
            seeds = requested
            allocation = "user_preregistered"
        elif accepted_plan_seeds:
            # Backward-compatible migration for Runs accepted before the durable
            # seed contract existed.  Preserve their already reviewed values.
            seeds = accepted_plan_seeds
            allocation = "accepted_plan_migration"
        else:
            count = self._requested_execution_seed_count(constraints, plan)
            seeds = secrets.SystemRandom().sample(range(1, 2**31 - 1), count)
            allocation = "backend_preregistered"
        payload = {
            "schema_version": 1,
            "seeds": list(seeds),
            "count": len(seeds),
            "allocation": allocation,
            "frozen": True,
        }
        return self.repository.add_artifact(
            run_id,
            "execution_seed_contract",
            "Frozen Backend Execution Seeds",
            payload,
            "research_plan",
            "Backend Seed Allocator",
            parent_artifact_id=(
                run.research_constraints_artifact_id or None
            ),
        )

    @staticmethod
    def _output_language(run) -> str:
        configured = str(getattr(run, "language", "") or "").strip()
        if configured in {"zh-CN", "en"}:
            return configured
        text = " ".join(
            str(value or "")
            for value in (run.title, run.domain, run.problem_input, run.constraints)
        )
        return "zh-CN" if any("\u4e00" <= char <= "\u9fff" for char in text) else "en"

    @staticmethod
    def _with_output_language(instructions: str, output_language: str) -> str:
        if output_language != "zh-CN":
            return instructions
        return (
            f"{instructions}\n\n## 强制输出语言\n"
            "本次研究使用中文。所有面向用户的分析、原因、限制、修改建议、"
            "候选方向、选择理由和下一步动作必须使用简体中文。机器枚举、"
            "JSON 字段名、错误码、原始指标键和英文学术检索式保持英文。"
        )

    @staticmethod
    def _normalize_iteration_analysis(value, result: dict) -> dict:
        analysis = value if isinstance(value, dict) else {}
        source_analysis = (
            result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
        )

        def strings(field: str, fallback=()):
            raw = analysis.get(field)
            if not isinstance(raw, list):
                raw = fallback
            return [str(item).strip() for item in raw if str(item).strip()]

        return {
            "measured_facts": strings(
                "measured_facts", source_analysis.get("observations") or []
            ),
            "failed_criteria": strings("failed_criteria"),
            "improved_metrics": strings("improved_metrics"),
            "degraded_metrics": strings("degraded_metrics"),
            "uncertainties": strings(
                "uncertainties", source_analysis.get("limitations") or []
            ),
            "methodological_issues": strings("methodological_issues"),
            "causal_hypotheses": strings("causal_hypotheses"),
            "knowledge_gaps": strings("knowledge_gaps"),
        }

    @staticmethod
    def _normalize_iteration_queries(value) -> list[dict]:
        if not isinstance(value, list):
            return []
        queries = []
        for item in value:
            if not isinstance(item, dict):
                continue
            query = str(item.get("query") or "").strip()
            if not query:
                continue
            queries.append(
                {
                    "question": str(item.get("question") or "").strip(),
                    "query": query,
                    "trigger_metric": str(item.get("trigger_metric") or "").strip(),
                    "observed_value": item.get("observed_value", ""),
                    "reason": str(item.get("reason") or "").strip(),
                }
            )
        return queries[:3]

    def _collect_iteration_evidence(
        self,
        run_id: str,
        query_specs: list[dict],
    ) -> dict:
        queries = [item["query"] for item in query_specs]
        if not queries:
            return {
                "status": "EVIDENCE_INSUFFICIENT",
                "query_specs": [],
                "references": [],
                "warnings": ["未生成需要外部资料回答的科学知识缺口。"],
                "sources": {"calls": [], "wiki": 0, "local": 0, "external": 0},
            }
        collected = self.knowledge_service.collect_queries(
            run_id,
            queries,
            knowledge_base_id=self.repository.get_run(run_id).knowledge_base_id,
        )
        references = []
        for index, card in enumerate(collected.references[:12], start=1):
            references.append(
                {
                    **card.model_dump(),
                    "evidence_id": f"iteration-evidence-{index}",
                }
            )
        if (
            collected.wiki_changes.papers
            or collected.wiki_changes.gaps
            or collected.wiki_changes.edges
        ):
            self.supervisor_agent.commit_wiki_changes(
                collected.wiki_changes, self.knowledge_service.wiki
            )
        return {
            "status": "SUFFICIENT" if references else "EVIDENCE_INSUFFICIENT",
            "query_specs": deepcopy(query_specs),
            "references": references,
            "warnings": list(collected.warnings),
            "sources": deepcopy(collected.sources),
        }

    @staticmethod
    def _normalize_iteration_direction(value) -> dict:
        direction = value if isinstance(value, dict) else {}
        assessments = direction.get("evidence_assessment")
        candidates = direction.get("optimization_candidates")
        selected = direction.get("selected_direction")
        return {
            "decision": normalize_feedback_decision(direction.get("decision")),
            "evidence_sufficiency": (
                direction.get("evidence_sufficiency")
                if direction.get("evidence_sufficiency")
                in {"SUFFICIENT", "EVIDENCE_INSUFFICIENT"}
                else "EVIDENCE_INSUFFICIENT"
            ),
            "evidence_assessment": [
                deepcopy(item)
                for item in (assessments if isinstance(assessments, list) else [])
                if isinstance(item, dict)
            ],
            "optimization_candidates": [
                deepcopy(item)
                for item in (candidates if isinstance(candidates, list) else [])[:4]
                if isinstance(item, dict)
            ],
            "selected_direction": deepcopy(selected) if isinstance(selected, dict) else {},
            "selection_reason": str(direction.get("selection_reason") or "").strip(),
            "next_action": str(direction.get("next_action") or "").strip(),
            "proposed_hypothesis": deepcopy(direction.get("proposed_hypothesis"))
            if isinstance(direction.get("proposed_hypothesis"), dict) else {},
        }

    _serial_direction_issues = staticmethod(direction_issues)

    @staticmethod
    def _iteration_direction_issues(
        direction: dict, output_language: str
    ) -> list[str]:
        issues = []
        decision = normalize_feedback_decision(direction.get("decision"))
        candidates = direction.get("optimization_candidates") or []
        if direction.get("evidence_sufficiency") == "SUFFICIENT" and not (
            2 <= len(candidates) <= 4
        ):
            issues.append("需要返回 2 至 4 个可比较的候选优化方向")
        selected = direction.get("selected_direction") or {}
        if decision in {"REVISE", "PIVOT"} and not selected:
            issues.append("继续实验必须提供一个可执行的选择方向")
        elif candidates and not selected and decision != "REPORT":
            issues.append("必须从候选方向中明确选择一个方向")
        if output_language == "zh-CN":
            narrative_values = [
                direction.get("selection_reason"),
                direction.get("next_action"),
                selected.get("name"),
                selected.get("success_rule"),
                selected.get("failure_rule"),
                selected.get("stop_rule"),
            ]
            narrative_values.extend(
                item.get("name") for item in candidates if isinstance(item, dict)
            )
            for value in narrative_values:
                text = str(value or "").strip()
                if text and not any("\u4e00" <= char <= "\u9fff" for char in text):
                    issues.append("中文研究的候选方向、选择理由和动作必须使用简体中文")
                    break
        return issues

    def _require_report_readiness(self, artifacts, latest: dict) -> None:
        evidence = latest.get("evidence")
        references = evidence.content.get("references") if evidence else None
        if (
            not isinstance(references, list)
            or not references
            or not any(reference.get("verified") for reference in references)
        ):
            raise ValueError("REPORT_EXPORT_NOT_READY:verified_evidence")
        result = latest.get("experiment_result")
        if result is None:
            raise ValueError("REPORT_EXPORT_NOT_READY:experiment_result")
        task = latest.get("experiment_task")
        if task is not None and result.content.get("experiment_id") != task.content.get("experiment_id"):
            raise ValueError("REPORT_EXPORT_NOT_READY:latest_experiment_result")
        bundle_ids = (
            experiment_bundle_ids(artifacts, task.id) if task is not None else set()
        )
        if bundle_ids and result.parent_artifact_id not in bundle_ids:
            raise ValueError("REPORT_EXPORT_NOT_READY:experiment_lineage")
        if self.competition_mode and not result.content.get("is_real_experiment"):
            raise ValueError("REPORT_EXPORT_NOT_READY:real_experiment_result")
        revision = latest.get("revision")
        if revision is None or revision.parent_artifact_id != result.id:
            raise ValueError("REPORT_EXPORT_NOT_READY:feedback_revision")
        revision_iteration = int(revision.content.get("iteration") or 0)
        if (
            feedback_requires_follow_up(revision.content)
            and revision_iteration < self.max_feedback_iterations
        ):
            raise ValueError("REPORT_EXPORT_NOT_READY:feedback_follow_up")

    @staticmethod
    def _skill_state(step_id: str, latest: dict) -> dict:
        state: dict = {}
        return state

    @staticmethod
    def _runtime_call(package: RuntimePackage) -> dict:
        return {
            "provider": "skill_runtime",
            "method": "invoke",
            "step_id": package.step_id,
            "agent_id": package.agent_id,
            "skills": list(package.skill_ids),
            "skill_invocations": package.audit["skill_invocations"],
            "authorized_tools": list(package.authorized_tools),
            "instruction_sha256": package.audit["instruction_sha256"],
            "denied_tools": package.audit["denied_tools"],
            "omitted_sections": list(package.omitted_sections),
        }

    @staticmethod
    def _require_tools(package: RuntimePackage, *tool_names: str) -> None:
        authorized = set(package.authorized_tools)
        for tool_name in tool_names:
            if tool_name not in authorized:
                raise ValueError(
                    f"SKILL_TOOL_UNAUTHORIZED:{package.step_id}:{tool_name}"
                )

    def _produce_validated(
        self,
        run_id: str,
        step_id: str,
        producer,
        *,
        diagnosis: bool = False,
        validation_context: dict | None = None,
    ) -> dict:
        if step_id == "research_plan":
            raise ValueError("PLAN_REVIEW_GOVERNANCE_ONLY")
        revision = None
        revision_limit = self.supervisor_agent.revision_limit(
            step_id, diagnosis=diagnosis
        )
        previous_attempt_artifact_id: str | None = None
        previous_candidate: dict | None = None
        previous_issues: tuple[str, ...] = ()
        for revision_number in range(revision_limit + 1):
            if (
                step_id == "report_export"
                and revision_number > 0
                and previous_candidate is not None
            ):
                candidate = self.writer_agent.repair_report(
                    previous_candidate,
                    self.repository.get_run(run_id).artifacts,
                    list(previous_issues),
                )
            else:
                candidate = producer(revision)
            if not isinstance(candidate, dict):
                candidate = {"value": candidate}
            attempt_evidence = candidate.pop("_candidate_attempt", None)
            decision = self._validate_candidate(
                run_id,
                step_id,
                candidate,
                validation_context=validation_context,
            )
            if step_id == "experiment_task" and isinstance(attempt_evidence, dict):
                attempt_payload = {
                    **deepcopy(attempt_evidence),
                    "attempt_id": "",
                    "parent_attempt_id": previous_attempt_artifact_id or "",
                    "attempt_number": revision_number + 1,
                    "accepted": bool(decision.accepted),
                    "validation_issues": list(decision.issues),
                }
                attempt_artifact = self.repository.add_artifact(
                    run_id,
                    "experiment_candidate_attempt",
                    f"Experiment Candidate Attempt {revision_number + 1}",
                    attempt_payload,
                    step_id,
                    self.experiment_agent.name,
                    parent_artifact_id=(
                        previous_attempt_artifact_id
                        or str(attempt_evidence.get("plan_artifact_id") or "")
                        or None
                    ),
                )
                attempt_payload["attempt_id"] = attempt_artifact.id
                # Preserve the immutable Artifact record while making its own id
                # available in its persisted content for artifact-only recovery.
                run = self.repository.get_run(run_id)
                for index, artifact in enumerate(run.artifacts):
                    if artifact.id == attempt_artifact.id:
                        run.artifacts[index] = artifact.model_copy(
                            update={"content": attempt_payload}
                        )
                        self.repository.save_run(run)
                        break
                previous_attempt_artifact_id = attempt_artifact.id
            if decision.accepted:
                return candidate
            previous_candidate = deepcopy(candidate)
            previous_issues = decision.issues
            if revision_number >= revision_limit:
                final_rejection = {
                    "step_id": step_id,
                    "attempt": revision_number + 1,
                    "limit": revision_limit,
                    "diagnosis": diagnosis,
                    "issues": list(decision.issues),
                    "status": "revision_limit_exceeded",
                }
                repair_history = candidate.get("_repair_history")
                if isinstance(repair_history, list):
                    final_rejection["repair_history"] = deepcopy(repair_history)
                self.repository.append_event(
                    run_id,
                    step_id,
                    self.supervisor_agent.name,
                    "Rejected candidate output after revision limit.",
                    data=final_rejection,
                    output_summary={"accepted": False, "issues": list(decision.issues)},
                    tool_calls=[
                        {
                            "provider": "supervisor_agent",
                            "method": "revision_limit",
                            **final_rejection,
                        }
                    ],
                    provider_mode=self.llm_provider.mode,
                    fallback_used=self.llm_provider.fallback,
                    fallback_reason=(
                        "Mock LLM development fallback." if self.llm_provider.fallback else ""
                    ),
                )
                self.supervisor_agent.require_revision(
                    step_id,
                    revision_number + 1,
                    decision.issues,
                    diagnosis=diagnosis,
                )
            revision = self.supervisor_agent.require_revision(
                step_id,
                revision_number + 1,
                decision.issues,
                diagnosis=diagnosis,
            )
            repair_history = candidate.get("_repair_history")
            if isinstance(repair_history, list):
                revision["repair_history"] = deepcopy(repair_history)
            self.repository.append_event(
                run_id,
                step_id,
                self.supervisor_agent.name,
                "Rejected candidate output and requested revision.",
                data=revision,
                output_summary={"accepted": False, "issues": list(decision.issues)},
                tool_calls=[
                    {
                        "provider": "supervisor_agent",
                        "method": "request_revision",
                        **revision,
                    }
                ],
                provider_mode=self.llm_provider.mode,
                fallback_used=self.llm_provider.fallback,
                fallback_reason=(
                    "Mock LLM development fallback." if self.llm_provider.fallback else ""
                ),
            )
        raise AssertionError("unreachable supervisor revision loop")

    def _validate_candidate(
        self,
        run_id: str,
        step_id: str,
        candidate: dict,
        *,
        validation_context: dict | None = None,
    ):
        internal_issues = candidate.get("_validation_issues")
        if isinstance(internal_issues, list) and internal_issues:
            return ValidationDecision(
                False,
                tuple(str(issue) for issue in internal_issues),
            )
        staging_dir = self.repository.store.data_dir / "staging" / run_id / step_id
        staging_dir.mkdir(parents=True, exist_ok=True)
        candidate_path = staging_dir / "candidate.json"
        candidate_path.write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        wiki_root = self.knowledge_service.wiki.root
        wiki_paths = tuple(
            path
            for path in (wiki_root / "query_pack.md", wiki_root / "index.md")
            if path.is_file()
        )
        try:
            validation_kwargs = {
                "artifact_path": candidate_path,
                "wiki_paths": wiki_paths,
            }
            validate_parameters = inspect.signature(
                self.supervisor_agent.validate
            ).parameters
            if validation_context is not None and "review_context" in validate_parameters:
                validation_kwargs["review_context"] = validation_context
            return self.supervisor_agent.validate(step_id, candidate, **validation_kwargs)
        finally:
            candidate_path.unlink(missing_ok=True)
            for path in (staging_dir, staging_dir.parent, staging_dir.parent.parent):
                try:
                    path.rmdir()
                except OSError:
                    break

    @staticmethod
    def _require_step_inputs(step_id: str, latest: dict) -> None:
        required = _STEP_REQUIRED_INPUTS.get(step_id, ())
        missing = [
            artifact_type for artifact_type in required if artifact_type not in latest
        ]
        if missing:
            raise ValueError(
                f"STEP_INPUT_MISSING:{step_id}:required={','.join(required)}:"
                f"missing={','.join(missing)}"
            )

    def _dataset_options(self) -> list[dict]:
        probe = getattr(self.experiment_provider, "dataset_availability", None)
        if not callable(probe):
            return []
        try:
            return list(probe())
        except Exception:
            return []

    def _inspect_configured_local_dataset(self, run=None) -> dict | None:
        settings = getattr(self.experiment_provider, "settings", None)
        if settings is None or getattr(settings, "dataset_source", "") != "local":
            return None
        dataset_dir = str(getattr(settings, "dataset_dir", "") or "").strip()
        if not dataset_dir:
            raise ValueError("DATASET_DIRECTORY_REQUIRED")
        resolved_root, canonical = resolve_local_dataset_directory(
            dataset_dir,
            str(getattr(run, "problem_input", "") or ""),
            str(getattr(run, "constraints", "") or ""),
        )
        return inspect_dataset_directory(
            str(resolved_root),
            canonical_name=canonical,
            display_name=dataset_display_name(canonical),
        )

    @staticmethod
    def _bind_plan_to_dataset(plan: dict, profile: dict) -> dict:
        return {
            **plan,
            "dataset": {
                "canonical_name": contract_canonical_name(profile),
                "display_name": profile.get("display_name") or profile["name"],
                "directory_name": profile.get("directory_name") or Path(profile["root"]).name,
                # Kept for legacy readers; it is never used for semantic validation.
                "name": profile.get("display_name") or profile["name"],
                "source": "local",
                "root": profile["root"],
                "contract_id": profile["contract_id"],
                "content_fingerprint": profile["content_fingerprint"],
                "inspection_status": profile["inspection_status"],
                "file_count": profile["file_count"],
                "file_types": profile["file_types"],
                "files": list(profile.get("files") or []),
                "schemas": profile["schemas"],
                "observed_structure": deepcopy(profile.get("observed_structure") or []),
                "limitations": list(profile.get("limitations") or []),
                "semantic_facts": deepcopy(profile.get("semantic_facts") or {}),
                "preprocessing": list(
                    (plan.get("dataset") or {}).get("preprocessing") or []
                ),
                "split": str((plan.get("dataset") or {}).get("split") or ""),
            },
        }

    @staticmethod
    def _plan_dataset_issues(plan: dict, dataset_options: list[dict]) -> list[str]:
        dataset = plan.get("dataset") or {}
        contract_id = str(dataset.get("contract_id") or "")
        if contract_id:
            option = next(
                (
                    item
                    for item in dataset_options
                    if item.get("contract_id") == contract_id
                    and item.get("status") == "bound"
                ),
                None,
            )
            if option is None:
                return [f"PLAN_DATASET_CONTRACT_UNKNOWN:{contract_id}"]
            if dataset.get("content_fingerprint") != option.get("content_fingerprint"):
                return [f"PLAN_DATASET_FINGERPRINT_MISMATCH:{contract_id}"]
            return []
        normalized = contract_canonical_name(plan.get("dataset") or {})
        if not normalized:
            return []
        status = next(
            (option["status"] for option in dataset_options if option["name"] == normalized),
            "",
        )
        if status != "missing":
            return []
        usable = [
            option["name"]
            for option in dataset_options
            if option["status"] in {"cached", "downloadable"}
        ]
        return [
            f"PLAN_DATASET_UNAVAILABLE:{normalized}. The dataset source is local and the files "
            "are not in the dataset cache. Choose a dataset from: "
            f"{', '.join(usable) if usable else '(none cached)'} or design a synthetic-data experiment."
        ]

    @staticmethod
    def _attach_dataset_card(plan: dict, dataset_options: list[dict]) -> dict:
        contract_id = str((plan.get("dataset") or {}).get("contract_id") or "")
        if contract_id:
            option = next(
                (
                    item
                    for item in dataset_options
                    if item.get("contract_id") == contract_id
                ),
                None,
            )
            if option is None:
                raise ValueError(f"PLAN_DATASET_CONTRACT_UNKNOWN:{contract_id}")
            dataset = dict(plan.get("dataset") or {})
            dataset["card"] = option["card"]
            dataset["availability"] = "bound"
            return {**plan, "dataset": dataset}
        normalized = contract_canonical_name(plan.get("dataset") or {})
        if not normalized:
            return plan
        option = next(
            (option for option in dataset_options if option["name"] == normalized), None
        )
        dataset = dict(plan.get("dataset") or {})
        dataset["normalized_name"] = normalized
        dataset["card"] = option["card"] if option else dataset_card(normalized)
        if option:
            dataset["availability"] = option["status"]
        return {**plan, "dataset": dataset}

    @staticmethod
    def _with_revision(instructions: str, revision: dict | None) -> str:
        if not revision:
            return instructions
        issues = list(revision["issues"])
        request = (
            "## Supervisor Revision Request\n"
            f"Revision {revision['attempt']} of {revision['limit']}. Correct all issues:\n"
            + "\n".join(f"- {issue}" for issue in issues)
        )
        if any(issue.startswith("EXPERIMENT_LOCAL_DATASET_SUBSTITUTION_FORBIDDEN") for issue in issues):
            request += (
                "\n\nThe dataset is locked by the research plan. The issue above states the "
                "expected canonical name, declared canonical name, contract ID, and required "
                "DATA_ROOT. Do not select or download another dataset; generate code that uses "
                "the locked contract through DATA_ROOT only."
            )
        return "\n\n".join(part for part in (instructions, request) if part)

    @staticmethod
    def _normalize_plan_review(value: dict) -> dict:
        verdict = str(value.get("verdict") or "").upper()
        feasibility = str(value.get("experiment_feasibility") or "").upper()
        if verdict not in {"ACCEPT", "REVISE", "REJECT"}:
            raise ValueError("MODEL_OUTPUT_VALIDATION_FAILURE:invalid review verdict")
        if feasibility not in {"FEASIBLE", "FEASIBLE_AFTER_REVISION", "NOT_FEASIBLE"}:
            raise ValueError("MODEL_OUTPUT_VALIDATION_FAILURE:invalid experiment feasibility")
        normalized = {
            "verdict": verdict,
            "issues": list(value.get("issues") or []),
            "closed_issue_ids": list(value.get("closed_issue_ids") or []),
            "reopened_issue_ids": list(value.get("reopened_issue_ids") or []),
            "required_changes": list(value.get("required_changes") or []),
            "suggested_fixes": list(value.get("suggested_fixes") or []),
            "revised_plan_guidance": list(value.get("revised_plan_guidance") or []),
            "fix_requirements": dict(value.get("fix_requirements") or {}),
            "scope_check": dict(value.get("scope_check") or {}),
            "experiment_feasibility": feasibility,
            "provider_mode": str(value.get("provider_mode") or "deepseek"),
            "model_used": str(value.get("model_used") or ""),
        }
        return normalized

    @staticmethod
    def _remove_backend_owned_review_fields(review: dict) -> dict:
        """Prevent reviewers from vetoing values only the backend may write.

        Candidate plans contain preregistered concrete seeds so the accepted
        plan and executable task share one immutable record. Those values are
        injected after model generation, therefore a reviewer must never demand
        that a revision changes ``seeds``. Keep such feedback visible as a
        warning, while removing it from the repairable contract fields.
        """
        sanitized = deepcopy(review)
        issues = sanitized.get("issues")
        if not isinstance(issues, list):
            return sanitized
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            fields = list(issue.get("contract_fields") or [])
            retained = [
                field
                for field in fields
                if canonical_contract_field(field) != "seeds"
            ]
            if len(retained) == len(fields):
                continue
            issue["contract_fields"] = retained
            issue["backend_owned_fields_ignored"] = ["seeds"]
            if not retained:
                issue["severity"] = "WARNING"
                issue["blocker_class"] = None
                issue["required_fix"] = None
                issue["reason"] = (
                    str(issue.get("reason") or "")
                    + " Concrete seeds are injected and frozen by the backend."
                ).strip()
        return sanitized

    def _execute_plan_review_governance(
        self,
        *,
        run_id: str,
        step_id: str,
        run,
        latest: dict,
        selection: dict,
        dataset_options: list[dict],
        dataset_profile: dict | None,
        policy_artifact,
        frozen_policy: dict,
        instructions: str,
        constraints_reference: dict,
        stage: str,
        profile: dict,
        protocol: dict,
        readiness: dict,
        dataset_state: dict,
        produce_initial_plan,
        execution_seeds: list[int],
    ):
        """Execute the sole scientific plan gate from durable append-only state."""
        # Preserve a historical policy's strictness, but allow an operator to
        # grant one extra bounded retry to a recoverable plan that was already
        # stopped for a known field-alignment omission.
        revision_limit = max(
            int(frozen_policy["max_content_revisions"]),
            self.max_deepseek_plan_revision,
        )
        current_run = self.repository.get_run(run_id)
        self._validate_plan_governance_history(
            current_run.artifacts,
            policy_artifact,
            frozen_policy,
            constraints_reference,
        )
        # A historical ledger is immutable, but an old implementation may have
        # elevated an execution concern into a Plan veto.  Re-adjudicate that
        # record under the current narrow authority before deciding whether the
        # user can continue; the resulting append-only audit record is the only
        # new state.
        self._recover_plan_review_for_continue(run_id, policy_artifact, frozen_policy)
        self._reconcile_plan_review_phases(
            run_id, policy_artifact, frozen_policy
        )
        current_run = self.repository.get_run(run_id)
        candidates = [
            item
            for item in current_run.artifacts
            if item.type == "research_plan_candidate"
            and (item.content or {}).get("policy_artifact_id") == policy_artifact.id
        ]
        by_round: dict[int, object] = {}
        for candidate in candidates:
            content = candidate.content or {}
            round_index = int(content.get("round_index") or 0)
            if (
                round_index < 1
                or round_index in by_round
                or content.get("plan_id") != candidate.id
                or content.get("research_constraints_reference") != constraints_reference
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:candidate_lineage"
                )
            by_round[round_index] = candidate
        if by_round and sorted(by_round) != list(range(1, max(by_round) + 1)):
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:candidate_round_gap"
            )
        artifact_index = {item.id: item for item in current_run.artifacts}
        for candidate_round, candidate in by_round.items():
            content = candidate.content or {}
            if candidate_round == 1:
                expected_parent = (frozen_policy.get("source_artifact_lineage") or {}).get(
                    "hypothesis_selection_artifact_id"
                )
                if content.get("parent_plan_id") or candidate.parent_artifact_id != expected_parent:
                    raise PlanReviewPolicyIntegrityError(
                        "PLAN_REVIEW_POLICY_INTEGRITY:initial_candidate_parent"
                    )
                continue
            parent_plan_id = str(content.get("parent_plan_id") or "")
            request_id = str(content.get("revision_request_id") or "")
            request = artifact_index.get(request_id)
            if (
                parent_plan_id != by_round[candidate_round - 1].id
                or candidate.parent_artifact_id != request_id
                or request is None
                or request.type
                not in {"plan_review_revision_request", "plan_review_change_request"}
                or (request.content or {}).get("plan_id") != parent_plan_id
                or int((request.content or {}).get("round_index") or 0)
                != candidate_round - 1
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:revision_candidate_lineage"
                )
        proposals = [
            item
            for item in current_run.artifacts
            if item.type == "plan_refinement_proposal"
            and (item.content or {}).get("policy_artifact_id") == policy_artifact.id
        ]
        change_requests = [
            item
            for item in current_run.artifacts
            if item.type == "plan_review_change_request"
            and (item.content or {}).get("policy_artifact_id") == policy_artifact.id
        ]
        requests_by_proposal = {
            str((item.content or {}).get("proposal_id") or ""): item
            for item in change_requests
        }
        candidate_request_ids = {
            str((item.content or {}).get("revision_request_id") or "")
            for item in by_round.values()
        }
        pending_proposals = [
            item
            for item in proposals
            if item.id not in requests_by_proposal
            or requests_by_proposal[item.id].id not in candidate_request_ids
        ]
        if len(pending_proposals) > 1:
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:multiple_pending_plan_proposals"
            )
        if pending_proposals:
            proposal = pending_proposals[0]
            proposal_position = next(
                index
                for index, artifact in enumerate(current_run.artifacts)
                if artifact.id == proposal.id
            )
            if not by_round or not is_plan_governance_accepted(
                current_run.artifacts[:proposal_position]
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:proposal_without_accepted_base"
                )
            parent_candidate = by_round[max(by_round)]
            if (proposal.content or {}).get("base_candidate_id") != parent_candidate.id:
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:proposal_base_candidate"
                )
            parent_round = int((parent_candidate.content or {}).get("round_index") or 0)
            parent_review = self._single_governance_artifact(
                current_run.artifacts,
                "plan_review",
                policy_artifact.id,
                round_index=parent_round,
                plan_id=parent_candidate.id,
            )
            parent_ledger = (
                self._single_governance_artifact(
                    current_run.artifacts,
                    "plan_review_issue_ledger",
                    policy_artifact.id,
                    round_index=parent_round,
                    plan_id=parent_candidate.id,
                    review_id=parent_review.id,
                )
                if parent_review is not None
                else None
            )
            # Acceptance was already proven against the complete history prefix
            # above.  Keep the raw ledger as immutable lineage, but do not make
            # it the sole authority: an append-only recovery adjudication may be
            # the canonical ACCEPT proof for a historical ledger.
            if parent_ledger is None:
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:proposal_base_acceptance"
                )
            change_request = requests_by_proposal.get(proposal.id)
            if change_request is None:
                change_payload = {
                    "schema_version": 1,
                    "policy_artifact_id": policy_artifact.id,
                    "policy_payload_sha256": frozen_policy["policy_payload_sha256"],
                    "proposal_id": proposal.id,
                    "plan_id": parent_candidate.id,
                    "review_id": parent_review.id,
                    "ledger_id": parent_ledger.id,
                    "round_index": parent_round,
                    "next_round_index": parent_round + 1,
                    "governance_cycle_index": int(
                        (parent_candidate.content or {}).get("governance_cycle_index")
                        or 1
                    )
                    + 1,
                }
                change_request = self.repository.add_artifact(
                    run_id,
                    "plan_review_change_request",
                    f"Plan Review Change Request {parent_round + 1}",
                    {
                        **change_payload,
                        "change_request_payload_sha256": canonical_sha256(change_payload),
                    },
                    step_id,
                    "Plan Review Governance",
                    parent_artifact_id=proposal.id,
                )
            next_round = parent_round + 1
            proposal_plan = deepcopy(
                (proposal.content or {}).get("normalized_plan") or {}
            )
            frozen_training_budget = self._frozen_model_training_budget(run_id)
            if frozen_training_budget is None:
                raise ValueError("MODEL_TRAINING_BUDGET_CONTRACT_MISSING")
            if frozen_training_budget.get("mode") == "epochs":
                proposal_plan = self._with_frozen_training_epochs(
                    proposal_plan,
                    int(frozen_training_budget["epochs"]),
                )
            proposal_plan["seeds"] = list(execution_seeds)
            plan_candidate = self.repository.add_artifact(
                run_id,
                "research_plan_candidate",
                f"Research Plan Candidate Round {next_round}",
                {
                    "plan_id": "",
                    "round_index": next_round,
                    "governance_cycle_index": int(
                        (change_request.content or {}).get("governance_cycle_index")
                        or 1
                    ),
                    "revision_attempt": 0,
                    "parent_plan_id": parent_candidate.id,
                    "revision_request_id": change_request.id,
                    "source_proposal_id": proposal.id,
                    "research_stage": stage,
                    "normalized_plan": proposal_plan,
                    "research_constraints_reference": constraints_reference,
                    "policy_artifact_id": policy_artifact.id,
                    "policy_payload_sha256": frozen_policy["policy_payload_sha256"],
                    "provider": self.llm_provider.mode,
                    "model": "planning.refine_plan",
                    "request_chars": 0,
                    "response_metadata": {},
                    "status": "review_pending",
                },
                step_id,
                self.planning_agent.name,
                parent_artifact_id=change_request.id,
                self_id_field="plan_id",
            )
            by_round[next_round] = plan_candidate
        if not by_round:
            plan = produce_initial_plan()
            plan_candidate = self.repository.add_artifact(
                run_id,
                "research_plan_candidate",
                "Research Plan Candidate Round 1",
                {
                    "plan_id": "",
                    "round_index": 1,
                    "governance_cycle_index": 1,
                    "revision_attempt": 0,
                    "parent_plan_id": "",
                    "research_stage": stage,
                    "normalized_plan": deepcopy(plan),
                    "research_constraints_reference": constraints_reference,
                    "policy_artifact_id": policy_artifact.id,
                    "policy_payload_sha256": frozen_policy["policy_payload_sha256"],
                    "provider": self.llm_provider.mode,
                    "model": "planning.build_plan",
                    "request_chars": 0,
                    "response_metadata": {},
                    "status": "review_pending",
                },
                step_id,
                self.planning_agent.name,
                parent_artifact_id=latest["hypothesis_selection"].id,
                self_id_field="plan_id",
            )
            by_round[1] = plan_candidate

        round_index = max(by_round)
        plan_candidate = by_round[round_index]
        while True:
            self._append_plan_review_phase(
                run_id,
                policy_artifact,
                plan_candidate,
                "CANDIDATE_CREATED",
                plan_candidate.id,
            )
            current_run = self.repository.get_run(run_id)
            plan = deepcopy((plan_candidate.content or {}).get("normalized_plan") or {})
            if not plan:
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:candidate_payload"
                )
            parent_plan_id = str((plan_candidate.content or {}).get("parent_plan_id") or "")
            previous_plan = self._plan_candidate_content(current_run.artifacts, parent_plan_id)
            prior_ledger_artifact = self._validated_prior_plan_ledger(
                current_run.artifacts,
                policy_artifact.id,
                frozen_policy["policy_payload_sha256"],
                round_index,
                parent_plan_id,
            )
            issue_ledger = deepcopy(
                (prior_ledger_artifact.content or {}).get("issues") or []
            ) if prior_ledger_artifact else []
            previous_review_artifact = self._single_governance_artifact(
                current_run.artifacts,
                "plan_review",
                policy_artifact.id,
                round_index=round_index - 1,
            ) if round_index > 1 else None
            changed_fields = changed_contract_fields(
                previous_plan,
                plan,
                field_registry=frozen_policy[
                    "canonical_contract_field_registry"
                ],
                field_aliases=frozen_policy["contract_field_aliases"],
            )
            new_evidence_artifact_ids = self._new_plan_review_input_artifact_ids(
                current_run.artifacts,
                previous_review_artifact.id if previous_review_artifact else "",
            )
            review_artifact = self._single_governance_artifact(
                current_run.artifacts,
                "plan_review",
                policy_artifact.id,
                round_index=round_index,
                plan_id=plan_candidate.id,
            )
            if review_artifact is None:
                revision_attempt = int(
                    (plan_candidate.content or {}).get("revision_attempt") or 0
                )
                review_context = self._research_plan_review_context(
                    run,
                    latest,
                    selection,
                    plan,
                    dataset_options,
                    frozen_policy=frozen_policy,
                    issue_ledger=issue_ledger,
                    round_index=revision_attempt + 1,
                    changed_fields=changed_fields,
                    new_evidence_artifact_ids=new_evidence_artifact_ids,
                    candidate_plan_id=plan_candidate.id,
                )
                try:
                    review = self._remove_backend_owned_review_fields(
                        self._normalize_plan_review(
                            self.planning_agent.review_plan(
                                review_context,
                                runtime_contract_snapshot=frozen_policy[
                                    "review_runtime_contract_snapshot"
                                ],
                                schema_snapshot=frozen_policy[
                                    "review_prompt_schema_snapshot"
                                ],
                            )
                        )
                    )
                except LLMRequestCancelled:
                    raise
                except Exception as exc:
                    self.repository.add_artifact(
                        run_id,
                        "failure_record",
                        "Research Plan Review Failure",
                        self._plan_review_failure(exc, round_index),
                        step_id,
                        "ModelCallReliability",
                        parent_artifact_id=plan_candidate.id,
                    )
                    raise
                review.update(
                    reviewer_verdict=review["verdict"],
                    reviewer_findings=deepcopy(review["issues"]),
                    review_id="",
                    plan_id=plan_candidate.id,
                    research_constraints_reference=constraints_reference,
                    policy_artifact_id=policy_artifact.id,
                    policy_payload_sha256=frozen_policy["policy_payload_sha256"],
                    round_index=round_index,
                    governance_cycle_index=int(
                        (plan_candidate.content or {}).get("governance_cycle_index")
                        or 1
                    ),
                    revision_attempt=revision_attempt,
                    review_mode="initial" if revision_attempt == 0 else "revision",
                )
                review_artifact = self.repository.add_artifact(
                    run_id,
                    "plan_review",
                    f"DeepSeek Plan Review {round_index}",
                    review,
                    step_id,
                    "DeepSeek Research Plan Reviewer",
                    parent_artifact_id=plan_candidate.id,
                    self_id_field="review_id",
                )
            self._validate_review_lineage(
                review_artifact, policy_artifact, plan_candidate, round_index, frozen_policy
            )
            self._append_plan_review_phase(
                run_id, policy_artifact, plan_candidate, "REVIEW_CREATED", review_artifact.id
            )

            current_run = self.repository.get_run(run_id)
            ledger_artifact = self._single_governance_artifact(
                current_run.artifacts,
                "plan_review_issue_ledger",
                policy_artifact.id,
                round_index=round_index,
                plan_id=plan_candidate.id,
                review_id=review_artifact.id,
            )
            if ledger_artifact is None:
                adjudication = adjudicate_review(
                    issue_ledger,
                    review_artifact.content or {},
                    frozen_policy=frozen_policy,
                    round_index=int(
                        (plan_candidate.content or {}).get("revision_attempt") or 0
                    )
                    + 1,
                    changed_fields=changed_fields,
                    new_evidence_artifact_ids=new_evidence_artifact_ids,
                    candidate_plan_id=plan_candidate.id,
                    review_id=review_artifact.id,
                )
                issue_ledger = adjudication.issues
                ledger_payload = {
                    "schema_version": 2,
                    "policy_artifact_id": policy_artifact.id,
                    "policy_payload_sha256": frozen_policy["policy_payload_sha256"],
                    "round_identity": self._plan_review_round_identity(
                        policy_artifact.id, round_index, plan_candidate.id
                    ),
                    "round_index": round_index,
                    "plan_id": plan_candidate.id,
                    "review_id": review_artifact.id,
                    "issues": deepcopy(issue_ledger),
                    "validated_open_blocker_ids": list(adjudication.validated_open_blocker_ids),
                    "warning_ids": list(adjudication.warning_ids),
                    "suggestion_ids": list(adjudication.suggestion_ids),
                    "closed_issue_ids": list(adjudication.closed_issue_ids),
                    "verdict": adjudication.verdict,
                }
                ledger_artifact = self.repository.add_artifact(
                    run_id,
                    "plan_review_issue_ledger",
                    f"Plan Review Issue Ledger Round {round_index}",
                    {
                        **ledger_payload,
                        "ledger_payload_sha256": canonical_sha256(ledger_payload),
                    },
                    step_id,
                    "Plan Review Governance",
                    parent_artifact_id=review_artifact.id,
                )
            ledger_content = self._validate_plan_review_ledger(
                ledger_artifact,
                policy_artifact,
                plan_candidate,
                review_artifact,
                round_index,
                frozen_policy,
            )
            recovery = self._plan_review_recovery_for(
                self.repository.get_run(run_id).artifacts,
                policy_artifact,
                frozen_policy,
                plan_candidate,
                review_artifact,
                ledger_artifact,
            )
            ledger_proof = recovery or ledger_artifact
            if recovery is not None:
                ledger_content = {
                    **ledger_content,
                    "issues": deepcopy(recovery.content["issues"]),
                    "validated_open_blocker_ids": list(
                        recovery.content["validated_open_blocker_ids"]
                    ),
                    "warning_ids": list(recovery.content.get("warning_ids") or []),
                    "suggestion_ids": list(recovery.content.get("suggestion_ids") or []),
                    "closed_issue_ids": list(recovery.content.get("closed_issue_ids") or []),
                    "verdict": recovery.content["verdict"],
                }
            issue_ledger = deepcopy(ledger_content["issues"])
            open_ids = tuple(ledger_content["validated_open_blocker_ids"])
            self._append_plan_review_phase(
                run_id, policy_artifact, plan_candidate, "LEDGER_COMMITTED", ledger_artifact.id
            )
            if not open_ids:
                if recovery is None:
                    self._append_plan_review_phase(
                        run_id, policy_artifact, plan_candidate, "ROUND_COMPLETE", ledger_proof.id,
                        outcome="ACCEPT",
                    )
                return plan, plan_candidate, issue_ledger

            revision_attempt = int(
                (plan_candidate.content or {}).get("revision_attempt") or 0
            )
            if revision_attempt >= revision_limit:
                required = self._single_governance_artifact(
                    self.repository.get_run(run_id).artifacts,
                    "plan_revision_required",
                    policy_artifact.id,
                    round_index=round_index,
                    plan_id=plan_candidate.id,
                )
                message = "DeepSeek did not produce an acceptable plan within the bounded revision limit."
                if required is None:
                    required = self.repository.add_artifact(
                        run_id,
                        "plan_revision_required",
                        "Research Plan Revision Required",
                        {
                            "code": "PLAN_REVISION_REQUIRED",
                            "message": message,
                            "attempt": revision_attempt,
                            "scientific_state_mutated": False,
                            "policy_artifact_id": policy_artifact.id,
                            "policy_payload_sha256": frozen_policy["policy_payload_sha256"],
                            "round_index": round_index,
                            "plan_id": plan_candidate.id,
                            "review_id": review_artifact.id,
                            "ledger_id": ledger_artifact.id,
                            "issue_ledger": issue_ledger,
                            "recoverable": True,
                            "user_action_required": True,
                        },
                        step_id,
                        "Scientific Stability Gate",
                        parent_artifact_id=ledger_artifact.id,
                    )
                self._append_plan_review_phase(
                    run_id, policy_artifact, plan_candidate, "ROUND_COMPLETE", required.id,
                    outcome="NEEDS_PLAN_REVISION",
                )
                self.repository.update_workflow_state(
                    run_id,
                    status="NEEDS_PLAN_REVISION",
                    current_step="research_plan",
                    automatic=False,
                    stop_requested=False,
                )
                self.repository.update_step_state(
                    run_id,
                    "research_plan",
                    "interrupted",
                    error={
                        "code": "PLAN_REVISION_REQUIRED",
                        "message": message,
                        "recoverable": True,
                        "user_action_required": True,
                    },
                )
                self._persist_scientific_world_state(
                    run_id, run, profile, dataset_state, protocol, readiness, stage, issue_ledger
                )
                return None

            open_blockers = [
                deepcopy(item) for item in issue_ledger if item.get("issue_id") in open_ids
            ]
            current_run = self.repository.get_run(run_id)
            revision_request = self._single_governance_artifact(
                current_run.artifacts,
                "plan_review_revision_request",
                policy_artifact.id,
                round_index=round_index,
                plan_id=plan_candidate.id,
            )
            if revision_request is None:
                revision_request = self.repository.add_artifact(
                    run_id,
                    "plan_review_revision_request",
                    f"Plan Review Revision Request {round_index}",
                    {
                        "schema_version": 1,
                        "policy_artifact_id": policy_artifact.id,
                        "policy_payload_sha256": frozen_policy["policy_payload_sha256"],
                        "round_identity": self._plan_review_round_identity(
                            policy_artifact.id, round_index, plan_candidate.id
                        ),
                        "round_index": round_index,
                        "plan_id": plan_candidate.id,
                        "review_id": review_artifact.id,
                        "ledger_id": ledger_artifact.id,
                        "validated_open_blocker_ids": list(open_ids),
                        "open_validated_blockers": deepcopy(open_blockers),
                    },
                    step_id,
                    "Plan Review Governance",
                    parent_artifact_id=ledger_artifact.id,
                )
            request_content = revision_request.content or {}
            if (
                revision_request.parent_artifact_id != ledger_artifact.id
                or request_content.get("policy_payload_sha256")
                != frozen_policy["policy_payload_sha256"]
                or request_content.get("round_identity")
                != self._plan_review_round_identity(
                    policy_artifact.id, round_index, plan_candidate.id
                )
                or request_content.get("review_id") != review_artifact.id
                or request_content.get("ledger_id") != ledger_artifact.id
                or list(request_content.get("validated_open_blocker_ids") or [])
                != list(open_ids)
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:revision_request"
                )
            self._append_plan_review_phase(
                run_id, policy_artifact, plan_candidate, "REVISION_REQUESTED", revision_request.id
            )
            current_run = self.repository.get_run(run_id)
            next_candidates = [
                item
                for item in current_run.artifacts
                if item.type == "research_plan_candidate"
                and (item.content or {}).get("policy_artifact_id") == policy_artifact.id
                and (item.content or {}).get("parent_plan_id") == plan_candidate.id
            ]
            if len(next_candidates) > 1:
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:multiple_revision_candidates"
                )
            if next_candidates:
                next_candidate = next_candidates[0]
                if int((next_candidate.content or {}).get("round_index") or 0) != round_index + 1:
                    raise PlanReviewPolicyIntegrityError(
                        "PLAN_REVIEW_POLICY_INTEGRITY:revision_candidate_round"
                    )
            else:
                closed_ledger = [
                    deepcopy(item) for item in issue_ledger if item.get("status") == "CLOSED"
                ]
                # Minimal revision input (deduplicated): each fact appears exactly once.
                #   current_candidate   = the full plan being patched (hypotheses/claims inside).
                #   open_validated_blockers + required_changes = the ONLY required patches.
                #   dataset_options     = the profile repackaged as an option card (supersedes dataset_profile).
                #   authoritative plan contract + schema live in the frozen runtime instructions, not here.
                revision_context = {
                    "current_candidate": plan,
                    "current_candidate_plan_id": plan_candidate.id,
                    "revision_request_id": revision_request.id,
                    "open_validated_blockers": open_blockers,
                    "closed_issue_ledger": closed_ledger,
                    "frozen_problem_anchor": deepcopy(frozen_policy["problem_anchor"]),
                    "required_changes": [item.get("required_fix") for item in open_blockers],
                    "suggested_fixes": [],
                    "revised_plan_guidance": [
                        "Patch only Plan Contract fields named by OPEN validated blockers and return an exact fix_map."
                    ],
                    "experiment_feasibility": (review_artifact.content or {}).get("experiment_feasibility"),
                    "dataset_options": dataset_options,
                    "run_constraints": run.constraints,
                    "research_constraints_reference": constraints_reference,
                }
                # Patch-only revision: the schema is narrowed to exactly the fields
                # the OPEN blockers named, so the model cannot regenerate the whole
                # contract.  Its patch is then merged onto the current candidate;
                # every field it omitted is carried forward unchanged.
                revision_patch = self.planning_agent.revise_from_review(
                    revision_context,
                    runtime_contract_snapshot=frozen_policy[
                        "revision_runtime_contract_snapshot"
                    ],
                    schema_snapshot=plan_revision_patch_schema(
                        open_blockers,
                        schema_snapshot=frozen_policy[
                            "revision_prompt_schema_snapshot"
                        ],
                        field_registry=frozen_policy[
                            "canonical_contract_field_registry"
                        ],
                        field_aliases=frozen_policy["contract_field_aliases"],
                    ),
                )
                revised_plan = normalize_plan(
                    merge_plan_patch(plan, revision_patch),
                    selection,
                    provider_mode=self.llm_provider.mode,
                    fallback_used=self.llm_provider.fallback,
                )
                frozen_training_budget = self._frozen_model_training_budget(run_id)
                if frozen_training_budget and frozen_training_budget.get("mode") == "epochs":
                    revised_plan = self._with_frozen_training_epochs(
                        revised_plan,
                        int(frozen_training_budget["epochs"]),
                    )
                backend_seed_changed = revised_plan.get("seeds") != execution_seeds
                revised_plan["seeds"] = list(execution_seeds)
                if backend_seed_changed:
                    seed_fix_map = dict(revised_plan.get("fix_map") or {})
                    for blocker in open_blockers:
                        blocker_fields = {
                            canonical_contract_field(field)
                            for field in blocker.get("contract_fields") or []
                        }
                        if "seeds" not in blocker_fields:
                            continue
                        blocker_id = str(blocker.get("issue_id") or "").strip()
                        if blocker_id:
                            seed_fix_map[blocker_id] = list(
                                dict.fromkeys(
                                    [*seed_fix_map.get(blocker_id, []), "seeds"]
                                )
                            )
                    revised_plan["fix_map"] = seed_fix_map
                claim_alignment_blocker = self._single_claim_alignment_blocker(
                    open_blockers, plan, revised_plan
                )
                if claim_alignment_blocker:
                    # This is a deterministic data-integrity repair, not a new
                    # scientific decision: the accepted candidate already names
                    # the PIVOT claim, while one duplicate claim field is stale.
                    revised_plan = self._apply_pivot_claim_contract(
                        revised_plan,
                        {
                            "claim": str(plan.get("primary_claim") or ""),
                            "parent_claim": str(
                                ((plan.get("hypotheses") or [""])[0]) or ""
                            ),
                        },
                    )
                    revised_plan["fix_map"] = {
                        str(claim_alignment_blocker): ["hypotheses"]
                    }
                comparable_base = {key: plan.get(key) for key in revised_plan}
                revision_changed_fields = changed_contract_fields(
                    comparable_base,
                    revised_plan,
                    field_registry=frozen_policy[
                        "canonical_contract_field_registry"
                    ],
                    field_aliases=frozen_policy["contract_field_aliases"],
                )
                # ``fix_map`` is derived from the verified contract diff. The
                # model no longer gets a second attempt to describe its own
                # edits, which previously consumed revision budget without
                # changing scientific content.
                revised_plan["fix_map"] = deterministic_fix_map(
                    open_blockers,
                    changed_fields=revision_changed_fields,
                    field_registry=frozen_policy[
                        "canonical_contract_field_registry"
                    ],
                    field_aliases=frozen_policy["contract_field_aliases"],
                )
                revision_fix_map_issues = fix_map_issues(
                    revised_plan.get("fix_map"),
                    open_blockers=open_blockers,
                    changed_fields=revision_changed_fields,
                    field_registry=frozen_policy[
                        "canonical_contract_field_registry"
                    ],
                    field_aliases=frozen_policy["contract_field_aliases"],
                )
                if revision_fix_map_issues:
                    raise ValueError(
                        "MODEL_OUTPUT_VALIDATION_FAILURE:"
                        + ";".join(revision_fix_map_issues)
                    )
                if dataset_profile:
                    revised_plan = self._bind_plan_to_dataset(revised_plan, dataset_profile)
                revised_plan = self._attach_dataset_card(revised_plan, dataset_options)
                revised_plan.update(
                    research_profile=profile,
                    protocol_state=protocol,
                    readiness_state=readiness,
                    research_stage=stage,
                    research_constraints_artifact_id=constraints_reference["artifact_id"],
                    research_constraints_reference=constraints_reference,
                )
                inherited_contract = plan.get("iteration_contract") or {}
                if inherited_contract.get("implementation_reference"):
                    revised_plan["iteration_contract"] = {
                        **(revised_plan.get("iteration_contract") or {}),
                        **{k: deepcopy(inherited_contract[k]) for k in
                           ("implementation_reference", "source_result_ids", "feedback_iteration")
                           if k in inherited_contract},
                    }
                revised_plan = self._synchronize_iteration_contract(revised_plan)
                next_candidate = self.repository.add_artifact(
                    run_id,
                    "research_plan_candidate",
                    f"Research Plan Candidate Round {round_index + 1}",
                    {
                        "plan_id": "",
                        "round_index": round_index + 1,
                        "governance_cycle_index": int(
                            (plan_candidate.content or {}).get(
                                "governance_cycle_index"
                            )
                            or 1
                        ),
                        "revision_attempt": revision_attempt + 1,
                        "parent_plan_id": plan_candidate.id,
                        "revision_request_id": revision_request.id,
                        "research_stage": stage,
                        "normalized_plan": deepcopy(revised_plan),
                        "research_constraints_reference": constraints_reference,
                        "policy_artifact_id": policy_artifact.id,
                        "policy_payload_sha256": frozen_policy["policy_payload_sha256"],
                        "provider": self.llm_provider.mode,
                        "model": "planning.revise_from_review",
                        "request_chars": 0,
                        "response_metadata": {},
                        "status": "review_pending",
                    },
                    step_id,
                    self.planning_agent.name,
                    parent_artifact_id=revision_request.id,
                    self_id_field="plan_id",
                )
            self._append_plan_review_phase(
                run_id, policy_artifact, plan_candidate, "REVISION_CREATED", next_candidate.id
            )
            self._append_plan_review_phase(
                run_id, policy_artifact, plan_candidate, "ROUND_COMPLETE", next_candidate.id,
                outcome="REVISE",
            )
            plan_candidate = next_candidate
            round_index += 1

    def _validate_plan_governance_history(
        self,
        artifacts,
        policy_artifact,
        frozen_policy: dict,
        constraints_reference: dict,
    ) -> None:
        """Validate every governed child before any model call or child write."""
        child_types = {
            "research_plan_candidate",
            "plan",
            "plan_refinement_proposal",
            "plan_review_change_request",
            "plan_review",
            "plan_review_issue_ledger",
            "plan_review_recovery_adjudication",
            "plan_review_revision_request",
            "plan_revision_required",
            "plan_review_round_state",
        }
        policy_id = policy_artifact.id
        policy_hash = frozen_policy["policy_payload_sha256"]
        index = {item.id: item for item in artifacts}
        positions = {item.id: position for position, item in enumerate(artifacts)}
        migration_id = str(
            (frozen_policy.get("source_artifact_lineage") or {}).get(
                "migration_artifact_id"
            )
            or ""
        )
        legacy_ids: set[str] = set()
        if migration_id:
            migration = index.get(migration_id)
            if migration is None:
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:migration_missing"
                )
            migration_content = validate_plan_governance_migration(
                migration.content or {},
                frozen_policy_artifact_id=policy_artifact.id,
                frozen_policy_payload=frozen_policy,
            )
            legacy_ids = {
                str(row.get("artifact_id") or "")
                for row in migration_content.get("legacy_lineage") or []
            }
        governed = [
            item
            for item in artifacts
            if item.type in child_types and item.id not in legacy_ids
        ]
        for item in governed:
            content = item.content or {}
            if content.get("policy_artifact_id") != policy_id:
                raise PlanReviewPolicyIntegrityError(
                    f"PLAN_REVIEW_POLICY_INTEGRITY:{item.type}_policy_id"
                )
            if item.type != "plan_review_round_state" and (
                content.get("policy_payload_sha256") != policy_hash
            ):
                raise PlanReviewPolicyIntegrityError(
                    f"PLAN_REVIEW_POLICY_INTEGRITY:{item.type}_policy_hash"
                )

        proposals = [
            item for item in governed if item.type == "plan_refinement_proposal"
        ]
        for proposal in proposals:
            content = deepcopy(proposal.content or {})
            expected_hash = str(content.pop("proposal_payload_sha256", ""))
            base_plan = index.get(str(content.get("base_plan_artifact_id") or ""))
            base_candidate = index.get(str(content.get("base_candidate_id") or ""))
            feedback = index.get(str(content.get("feedback_revision_id") or ""))
            if (
                content.get("schema_version") != 1
                or not expected_hash
                or canonical_sha256(content) != expected_hash
                or base_plan is None
                or base_plan.type != "plan"
                or base_candidate is None
                or base_candidate.type != "research_plan_candidate"
                or (base_plan.content or {}).get("plan_candidate_id")
                != base_candidate.id
                or feedback is None
                or feedback.type != "revision"
                or proposal.parent_artifact_id != feedback.id
                or not isinstance(content.get("normalized_plan"), dict)
                or not content.get("normalized_plan")
                or positions[base_plan.id] >= positions[feedback.id]
                or positions[feedback.id] >= positions[proposal.id]
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:refinement_proposal"
                )

        change_requests = [
            item for item in governed if item.type == "plan_review_change_request"
        ]
        seen_change_proposals: set[str] = set()
        for request in change_requests:
            content = deepcopy(request.content or {})
            expected_hash = str(content.pop("change_request_payload_sha256", ""))
            proposal = index.get(str(content.get("proposal_id") or ""))
            parent_candidate = index.get(str(content.get("plan_id") or ""))
            ledger = index.get(str(content.get("ledger_id") or ""))
            proposal_id = str(content.get("proposal_id") or "")
            if (
                content.get("schema_version") != 1
                or not expected_hash
                or canonical_sha256(content) != expected_hash
                or proposal is None
                or proposal.type != "plan_refinement_proposal"
                or request.parent_artifact_id != proposal.id
                or parent_candidate is None
                or parent_candidate.type != "research_plan_candidate"
                or (proposal.content or {}).get("base_candidate_id")
                != parent_candidate.id
                or ledger is None
                or ledger.type != "plan_review_issue_ledger"
                or (ledger.content or {}).get("plan_id") != parent_candidate.id
                or content.get("review_id")
                != (ledger.content or {}).get("review_id")
                or not is_plan_governance_accepted(
                    artifacts[: positions[proposal.id]]
                )
                or int(content.get("round_index") or 0)
                != int((parent_candidate.content or {}).get("round_index") or 0)
                or int(content.get("next_round_index") or 0)
                != int(content.get("round_index") or 0) + 1
                or int(content.get("governance_cycle_index") or 0)
                != int(
                    (parent_candidate.content or {}).get("governance_cycle_index")
                    or 0
                )
                + 1
                or proposal_id in seen_change_proposals
                or positions[ledger.id] >= positions[proposal.id]
                or positions[proposal.id] >= positions[request.id]
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:change_request"
                )
            seen_change_proposals.add(proposal_id)

        candidates = [item for item in governed if item.type == "research_plan_candidate"]
        by_round: dict[int, object] = {}
        for candidate in candidates:
            content = candidate.content or {}
            round_index = int(content.get("round_index") or 0)
            if (
                round_index < 1
                or round_index in by_round
                or content.get("plan_id") != candidate.id
                or content.get("research_constraints_reference")
                != constraints_reference
                or not isinstance(content.get("normalized_plan"), dict)
                or not content.get("normalized_plan")
                or int(content.get("governance_cycle_index") or 0) < 1
                or int(content.get("revision_attempt", -1)) < 0
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:candidate_lineage"
                )
            by_round[round_index] = candidate

        for plan in [item for item in governed if item.type == "plan"]:
            content = plan.content or {}
            candidate = index.get(str(content.get("plan_candidate_id") or ""))
            if (
                candidate is None
                or candidate.type != "research_plan_candidate"
                or plan.parent_artifact_id != candidate.id
                or content.get("accepted_candidate_payload_sha256")
                != canonical_sha256(
                    (candidate.content or {}).get("normalized_plan") or {}
                )
                or changed_contract_fields(
                    (candidate.content or {}).get("normalized_plan") or {},
                    content,
                    field_registry=frozen_policy[
                        "canonical_contract_field_registry"
                    ],
                    field_aliases=frozen_policy["contract_field_aliases"],
                )
                or positions[candidate.id] >= positions[plan.id]
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:accepted_plan_lineage"
                )
        if by_round and sorted(by_round) != list(range(1, max(by_round) + 1)):
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:candidate_round_gap"
            )
        expected_initial_parent = (
            frozen_policy.get("source_artifact_lineage") or {}
        ).get("hypothesis_selection_artifact_id")
        for round_index, candidate in by_round.items():
            content = candidate.content or {}
            if round_index == 1:
                if (
                    content.get("parent_plan_id")
                    or candidate.parent_artifact_id != expected_initial_parent
                    or int(content.get("governance_cycle_index") or 0) != 1
                    or int(content.get("revision_attempt", -1)) != 0
                ):
                    raise PlanReviewPolicyIntegrityError(
                        "PLAN_REVIEW_POLICY_INTEGRITY:initial_candidate_parent"
                    )
                continue
            request = index.get(str(content.get("revision_request_id") or ""))
            previous = by_round.get(round_index - 1)
            if (
                previous is None
                or content.get("parent_plan_id") != previous.id
                or request is None
                or request.type
                not in {"plan_review_revision_request", "plan_review_change_request"}
                or candidate.parent_artifact_id != request.id
                or (request.content or {}).get("plan_id") != previous.id
                or int((request.content or {}).get("round_index") or 0)
                != round_index - 1
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:revision_candidate_lineage"
                )
            previous_content = previous.content or {}
            if request.type == "plan_review_revision_request":
                valid_counter = (
                    int(content.get("governance_cycle_index") or 0)
                    == int(previous_content.get("governance_cycle_index") or 0)
                    and int(content.get("revision_attempt", -1))
                    == int(previous_content.get("revision_attempt") or 0) + 1
                )
            else:
                valid_counter = (
                    int(content.get("governance_cycle_index") or 0)
                    == int(previous_content.get("governance_cycle_index") or 0) + 1
                    and int(content.get("revision_attempt", -1)) == 0
                    and content.get("source_proposal_id")
                    == (request.content or {}).get("proposal_id")
                )
            if not valid_counter:
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:candidate_revision_counter"
                )

        seen: set[tuple] = set()
        for review in [item for item in governed if item.type == "plan_review"]:
            content = review.content or {}
            candidate = index.get(str(content.get("plan_id") or ""))
            key = ("review", int(content.get("round_index") or 0), content.get("plan_id"))
            if key in seen or candidate is None or candidate.type != "research_plan_candidate":
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:review_identity"
                )
            seen.add(key)
            self._validate_review_lineage(
                review,
                policy_artifact,
                candidate,
                int(content.get("round_index") or 0),
                frozen_policy,
            )

        for ledger in [
            item for item in governed if item.type == "plan_review_issue_ledger"
        ]:
            content = ledger.content or {}
            candidate = index.get(str(content.get("plan_id") or ""))
            review = index.get(str(content.get("review_id") or ""))
            key = (
                "ledger",
                int(content.get("round_index") or 0),
                content.get("plan_id"),
                content.get("review_id"),
            )
            if (
                key in seen
                or candidate is None
                or candidate.type != "research_plan_candidate"
                or review is None
                or review.type != "plan_review"
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:ledger_identity"
                )
            seen.add(key)
            self._validate_plan_review_ledger(
                ledger,
                policy_artifact,
                candidate,
                review,
                int(content.get("round_index") or 0),
                frozen_policy,
            )

        for recovery in [
            item for item in governed if item.type == "plan_review_recovery_adjudication"
        ]:
            content = recovery.content or {}
            candidate = index.get(str(content.get("plan_id") or ""))
            review = index.get(str(content.get("review_id") or ""))
            ledger = index.get(str(content.get("ledger_id") or ""))
            if (
                candidate is None
                or candidate.type != "research_plan_candidate"
                or review is None
                or review.type != "plan_review"
                or ledger is None
                or ledger.type != "plan_review_issue_ledger"
                or recovery.parent_artifact_id != ledger.id
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:recovery_lineage"
                )
            validate_plan_review_recovery(
                content,
                policy_artifact_id=policy_id,
                policy_payload_sha256=policy_hash,
                candidate_plan_id=candidate.id,
                review_id=review.id,
                ledger_id=ledger.id,
            )

        for request in [
            item for item in governed if item.type == "plan_review_revision_request"
        ]:
            content = request.content or {}
            candidate = index.get(str(content.get("plan_id") or ""))
            review = index.get(str(content.get("review_id") or ""))
            ledger = index.get(str(content.get("ledger_id") or ""))
            round_index = int(content.get("round_index") or 0)
            key = ("revision_request", round_index, content.get("plan_id"))
            ledger_open = list((ledger.content or {}).get("validated_open_blocker_ids") or []) if ledger else []
            if (
                key in seen
                or candidate is None
                or review is None
                or ledger is None
                or request.parent_artifact_id != ledger.id
                or content.get("round_identity")
                != self._plan_review_round_identity(policy_id, round_index, candidate.id)
                or content.get("review_id") != review.id
                or content.get("ledger_id") != ledger.id
                or list(content.get("validated_open_blocker_ids") or []) != ledger_open
                or not ledger_open
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:revision_request"
                )
            seen.add(key)

        for required in [
            item for item in governed if item.type == "plan_revision_required"
        ]:
            content = required.content or {}
            candidate = index.get(str(content.get("plan_id") or ""))
            review = index.get(str(content.get("review_id") or ""))
            ledger = index.get(str(content.get("ledger_id") or ""))
            round_index = int(content.get("round_index") or 0)
            key = ("revision_required", round_index, content.get("plan_id"))
            if (
                key in seen
                or content.get("code") != "PLAN_REVISION_REQUIRED"
                or candidate is None
                or review is None
                or ledger is None
                or required.parent_artifact_id != ledger.id
                or int((candidate.content or {}).get("revision_attempt", -1))
                < int(frozen_policy["max_content_revisions"])
                or not (ledger.content or {}).get("validated_open_blocker_ids")
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:revision_required"
                )
            seen.add(key)

        phase_order = {
            "CANDIDATE_CREATED": 0,
            "REVIEW_CREATED": 1,
            "LEDGER_COMMITTED": 2,
            "REVISION_REQUESTED": 3,
            "REVISION_CREATED": 4,
            "ROUND_COMPLETE": 5,
        }
        phase_positions: dict[str, list[tuple[int, int]]] = {}
        for checkpoint in [
            item for item in governed if item.type == "plan_review_round_state"
        ]:
            content = deepcopy(checkpoint.content or {})
            expected_hash = str(content.pop("round_state_payload_sha256", ""))
            phase = str(content.get("phase") or "")
            candidate = index.get(str(content.get("plan_id") or ""))
            round_index = int(content.get("round_index") or 0)
            identity = self._plan_review_round_identity(
                policy_id, round_index, str(content.get("plan_id") or "")
            )
            key = ("phase", content.get("round_identity"), phase)
            parent_id = str(content.get("phase_parent_id") or "")
            proof = index.get(parent_id)
            if (
                key in seen
                or phase not in phase_order
                or candidate is None
                or candidate.type != "research_plan_candidate"
                or content.get("policy_payload_sha256") != policy_hash
                or content.get("round_identity") != identity
                or int((candidate.content or {}).get("round_index") or 0)
                != round_index
                or checkpoint.parent_artifact_id != parent_id
                or proof is None
                or positions[proof.id] >= positions[checkpoint.id]
                or not expected_hash
                or canonical_sha256(content) != expected_hash
                or not self._plan_review_phase_proof_valid(
                    phase, content, proof, candidate, index
                )
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:round_phase_lineage"
                )
            seen.add(key)
            phase_positions.setdefault(identity, []).append(
                (positions[checkpoint.id], phase_order[phase])
            )
        for rows in phase_positions.values():
            logical = [order for _, order in sorted(rows)]
            if logical != sorted(logical):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:round_phase_chronology"
                )

    @staticmethod
    def _plan_review_phase_proof_valid(
        phase: str, content: dict, proof, candidate, index: dict
    ) -> bool:
        round_index = int(content.get("round_index") or 0)
        outcome = str(content.get("outcome") or "")
        if phase == "CANDIDATE_CREATED":
            return proof.id == candidate.id and proof.type == "research_plan_candidate"
        if phase == "REVIEW_CREATED":
            return (
                proof.type == "plan_review"
                and (proof.content or {}).get("plan_id") == candidate.id
                and int((proof.content or {}).get("round_index") or 0) == round_index
            )
        if phase == "LEDGER_COMMITTED":
            return (
                proof.type == "plan_review_issue_ledger"
                and (proof.content or {}).get("plan_id") == candidate.id
                and int((proof.content or {}).get("round_index") or 0) == round_index
            )
        if phase == "REVISION_REQUESTED":
            return (
                proof.type == "plan_review_revision_request"
                and (proof.content or {}).get("plan_id") == candidate.id
            )
        if phase == "REVISION_CREATED":
            return (
                proof.type == "research_plan_candidate"
                and (proof.content or {}).get("parent_plan_id") == candidate.id
                and int((proof.content or {}).get("round_index") or 0)
                == round_index + 1
            )
        if phase != "ROUND_COMPLETE":
            return False
        if outcome == "ACCEPT":
            return (
                proof.type in {
                    "plan_review_issue_ledger",
                    "plan_review_recovery_adjudication",
                }
                and (proof.content or {}).get("plan_id") == candidate.id
                and not (proof.content or {}).get("validated_open_blocker_ids")
                and (proof.content or {}).get("verdict") == "ACCEPT"
            )
        if outcome == "REVISE":
            return (
                proof.type == "research_plan_candidate"
                and (proof.content or {}).get("parent_plan_id") == candidate.id
            )
        if outcome == "NEEDS_PLAN_REVISION":
            return (
                proof.type == "plan_revision_required"
                and (proof.content or {}).get("plan_id") == candidate.id
            )
        return False

    def recover_plan_review_for_continue(self, run_id: str) -> bool:
        """Attempt the append-only reduced-scope recovery used by Continue."""
        run = self.repository.get_run(run_id)
        policies = [item for item in run.artifacts if item.type == "plan_review_policy"]
        if len(policies) != 1:
            return False
        try:
            frozen = validate_frozen_review_policy(policies[0].content or {})
            self._validate_plan_governance_history(
                run.artifacts,
                policies[0],
                frozen,
                (frozen.get("research_constraints_reference") or {}),
            )
        except PlanReviewPolicyIntegrityError:
            return False
        return self._recover_plan_review_for_continue(run_id, policies[0], frozen) is not None

    def _recover_plan_review_for_continue(self, run_id: str, policy_artifact, frozen_policy: dict):
        """Re-adjudicate the latest reviewed candidate without rewriting its history."""
        artifacts = self.repository.get_run(run_id).artifacts
        candidates = [
            item for item in artifacts
            if item.type == "research_plan_candidate"
            and (item.content or {}).get("policy_artifact_id") == policy_artifact.id
        ]
        if not candidates:
            return None
        candidate = max(candidates, key=lambda item: int((item.content or {}).get("round_index") or 0))
        round_index = int((candidate.content or {}).get("round_index") or 0)
        review = self._single_governance_artifact(
            artifacts, "plan_review", policy_artifact.id,
            round_index=round_index, plan_id=candidate.id,
        )
        if review is None:
            return None
        ledger = self._single_governance_artifact(
            artifacts, "plan_review_issue_ledger", policy_artifact.id,
            round_index=round_index, plan_id=candidate.id, review_id=review.id,
        )
        if ledger is None:
            return None
        existing = self._plan_review_recovery_for(
            artifacts, policy_artifact, frozen_policy, candidate, review, ledger,
        )
        if existing is not None:
            return existing
        ledger_content = self._validate_plan_review_ledger(
            ledger, policy_artifact, candidate, review, round_index, frozen_policy,
        )
        if not ledger_content.get("validated_open_blocker_ids"):
            return ledger
        previous = self._validated_prior_plan_ledger(
            artifacts,
            policy_artifact.id,
            frozen_policy["policy_payload_sha256"],
            round_index,
            str((candidate.content or {}).get("parent_plan_id") or ""),
        )
        parent = next(
            (item for item in artifacts if item.id == (candidate.content or {}).get("parent_plan_id")),
            None,
        )
        changed = changed_contract_fields(
            (parent.content or {}).get("normalized_plan") if parent else {},
            (candidate.content or {}).get("normalized_plan") or {},
            field_registry=frozen_policy["canonical_contract_field_registry"],
            field_aliases=frozen_policy["contract_field_aliases"],
        )
        candidate_position = artifacts.index(candidate)
        new_evidence = [item.id for item in artifacts[:candidate_position + 1]]
        adjudication = adjudicate_review(
            (previous.content or {}).get("issues") if previous else [],
            review.content or {},
            frozen_policy=frozen_policy,
            round_index=round_index,
            changed_fields=changed,
            new_evidence_artifact_ids=new_evidence,
            candidate_plan_id=candidate.id,
            review_id=review.id,
        )
        if adjudication.validated_open_blocker_ids:
            return None
        payload = freeze_plan_review_recovery(
            policy_artifact_id=policy_artifact.id,
            policy_payload_sha256=frozen_policy["policy_payload_sha256"],
            candidate_plan_id=candidate.id,
            review_id=review.id,
            ledger_id=ledger.id,
            adjudication=adjudication,
        )
        return self.repository.add_artifact(
            run_id,
            "plan_review_recovery_adjudication",
            f"Plan Review Recovery Adjudication Round {round_index}",
            payload,
            "research_plan",
            "Plan Review Governance",
            parent_artifact_id=ledger.id,
        )

    def _plan_review_recovery_for(
        self, artifacts, policy_artifact, frozen_policy: dict, candidate, review, ledger,
    ):
        recoveries = [
            item for item in artifacts
            if item.type == "plan_review_recovery_adjudication"
            and item.parent_artifact_id == ledger.id
        ]
        if len(recoveries) > 1:
            raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:multiple_recovery")
        if not recoveries:
            return None
        validate_plan_review_recovery(
            recoveries[0].content or {},
            policy_artifact_id=policy_artifact.id,
            policy_payload_sha256=frozen_policy["policy_payload_sha256"],
            candidate_plan_id=candidate.id,
            review_id=review.id,
            ledger_id=ledger.id,
        )
        return recoveries[0]

    def _reconcile_plan_review_phases(
        self, run_id: str, policy_artifact, frozen_policy: dict
    ) -> None:
        """Append only missing checkpoints whose immutable proof already exists."""
        artifacts = self.repository.get_run(run_id).artifacts
        candidates = sorted(
            (
                item
                for item in artifacts
                if item.type == "research_plan_candidate"
                and (item.content or {}).get("policy_artifact_id")
                == policy_artifact.id
            ),
            key=lambda item: int((item.content or {}).get("round_index") or 0),
        )
        for candidate in candidates:
            round_index = int((candidate.content or {}).get("round_index") or 0)
            self._append_plan_review_phase(
                run_id,
                policy_artifact,
                candidate,
                "CANDIDATE_CREATED",
                candidate.id,
            )
            artifacts = self.repository.get_run(run_id).artifacts
            review = self._single_governance_artifact(
                artifacts,
                "plan_review",
                policy_artifact.id,
                round_index=round_index,
                plan_id=candidate.id,
            )
            if review is None:
                continue
            self._append_plan_review_phase(
                run_id, policy_artifact, candidate, "REVIEW_CREATED", review.id
            )
            artifacts = self.repository.get_run(run_id).artifacts
            ledger = self._single_governance_artifact(
                artifacts,
                "plan_review_issue_ledger",
                policy_artifact.id,
                round_index=round_index,
                plan_id=candidate.id,
                review_id=review.id,
            )
            if ledger is None:
                continue
            self._append_plan_review_phase(
                run_id, policy_artifact, candidate, "LEDGER_COMMITTED", ledger.id
            )
            open_ids = list(
                (ledger.content or {}).get("validated_open_blocker_ids") or []
            )
            recovery = self._plan_review_recovery_for(
                artifacts, policy_artifact, frozen_policy, candidate, review, ledger,
            )
            if recovery is not None:
                open_ids = list(
                    (recovery.content or {}).get("validated_open_blocker_ids") or []
                )
            artifacts = self.repository.get_run(run_id).artifacts
            if not open_ids:
                if recovery is None:
                    self._append_plan_review_phase(
                        run_id,
                        policy_artifact,
                        candidate,
                        "ROUND_COMPLETE",
                        ledger.id,
                        outcome="ACCEPT",
                    )
                continue
            required = self._single_governance_artifact(
                artifacts,
                "plan_revision_required",
                policy_artifact.id,
                round_index=round_index,
                plan_id=candidate.id,
            )
            if required is not None:
                self._append_plan_review_phase(
                    run_id,
                    policy_artifact,
                    candidate,
                    "ROUND_COMPLETE",
                    required.id,
                    outcome="NEEDS_PLAN_REVISION",
                )
                continue
            request = self._single_governance_artifact(
                artifacts,
                "plan_review_revision_request",
                policy_artifact.id,
                round_index=round_index,
                plan_id=candidate.id,
            )
            if request is None:
                continue
            self._append_plan_review_phase(
                run_id,
                policy_artifact,
                candidate,
                "REVISION_REQUESTED",
                request.id,
            )
            artifacts = self.repository.get_run(run_id).artifacts
            next_candidates = [
                item
                for item in artifacts
                if item.type == "research_plan_candidate"
                and (item.content or {}).get("policy_artifact_id")
                == policy_artifact.id
                and (item.content or {}).get("parent_plan_id") == candidate.id
            ]
            if not next_candidates:
                continue
            next_candidate = next_candidates[0]
            self._append_plan_review_phase(
                run_id,
                policy_artifact,
                candidate,
                "REVISION_CREATED",
                next_candidate.id,
            )
            self._append_plan_review_phase(
                run_id,
                policy_artifact,
                candidate,
                "ROUND_COMPLETE",
                next_candidate.id,
                outcome="REVISE",
            )

    @staticmethod
    def _plan_review_round_identity(policy_id: str, round_index: int, plan_id: str) -> str:
        return canonical_sha256(
            {"policy_artifact_id": policy_id, "round_index": round_index, "plan_id": plan_id}
        )

    def _append_plan_review_phase(
        self, run_id: str, policy_artifact, plan_candidate, phase: str, parent_id: str,
        *, outcome: str = "",
    ):
        round_index = int((plan_candidate.content or {}).get("round_index") or 0)
        identity = self._plan_review_round_identity(
            policy_artifact.id, round_index, plan_candidate.id
        )
        policy_hash = str(
            (policy_artifact.content or {}).get("policy_payload_sha256") or ""
        )
        existing = [
            item
            for item in self.repository.get_run(run_id).artifacts
            if item.type == "plan_review_round_state"
            and (item.content or {}).get("round_identity") == identity
            and (item.content or {}).get("phase") == phase
        ]
        if len(existing) > 1:
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:duplicate_round_phase"
            )
        if existing:
            content = deepcopy(existing[0].content or {})
            expected_hash = str(content.pop("round_state_payload_sha256", ""))
            if (
                existing[0].parent_artifact_id != parent_id
                or not expected_hash
                or canonical_sha256(content) != expected_hash
                or content.get("policy_payload_sha256") != policy_hash
                or content.get("phase_parent_id") != parent_id
                or content.get("plan_id") != plan_candidate.id
                or int(content.get("round_index") or 0) != round_index
                or (outcome and content.get("outcome") != outcome)
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:round_phase_lineage"
                )
            return existing[0]
        payload = {
            "schema_version": 2,
            "policy_artifact_id": policy_artifact.id,
            "policy_payload_sha256": policy_hash,
            "round_identity": identity,
            "round_index": round_index,
            "plan_id": plan_candidate.id,
            "phase": phase,
            "outcome": outcome,
            "phase_parent_id": parent_id,
        }
        return self.repository.add_artifact(
            run_id,
            "plan_review_round_state",
            f"Plan Review Round {round_index} {phase}",
            {
                **payload,
                "round_state_payload_sha256": canonical_sha256(payload),
            },
            "research_plan",
            "Plan Review Governance",
            parent_artifact_id=parent_id,
        )

    @staticmethod
    def _single_governance_artifact(
        artifacts, artifact_type: str, policy_id: str, *, round_index: int,
        plan_id: str = "", review_id: str = "",
    ):
        matches = [
            item
            for item in artifacts
            if item.type == artifact_type
            and (item.content or {}).get("policy_artifact_id") == policy_id
            and int((item.content or {}).get("round_index") or 0) == round_index
            and (not plan_id or (item.content or {}).get("plan_id") == plan_id)
            and (not review_id or (item.content or {}).get("review_id") == review_id)
        ]
        if len(matches) > 1:
            raise PlanReviewPolicyIntegrityError(
                f"PLAN_REVIEW_POLICY_INTEGRITY:duplicate_{artifact_type}"
            )
        return matches[0] if matches else None

    def _validated_prior_plan_ledger(
        self, artifacts, policy_id: str, policy_hash: str, round_index: int,
        parent_plan_id: str,
    ):
        if round_index == 1:
            return None
        candidate = next(
            (item for item in artifacts if item.id == parent_plan_id and item.type == "research_plan_candidate"),
            None,
        )
        if candidate is None:
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:previous_candidate_missing"
            )
        review = self._single_governance_artifact(
            artifacts, "plan_review", policy_id,
            round_index=round_index - 1, plan_id=parent_plan_id,
        )
        if review is None:
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:previous_review_missing"
            )
        ledger = self._single_governance_artifact(
            artifacts, "plan_review_issue_ledger", policy_id,
            round_index=round_index - 1, plan_id=parent_plan_id, review_id=review.id,
        )
        if ledger is None:
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:previous_ledger_missing"
            )
        content = ledger.content or {}
        payload = deepcopy(content)
        expected_hash = str(payload.pop("ledger_payload_sha256", ""))
        derived_open = [
            item.get("issue_id")
            for item in payload.get("issues") or []
            if isinstance(item, dict)
            and item.get("severity") == "BLOCKER"
            and item.get("validated_blocker") is True
            and item.get("status") in {"OPEN", "REOPENED"}
        ]
        if (
            content.get("policy_payload_sha256") != policy_hash
            or ledger.parent_artifact_id != review.id
            or not expected_hash
            or canonical_sha256(payload) != expected_hash
            or derived_open != list(payload.get("validated_open_blocker_ids") or [])
        ):
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:previous_ledger"
            )
        recoveries = [
            item
            for item in artifacts
            if item.type == "plan_review_recovery_adjudication"
            and item.parent_artifact_id == ledger.id
        ]
        if len(recoveries) > 1:
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:multiple_recovery"
            )
        if recoveries:
            validate_plan_review_recovery(
                recoveries[0].content or {},
                policy_artifact_id=policy_id,
                policy_payload_sha256=policy_hash,
                candidate_plan_id=candidate.id,
                review_id=review.id,
                ledger_id=ledger.id,
            )
            return recoveries[0]
        return ledger

    @staticmethod
    def _validate_review_lineage(review, policy, candidate, round_index: int, frozen_policy: dict) -> None:
        content = review.content or {}
        if (
            review.parent_artifact_id != candidate.id
            or content.get("review_id") != review.id
            or content.get("plan_id") != candidate.id
            or content.get("policy_artifact_id") != policy.id
            or content.get("policy_payload_sha256") != frozen_policy["policy_payload_sha256"]
            or int(content.get("round_index") or 0) != round_index
        ):
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:review_lineage"
            )

    def _validate_plan_review_ledger(
        self, ledger, policy, candidate, review, round_index: int, frozen_policy: dict,
    ) -> dict:
        content = deepcopy(ledger.content or {})
        expected = str(content.pop("ledger_payload_sha256", ""))
        if (
            content.get("schema_version") != 2
            or not expected
            or canonical_sha256(content) != expected
            or ledger.parent_artifact_id != review.id
            or content.get("policy_artifact_id") != policy.id
            or content.get("policy_payload_sha256") != frozen_policy["policy_payload_sha256"]
            or content.get("plan_id") != candidate.id
            or content.get("review_id") != review.id
            or int(content.get("round_index") or 0) != round_index
            or content.get("round_identity") != self._plan_review_round_identity(
                policy.id, round_index, candidate.id
            )
            or not isinstance(content.get("issues"), list)
            or not isinstance(content.get("validated_open_blocker_ids"), list)
        ):
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:ledger"
            )
        derived_open = [
            item.get("issue_id")
            for item in content["issues"]
            if isinstance(item, dict)
            and item.get("severity") == "BLOCKER"
            and item.get("validated_blocker") is True
            and item.get("status") in {"OPEN", "REOPENED"}
        ]
        if derived_open != content["validated_open_blocker_ids"]:
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:ledger_open_projection"
            )
        return {**content, "ledger_payload_sha256": expected}

    def _ensure_plan_review_policy(
        self,
        run_id: str,
        *,
        package: RuntimePackage,
        run,
        latest: dict,
        selection: dict,
        constraints_artifact,
    ):
        policies = [item for item in run.artifacts if item.type == "plan_review_policy"]
        if len(policies) > 1:
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:multiple_policy_artifacts"
            )
        if policies:
            existing = policies[0]
            content = validate_frozen_review_policy(existing.content or {})
            self._validate_plan_review_policy_lineage(
                run, existing, content, constraints_artifact
            )
            return existing, content

        governance_history = [
            item
            for item in run.artifacts
            if item.type in {
                "plan_review_issue_ledger",
                "plan_review_round_state",
                "plan_review_revision_request",
                "plan_review_change_request",
                "plan_refinement_proposal",
                "plan_revision_required",
                "plan_governance_migration",
            }
            or (
                item.type in {"research_plan_candidate", "plan_review", "plan"}
                and bool((item.content or {}).get("policy_artifact_id"))
            )
        ]
        if governance_history:
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:policy_missing_with_governance_history"
            )

        loaded = self.skill_loader.load_policy("plan-review-governance")
        contexts = [self.skill_loader.load_complete(skill_id) for skill_id in package.skill_ids]
        skill_snapshots = [
            {
                "skill_name": context.id,
                "normalized_content": normalize_skill_content(context.instructions),
            }
            for context in contexts
        ]
        problem = deepcopy((latest.get("problem").content if latest.get("problem") else {}) or {})
        selected = (
            (selection.get("selected") or [{}])[0]
            if isinstance(selection, dict)
            else {}
        )
        selected = selected if isinstance(selected, dict) else {}
        non_goals = problem.get("non_goals") or problem.get("out_of_scope") or []
        if isinstance(non_goals, str):
            non_goals = [non_goals]
        elif not isinstance(non_goals, list):
            non_goals = []
        constraints_reference = {
            "artifact_id": constraints_artifact.id,
            "schema_version": int((constraints_artifact.content or {}).get("schema_version") or 1),
        }
        legacy_plan_artifacts = [
            item
            for item in run.artifacts
            if item.source_step == "research_plan"
            and item.type not in {"scientific_world_state"}
        ]
        migration_artifact_id = ""
        policy_artifact_id = ""
        legacy_plan_artifact = None
        if legacy_plan_artifacts:
            migration_artifact_id = f"art_{uuid4().hex[:12]}"
            policy_artifact_id = f"art_{uuid4().hex[:12]}"
            legacy_plan_artifact = next(
                (item for item in reversed(legacy_plan_artifacts) if item.type == "plan"),
                next(
                    (
                        item
                        for item in reversed(legacy_plan_artifacts)
                        if item.type == "research_plan_candidate"
                    ),
                    legacy_plan_artifacts[-1],
                ),
            )
        parent_artifact_id = (
            migration_artifact_id
            if migration_artifact_id
            else latest.get("hypothesis_selection").id
            if latest.get("hypothesis_selection")
            else constraints_artifact.id
        )
        contract_snapshot = authoritative_plan_contract()
        review_runtime_contract = build_plan_review_runtime_contract(
            package.instructions,
            contract_snapshot,
            PLAN_REVIEW_FIXED_INSTRUCTIONS,
        )
        revision_runtime_contract = build_plan_revision_runtime_contract(
            package.instructions,
            contract_snapshot,
            PLAN_REVISION_FIXED_INSTRUCTIONS,
        )
        frozen = freeze_review_policy(
            loaded.content,
            policy_sha256=loaded.sha256,
            active_skill_ids=package.skill_ids,
            instruction_hashes=package.audit.get("skill_hashes") or {},
            skill_snapshots=skill_snapshots,
            runtime_instructions=package.instructions,
            review_runtime_contract_snapshot=review_runtime_contract,
            revision_runtime_contract_snapshot=revision_runtime_contract,
            authoritative_plan_contract_snapshot=contract_snapshot,
            canonical_contract_field_registry=CANONICAL_PLAN_CONTRACT_FIELDS,
            contract_field_aliases=FIELD_ALIAS_TO_CANONICAL,
            planner_fixed_review_instructions=PLAN_REVIEW_FIXED_INSTRUCTIONS,
            planner_fixed_revision_instructions=PLAN_REVISION_FIXED_INSTRUCTIONS,
            review_prompt_schema_snapshot=plan_review_schema_snapshot(),
            revision_prompt_schema_snapshot=plan_revision_schema_snapshot(),
            prompt_schema_version=PLAN_REVIEW_PROMPT_SCHEMA_VERSION,
            governance_semantic_version=GOVERNANCE_IMPLEMENTATION_SEMANTIC_VERSION,
            max_content_revisions=self.max_deepseek_plan_revision,
            problem_anchor={
                "original_question": run.problem_input,
                "selected_primary_claim": str(
                    selected.get("claim")
                    or (selected.get("idea_card") or {}).get("claim")
                    or ""
                ),
                "non_goals": deepcopy(non_goals),
                "frozen_constraints": deepcopy(constraints_artifact.content or {}),
                "structured_problem_artifact_id": latest.get("problem").id if latest.get("problem") else "",
                "hypothesis_selection_artifact_id": latest.get("hypothesis_selection").id if latest.get("hypothesis_selection") else "",
            },
            research_constraints_reference=constraints_reference,
            source_artifact_lineage={
                "parent_artifact_id": parent_artifact_id,
                "problem_artifact_id": latest.get("problem").id if latest.get("problem") else "",
                "hypothesis_selection_artifact_id": latest.get("hypothesis_selection").id if latest.get("hypothesis_selection") else "",
                "constraints_artifact_id": constraints_artifact.id,
                "migration_artifact_id": migration_artifact_id,
            },
        )
        if migration_artifact_id:
            legacy_lineage = [
                {
                    "artifact_id": item.id,
                    "artifact_type": item.type,
                    "artifact_content_sha256": canonical_sha256(item.content or {}),
                    "parent_artifact_id": item.parent_artifact_id or "",
                    "version": int(item.version),
                }
                for item in legacy_plan_artifacts
            ]
            migration_content = freeze_plan_governance_migration(
                legacy_plan_id=legacy_plan_artifact.id,
                legacy_plan_content=legacy_plan_artifact.content or {},
                legacy_lineage=legacy_lineage,
                frozen_policy_artifact_id=policy_artifact_id,
                frozen_policy_payload=frozen,
                migration_source_state={
                    "run_id": run.id,
                    "run_status": run.status,
                    "current_step": run.current_step,
                    "legacy_artifact_ids": [item.id for item in legacy_plan_artifacts],
                },
            )
            _, artifact = self.repository.add_artifacts_atomic(
                run_id,
                [
                    {
                        "artifact_id": migration_artifact_id,
                        "artifact_type": "plan_governance_migration",
                        "title": "Plan Governance Migration Boundary",
                        "content": migration_content,
                        "source_step": "research_plan",
                        "created_by": "Plan Review Governance",
                        "parent_artifact_id": legacy_plan_artifact.id,
                    },
                    {
                        "artifact_id": policy_artifact_id,
                        "artifact_type": "plan_review_policy",
                        "title": "Frozen Plan Review Policy",
                        "content": frozen,
                        "source_step": "research_plan",
                        "created_by": "Plan Review Governance",
                        "parent_artifact_id": migration_artifact_id,
                    },
                ],
            )
        else:
            artifact = self.repository.add_artifact(
                run_id,
                "plan_review_policy",
                "Frozen Plan Review Policy",
                frozen,
                "research_plan",
                "Plan Review Governance",
                parent_artifact_id=parent_artifact_id,
            )
        return artifact, frozen

    @staticmethod
    def _validate_plan_review_policy_lineage(run, artifact, content: dict, constraints_artifact) -> None:
        artifacts = {item.id: item for item in run.artifacts}
        lineage = content.get("source_artifact_lineage") or {}
        if artifact.parent_artifact_id != lineage.get("parent_artifact_id"):
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:parent_lineage"
            )
        typed = {
            "problem_artifact_id": "problem",
            "hypothesis_selection_artifact_id": "hypothesis_selection",
            "constraints_artifact_id": "research_constraints",
        }
        for field, artifact_type in typed.items():
            artifact_id = str(lineage.get(field) or "")
            if not artifact_id or artifact_id not in artifacts or artifacts[artifact_id].type != artifact_type:
                raise PlanReviewPolicyIntegrityError(
                    f"PLAN_REVIEW_POLICY_INTEGRITY:{field}"
                )
        constraints_reference = content.get("research_constraints_reference") or {}
        if (
            constraints_reference.get("artifact_id") != constraints_artifact.id
            or lineage.get("constraints_artifact_id") != constraints_artifact.id
        ):
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:constraints_lineage"
            )
        anchor = content.get("problem_anchor") or {}
        if (
            anchor.get("original_question") != run.problem_input
            or anchor.get("structured_problem_artifact_id") != lineage.get("problem_artifact_id")
            or anchor.get("hypothesis_selection_artifact_id")
            != lineage.get("hypothesis_selection_artifact_id")
            or anchor.get("frozen_constraints") != constraints_artifact.content
        ):
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:anchor_lineage"
            )
        migration_id = str(lineage.get("migration_artifact_id") or "")
        migrations = [
            item for item in run.artifacts if item.type == "plan_governance_migration"
        ]
        if migration_id:
            if (
                len(migrations) != 1
                or migration_id not in artifacts
                or artifacts[migration_id].type != "plan_governance_migration"
                or artifact.parent_artifact_id != migration_id
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:migration_lineage"
                )
            migration = artifacts[migration_id]
            migration_content = validate_plan_governance_migration(
                migration.content or {},
                frozen_policy_artifact_id=artifact.id,
                frozen_policy_payload=content,
            )
            legacy_plan_id = str(migration_content.get("legacy_plan_id") or "")
            legacy_plan = artifacts.get(legacy_plan_id)
            positions = {item.id: index for index, item in enumerate(run.artifacts)}
            if (
                legacy_plan is None
                or migration.parent_artifact_id != legacy_plan_id
                or canonical_sha256(legacy_plan.content or {})
                != migration_content.get("legacy_plan_hash")
                or not (
                    positions[legacy_plan_id]
                    < positions[migration_id]
                    < positions[artifact.id]
                )
            ):
                raise PlanReviewPolicyIntegrityError(
                    "PLAN_REVIEW_POLICY_INTEGRITY:migration_legacy_plan"
                )
            for row in migration_content.get("legacy_lineage") or []:
                source = artifacts.get(str(row.get("artifact_id") or ""))
                if (
                    source is None
                    or source.type != row.get("artifact_type")
                    or int(source.version) != int(row.get("version") or 0)
                    or (source.parent_artifact_id or "")
                    != str(row.get("parent_artifact_id") or "")
                    or canonical_sha256(source.content or {})
                    != row.get("artifact_content_sha256")
                    or positions[source.id] >= positions[migration_id]
                ):
                    raise PlanReviewPolicyIntegrityError(
                        "PLAN_REVIEW_POLICY_INTEGRITY:migration_legacy_lineage"
                    )
        elif migrations:
            raise PlanReviewPolicyIntegrityError(
                "PLAN_REVIEW_POLICY_INTEGRITY:unexpected_migration"
            )

    @staticmethod
    def _plan_candidate_content(artifacts, artifact_id: str) -> dict:
        if not artifact_id:
            return {}
        artifact = next(
            (
                item
                for item in artifacts
                if item.id == artifact_id and item.type == "research_plan_candidate"
            ),
            None,
        )
        return deepcopy((artifact.content or {}).get("normalized_plan") or {}) if artifact else {}

    @staticmethod
    def _new_plan_review_input_artifact_ids(
        artifacts, previous_review_artifact_id: str
    ) -> tuple[str, ...]:
        if not previous_review_artifact_id:
            return ()
        previous_index = next(
            (
                index
                for index, artifact in enumerate(artifacts)
                if artifact.id == previous_review_artifact_id
            ),
            None,
        )
        if previous_index is None:
            return ()
        return tuple(
            artifact.id
            for artifact in artifacts[previous_index + 1 :]
            if artifact.source_step != "research_plan"
        )

    @staticmethod
    def _plan_review_failure(exc: Exception, attempt: int) -> dict:
        message = str(exc)
        code = "MODEL_OUTPUT_VALIDATION_FAILURE"
        if "TIMEOUT" in message.upper() or "Timeout" in type(exc).__name__:
            code = "MODEL_CALL_TIMEOUT"
        elif "VALIDATION" not in message.upper() and isinstance(exc, RuntimeError):
            code = "MODEL_PROVIDER_FAILURE"
        return {
            "code": code,
            "message": message,
            "attempt": attempt,
            "scientific_state_mutated": False,
            "hypothesis_rejected": False,
        }

    @staticmethod
    def _research_plan_review_context(run, latest: dict, selection: dict,
                                      plan: dict, dataset_options: list[dict], *,
                                      frozen_policy: dict | None = None,
                                      issue_ledger: list[dict] | None = None,
                                      round_index: int = 1,
                                      changed_fields: tuple[str, ...] = (),
                                      new_evidence_artifact_ids: tuple[str, ...] = (),
                                      candidate_plan_id: str = "") -> dict:
        problem = compact_problem(latest["problem"].content)
        evidence_content = latest.get("evidence").content if latest.get("evidence") else {}
        references = evidence_content.get("core_references") or evidence_content.get("references") or []
        evidence, evidence_telemetry = select_units_bounded(
            [literature_card(card) for card in references],
            PromptContextBudget().max_reference_chars,
        )
        reasoning = latest.get("reasoning").content if latest.get("reasoning") else {}
        selected = (selection.get("selected") or [{}])[0] if isinstance(selection, dict) else selection
        profile = problem.get("dataset_profile") or {}
        selected_digest, selected_telemetry = selected_hypothesis_digest(selection, reasoning, budget=12_000)
        result = {
            "research_problem_summary": problem,
            "research_profile": profile,
            "selected_hypothesis": selected,
            "selected_hypothesis_rationale": selection.get("selection_reason", "") if isinstance(selection, dict) else "",
            "evidence_literature_compact_summary": evidence,
            "evidence_reasoning_critic_summary": {
                "selection_guidance": reasoning.get("selection_guidance", ""),
                "selected_hypothesis_evidence_digest": selected_digest,
            },
            "current_research_plan": plan,
            "current_candidate_plan_id": candidate_plan_id,
            "review_mode": "initial" if round_index == 1 else "revision",
            "review_round": round_index,
            "frozen_plan_review_policy": deepcopy(frozen_policy or {}),
            "previous_issue_ledger": deepcopy(issue_ledger or []),
            "open_validated_blockers": [
                deepcopy(item)
                for item in issue_ledger or []
                if item.get("severity") == "BLOCKER"
                and item.get("status") in {"OPEN", "REOPENED"}
                and item.get("validated_blocker") is True
            ],
            "closed_issue_ledger": [
                deepcopy(item)
                for item in issue_ledger or []
                if item.get("status") == "CLOSED"
            ],
            "artifact_chronology": {
                "changed_contract_fields": list(changed_fields),
                "new_input_artifact_ids": list(new_evidence_artifact_ids),
            },
            "authoritative_plan_contract": deepcopy(
                (frozen_policy or {}).get("authoritative_plan_contract_snapshot")
                or {}
            ),
            "dataset_profile": profile,
            "available_split_information": {
                "dataset_card": ((plan.get("dataset") or {}).get("card") or {}),
                "bound_split_contract": plan.get("split_contract") or ((plan.get("dataset") or {}).get("split_contract") or {}),
            },
            "experiment_capability_constraints": dataset_options,
            "repository_dataset_resource_constraints": {
                "run_constraints": run.constraints,
                "research_constraints_reference": {"artifact_id": run.research_constraints_artifact_id or "", "schema_version": 1},
                "dataset_options": dataset_options,
            },
            "context_policy": "compact_summaries_only_no_unbounded_artifact_injection",
        }
        latest_feedback = latest.get("revision")
        if latest_feedback and latest_feedback.content.get("research_context"):
            result["iteration_context"] = {
                k: deepcopy(latest_feedback.content.get(k)) for k in
                ("research_context", "selected_direction", "scientific_synthesis", "implementation_reference")
            }
        result["context_telemetry"] = context_telemetry(
            [("research_problem_summary", problem), ("literature", evidence),
             ("selected_hypothesis_digest", selected_digest), ("current_research_plan", plan)],
            PromptContextBudget().max_total_chars,
        )
        result["context_telemetry"]["literature"] = evidence_telemetry
        result["context_telemetry"]["selected_hypothesis_digest"] = selected_telemetry
        return result

    def _has_locked_output(self, artifacts, step_id: str) -> bool:
        has_locked = any(
            artifact.locked and artifact.source_step == step_id for artifact in artifacts
        )
        if not has_locked:
            return False
        if step_id == "experiment_task" and self._formal_validation_pending(artifacts):
            return False
        latest = self._latest_by_type(artifacts)
        if step_id == "feedback_revision":
            result = latest.get("experiment_result")
            return result is not None and any(
                artifact.type == "revision"
                and artifact.locked
                and artifact.source_step == step_id
                and artifact.parent_artifact_id == result.id
                for artifact in artifacts
            )
        if step_id != "evidence_reasoning":
            return has_locked
        reasoning = latest.get("reasoning")
        hypothesis = latest.get("hypothesis")
        try:
            self._require_evidence_reasoned_hypothesis_selection(latest)
        except ValueError:
            return False
        # A locked reasoning Artifact only locks its own hypothesis round.  A
        # later append-only revision must still evaluate the newly created
        # hypothesis rather than being silently skipped by historic evidence.
        return bool(
            reasoning is not None
            and reasoning.locked
            and reasoning.source_step == "evidence_reasoning"
            and hypothesis is not None
            and reasoning.parent_artifact_id == hypothesis.id
        )

    @staticmethod
    def _is_engineering_failure(result: dict) -> bool:
        return str((result or {}).get("status") or "").lower() == "failed"

    @staticmethod
    def _formal_validation_pending(artifacts) -> bool:
        latest_evidence = next(
            (item for item in reversed(artifacts) if item.type == "result_evidence"),
            None,
        )
        if not latest_evidence:
            return False
        content = latest_evidence.content or {}
        if content.get("stage") != "small_scale" or content.get("status") not in {
            "positive_stable", "inconclusive", "negative"
        }:
            return False
        latest_plan = next(
            (item for item in reversed(artifacts) if item.type == "plan"), None
        )
        return not any(
            item.type == "experiment_task"
            and (latest_plan is None or item.parent_artifact_id == latest_plan.id)
            and (item.content or {}).get("phase2_protocol", {}).get("stage")
            == "formal_validation"
            for item in artifacts
        )

    def _trace(
        self,
        run_id: str,
        step_id: str,
        actor: str,
        message: str,
        input_summary: dict,
        output_summary: dict,
        tool_calls: list[dict] | None = None,
        skill_calls: list[dict] | None = None,
    ):
        trace_calls = tool_calls if tool_calls is not None else [
            {"provider": self.llm_provider.mode, "method": "generate_json"}
        ]
        call_metadata = {}
        if any(call.get("method") == "generate_json" for call in trace_calls):
            consume_metadata = getattr(self.llm_provider, "consume_call_metadata", None)
            if callable(consume_metadata):
                call_metadata = consume_metadata()
            if call_metadata:
                trace_calls = [
                    {**call, **call_metadata}
                    if call.get("method") == "generate_json"
                    else call
                    for call in trace_calls
                ]
        self.repository.append_event(
            run_id,
            step_id,
            actor,
            message,
            data={"llm_call": call_metadata} if call_metadata else {},
            input_summary=input_summary,
            output_summary=output_summary,
            tool_calls=trace_calls + (skill_calls or []),
            provider_mode=self.llm_provider.mode,
            fallback_used=self.llm_provider.fallback,
            fallback_reason="Mock LLM development fallback." if self.llm_provider.fallback else "",
        )
