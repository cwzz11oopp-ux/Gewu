from __future__ import annotations

from collections.abc import Callable
from threading import Lock, Thread

from backend.app.providers.llm import LLMRequestCancelled
from backend.app.storage.repository import Repository
from backend.app.workflow.artifact_lineage import result_for_experiment_task
from backend.app.workflow.steps import ORDER
from backend.app.workflow.scientific_stability import failure_state_for
from backend.app.workflow.plan_review_governance import is_plan_governance_accepted


MAX_FEEDBACK_ITERATIONS = 4
AWAIT_HYPOTHESIS_SELECTION = "await_hypothesis_selection"


class WorkflowOrchestrator:
    """Runs durable pipelines outside the browser and resumes them after restarts."""

    def __init__(self, repository: Repository, engine: Callable[[], object]) -> None:
        self.repository = repository
        self._engine = engine
        self._threads: dict[str, Thread] = {}
        self._guard = Lock()

    def start(self, run_id: str):
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
        current_step = next(
            (item for item in run.steps if item.id == run.current_step),
            None,
        )
        if (
            current_step
            and current_step.status == "interrupted"
            and isinstance(current_step.error, dict)
            and current_step.error.get("recoverable") is True
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
                        if getattr(selection_result, "status", "") == "hypothesis_revision_required":
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
                engine.run_step(run_id, step_id)
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
                    }
                },
            )
        finally:
            with self._guard:
                self._threads.pop(run_id, None)

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
        if "plan" not in latest or not is_plan_governance_accepted(run.artifacts):
            return "research_plan"

        plan = latest["plan"]
        task = cls._task_for_plan(run.artifacts, plan)
        if task is None:
            return "experiment_task"
        result = cls._result_for_task(run.artifacts, task)
        if result is None:
            return "experiment_run_analysis"
        if cls._is_smoke_preflight_failure(result.content or {}):
            return None
        revision = cls._revision_for_result(run.artifacts, result)
        if revision is None:
            return "feedback_revision"
        if revision.content.get("requires_follow_up") is True:
            iteration = int(revision.content.get("iteration") or 0)
            if iteration >= max_feedback_iterations:
                return "report_export" if "report" not in latest else None
            refined_plan = cls._child_artifact(run.artifacts, revision.id, "plan")
            if refined_plan is None:
                return "feedback_revision"
            next_task = cls._task_for_plan(run.artifacts, refined_plan)
            if next_task is None:
                return "experiment_task"
            next_result = cls._result_for_task(run.artifacts, next_task)
            if next_result is None:
                return "experiment_run_analysis"
            if cls._revision_for_result(run.artifacts, next_result) is None:
                return "feedback_revision"
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
    def _is_smoke_preflight_failure(result: dict) -> bool:
        return (
            str(result.get("status") or "").lower() == "failed"
            and str(result.get("error") or "").startswith(
                "EXPERIMENT_BUNDLE_SMOKE_TEST_FAILED:"
            )
        )

    @staticmethod
    def _formal_validation_pending(artifacts) -> bool:
        latest_evidence = next(
            (item for item in reversed(artifacts) if item.type == "result_evidence"),
            None,
        )
        if not latest_evidence:
            return False
        content = latest_evidence.content or {}
        if (
            content.get("stage") != "small_scale"
            or content.get("route") != "expand_validation"
        ):
            return False
        return not any(
            item.type == "experiment_task"
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
