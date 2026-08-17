from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.experiment.workspace_adapter import (
    RepositoryExperimentContract,
    WorkspaceExperimentAdapter,
)
from backend.app.models.gateway import ModelGateway
from backend.app.research.actions import ResearchAction
from backend.app.research.experiment import ExperimentRecord
from backend.app.research.frontier import ResearchBranch
from backend.app.research.protocol import ExperimentProtocol
from backend.app.workspace.git import GitRepository


class RepositoryInspectionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[str] = Field(min_length=1, max_length=20)
    rationale: str = Field(min_length=1)


class PlannedFileEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    replacement_content: str
    rationale: str = Field(min_length=1)


class RepositoryImplementationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    edits: list[PlannedFileEdit] = Field(min_length=1, max_length=8)
    expected_effect: str = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)


class RepositoryPlanningTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_commit: str
    tracked_file_count: int
    inspection_rationale: str
    inspected_files: list[str]
    plan_summary: str
    edited_files: list[str]
    edit_rationales: dict[str, str]
    safety_validated: bool
    phases: list[str] = Field(
        default_factory=lambda: [
            "repository_inspection",
            "structured_implementation_plan",
            "safety_path_validation",
            "edit_existing_repository",
            "static_validation",
            "smoke",
            "formal_experiment",
            "experiment_record",
        ]
    )


class PlannedExperimentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace: RepositoryPlanningTrace
    record: ExperimentRecord


