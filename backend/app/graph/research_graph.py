from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.controller.research_loop import ExperimentExecutor, ResearchIteration, ResearchLoop
from backend.app.research.actions import ResearchAction
from backend.app.research.experiment import ExperimentRecord
from backend.app.state.research import ResearchState
from backend.app.storage.json_store import JsonStore
from backend.app.storage.v2 import V2Stores


class ResearchGraphStatus(StrEnum):
    READY = "ready"
    ACTION_PENDING = "action_pending"
    STOPPED = "stopped"
    FAILED = "failed"


class GraphCheckpoint(BaseModel):
    """Small runtime checkpoint; scientific records live in their dedicated stores."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: ResearchGraphStatus
    current_action: ResearchAction | None = None
    active_branch_ids: list[str] = Field(default_factory=list)
    state_ref: str
    frontier_ref: str
    iteration: int = Field(ge=0)
    budget_summary: dict[str, float | int]
    interrupt_state: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class _CheckpointStore:
    filename = "v2-graph-checkpoints.json"

    def __init__(self, data_dir: str) -> None:
        self.store = JsonStore(data_dir)

    def save(self, checkpoint: GraphCheckpoint) -> None:
        values = self.store.read(self.filename)
        values[checkpoint.session_id] = checkpoint.model_dump(mode="json")
        self.store.write(self.filename, values)

    def get(self, session_id: str) -> GraphCheckpoint:
        values = self.store.read(self.filename)
        if session_id not in values:
            raise KeyError(session_id)
        return GraphCheckpoint.model_validate(values[session_id])


PendingRecovery = Callable[[ResearchAction, ResearchState], ExperimentRecord | None]


class ResearchGraph:
    """Beta graph wrapper providing condition, cycle, checkpoint, and resume semantics."""

    def __init__(
        self,
        data_dir: str,
        *,
        loop: ResearchLoop | None = None,
    ) -> None:
        self.loop = loop or ResearchLoop()
        self.stores = V2Stores(data_dir)
        self.checkpoints = _CheckpointStore(data_dir)

    def start(self, state: ResearchState) -> GraphCheckpoint:
        self.stores.persist(state)
        checkpoint = self._checkpoint(state, ResearchGraphStatus.READY)
        self.checkpoints.save(checkpoint)
        return checkpoint

    def step(
        self,
        session_id: str,
        executor: ExperimentExecutor,
        *,
        recover_pending: PendingRecovery | None = None,
    ) -> ResearchIteration:
        checkpoint = self.checkpoints.get(session_id)
        state = self.stores.states.get(session_id)
        if checkpoint.status == ResearchGraphStatus.ACTION_PENDING:
            if checkpoint.current_action is None or recover_pending is None:
                raise RuntimeError("GRAPH_ACTION_RECOVERY_REQUIRED")
            recovered = recover_pending(checkpoint.current_action, state)
            if recovered is None:
                raise RuntimeError("GRAPH_PENDING_ACTION_NOT_RECOVERED")
            updated = self.loop.updater.apply_experiment(
                state, checkpoint.current_action, recovered
            )
            iteration = ResearchIteration(
                action=checkpoint.current_action,
                experiment=recovered,
                state=updated,
            )
        else:
            action = self.loop.controller.next_action(state)
            active = state.model_copy(update={"current_action": action})
            self.stores.states.save(active)
            self.checkpoints.save(
                self._checkpoint(
                    active,
                    ResearchGraphStatus.ACTION_PENDING,
                    current_action=action,
                )
            )
            experiment = executor(action, active)
            updated = self.loop.updater.apply_experiment(
                active, action, experiment
            )
            iteration = ResearchIteration(
                action=action,
                experiment=experiment,
                state=updated,
            )
        self.stores.persist(iteration.state)
        self.checkpoints.save(
            self._checkpoint(iteration.state, ResearchGraphStatus.READY)
        )
        return iteration

    def run(
        self,
        session_id: str,
        executor: ExperimentExecutor,
        *,
        max_iterations: int,
        recover_pending: PendingRecovery | None = None,
    ) -> list[ResearchIteration]:
        if max_iterations <= 0:
            raise ValueError("GRAPH_MAX_ITERATIONS_MUST_BE_POSITIVE")
        completed: list[ResearchIteration] = []
        for _ in range(max_iterations):
            try:
                completed.append(
                    self.step(
                        session_id,
                        executor,
                        recover_pending=recover_pending,
                    )
                )
            except StopIteration as exc:
                state = self.stores.states.get(session_id).model_copy(
                    update={"stopped": True, "stop_reason": str(exc)}
                )
                self.stores.persist(state)
                self.checkpoints.save(
                    self._checkpoint(state, ResearchGraphStatus.STOPPED)
                )
                break
        return completed

    def resume(
        self,
        session_id: str,
        executor: ExperimentExecutor,
        *,
        max_iterations: int,
        recover_pending: PendingRecovery | None = None,
    ) -> list[ResearchIteration]:
        self.checkpoints.get(session_id)
        return self.run(
            session_id,
            executor,
            max_iterations=max_iterations,
            recover_pending=recover_pending,
        )

    @staticmethod
    def _checkpoint(
        state: ResearchState,
        status: ResearchGraphStatus,
        *,
        current_action: ResearchAction | None = None,
    ) -> GraphCheckpoint:
        active = [
            branch.id
            for branch in state.frontier.selectable()
        ]
        return GraphCheckpoint(
            session_id=state.session_id,
            status=status,
            current_action=current_action,
            active_branch_ids=active,
            state_ref=state.session_id,
            frontier_ref=state.session_id,
            iteration=state.iteration,
            budget_summary={
                "experiments_used": state.budget.experiments_used,
                "experiment_limit": state.budget.experiment_limit,
                "compute_minutes_used": state.budget.compute_minutes_used,
                "compute_minutes_limit": state.budget.compute_minutes_limit,
                "model_calls_used": state.budget.model_calls_used,
                "model_call_limit": state.budget.model_call_limit,
            },
        )
