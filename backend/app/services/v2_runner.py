from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.baseline import BaselineReproducer, BaselineReproductionRequest
from backend.app.experiment.general_planner import (
    GeneralRepositoryExperimentContract,
    GeneralRepositoryImplementationPlanner,
    PlannedRepositoryExperimentAdapter,
)
from backend.app.experiment.workspace_adapter import WorkspaceExperimentAdapter
from backend.app.models.gateway import ModelGateway
from backend.app.research.actions import ResearchOperator
from backend.app.research.profiles import ProblemProfile
from backend.app.research.protocol import ExperimentProtocol
from backend.app.services.v2_sessions import ResearchSessionService, SessionTransition
from backend.app.workspace import RepositoryWorkspace, WorktreeManager


class SupportedRepositoryExecutor(BaseModel):
    """Repository-owned commands required for a real V2 run.

    The runner deliberately does not guess training commands or fabricate result
    records.  A repository opts in with this tracked manifest, so every command
    and result path remains auditable and reproducible.
    """

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    protocol: ExperimentProtocol
    baseline_command: list[str] = Field(min_length=1)
    baseline_result_path: str = Field(min_length=1)
    static_commands: list[list[str]] = Field(default_factory=list)
    smoke_commands: list[list[str]] = Field(default_factory=list)
    formal_command: list[str] = Field(min_length=1)
    result_path: str = Field(min_length=1)
    environment: dict[str, Any] = Field(default_factory=dict)


class V2ResearchRunner:
    """Thin bridge from a pending ResearchAction to an audited local executor."""

    MANIFEST = ".ai-scientist-v2.json"
    EXECUTABLE_OPERATORS = {
        ResearchOperator.REPRODUCE_BASELINE,
        ResearchOperator.RUN_EXPERIMENT,
        ResearchOperator.RUN_ABLATION,
        ResearchOperator.RUN_REPLICATION,
        ResearchOperator.RUN_ROBUSTNESS,
    }

    def __init__(self, sessions: ResearchSessionService, gateway: ModelGateway, data_dir: str) -> None:
        self.sessions = sessions
        self.gateway = gateway
        self.data_dir = Path(data_dir).resolve()

    def run_next(self, session_id: str) -> SessionTransition:
        state = self.sessions.get(session_id)
        if state.current_action is None:
            transition = self.sessions.start(session_id)
            state = transition.state
        action = state.current_action
        if action is None:
            return self.sessions.start(session_id)
        if action.operator not in self.EXECUTABLE_OPERATORS:
            return self._wait(session_id, action.operator, "NO_SUPPORTED_EXECUTOR")

        try:
            executor = self._load_executor(state.problem.repository)
            if executor.task != state.problem.task or executor.protocol.dataset != state.problem.dataset:
                return self._wait(session_id, action.operator, "EXECUTOR_PROBLEM_PROTOCOL_MISMATCH")
            if action.operator == ResearchOperator.REPRODUCE_BASELINE:
                return self._reproduce_baseline(session_id, state, executor)
            if state.baseline is None or not state.baseline.can_be_comparison_denominator:
                return self._wait(session_id, action.operator, "VALIDATED_BASELINE_REQUIRED")
            return self._run_experiment(session_id, state, executor)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            return self._wait(session_id, action.operator, f"EXECUTOR_UNAVAILABLE:{type(exc).__name__}:{exc}")

    def prepare_problem(self, problem: ProblemProfile) -> ProblemProfile:
        """Bind a local repository session to its tracked executor protocol.

        The UI supplies the question and budget; the repository remains the
        authority for task, dataset identity, and formal commands.
        """
        try:
            executor = self._load_executor(problem.repository)
        except (FileNotFoundError, ValueError, OSError):
            return problem
        return problem.model_copy(update={
            "task": executor.task,
            "dataset": executor.protocol.dataset,
        })

    def _reproduce_baseline(self, session_id, state, executor) -> SessionTransition:
        workspace = RepositoryWorkspace(state.problem.repository, allowed_executables={Path(executor.baseline_command[0]).name})
        baseline = BaselineReproducer(workspace).reproduce_and_validate(
            BaselineReproductionRequest(
                repository=state.problem.repository,
                commit=workspace.git.head(),
                task=state.problem.task,
                entrypoint=executor.entrypoint,
                protocol=executor.protocol,
                command=executor.baseline_command,
                result_path=executor.baseline_result_path,
                environment=executor.environment,
            )
        )
        if not baseline.can_be_comparison_denominator:
            return self._wait(session_id, state.current_action.operator, f"BASELINE_NOT_VALIDATED:{baseline.validation_reason}")
        return self.sessions.continue_session(session_id, baseline=baseline)

    def _run_experiment(self, session_id, state, executor) -> SessionTransition:
        action = state.current_action
        assert action is not None and state.baseline is not None
        branch = state.frontier.get(action.branch_id or "")
        sequence = len(state.experiments) + 1
        result = PlannedRepositoryExperimentAdapter(
            GeneralRepositoryImplementationPlanner(self.gateway),
            WorkspaceExperimentAdapter(WorktreeManager(
                state.problem.repository,
                self.data_dir / "v2-worktrees" / session_id,
            )),
        ).execute(GeneralRepositoryExperimentContract(
            experiment_id=f"{session_id}_exp_{sequence}",
            action=action,
            branch=branch,
            worktree_branch=f"v2/{session_id}-exp-{sequence}",
            repository=state.problem.repository,
            base_commit=state.baseline.commit,
            protocol=executor.protocol,
            baseline_protocol=state.baseline.protocol,
            baseline_metrics=state.baseline.local_metrics,
            config={"operator": action.operator, "runner": "V2ResearchRunner"},
            static_commands=executor.static_commands,
            smoke_commands=executor.smoke_commands,
            formal_command=executor.formal_command,
            result_path=executor.result_path,
            environment=executor.environment,
            commit_message=f"v2 experiment: {action.operator}",
        ))
        return self.sessions.continue_session(session_id, experiment=result.record)

    def _wait(self, session_id: str, operator: ResearchOperator, reason: str) -> SessionTransition:
        state = self.sessions.stop(session_id, f"WAITING_FOR_SUPPORTED_EXECUTOR:{operator}:{reason}")
        return SessionTransition(state=state, action=None)

    def _load_executor(self, repository: str) -> SupportedRepositoryExecutor:
        root = Path(repository).resolve()
        manifest = root / self.MANIFEST
        if not manifest.is_file():
            raise FileNotFoundError(f"{self.MANIFEST} is required in the repository root")
        return SupportedRepositoryExecutor.model_validate(json.loads(manifest.read_text(encoding="utf-8")))
