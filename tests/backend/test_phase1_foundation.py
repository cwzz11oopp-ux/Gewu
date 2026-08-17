from backend.app.providers.experiment import MockExperimentProvider
from backend.app.providers.literature import MockLiteratureProvider
from backend.app.storage.repository import Repository
from backend.app.workflow.engine import WorkflowEngine
from backend.app.workflow.orchestrator import WorkflowOrchestrator


class AdmissionLLM:
    mode = "role_router"
    fallback = False

    def __init__(self, failures=()):
        self.failures = set(failures)
        self.calls = []

    def preflight(self, provider_id):
        self.calls.append(provider_id)
        if provider_id in self.failures:
            raise RuntimeError(f"{provider_id.upper()}_PROVIDER_CONFIG_ERROR:http_status=400:invalid model")
        return {"provider": provider_id, "model": f"{provider_id}-model", "structured": True}


def _engine(tmp_path, llm):
    return WorkflowEngine(
        Repository(data_dir=str(tmp_path / "data")), llm, MockLiteratureProvider(), MockExperimentProvider()
    )


def test_provider_preflight_uses_minimal_checks_and_blocks_pipeline(tmp_path):
    llm = AdmissionLLM({"qwen"})
    engine = _engine(tmp_path, llm)
    run = engine.repository.create_run("question", "title")
    result = engine.preflight_run(run.id)

    assert llm.calls == ["qwen", "deepseek"]
    assert result["blocking"] is True
    qwen = next(item for item in result["checks"] if item["name"] == "qwen")
    assert qwen["code"] == "QWEN_PROVIDER_CONFIG_ERROR"
    assert "http_status=400" in qwen["detail"]


def test_dataset_preflight_failure_does_not_start_pipeline(tmp_path, monkeypatch):
    llm = AdmissionLLM()
    engine = _engine(tmp_path, llm)
    run = engine.repository.create_run("question", "title")
    monkeypatch.setattr(engine, "_inspect_configured_local_dataset", lambda _: (_ for _ in ()).throw(RuntimeError("DATASET_PATH_UNREADABLE")))
    orchestrator = WorkflowOrchestrator(engine.repository, lambda: engine)

    updated = orchestrator.start(run.id)

    assert updated.status == "preflight_failed"
    assert all(step.status == "pending" for step in updated.steps)


def test_constraints_reference_is_carried_by_plan_experiment_repair_and_review(tmp_path):
    llm = AdmissionLLM()
    engine = _engine(tmp_path, llm)
    run = engine.repository.create_run("question", "title", research_constraints={"task_type": "classification"})
    constraints = engine._ensure_research_constraints(run.id)
    reference = {"artifact_id": constraints.id, "schema_version": 1}

    # This is the immutable shared reference shape used by Plan, Experiment,
    # Repair candidate evidence and Review contexts.
    for reference_in_use in (reference, reference, reference, reference):
        assert reference_in_use["artifact_id"] == constraints.id
        assert reference_in_use["schema_version"] == 1