class GeneralRepositoryExperimentContract(BaseModel):
    """Autonomous path: callers provide scientific intent, never file contents."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(min_length=1)
    action: ResearchAction
    branch: ResearchBranch
    worktree_branch: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    base_commit: str = Field(min_length=1)
    protocol: ExperimentProtocol
    baseline_protocol: ExperimentProtocol
    baseline_metrics: dict[str, float]
    config: dict[str, Any]
    static_commands: list[list[str]] = Field(default_factory=list)
    smoke_commands: list[list[str]] = Field(default_factory=list)
    formal_command: list[str] = Field(min_length=1)
    result_path: str = Field(min_length=1)
    environment: dict[str, Any]
    commit_message: str = Field(min_length=1)
    cleanup_worktree: bool = True

    @model_validator(mode="after")
    def scientific_contract_is_consistent(self):
        if self.action.branch_id != self.branch.id:
            raise ValueError("GENERAL_PLANNER_ACTION_BRANCH_MISMATCH")
        expected = {item.name for item in self.protocol.metrics}
        if set(self.baseline_metrics) != expected:
            raise ValueError("GENERAL_PLANNER_BASELINE_METRICS_MISMATCH")
        return self


class GeneralRepositoryImplementationPlanner:
    MAX_TRACKED_FILES = 4000
    MAX_SOURCE_CHARS = 250_000
    MAX_TOTAL_EDIT_CHARS = 500_000
    FORBIDDEN_NAMES = {
        ".env",
        ".git",
        "credentials",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
    }

    def __init__(self, gateway: ModelGateway) -> None:
        self.gateway = gateway

    def plan(
        self,
        repository: GitRepository,
        base_commit: str,
        action: ResearchAction,
        branch: ResearchBranch,
        execution_context: dict[str, Any] | None = None,
    ) -> tuple[RepositoryImplementationPlan, RepositoryPlanningTrace]:
        tracked = repository.tracked_files_at(base_commit)
        if len(tracked) > self.MAX_TRACKED_FILES:
            raise ValueError("GENERAL_PLANNER_REPOSITORY_TOO_LARGE")
        inspection = self.gateway.invoke_structured(
            "v2.repository.inspect",
            [{"role": "user", "content": "Select the minimum existing source files needed to test this mechanism."}],
            RepositoryInspectionPlan,
            context={
                "action": action.model_dump(mode="json"),
                "branch": branch.model_dump(mode="json"),
                "tracked_files": tracked,
            },
        )
        selected = list(dict.fromkeys(self._validate_path(path, tracked) for path in inspection.files))
        sources: dict[str, str] = {}
        total = 0
        for path in selected:
            source = repository.read_text_at(base_commit, path)
            total += len(source)
            if total > self.MAX_SOURCE_CHARS:
                raise ValueError("GENERAL_PLANNER_INSPECTION_TOO_LARGE")
            sources[path] = source

        plan_context = {
            "action": action.model_dump(mode="json"),
            "hypothesis": branch.hypothesis,
            "mechanism": branch.mechanism,
            "minimal_experiment": branch.minimal_experiment,
            "selected_sources": sources,
            "execution_contract": execution_context or {},
        }
        operator_instruction = {
            "RUN_ABLATION": (
                "This is an ablation. Remove or disable only the currently implemented proposed "
                "mechanism so the fixed formal command exercises the ablated control. The replacement "
                "must differ from the current source and preserve protocol/evaluation code."
            ),
            "INVESTIGATE_FAILURE": (
                "Change only implementation code needed to test the diagnosed failure explanation."
            ),
            "REFINE_HYPOTHESIS": (
                "Implement the smallest source change that distinguishes the refined hypothesis."
            ),
        }.get(
            action.operator,
            "Implement the smallest source change that directly tests the selected hypothesis.",
        )
        repair_used = False
        try:
            plan = self.gateway.invoke_structured(
                "v2.repository.implementation_plan",
                [{"role": "user", "content": operator_instruction + " Produce complete full-file replacements and preserve unrelated behavior."}],
                RepositoryImplementationPlan,
                context=plan_context,
            )
        except Exception as exc:
            # One bounded repair remains model-driven and schema-gated. It never
            # falls back to caller-supplied edited contents.
            plan = self.gateway.invoke_structured(
                "v2.repository.implementation_plan",
                [
                    {
                        "role": "user",
                        "content": (
                            "Repair the previous implementation plan so it matches the schema, "
                            "edits only selected existing files, and contains complete file replacements."
                        ),
                    }
                ],
                RepositoryImplementationPlan,
                context={
                    **plan_context,
                    "repair_reason": type(exc).__name__,
                    "repair_attempt": 1,
                },
            )
            repair_used = True
        issue = self._plan_issue(plan, selected, sources)
        if issue and not repair_used:
            plan = self.gateway.invoke_structured(
                "v2.repository.implementation_plan",
                [
                    {
                        "role": "user",
                        "content": (
                            "Repair the plan. It must make a real, minimal source change, use only "
                            "selected existing files, and work with the fixed execution commands. "
                            + operator_instruction
                        ),
                    }
                ],
                RepositoryImplementationPlan,
                context={
                    **plan_context,
                    "previous_plan": plan.model_dump(mode="json"),
                    "repair_reason": issue,
                    "repair_attempt": 1,
                },
            )
            repair_used = True
            issue = self._plan_issue(plan, selected, sources)
        if issue:
            raise ValueError(f"GENERAL_PLANNER_INVALID_PLAN:{issue}")
        edited: list[str] = []
        rationales: dict[str, str] = {}
        edit_size = 0
        for edit in plan.edits:
            path = self._validate_path(edit.path, selected)
            if path in edited:
                raise ValueError(f"GENERAL_PLANNER_DUPLICATE_EDIT:{path}")
            edit_size += len(edit.replacement_content)
            if edit_size > self.MAX_TOTAL_EDIT_CHARS:
                raise ValueError("GENERAL_PLANNER_EDITS_TOO_LARGE")
            edited.append(path)
            rationales[path] = edit.rationale
        trace = RepositoryPlanningTrace(
            base_commit=base_commit,
            tracked_file_count=len(tracked),
            inspection_rationale=inspection.rationale,
            inspected_files=selected,
            plan_summary=plan.summary,
            edited_files=edited,
            edit_rationales=rationales,
            safety_validated=True,
        )
        return plan, trace

    def _plan_issue(
        self,
        plan: RepositoryImplementationPlan,
        selected: list[str],
        sources: dict[str, str],
    ) -> str:
        paths: list[str] = []
        total = 0
        try:
            for edit in plan.edits:
                path = self._validate_path(edit.path, selected)
                if path in paths:
                    return f"duplicate_edit:{path}"
                paths.append(path)
                total += len(edit.replacement_content)
        except ValueError as exc:
            return str(exc)
        if total > self.MAX_TOTAL_EDIT_CHARS:
            return "edits_too_large"
        if not any(
            edit.replacement_content
            != sources.get(edit.path.replace("\\", "/").strip())
            for edit in plan.edits
        ):
            return "no_effect_all_replacements_match_base_sources"
        return ""

    def _validate_path(self, raw: str, allowed: list[str]) -> str:
        path = raw.replace("\\", "/").strip()
        parsed = PurePosixPath(path)
        lowered = {part.lower() for part in parsed.parts}
        if (
            not path
            or parsed.is_absolute()
            or ".." in parsed.parts
            or lowered & self.FORBIDDEN_NAMES
            or path not in allowed
        ):
            raise ValueError(f"GENERAL_PLANNER_UNSAFE_OR_UNKNOWN_PATH:{raw}")
        return path


class PlannedRepositoryExperimentAdapter:
    def __init__(
        self,
        planner: GeneralRepositoryImplementationPlanner,
        executor: WorkspaceExperimentAdapter,
    ) -> None:
        self.planner = planner
        self.executor = executor

    def execute(self, contract: GeneralRepositoryExperimentContract) -> PlannedExperimentResult:
        repository = GitRepository(contract.repository)
        plan, trace = self.planner.plan(
            repository,
            contract.base_commit,
            contract.action,
            contract.branch,
            execution_context={
                "static_commands": contract.static_commands,
                "smoke_commands": contract.smoke_commands,
                "formal_command": contract.formal_command,
                "result_path": contract.result_path,
                "constraint": (
                    "Commands are fixed and will not receive new flags. The default execution path "
                    "after editing must exercise the planned variant or ablation. Do not modify tests, "
                    "evaluation data, metric computation, protocol fingerprints, or result reporting."
                ),
            },
        )
        record = self.executor.execute(
            RepositoryExperimentContract(
                experiment_id=contract.experiment_id,
                branch_id=contract.branch.id,
                worktree_branch=contract.worktree_branch,
                purpose=f"{contract.action.operator}: {contract.action.reason}",
                repository=contract.repository,
                base_commit=contract.base_commit,
                protocol=contract.protocol,
                baseline_protocol=contract.baseline_protocol,
                baseline_metrics=contract.baseline_metrics,
                config=contract.config,
                implementation_files={edit.path: edit.replacement_content for edit in plan.edits},
                static_commands=contract.static_commands,
                smoke_commands=contract.smoke_commands,
                formal_command=contract.formal_command,
                result_path=contract.result_path,
                environment=contract.environment,
                commit_message=contract.commit_message,
                cleanup_worktree=contract.cleanup_worktree,
            )
        )
        return PlannedExperimentResult(trace=trace, record=record)
