from backend.app.storage.repository import Repository
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


def test_next_step_requires_result_for_latest_feedback_plan_task(tmp_path):
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
    assert WorkflowOrchestrator._next_step(run) == "experiment_task"

    _artifact(
        repository,
        run.id,
        "experiment_task",
        "experiment_task",
        plan_2.id,
        {"experiment_id": "experiment_2"},
    )
    run = repository.get_run(run.id)
    assert WorkflowOrchestrator._next_step(run) == "experiment_run_analysis"


def test_next_step_finds_result_beneath_repaired_bundle_lineage(tmp_path):
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

    assert WorkflowOrchestrator._next_step(run) == "feedback_revision"


def test_next_step_waits_for_user_after_evidence_reasoning(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    _artifact(repository, run.id, "problem", "problem_understanding")
    _artifact(repository, run.id, "evidence", "knowledge_integration")
    _artifact(repository, run.id, "hypothesis", "hypothesis_generation")
    _artifact(repository, run.id, "reasoning", "evidence_reasoning")

    run = repository.get_run(run.id)
    assert WorkflowOrchestrator._next_step(run) == AWAIT_HYPOTHESIS_SELECTION


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
