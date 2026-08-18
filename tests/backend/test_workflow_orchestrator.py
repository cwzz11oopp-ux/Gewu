from threading import Event

import pytest

from backend.app.storage.repository import Repository
import backend.app.workflow.orchestrator as orchestrator_module
from backend.app.workflow.orchestrator import (
    AWAIT_HYPOTHESIS_SELECTION,
    WorkflowOrchestrator,
)


def _artifact(repository, run_id, artifact_type, source_step, parent=None, content=None):
    return repository.add_artifact(
        run_id,
        artifact_type,
        artifact_type,
        content or {},
        source_step,
        "test",
        parent_artifact_id=parent,
    )


def test_reconcile_marks_inflight_step_interrupted_and_resumable(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    repository.update_workflow_state(
        run.id, status="running", automatic=True, current_step="knowledge_integration"
    )
    repository.update_step_state(run.id, "knowledge_integration", "running")

    resumable = repository.reconcile_interrupted_runs()
    recovered = repository.get_run(run.id)

    assert resumable == [run.id]
    assert recovered.status == "interrupted"
    step = next(item for item in recovered.steps if item.id == "knowledge_integration")
    assert step.status == "interrupted"
    assert step.error["code"] == "PROCESS_INTERRUPTED"


def _prepare_research_plan_resume(repository, run_id, *, status):
    for artifact_type, source_step in [
        ("problem", "problem_understanding"),
        ("evidence", "knowledge_integration"),
        ("hypothesis", "hypothesis_generation"),
        ("reasoning", "evidence_reasoning"),
        ("hypothesis_selection", "evidence_reasoning"),
    ]:
        _artifact(repository, run_id, artifact_type, source_step)
    constraints = _artifact(
        repository, run_id, "research_constraints", "problem_understanding"
    )
    run = repository.get_run(run_id)
    run.research_constraints_artifact_id = constraints.id
    repository.save_run(run)
    repository.update_workflow_state(
        run_id,
        status=status,
        automatic=False,
        current_step="research_plan",
    )
    repository.update_step_state(
        run_id,
        "research_plan",
        "interrupted",
        error={"code": "MODEL_OUTPUT_VALIDATION_FAILURE", "recoverable": True},
    )


@pytest.mark.parametrize("initial_status", ["RECOVERABLE_PROVIDER_ERROR", "completed"])
def test_explicit_start_resumes_recoverable_interrupted_research_plan(
    tmp_path, initial_status
):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    _prepare_research_plan_resume(repository, run.id, status=initial_status)

    class Engine:
        max_feedback_iterations = 4

        def __init__(self):
            self.steps = []
            self.routed_step = None
            self.called = Event()

        def run_step(self, run_id, step_id):
            self.routed_step = WorkflowOrchestrator._next_step(
                repository.get_run(run_id), automatic_execution=True
            )
            self.steps.append(step_id)
            self.called.set()
            raise RuntimeError("STOP_AFTER_RESUME_PROBE")

    engine = Engine()
    orchestrator = WorkflowOrchestrator(repository, lambda: engine)

    orchestrator.start(run.id)
    assert engine.called.wait(2)
    worker = orchestrator._threads.get(run.id)
    if worker is not None:
        worker.join(2)
    resumed = repository.get_run(run.id)

    assert engine.routed_step == "research_plan"
    assert engine.steps == ["research_plan"]
    assert resumed.status == "failed"
    assert resumed.status != "completed"


def test_start_keeps_plan_governance_stop_states_stopped(tmp_path):
    for status in ("NEEDS_PLAN_REVISION", "POLICY_INTEGRITY_REQUIRED"):
        repository = Repository(str(tmp_path / status))
        run = repository.create_run("problem", "title")
        repository.update_workflow_state(run.id, status=status, automatic=False)
        orchestrator = WorkflowOrchestrator(repository, lambda: object())

        stopped = orchestrator.start(run.id)

        assert stopped.status == status
        assert stopped.automatic is False


def test_start_does_not_resume_user_action_required_checkpoint(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    constraints = _artifact(
        repository, run.id, "research_constraints", "problem_understanding"
    )
    run = repository.get_run(run.id)
    run.research_constraints_artifact_id = constraints.id
    repository.save_run(run)
    repository.update_workflow_state(
        run.id,
        status="RECOVERABLE_PROVIDER_ERROR",
        automatic=False,
        current_step="research_plan",
    )
    repository.update_step_state(
        run.id,
        "research_plan",
        "interrupted",
        error={
            "code": "USER_ACTION_REQUIRED",
            "recoverable": True,
            "user_action_required": True,
        },
    )
    orchestrator = WorkflowOrchestrator(repository, lambda: object())

    stopped = orchestrator.start(run.id)

    step = next(item for item in stopped.steps if item.id == "research_plan")
    assert stopped.status == "RECOVERABLE_PROVIDER_ERROR"
    assert stopped.automatic is False
    assert step.status == "interrupted"
    assert step.error["user_action_required"] is True


def test_drive_does_not_mark_recoverable_interrupted_work_completed(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    repository.update_workflow_state(
        run.id, status="queued", automatic=True, current_step="research_plan"
    )
    repository.update_step_state(
        run.id,
        "research_plan",
        "interrupted",
        error={"code": "MODEL_OUTPUT_VALIDATION_FAILURE", "recoverable": True},
    )
    orchestrator = WorkflowOrchestrator(repository, lambda: object())

    orchestrator._drive(run.id)

    stopped = repository.get_run(run.id)
    assert stopped.status == "RECOVERABLE_PROVIDER_ERROR"
    assert stopped.automatic is False


def test_drive_marks_truly_finished_workflow_completed(tmp_path, monkeypatch):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    for artifact_type, source_step in [
        ("problem", "problem_understanding"),
        ("evidence", "knowledge_integration"),
        ("hypothesis", "hypothesis_generation"),
        ("reasoning", "evidence_reasoning"),
        ("hypothesis_selection", "evidence_reasoning"),
    ]:
        _artifact(repository, run.id, artifact_type, source_step)
    plan = _artifact(repository, run.id, "plan", "research_plan")
    task = _artifact(
        repository,
        run.id,
        "experiment_task",
        "experiment_task",
        plan.id,
        {"experiment_id": "experiment_1"},
    )
    bundle = _artifact(
        repository, run.id, "experiment_bundle", "experiment_task", task.id
    )
    result = _artifact(
        repository,
        run.id,
        "experiment_result",
        "experiment_run_analysis",
        bundle.id,
        {"experiment_id": "experiment_1"},
    )
    _artifact(
        repository,
        run.id,
        "revision",
        "feedback_revision",
        result.id,
        {"requires_follow_up": False},
    )
    _artifact(repository, run.id, "report", "report_export")
    repository.update_workflow_state(run.id, status="queued", automatic=True)
    monkeypatch.setattr(orchestrator_module, "is_plan_governance_accepted", lambda _artifacts: True)
    orchestrator = WorkflowOrchestrator(repository, lambda: object())

    orchestrator._drive(run.id)

    completed = repository.get_run(run.id)
    assert WorkflowOrchestrator._next_step(completed) is None
    assert completed.status == "completed"
    assert completed.automatic is False


def test_next_step_routes_legacy_feedback_plan_through_governance(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    _artifact(repository, run.id, "problem", "problem_understanding")
    _artifact(repository, run.id, "evidence", "knowledge_integration")
    _artifact(repository, run.id, "hypothesis", "hypothesis_generation")
    _artifact(repository, run.id, "reasoning", "evidence_reasoning")
    _artifact(repository, run.id, "hypothesis_selection", "evidence_reasoning")
    plan_1 = _artifact(repository, run.id, "plan", "research_plan")
    task_1 = _artifact(
        repository,
        run.id,
        "experiment_task",
        "experiment_task",
        plan_1.id,
        {"experiment_id": "experiment_1"},
    )
    bundle_1 = _artifact(
        repository, run.id, "experiment_bundle", "experiment_task", task_1.id
    )
    result_1 = _artifact(
        repository,
        run.id,
        "experiment_result",
        "experiment_run_analysis",
        bundle_1.id,
        {"experiment_id": "experiment_1"},
    )
    revision = _artifact(
        repository,
        run.id,
        "revision",
        "feedback_revision",
        result_1.id,
        {"iteration": 1, "requires_follow_up": True},
    )
    plan_2 = _artifact(repository, run.id, "plan", "research_plan", revision.id)

    run = repository.get_run(run.id)
    assert WorkflowOrchestrator._next_step(run) == "research_plan"

    _artifact(
        repository,
        run.id,
        "experiment_task",
        "experiment_task",
        plan_2.id,
        {"experiment_id": "experiment_2"},
    )
    run = repository.get_run(run.id)
    assert WorkflowOrchestrator._next_step(run) == "research_plan"


def test_next_step_routes_legacy_repaired_bundle_history_through_governance(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    _artifact(repository, run.id, "problem", "problem_understanding")
    _artifact(repository, run.id, "evidence", "knowledge_integration")
    _artifact(repository, run.id, "hypothesis", "hypothesis_generation")
    _artifact(repository, run.id, "reasoning", "evidence_reasoning")
    _artifact(repository, run.id, "hypothesis_selection", "evidence_reasoning")
    plan = _artifact(repository, run.id, "plan", "research_plan")
    task = _artifact(
        repository,
        run.id,
        "experiment_task",
        "experiment_task",
        plan.id,
        {"experiment_id": "experiment_1"},
    )
    original_bundle = _artifact(
        repository, run.id, "experiment_bundle", "experiment_task", task.id
    )
    diagnosis = _artifact(
        repository,
        run.id,
        "experiment_diagnosis",
        "experiment_run_analysis",
        original_bundle.id,
    )
    repaired_bundle = _artifact(
        repository,
        run.id,
        "experiment_bundle",
        "experiment_run_analysis",
        diagnosis.id,
    )
    _artifact(
        repository,
        run.id,
        "experiment_result",
        "experiment_run_analysis",
        repaired_bundle.id,
        {"experiment_id": "experiment_1"},
    )

    run = repository.get_run(run.id)

    assert WorkflowOrchestrator._next_step(run) == "research_plan"


def test_next_step_waits_for_user_after_evidence_reasoning(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    _artifact(repository, run.id, "problem", "problem_understanding")
    _artifact(repository, run.id, "evidence", "knowledge_integration")
    _artifact(repository, run.id, "hypothesis", "hypothesis_generation")
    _artifact(repository, run.id, "reasoning", "evidence_reasoning")

    run = repository.get_run(run.id)
    assert WorkflowOrchestrator._next_step(run) == AWAIT_HYPOTHESIS_SELECTION


def test_small_scale_expand_routes_to_formal_and_smoke_failure_stops_scientific_feedback(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        orchestrator_module, "is_plan_governance_accepted", lambda _artifacts: True
    )
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    for artifact_type, source_step in [
        ("problem", "problem_understanding"),
        ("evidence", "knowledge_integration"),
        ("hypothesis", "hypothesis_generation"),
        ("reasoning", "evidence_reasoning"),
        ("hypothesis_selection", "evidence_reasoning"),
    ]:
        _artifact(repository, run.id, artifact_type, source_step)
    plan = _artifact(repository, run.id, "plan", "research_plan")
    task = _artifact(
        repository,
        run.id,
        "experiment_task",
        "experiment_task",
        plan.id,
        {"experiment_id": "experiment_1", "phase2_protocol": {"stage": "small_scale"}},
    )
    bundle = _artifact(repository, run.id, "experiment_bundle", "experiment_task", task.id)
    result = _artifact(
        repository,
        run.id,
        "experiment_result",
        "experiment_run_analysis",
        bundle.id,
        {"experiment_id": "experiment_1"},
    )
    _artifact(
        repository,
        run.id,
        "result_evidence",
        "experiment_run_analysis",
        result.id,
        {"stage": "small_scale", "route": "expand_validation"},
    )

    assert WorkflowOrchestrator._next_step(repository.get_run(run.id)) == "experiment_task"

    smoke_failure = {"status": "failed", "error": "EXPERIMENT_BUNDLE_SMOKE_TEST_FAILED:boom"}
    assert WorkflowOrchestrator._is_smoke_preflight_failure(smoke_failure) is True


def test_automatic_drive_selects_and_continues_without_human_pause(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    for artifact_type, source_step in [
        ("problem", "problem_understanding"),
        ("evidence", "knowledge_integration"),
        ("hypothesis", "hypothesis_generation"),
        ("reasoning", "evidence_reasoning"),
    ]:
        _artifact(repository, run.id, artifact_type, source_step)
    repository.update_workflow_state(run.id, status="running", automatic=True)

    class Engine:
        max_feedback_iterations = 4
        selected = 0
        steps = []

        def auto_select_hypothesis(self, run_id):
            self.selected += 1
            _artifact(repository, run_id, "hypothesis_selection", "evidence_reasoning")

        def run_step(self, run_id, step_id):
            self.steps.append(step_id)
            raise RuntimeError("STOP_AFTER_ROUTING_PROBE")

    engine = Engine()
    orchestrator = WorkflowOrchestrator(repository, lambda: engine)
    orchestrator._drive(run.id)

    assert engine.selected == 1
    assert engine.steps == ["research_plan"]
    assert repository.get_run(run.id).status == "failed"


def test_stop_does_not_turn_failed_run_into_stopping(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    repository.update_workflow_state(run.id, status="failed", automatic=False)
    orchestrator = WorkflowOrchestrator(repository, lambda: object())

    stopped = orchestrator.stop(run.id)

    assert stopped.status == "failed"
    assert stopped.stop_requested is False


def test_stop_active_run_requests_provider_cancellation(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    repository.update_workflow_state(run.id, status="running", automatic=True)

    class Engine:
        cancelled = []

        def cancel(self, run_id):
            self.cancelled.append(run_id)
            return True

    class AliveThread:
        @staticmethod
        def is_alive():
            return True

    engine = Engine()
    orchestrator = WorkflowOrchestrator(repository, lambda: engine)
    orchestrator._threads[run.id] = AliveThread()

    stopped = orchestrator.stop(run.id)

    assert stopped.status == "stopping"
    assert stopped.stop_requested is True
    assert engine.cancelled == [run.id]


def test_stop_manual_running_step_requests_provider_cancellation(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    repository.update_workflow_state(
        run.id,
        status="running",
        automatic=False,
        current_step="evidence_reasoning",
    )
    repository.update_step_state(run.id, "evidence_reasoning", "running")

    class Engine:
        cancelled = []

        def cancel(self, run_id):
            self.cancelled.append(run_id)
            return True

    engine = Engine()
    orchestrator = WorkflowOrchestrator(repository, lambda: engine)

    stopped = orchestrator.stop(run.id)

    assert stopped.status == "paused"
    assert stopped.stop_requested is True
    assert engine.cancelled == [run.id]
    step = next(item for item in stopped.steps if item.id == "evidence_reasoning")
    assert step.status == "interrupted"
    assert step.error["code"] == "PIPELINE_STOPPED"
