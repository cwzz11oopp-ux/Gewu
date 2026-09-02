from threading import Event

import pytest

from backend.app.storage.repository import Repository
import backend.app.workflow.orchestrator as orchestrator_module
from backend.app.workflow.orchestrator import (
    AWAIT_EXPERIMENT_RETRY,
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


def test_reconcile_finishes_a_persisted_stop_request(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    repository.update_workflow_state(
        run.id,
        status="stopping",
        automatic=True,
        stop_requested=True,
        current_step="hypothesis_generation",
    )
    repository.update_step_state(run.id, "hypothesis_generation", "running")

    resumable = repository.reconcile_interrupted_runs()
    recovered = repository.get_run(run.id)

    assert resumable == []
    assert recovered.status == "paused"
    assert recovered.stop_requested is True
    step = next(item for item in recovered.steps if item.id == "hypothesis_generation")
    assert step.status == "interrupted"
    assert step.error["code"] == "PIPELINE_STOPPED"


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


def test_drive_retries_recoverable_model_output_before_pausing(tmp_path, monkeypatch):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    repository.update_workflow_state(
        run.id, status="queued", automatic=True, current_step="research_plan"
    )

    class Engine:
        universal_scientific_stability = True
        max_feedback_iterations = 4

        def __init__(self):
            self.calls = 0

        def run_step(self, run_id, step_id):
            self.calls += 1
            repository.update_step_state(run_id, step_id, "running")
            if self.calls == 1:
                repository.update_step_state(
                    run_id,
                    step_id,
                    "interrupted",
                    error={
                        "code": "MODEL_EMPTY_OUTPUT",
                        "message": "MODEL_EMPTY_OUTPUT:provider=qwen",
                        "recoverable": True,
                        "user_action_required": False,
                    },
                )
                raise ValueError("MODEL_EMPTY_OUTPUT:provider=qwen")
            repository.update_step_state(run_id, step_id, "completed")

    engine = Engine()
    orchestrator = WorkflowOrchestrator(
        repository,
        lambda: engine,
        provider_retry_limit=2,
        provider_retry_backoff_seconds=0,
    )
    routes = iter(("research_plan", "research_plan", None))
    monkeypatch.setattr(orchestrator, "_next_step", lambda *_args, **_kwargs: next(routes))

    orchestrator._drive(run.id)

    completed = repository.get_run(run.id)
    assert engine.calls == 2
    assert completed.status == "completed"
    assert completed.provider_retry_state == {}
    assert any(
        event.message == "Retrying provider operation automatically (1/2)."
        for event in completed.events
    )


def test_drive_retries_recoverable_interruption_returned_by_plan_governance(
    tmp_path, monkeypatch
):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    repository.update_workflow_state(
        run.id, status="queued", automatic=True, current_step="research_plan"
    )

    class Engine:
        universal_scientific_stability = True
        max_feedback_iterations = 4

        def __init__(self):
            self.calls = 0

        def run_step(self, run_id, step_id):
            self.calls += 1
            repository.update_step_state(run_id, step_id, "running")
            if self.calls == 1:
                repository.update_step_state(
                    run_id,
                    step_id,
                    "interrupted",
                    error={
                        "code": "MODEL_OUTPUT_VALIDATION_FAILURE",
                        "message": (
                            "MODEL_OUTPUT_VALIDATION_FAILURE:"
                            "PLAN_REVIEW_FIX_MAP_EMPTY:BR-2"
                        ),
                        "recoverable": True,
                        "user_action_required": False,
                    },
                )
                return
            repository.update_step_state(run_id, step_id, "completed")

    engine = Engine()
    orchestrator = WorkflowOrchestrator(
        repository,
        lambda: engine,
        provider_retry_limit=2,
        provider_retry_backoff_seconds=0,
    )
    routes = iter(("research_plan", "research_plan", None))
    monkeypatch.setattr(orchestrator, "_next_step", lambda *_args, **_kwargs: next(routes))

    orchestrator._drive(run.id)

    completed = repository.get_run(run.id)
    assert engine.calls == 2
    assert completed.status == "completed"
    assert completed.provider_retry_state == {}
    assert any(
        event.message == "Retrying provider operation automatically (1/2)."
        for event in completed.events
    )


def test_drive_pauses_only_after_the_provider_retry_budget_is_exhausted(
    tmp_path, monkeypatch
):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    repository.update_workflow_state(
        run.id, status="queued", automatic=True, current_step="research_plan"
    )

    class Engine:
        universal_scientific_stability = True
        max_feedback_iterations = 4

        def __init__(self):
            self.calls = 0

        def run_step(self, run_id, step_id):
            self.calls += 1
            repository.update_step_state(run_id, step_id, "running")
            repository.update_step_state(
                run_id,
                step_id,
                "interrupted",
                error={
                    "code": "MODEL_EMPTY_OUTPUT",
                    "message": "MODEL_EMPTY_OUTPUT:provider=qwen",
                    "recoverable": True,
                    "user_action_required": False,
                },
            )
            raise ValueError("MODEL_EMPTY_OUTPUT:provider=qwen")

    engine = Engine()
    orchestrator = WorkflowOrchestrator(
        repository,
        lambda: engine,
        provider_retry_limit=2,
        provider_retry_backoff_seconds=0,
    )
    monkeypatch.setattr(
        orchestrator, "_next_step", lambda *_args, **_kwargs: "research_plan"
    )

    orchestrator._drive(run.id)

    stopped = repository.get_run(run.id)
    assert engine.calls == 3  # initial request plus two automatic recoveries
    assert stopped.status == "RECOVERABLE_PROVIDER_ERROR"
    assert stopped.provider_retry_state["research_plan"]["attempts"] == 2
    assert any(
        event.message.startswith("Automatic provider recovery exhausted")
        for event in stopped.events
    )


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


def test_terminal_feedback_ignores_abandoned_later_refinement_proposal(
    tmp_path, monkeypatch
):
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
    original_revision = _artifact(
        repository,
        run.id,
        "revision",
        "feedback_revision",
        result.id,
        {"iteration": 1, "requires_follow_up": True},
    )
    _artifact(
        repository,
        run.id,
        "plan_refinement_proposal",
        "research_plan",
        original_revision.id,
    )
    _artifact(
        repository,
        run.id,
        "revision",
        "feedback_revision",
        result.id,
        {
            "iteration": 1,
            "decision": "REPORT",
            "requires_follow_up": False,
            "recovered_from_revision_id": original_revision.id,
        },
    )
    monkeypatch.setattr(
        orchestrator_module,
        "is_plan_governance_accepted",
        lambda artifacts: not any(
            artifact.type == "plan_refinement_proposal" for artifact in artifacts
        ),
    )

    assert WorkflowOrchestrator._next_step(repository.get_run(run.id)) == (
        "report_export"
    )


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
        {
            "stage": "small_scale",
            "status": "positive_stable",
            "route": "expand_validation",
        },
    )

    assert WorkflowOrchestrator._next_step(repository.get_run(run.id)) == "experiment_task"

    smoke_failure = {"status": "failed", "error": "EXPERIMENT_BUNDLE_SMOKE_TEST_FAILED:boom"}
    assert WorkflowOrchestrator._is_engineering_failure(smoke_failure) is True
    assert WorkflowOrchestrator._is_engineering_failure({"status": "completed"}) is False


def test_formal_validation_is_scoped_to_latest_plan_lineage(tmp_path):
    repository = Repository(str(tmp_path))
    run = repository.create_run("problem", "title")
    plan_1 = _artifact(repository, run.id, "plan", "research_plan")
    _artifact(
        repository,
        run.id,
        "experiment_task",
        "experiment_task",
        plan_1.id,
        {"phase2_protocol": {"stage": "formal_validation"}},
    )
    plan_2 = _artifact(repository, run.id, "plan", "research_plan")
    task_2 = _artifact(
        repository,
        run.id,
        "experiment_task",
        "experiment_task",
        plan_2.id,
        {"phase2_protocol": {"stage": "small_scale"}},
    )
    result_2 = _artifact(
        repository, run.id, "experiment_result", "experiment_run_analysis", task_2.id
    )
    _artifact(
        repository,
        run.id,
        "result_evidence",
        "experiment_run_analysis",
        result_2.id,
        {"stage": "small_scale", "status": "negative", "route": "scientific_review"},
    )

    assert WorkflowOrchestrator._formal_validation_pending(
        repository.get_run(run.id).artifacts
    ) is True


def test_continue_retries_failed_smoke_with_latest_task_and_bundle(tmp_path, monkeypatch):
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
    constraints = _artifact(
        repository, run.id, "research_constraints", "problem_understanding"
    )
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
    failed_result = _artifact(
        repository,
        run.id,
        "experiment_result",
        "experiment_run_analysis",
        bundle.id,
        {
            "experiment_id": "experiment_1",
            "status": "failed",
            "error": "EXPERIMENT_BUNDLE_SMOKE_TEST_FAILED:HARNESS_IMPLEMENTATION_SEED_MISMATCH",
        },
    )
    run = repository.get_run(run.id)
    run.research_constraints_artifact_id = constraints.id
    repository.save_run(run)
    repository.update_workflow_state(
        run.id,
        status="completed",
        automatic=False,
        current_step="experiment_run_analysis",
    )
    repository.update_step_state(run.id, "experiment_run_analysis", "completed")

    # A completed failed result is not silently treated as completion; it pauses
    # awaiting an explicit Continue, which marks the current experiment step pending.
    assert WorkflowOrchestrator._next_step(repository.get_run(run.id)) == AWAIT_EXPERIMENT_RETRY

    class Engine:
        max_feedback_iterations = 4

        def __init__(self):
            self.steps = []
            self.called = Event()

        def run_step(self, run_id, step_id):
            current = repository.get_run(run_id)
            self.steps.append(step_id)
            assert step_id == "experiment_run_analysis"
            assert current.artifacts[-1].id == failed_result.id
            assert next(item for item in reversed(current.artifacts) if item.type == "experiment_task").id == task.id
            assert next(item for item in reversed(current.artifacts) if item.type == "experiment_bundle").id == bundle.id
            self.called.set()
            raise RuntimeError("STOP_AFTER_CONTINUE_PROBE")

    engine = Engine()
    orchestrator = WorkflowOrchestrator(repository, lambda: engine)
    orchestrator.start(run.id)
    assert engine.called.wait(2)
    worker = orchestrator._threads.get(run.id)
    if worker is not None:
        worker.join(2)

    resumed = repository.get_run(run.id)
    assert engine.steps == ["experiment_run_analysis"]
    assert [item.id for item in resumed.artifacts if item.type == "experiment_task"] == [task.id]
    assert [item.id for item in resumed.artifacts if item.type == "experiment_bundle"] == [bundle.id]
    assert [item.id for item in resumed.artifacts if item.type == "experiment_result"] == [failed_result.id]
    assert "experiment_1" in resumed.force_new_attempt_experiment_ids


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
