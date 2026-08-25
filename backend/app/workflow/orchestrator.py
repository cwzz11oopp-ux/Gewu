from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread
import time

from backend.app.providers.llm import LLMRequestCancelled
from backend.app.storage.repository import Repository
from backend.app.workflow.artifact_lineage import result_for_experiment_task
from backend.app.workflow.steps import ORDER
from backend.app.workflow.scientific_stability import failure_state_for
from backend.app.workflow.plan_review_governance import is_plan_governance_accepted
from backend.app.workflow.policies import feedback_requires_follow_up


MAX_FEEDBACK_ITERATIONS = 4
AWAIT_HYPOTHESIS_SELECTION = "await_hypothesis_selection"
AWAIT_EXPERIMENT_RETRY = "await_experiment_retry"
DEFAULT_PROVIDER_RETRY_LIMIT = 3
DEFAULT_PROVIDER_RETRY_BACKOFF_SECONDS = 1.0


class WorkflowOrchestrator:
    """Runs durable pipelines outside the browser and resumes them after restarts."""

    def __init__(
        self,
        repository: Repository,
        engine: Callable[[], object],
        config_sync: Callable[[], bool] | None = None,
        provider_retry_limit: int = DEFAULT_PROVIDER_RETRY_LIMIT,
        provider_retry_backoff_seconds: float = DEFAULT_PROVIDER_RETRY_BACKOFF_SECONDS,
    ) -> None:
        self.repository = repository
        self._engine = engine
        self._config_sync = config_sync
        self.provider_retry_limit = max(0, int(provider_retry_limit))
        self.provider_retry_backoff_seconds = max(
            0.0, float(provider_retry_backoff_seconds)
        )
        self._threads: dict[str, Thread] = {}
        self._guard = Lock()

    def start(self, run_id: str):
        # Admission boundary: pick up a model-config change saved by another
        # backend process before this run builds providers or calls any model.
        if self._config_sync is not None:
            self._config_sync()
        engine = self._engine()
        run = self.repository.get_run(run_id)
        recovered_plan_review = False
        if run.status == "POLICY_INTEGRITY_REQUIRED":
            return self.repository.update_workflow_state(
                run_id, automatic=False, stop_requested=False
            )
        if run.status == "NEEDS_PLAN_REVISION":
            recover = getattr(engine, "recover_plan_review_for_continue", None)
            if not callable(recover) or not recover(run_id):
                return self.repository.update_workflow_state(
                    run_id, automatic=False, stop_requested=False
                )
            run = self.repository.get_run(run_id)
            recovered_plan_review = True
        # Admission is an external side effect.  A durable constraints artifact
        # means it already passed, so resume/continue must not call providers again.
        if not getattr(run, "research_constraints_artifact_id", ""):
            preflight = getattr(engine, "preflight_run", None)
            if callable(preflight):
                result = preflight(run_id)
                if result.get("blocking"):
                    self.repository.update_workflow_state(run_id, status="preflight_failed", automatic=False)
                    return self.repository.get_run(run_id)
            ensure_constraints = getattr(engine, "_ensure_research_constraints", None)
            if callable(ensure_constraints):
                ensure_constraints(run_id)
        run = self.repository.get_run(run_id)
        if self._should_continue_failed_experiment(run):
            # A failed engineering attempt is a recovery point, not a scientific
            # result.  Make this existing step runnable again while retaining its
            # task, bundle, failed Result, and diagnosis lineage.
            self.repository.update_step_state(
                run_id, "experiment_run_analysis", "pending", error=None
            )
            run = self.repository.get_run(run_id)
            task = self._task_for_plan(
                run.artifacts, self._latest_by_type(run.artifacts)["plan"]
            )
            experiment_id = str((task.content or {}).get("experiment_id") or "")
            if experiment_id and experiment_id not in run.force_new_attempt_experiment_ids:
                run.force_new_attempt_experiment_ids.append(experiment_id)
                self.repository.save_run(run)
            run = self.repository.get_run(run_id)
        current_step = next(
            (item for item in run.steps if item.id == run.current_step),
            None,
        )
        if (
            current_step
            and current_step.status == "interrupted"
            and isinstance(current_step.error, dict)
            and (
                current_step.error.get("recoverable") is True
                or current_step.error.get("code") == "PIPELINE_STOPPED"
            )
        ):
            if (
                current_step.error.get("user_action_required") is True
                and not recovered_plan_review
            ):
                return self.repository.update_workflow_state(
                    run_id, automatic=False, stop_requested=False
                )
            self.repository.update_step_state(
                run_id, current_step.id, "pending", error=None
            )
            # A user selecting Continue starts a new bounded recovery cycle.
            self.repository.update_provider_retry_state(
                run_id, current_step.id, None
            )
        self.repository.update_workflow_state(
            run_id,
            status="queued",
            automatic=True,
            stop_requested=False,
        )
        with self._guard:
            existing = self._threads.get(run_id)
            if existing and existing.is_alive():
                return self.repository.get_run(run_id)
            thread = Thread(
                target=self._drive,
                args=(run_id,),
                name=f"workflow-{run_id}",
                daemon=True,
            )
            self._threads[run_id] = thread
            thread.start()
        return self.repository.get_run(run_id)

    def stop(self, run_id: str):
        run = self.repository.get_run(run_id)
        if run.status in {"completed", "failed", "paused"}:
            return run
        with self._guard:
            worker = self._threads.get(run_id)
            worker_alive = bool(worker and worker.is_alive())
        if not worker_alive:
            current_step = next(
                (step for step in run.steps if step.id == run.current_step),
                None,
            )
            if current_step and current_step.status == "failed":
                status = "FAILED_SYSTEM"
            else:
                if current_step and current_step.status == "running":
                    engine = self._engine()
                    cancel = getattr(engine, "cancel", None)
                    if callable(cancel):
                        cancel(run_id)
                    self.repository.update_step_state(
                        run_id,
                        run.current_step,
                        "interrupted",
                        error={
                            "code": "PIPELINE_STOPPED",
                            "message": "The user stopped this run.",
                        },
                    )
                status = "paused"
            return self.repository.update_workflow_state(
                run_id,
                status=status,
                automatic=False,
                stop_requested=True,
            )
        updated = self.repository.update_workflow_state(
            run_id,
            status="stopping",
            stop_requested=True,
        )
        engine = self._engine()
        cancel = getattr(engine, "cancel", None)
        if callable(cancel):
            cancel(run_id)
        return updated

    def recover(self) -> list[str]:
        run_ids = self.repository.reconcile_interrupted_runs()
        for run_id in run_ids:
            self.start(run_id)
        return run_ids

    def mark_for_shutdown(self) -> None:
        for run in self.repository.list_runs():
            if run.status in {"queued", "running", "stopping"}:
                self.repository.update_workflow_state(run.id, status="interrupted")

    def _drive(self, run_id: str) -> None:
        engine = self._engine()
        try:
            resumable = self.repository.get_run(run_id)
            if resumable.status in {"NEEDS_PLAN_REVISION", "POLICY_INTEGRITY_REQUIRED"}:
                self.repository.update_workflow_state(
                    run_id, automatic=False, stop_requested=False
                )
                return
            if resumable.status == "RECOVERABLE_PROVIDER_ERROR":
                interrupted = next(
                    (item for item in resumable.steps if item.id == resumable.current_step),
                    None,
                )
                if (
                    interrupted
                    and interrupted.status == "interrupted"
                    and isinstance(interrupted.error, dict)
                    and interrupted.error.get("recoverable") is True
                    and interrupted.error.get("user_action_required") is not True
                ):
                    # Entering _drive is an explicit provider-recovery request.
                    # Clear only that operational checkpoint; scientific/user
                    # action states remain forbidden from automatic execution.
                    self.repository.update_step_state(
                        run_id, interrupted.id, "pending", error=None
                    )
            self.repository.update_workflow_state(
                run_id, status="running", automatic=True, stop_requested=False
            )
            while True:
                run = self.repository.get_run(run_id)
                if run.stop_requested:
                    self.repository.update_workflow_state(
                        run_id, status="paused", automatic=False
                    )
                    return
                engine = self._engine()
                step_id = self._next_step(
                    run,
                    max_feedback_iterations=getattr(
                        engine,
                        "max_feedback_iterations",
                        MAX_FEEDBACK_ITERATIONS,
                    ),
                    automatic_execution=True,
                )
                if step_id == AWAIT_HYPOTHESIS_SELECTION:
                    if run.automatic:
                        selection_result = engine.auto_select_hypothesis(run_id)
                        status = getattr(selection_result, "status", "")
                        if status == "hypothesis_revision_required":
                            return
                        if status == "paused":
                            # No candidate cleared the auto-select threshold; the
                            # engine already paused for manual selection.
                            return
                        continue
                    self.repository.update_workflow_state(
                        run_id,
                        status="paused",
                        current_step="evidence_reasoning",
                        automatic=False,
                        stop_requested=False,
                    )
                    self.repository.append_event(
                        run_id,
                        "evidence_reasoning",
                        "Workflow Orchestrator",
                        "Evidence reasoning completed; waiting for the user to select a hypothesis.",
                        data={"status": "awaiting_user_selection"},
                        output_summary={"recoverable": True, "user_action_required": True},
                    )
                    return
                if step_id == AWAIT_EXPERIMENT_RETRY:
                    self.repository.update_workflow_state(
                        run_id,
                        status="paused",
                        current_step="experiment_run_analysis",
                        automatic=False,
                        stop_requested=False,
                    )
                    self.repository.append_event(
                        run_id,
                        "experiment_run_analysis",
                        "Workflow Orchestrator",
                        "Experiment failed with a recoverable engineering error; waiting for the user to continue.",
                        data={"status": "awaiting_experiment_retry"},
                        output_summary={"recoverable": True, "user_action_required": True},
                    )
                    return
                if step_id is None:
                    interrupted = next(
                        (item for item in run.steps if item.id == run.current_step),
                        None,
                    )
                    if (
                        interrupted
                        and interrupted.status == "interrupted"
                        and isinstance(interrupted.error, dict)
                        and interrupted.error.get("recoverable") is True
                    ):
                        self.repository.update_workflow_state(
                            run_id,
                            status=(
                                "paused"
                                if interrupted.error.get("user_action_required") is True
                                else "RECOVERABLE_PROVIDER_ERROR"
                            ),
                            automatic=False,
                        )
                        return
                    self.repository.update_workflow_state(
                        run_id, status="completed", automatic=False
                    )
                    return
                try:
                    engine.run_step(run_id, step_id)
                except Exception as exc:
                    if self._schedule_provider_retry(run_id, step_id, exc):
                        continue
                    if self.repository.get_run(run_id).stop_requested:
                        raise LLMRequestCancelled()
                    raise
                self.repository.update_provider_retry_state(run_id, step_id, None)
                if self._must_stop_automatic(self.repository.get_run(run_id)):
                    return
        except LLMRequestCancelled:
            current = self.repository.get_run(run_id)
            self.repository.update_workflow_state(
                run_id,
                status="paused",
                automatic=False,
                stop_requested=True,
            )
            self.repository.append_event(
                run_id,
                current.current_step,
                "Workflow Orchestrator",
                "Run stopped by the user.",
                data={"status": "paused"},
                output_summary={"recoverable": True},
            )
        except Exception as exc:  # state must survive unexpected provider/runtime failures
            current = self.repository.get_run(run_id)
            # Small legacy/custom engines retain the historic ``failed`` value;
            # the production WorkflowEngine opts into the explicit contract.
            state = failure_state_for(exc) if getattr(engine, "universal_scientific_stability", False) else "failed"
            self.repository.update_workflow_state(
                run_id,
                status=state,
                automatic=False,
            )
            self.repository.append_event(
                run_id,
                current.current_step,
                "Workflow Orchestrator",
                "Automatic pipeline stopped after a step failure.",
                data={"error": str(exc), "error_type": type(exc).__name__},
                output_summary={
                    "recoverable": state in {
                        "RECOVERABLE_PROVIDER_ERROR",
                        "POLICY_INTEGRITY_REQUIRED",
                        "EVIDENCE_RETRY_REQUIRED",
                    }
                },
            )
        finally:
            with self._guard:
                self._threads.pop(run_id, None)

    @staticmethod
    def _is_auto_retryable_provider_failure(exc: Exception) -> bool:
        """Keep operational recovery narrow; science and configuration stay stopped."""
        if failure_state_for(exc) != "RECOVERABLE_PROVIDER_ERROR":
            return False
        message = str(exc).upper()
        if any(
            marker in message
            for marker in (
                "API_KEY_MISSING",
                "MODEL_PROVIDER_CONFIG_ERROR",
                "MODEL_PROVIDER_NOT_CONFIGURED",
                "QWEN_API_KEY_MISSING",
            )
        ):
            return False
        return (
            type(exc).__name__ == "JSONDecodeError"
            or message.startswith("MODEL_REQUEST_FAILED:")
            or message.startswith("MODEL_REQUEST_TIMEOUT:")
            or message.startswith("MODEL_EMPTY_OUTPUT:")
            or message.startswith("MODEL_OUTPUT_INVALID_JSON:")
            or message.startswith("MODEL_OUTPUT_VALIDATION_FAILURE:")
        )

    def _schedule_provider_retry(
        self, run_id: str, step_id: str, exc: Exception
    ) -> bool:
        """Persist and schedule one bounded, same-step provider recovery attempt."""
        if not self._is_auto_retryable_provider_failure(exc):
            return False
        run = self.repository.get_run(run_id)
        if run.stop_requested:
            return False
        step = next((item for item in run.steps if item.id == step_id), None)
        if (
            step is None
            or step.status != "interrupted"
            or not isinstance(step.error, dict)
            or step.error.get("recoverable") is not True
            or step.error.get("user_action_required") is True
        ):
            return False
        previous = dict((run.provider_retry_state or {}).get(step_id) or {})
        retry_number = int(previous.get("attempts", 0)) + 1
        if retry_number > self.provider_retry_limit:
            self.repository.append_event(
                run_id,
                step_id,
                "Workflow Orchestrator",
                "Automatic provider recovery exhausted; waiting for the user to continue.",
                data={
                    "automatic_retry": False,
                    "attempts": int(previous.get("attempts", 0)),
                    "retry_limit": self.provider_retry_limit,
                    "error": str(exc),
                },
                output_summary={"recoverable": True, "retry_exhausted": True},
            )
            return False

        delay_seconds = self.provider_retry_backoff_seconds * (2 ** (retry_number - 1))
        self.repository.update_provider_retry_state(
            run_id,
            step_id,
            {
                "attempts": retry_number,
                "retry_limit": self.provider_retry_limit,
                "last_error": str(exc),
                "next_delay_seconds": delay_seconds,
            },
        )
        self.repository.append_event(
            run_id,
            step_id,
            "Workflow Orchestrator",
            f"Retrying provider operation automatically ({retry_number}/{self.provider_retry_limit}).",
            data={
                "automatic_retry": True,
                "attempt": retry_number,
                "retry_limit": self.provider_retry_limit,
                "delay_seconds": delay_seconds,
                "error": str(exc),
            },
            output_summary={"recoverable": True, "automatic_retry": True},
        )
        self.repository.update_step_state(run_id, step_id, "pending", error=None)
        if delay_seconds:
            time.sleep(delay_seconds)
        return True

    @classmethod
    def _next_step(
        cls,
        run,
        max_feedback_iterations: int = MAX_FEEDBACK_ITERATIONS,
        *,
        automatic_execution: bool = False,
    ) -> str | None:
        if automatic_execution and cls._must_stop_automatic(run):
            return None
        latest = cls._latest_by_type(run.artifacts)
        simple_outputs = {
            "problem_understanding": "problem",
            "knowledge_integration": "evidence",
            "hypothesis_generation": "hypothesis",
            "evidence_reasoning": "reasoning",
        }
        for step_id, artifact_type in simple_outputs.items():
            if artifact_type not in latest:
                return step_id
        if "hypothesis_selection" not in latest:
            return AWAIT_HYPOTHESIS_SELECTION
        if "plan" not in latest:
            return "research_plan"

        plan = latest["plan"]
        task = cls._task_for_plan(run.artifacts, plan)
        if task is not None:
            result = cls._result_for_task(run.artifacts, task)
            if result is not None and not cls._is_engineering_failure(result.content or {}):
                revision = cls._revision_for_result(run.artifacts, result)
                if revision is not None and not feedback_requires_follow_up(
                    revision.content
                ):
                    task_position = next(
                        index
                        for index, artifact in enumerate(run.artifacts)
                        if artifact.id == task.id
                    )
                    if is_plan_governance_accepted(
                        run.artifacts[: task_position + 1]
                    ):
                        return "report_export" if "report" not in latest else None
        if not is_plan_governance_accepted(run.artifacts):
            return "research_plan"
        if task is None:
            return "experiment_task"
        result = cls._result_for_task(run.artifacts, task)
        if result is None:
            return "experiment_run_analysis"
        if cls._is_engineering_failure(result.content or {}):
            # A failed engineering attempt (smoke, harness, runtime, provider,
            # schema, dependency, dataset loader, resume) is a recovery point,
            # not a scientific result.  It must never advance into scientific
            # feedback or mark the run complete.  An explicit Continue resets the
            # step to pending, in which case we re-enter it; otherwise the run
            # pauses awaiting that retry.
            current = next(
                (item for item in run.steps if item.id == "experiment_run_analysis"),
                None,
            )
            if run.current_step == "experiment_run_analysis" and current and current.status == "pending":
                return "experiment_run_analysis"
            return AWAIT_EXPERIMENT_RETRY
        revision = cls._revision_for_result(run.artifacts, result)
        if revision is None:
            return "feedback_revision"
        if feedback_requires_follow_up(revision.content):
            iteration = int(revision.content.get("iteration") or 0)
            if iteration >= max_feedback_iterations:
                return "report_export" if "report" not in latest else None
            refinement_proposal = cls._child_artifact(
                run.artifacts, revision.id, "plan_refinement_proposal"
            )
            if refinement_proposal is None:
                return "feedback_revision"
            return "research_plan"
        if "report" not in latest:
            return "report_export"
        return None

    @staticmethod
    def _must_stop_automatic(run) -> bool:
        if run.status in {"NEEDS_PLAN_REVISION", "POLICY_INTEGRITY_REQUIRED"}:
            return True
        if not run.automatic:
            return True
        current = next((item for item in run.steps if item.id == run.current_step), None)
        return bool(
            current
            and current.status == "interrupted"
            and isinstance(current.error, dict)
            and (
                current.error.get("recoverable") is True
                or current.error.get("user_action_required") is True
            )
        )

    @staticmethod
    def _latest_by_type(artifacts) -> dict:
        latest = {}
        for artifact in artifacts:
            latest[artifact.type] = artifact
        return latest

    @staticmethod
    def _child_artifact(artifacts, parent_id: str, artifact_type: str):
        return next(
            (
                artifact
                for artifact in reversed(artifacts)
                if artifact.type == artifact_type
                and artifact.parent_artifact_id == parent_id
            ),
            None,
        )

    @classmethod
    def _task_for_plan(cls, artifacts, plan):
        direct = cls._child_artifact(artifacts, plan.id, "experiment_task")
        if direct is not None:
            if cls._formal_validation_pending(artifacts):
                return None
            return direct
        # Compatibility for runs created before task-to-plan lineage was persisted.
        return next(
            (
                artifact
                for artifact in reversed(artifacts)
                if artifact.type == "experiment_task"
                and artifact.created_at >= plan.created_at
            ),
            None,
        )

    @staticmethod
    def _is_engineering_failure(result: dict) -> bool:
        # A failed engineering attempt never produced an audited scientific
        # result, regardless of the concrete error class (smoke, harness,
        # runtime, provider, schema, dependency, dataset loader, resume).
        return str((result or {}).get("status") or "").lower() == "failed"

    @classmethod
    def _should_continue_failed_experiment(cls, run) -> bool:
        if run.current_step != "experiment_run_analysis":
            return False
        current = next(
            (item for item in run.steps if item.id == "experiment_run_analysis"),
            None,
        )
        if current is None or current.status != "completed":
            return False
        latest = cls._latest_by_type(run.artifacts)
        plan = latest.get("plan")
        if plan is None:
            return False
        task = cls._task_for_plan(run.artifacts, plan)
        if task is None:
            return False
        result = cls._result_for_task(run.artifacts, task)
        return bool(result and cls._is_engineering_failure(result.content or {}))

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

    @classmethod
    def _result_for_task(cls, artifacts, task):
        return result_for_experiment_task(artifacts, task)

    @classmethod
    def _revision_for_result(cls, artifacts, result):
        return cls._child_artifact(artifacts, result.id, "revision")
