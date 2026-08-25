from fastapi.testclient import TestClient
import json
import subprocess

from backend.app.main import create_app
from backend.app.models.provider import EvidenceCard
from backend.app.providers.llm import MockLLMProvider
from backend.app.storage.research_wiki import ResearchWikiStore
from backend.app.api import runs as runs_api
from backend.app.workflow.hypothesis_contract import MAX_HYPOTHESIS_CANDIDATES


def mock_app(tmp_path):
    return create_app(data_dir=str(tmp_path), env={
        "COMPETITION_MODE": "false",
        "LLM_PROVIDER": "mock",
        "EXPERIMENT_PROVIDER": "mock",
        "LITERATURE_PROVIDER": "mock",
    })


def run_to_completed_feedback(client, run_id):
    latest = None
    for step_id in [
        "problem_understanding",
        "knowledge_integration",
        "hypothesis_generation",
        "evidence_reasoning",
        "research_plan",
        "experiment_task",
        "experiment_run_analysis",
        "feedback_revision",
    ]:
        response = client.post(f"/api/runs/{run_id}/steps/{step_id}/run")
        assert response.status_code == 200, response.text
        latest = response.json()
        if step_id == "evidence_reasoning":
            response = client.post(
                f"/api/runs/{run_id}/hypotheses/select",
                json={"candidate_index": 0},
            )
            assert response.status_code == 200, response.text
            latest = response.json()
    revision = [artifact for artifact in latest["artifacts"] if artifact["type"] == "revision"][-1]
    while revision["content"]["requires_follow_up"]:
        response = client.post(f"/api/runs/{run_id}/steps/research_plan/run")
        assert response.status_code == 200, response.text
        latest = response.json()
        for step_id in ["experiment_task", "experiment_run_analysis", "feedback_revision"]:
            response = client.post(f"/api/runs/{run_id}/steps/{step_id}/run")
            assert response.status_code == 200, response.text
            latest = response.json()
        revision = [
            artifact for artifact in latest["artifacts"] if artifact["type"] == "revision"
        ][-1]
    return latest


def test_terminate_experiment_preserves_or_clears_only_current_attempt(tmp_path, monkeypatch):
    experiment_root = tmp_path / "experiments"
    experiment_dir = experiment_root / "run_test" / "experiment_1"
    attempt_dir = experiment_dir / "attempts" / "attempt_test"
    attempt_dir.mkdir(parents=True)
    status = {
        "run_id": "run_test",
        "experiment_id": "experiment_1",
        "attempt_id": "attempt_test",
        "state": "orphaned",
        "pid": 12345,
        "updated_at": "2026-07-19T00:00:00+00:00",
    }
    (experiment_dir / "runtime_status.json").write_text(json.dumps(status), encoding="utf-8")
    (attempt_dir / "runtime_status.json").write_text(json.dumps(status), encoding="utf-8")
    (attempt_dir / "training.log").write_text("keep until cleared", encoding="utf-8")
    monkeypatch.setattr(runs_api, "_terminate_owned_experiment_process", lambda *_: True)
    monkeypatch.setattr(runs_api.time, "sleep", lambda *_: None)
    app = create_app(
        data_dir=str(tmp_path / "data"),
        env={
            "COMPETITION_MODE": "false",
            "LLM_PROVIDER": "mock",
            "EXPERIMENT_PROVIDER": "local_gpu",
            "LOCAL_GPU_ENABLED": "true",
            "LOCAL_EXPERIMENT_WORKDIR": str(experiment_root),
            "LOCAL_GPU_PYTHON": "python",
        },
    )
    client = TestClient(app)

    kept = client.post(
        "/api/runs/run_test/experiments/experiment_1/terminate",
        json={"clear_attempt": False},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["cleared"] is False
    assert attempt_dir.is_dir()
    assert json.loads((experiment_dir / "runtime_status.json").read_text())["state"] == "terminated"

    cleared = client.post(
        "/api/runs/run_test/experiments/experiment_1/terminate",
        json={"clear_attempt": True},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["cleared"] is True
    assert not attempt_dir.exists()
    assert not (experiment_dir / "runtime_status.json").exists()


def test_report_get_is_read_only_when_no_report_exists(tmp_path):
    client = TestClient(mock_app(tmp_path))
    created = client.post(
        "/api/runs",
        json={"title": "read only report", "problem_input": "train compact cnn"},
    ).json()
    run_to_completed_feedback(client, created["id"])

    response = client.get(f"/api/runs/{created['id']}/report")

    assert response.status_code == 404
    stored = client.get(f"/api/runs/{created['id']}").json()
    assert not any(artifact["type"] == "report" for artifact in stored["artifacts"])


def test_report_post_generates_once_and_get_returns_existing_artifact(tmp_path):
    client = TestClient(mock_app(tmp_path))
    created = client.post(
        "/api/runs",
        json={"title": "single report", "problem_input": "train compact cnn"},
    ).json()
    run_to_completed_feedback(client, created["id"])

    generated = client.post(f"/api/runs/{created['id']}/steps/report_export/run")
    first_get = client.get(f"/api/runs/{created['id']}/report")
    second_get = client.get(f"/api/runs/{created['id']}/report")

    assert generated.status_code == 200, generated.text
    assert first_get.status_code == 200
    assert second_get.status_code == 200
    assert first_get.json() == second_get.json()
    stored = client.get(f"/api/runs/{created['id']}").json()
    reports = [artifact for artifact in stored["artifacts"] if artifact["type"] == "report"]
    assert len(reports) == 1
    assert reports[0]["version"] == 1


class FakeVerificationProvider:
    provider_name = "fake_external"

    def search(self, query: str, limit: int):
        return []

    def verify(self, card: EvidenceCard) -> EvidenceCard:
        return card.model_copy(update={"verified": True})

    def verify_identifier(self, identifiers: dict[str, str]) -> EvidenceCard | None:
        if not identifiers.get("arxiv") and not identifiers.get("doi"):
            return None
        return EvidenceCard(
            title="Canonical Paper",
            authors=["Ada Researcher"],
            year=2024,
            source=self.provider_name,
            claim="verified metadata",
            url="https://arxiv.org/abs/2401.00001",
            identifiers={key: value for key, value in identifiers.items() if value},
            verified=True,
        )


def test_api_creates_run_and_runs_problem_step(tmp_path):
    client = TestClient(mock_app(tmp_path))

    created = client.post("/api/runs", json={"title": "CNN run", "problem_input": "train compact cnn"}).json()
    run_id = created["id"]
    stepped = client.post(f"/api/runs/{run_id}/steps/problem_understanding/run").json()

    assert stepped["id"] == run_id
    assert stepped["artifacts"][0]["type"] == "problem"


def test_upload_list_get_and_delete_local_literature(tmp_path):
    client = TestClient(mock_app(tmp_path))

    response = client.post(
        "/api/literature/documents",
        files={"file": ("paper.txt", b"robust training", "text/plain")},
        data={"title": "Robust Training"},
    )

    assert response.status_code == 200
    document = response.json()
    assert document["source"] == "local_upload"
    assert client.get("/api/literature/documents").json()[0]["id"] == document["id"]
    assert client.get(f"/api/literature/documents/{document['id']}").status_code == 200
    assert client.delete(f"/api/literature/documents/{document['id']}").json()["deleted"] is True
    assert client.get(f"/api/literature/documents/{document['id']}").status_code == 404


def test_research_knowledge_bases_lists_default_document_and_run_scopes(tmp_path):
    client = TestClient(mock_app(tmp_path))
    client.post(
        "/api/literature/documents",
        files={"file": ("paper.txt", b"robust training", "text/plain")},
        data={"title": "Robust Training", "knowledge_base_id": "vision"},
    )
    client.post("/api/runs", json={"title": "NLP run", "problem_input": "classify text", "knowledge_base_id": "nlp"})

    response = client.get("/api/research-wiki/knowledge-bases")

    assert response.status_code == 200
    assert [item["knowledge_base_id"] for item in response.json()] == ["default", "nlp", "vision"]


def test_online_literature_search_reuses_configured_provider(tmp_path):
    client = TestClient(mock_app(tmp_path))

    response = client.get("/api/literature/search", params={"query": "robust training", "limit": 2})

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "robust training"
    assert payload["provider"] == "mock_literature"
    assert len(payload["results"]) == 2
    assert "claim" in payload["results"][0]


def test_duplicate_literature_upload_returns_409_with_existing_id(tmp_path):
    client = TestClient(mock_app(tmp_path))
    first = client.post(
        "/api/literature/documents",
        files={"file": ("one.txt", b"same", "text/plain")},
    ).json()

    duplicate = client.post(
        "/api/literature/documents",
        files={"file": ("two.txt", b"same", "text/plain")},
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == {
        "code": "LITERATURE_DUPLICATE",
        "document_id": first["id"],
    }


def test_verify_local_literature_uses_external_identifier_provider(tmp_path):
    app = create_app(
        data_dir=str(tmp_path),
        env={
            "COMPETITION_MODE": "false",
            "LLM_PROVIDER": "mock",
            "EXPERIMENT_PROVIDER": "mock",
            "LITERATURE_PROVIDER": "mock",
        },
        literature_provider_override=FakeVerificationProvider(),
    )
    client = TestClient(app)
    paper_id = client.post(
        "/api/literature/documents",
        files={"file": ("paper.txt", b"paper", "text/plain")},
        data={"arxiv": "2401.00001"},
    ).json()["id"]

    response = client.post(f"/api/literature/documents/{paper_id}/verify")

    assert response.status_code == 200
    assert response.json()["verification"]["verified"] is True
    assert response.json()["verification"]["provider"] == "fake_external"
    assert response.json()["title"] == "Canonical Paper"


def test_verify_local_literature_without_identifier_is_422(tmp_path):
    client = TestClient(mock_app(tmp_path))
    paper_id = client.post(
        "/api/literature/documents",
        files={"file": ("paper.txt", b"paper", "text/plain")},
    ).json()["id"]

    response = client.post(f"/api/literature/documents/{paper_id}/verify")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "LITERATURE_REFERENCE_UNVERIFIED"


def test_verification_drops_user_identifier_not_returned_by_provider(tmp_path):
    class ArxivOnlyProvider(FakeVerificationProvider):
        def verify_identifier(self, identifiers):
            card = super().verify_identifier({"arxiv": identifiers.get("arxiv", "")})
            return card.model_copy(update={"identifiers": {"arxiv": "2401.00001"}})

    app = create_app(
        data_dir=str(tmp_path),
        env={
            "COMPETITION_MODE": "false",
            "LLM_PROVIDER": "mock",
            "EXPERIMENT_PROVIDER": "mock",
            "LITERATURE_PROVIDER": "mock",
        },
        literature_provider_override=ArxivOnlyProvider(),
    )
    client = TestClient(app)
    document = client.post(
        "/api/literature/documents",
        files={"file": ("paper.txt", b"paper identifiers", "text/plain")},
        data={"arxiv": "2401.00001", "doi": "10.9999/wrong-paper"},
    ).json()

    verified = client.post(
        f"/api/literature/documents/{document['id']}/verify"
    ).json()

    assert verified["identifiers"] == {"arxiv": "2401.00001"}


def test_attach_local_literature_to_run_creates_local_only_evidence(tmp_path):
    client = TestClient(mock_app(tmp_path))
    run = client.post(
        "/api/runs", json={"title": "Literature", "problem_input": "test"}
    ).json()
    document = client.post(
        "/api/literature/documents",
        files={"file": ("paper.txt", b"paper", "text/plain")},
        data={"title": "Local Paper"},
    ).json()

    response = client.post(
        f"/api/runs/{run['id']}/literature/{document['id']}/attach"
    )

    assert response.status_code == 200
    evidence = response.json()
    assert evidence["type"] == "evidence"
    assert evidence["content"]["local_only"][0]["local_document_id"] == document["id"]
    assert evidence["content"]["local_only"][0]["verified"] is False


def test_attach_verified_local_literature_adds_it_to_research_references(tmp_path):
    app = create_app(
        data_dir=str(tmp_path),
        env={
            "COMPETITION_MODE": "false",
            "LLM_PROVIDER": "mock",
            "EXPERIMENT_PROVIDER": "mock",
            "LITERATURE_PROVIDER": "mock",
        },
        literature_provider_override=FakeVerificationProvider(),
    )
    client = TestClient(app)
    run = client.post(
        "/api/runs", json={"title": "Verified local", "problem_input": "test"}
    ).json()
    document = client.post(
        "/api/literature/documents",
        files={"file": ("paper.txt", b"verified paper", "text/plain")},
        data={"arxiv": "2401.00001"},
    ).json()
    verified = client.post(
        f"/api/literature/documents/{document['id']}/verify"
    ).json()

    response = client.post(
        f"/api/runs/{run['id']}/literature/{document['id']}/attach"
    )

    assert response.status_code == 200
    evidence = response.json()["content"]
    reference = next(
        item for item in evidence["references"]
        if item["local_document_id"] == document["id"]
    )
    assert reference["verified"] is True
    assert reference["identifiers"] == verified["identifiers"]
    assert not any(
        item["local_document_id"] == document["id"]
        for item in evidence.get("local_only", [])
    )


def test_verifying_already_attached_document_upgrades_run_evidence(tmp_path):
    app = create_app(
        data_dir=str(tmp_path),
        env={
            "COMPETITION_MODE": "false",
            "LLM_PROVIDER": "mock",
            "EXPERIMENT_PROVIDER": "mock",
            "LITERATURE_PROVIDER": "mock",
        },
        literature_provider_override=FakeVerificationProvider(),
    )
    client = TestClient(app)
    run = client.post(
        "/api/runs", json={"title": "Upgrade evidence", "problem_input": "test"}
    ).json()
    document = client.post(
        "/api/literature/documents",
        files={"file": ("paper.txt", b"upgrade paper", "text/plain")},
        data={"arxiv": "2401.00001"},
    ).json()
    client.post(f"/api/runs/{run['id']}/literature/{document['id']}/attach")

    response = client.post(f"/api/literature/documents/{document['id']}/verify")

    assert response.status_code == 200
    latest = client.get(f"/api/runs/{run['id']}").json()
    evidence = [
        artifact for artifact in latest["artifacts"] if artifact["type"] == "evidence"
    ][-1]["content"]
    assert any(
        item["local_document_id"] == document["id"] and item["verified"]
        for item in evidence["references"]
    )
    assert not any(
        item["local_document_id"] == document["id"]
        for item in evidence.get("local_only", [])
    )


def test_verifying_document_already_in_wiki_upgrades_wiki_node(tmp_path):
    app = create_app(
        data_dir=str(tmp_path),
        env={
            "COMPETITION_MODE": "false",
            "LLM_PROVIDER": "mock",
            "EXPERIMENT_PROVIDER": "mock",
            "LITERATURE_PROVIDER": "mock",
        },
        literature_provider_override=FakeVerificationProvider(),
    )
    client = TestClient(app)
    document = client.post(
        "/api/literature/documents",
        files={"file": ("paper.txt", b"wiki upgrade", "text/plain")},
        data={"title": "Draft", "arxiv": "2401.00001"},
    ).json()
    client.post(f"/api/literature/documents/{document['id']}/wiki")

    response = client.post(f"/api/literature/documents/{document['id']}/verify")

    assert response.status_code == 200
    papers = ResearchWikiStore(tmp_path / "research-wiki").query("Canonical Paper").papers
    assert papers[0]["verified"] is True
    assert papers[0]["local_document_id"] == document["id"]


def test_add_unverified_local_document_to_wiki_preserves_unverified_status(tmp_path):
    client = TestClient(mock_app(tmp_path))
    document = client.post(
        "/api/literature/documents",
        files={"file": ("paper.txt", b"robust local evidence", "text/plain")},
        data={"title": "Local Robustness Note"},
    ).json()

    response = client.post(f"/api/literature/documents/{document['id']}/wiki")

    assert response.status_code == 200
    stored = client.get(f"/api/literature/documents/{document['id']}").json()
    assert stored["wiki_node_id"].startswith("paper:")
    assert stored["verification"]["verified"] is False


def test_api_creates_run_with_editable_topic_fields(tmp_path):
    client = TestClient(mock_app(tmp_path))

    created = client.post(
        "/api/runs",
        json={
            "title": "My topic",
            "domain": "graph neural networks",
            "problem_input": "Can GNN pruning preserve accuracy?",
            "constraints": "Use real datasets and fixed seeds.",
        },
    ).json()

    assert created["title"] == "My topic"
    assert created["domain"] == "graph neural networks"
    assert created["problem_input"] == "Can GNN pruning preserve accuracy?"
    assert created["constraints"] == "Use real datasets and fixed seeds."


def test_api_deletes_run_from_history(tmp_path):
    client = TestClient(mock_app(tmp_path))
    created = client.post("/api/runs", json={"title": "delete me", "problem_input": "x"}).json()

    response = client.delete(f"/api/runs/{created['id']}")

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "run_id": created["id"]}
    assert client.get("/api/runs").json() == []
    assert client.get(f"/api/runs/{created['id']}").status_code == 404


def test_provider_settings_endpoint_reports_modes(tmp_path):
    app = create_app(
        data_dir=str(tmp_path),
        env={"COMPETITION_MODE": "true", "EXPERIMENT_PROVIDER": "mock", "LLM_PROVIDER": "qwen"},
    )
    client = TestClient(app)

    response = client.get("/api/settings/providers")

    assert response.status_code == 200
    assert response.json()["llm"]["code"] == "QWEN_API_KEY_MISSING"
    assert response.json()["experiment"]["code"] == "REAL_EXPERIMENT_REQUIRED"


def test_runtime_info_is_non_secret_and_identifies_the_loaded_workflow(tmp_path):
    client = TestClient(mock_app(tmp_path))

    response = client.get("/api/system/runtime-info")

    assert response.status_code == 200
    body = response.json()
    assert body["pid"] > 0
    assert body["workflow_version"] == "research-loop-v2"
    assert len(body["workflow_hash"]) == 64
    assert body["module_paths"]["backend.app.workflow.engine"].endswith("backend\\app\\workflow\\engine.py")
    assert body["skills"]["evidence-recovery"] != "missing"
    assert "api_key" not in str(body).lower()


def test_experiment_settings_can_be_saved_and_reflected_in_provider_status(tmp_path):
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "true", "LLM_PROVIDER": "qwen"})
    client = TestClient(app)

    response = client.post(
        "/api/settings/experiment",
        json={
            "provider": "remote_gpu",
            "remote": {
                "host": "gpu.example.com",
                "user": "runner",
                "port": 22,
                "ssh_key_path": "C:/Users/runner/.ssh/id_rsa",
                "project_dir": "/srv/ai-scientist",
                "python": "python",
                "cuda_visible_devices": "0",
                "timeout_seconds": 900,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "remote_gpu"
    status = client.get("/api/settings/providers").json()
    assert status["experiment"]["ready"] is True
    assert status["experiment"]["code"] == ""


def test_local_experiment_settings_create_missing_workdir_on_save(tmp_path):
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "false", "LLM_PROVIDER": "mock"})
    client = TestClient(app)
    missing_workdir = tmp_path / "does-not-exist"

    response = client.post(
        "/api/settings/experiment",
        json={
            "provider": "local_gpu",
            "local": {"enabled": True, "workdir": str(missing_workdir)},
        },
    )

    assert response.status_code == 200
    assert missing_workdir.is_dir()
    body = response.json()
    assert body["provider"] == "local_gpu"
    assert body["local"]["workdir"] == str(missing_workdir)


def test_qwen_key_can_be_saved_without_returning_secret(tmp_path):
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "true", "LLM_PROVIDER": "qwen"})
    client = TestClient(app)

    response = client.post("/api/settings/qwen-key", json={"api_key": "sk-test", "model": "qwen-plus"})

    assert response.status_code == 200
    assert response.json() == {"configured": True, "model": "qwen-plus"}
    status = client.get("/api/settings/providers").json()
    assert status["llm"]["ready"] is True
    assert status["llm"]["code"] == ""


def test_qwen_key_save_rebuilds_provider_for_subsequent_requests(tmp_path, monkeypatch):
    applied = []

    def recording_factory(settings):
        applied.append((settings.qwen_api_key, settings.qwen_model))
        return MockLLMProvider()

    monkeypatch.setattr("backend.app.main.get_llm_provider", recording_factory)
    app = create_app(
        data_dir=str(tmp_path),
        env={"COMPETITION_MODE": "false", "LLM_PROVIDER": "qwen"},
    )

    response = TestClient(app).post(
        "/api/settings/qwen-key",
        json={"api_key": "sk-replacement", "model": "qwen-plus"},
    )

    assert response.status_code == 200
    assert applied == [("", "qwen3.7-plus"), ("sk-replacement", "qwen-plus")]


def test_experiment_connection_test_reports_missing_remote_fields(tmp_path):
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "true", "LLM_PROVIDER": "qwen"})
    client = TestClient(app)

    response = client.post("/api/settings/experiment/test", json={"provider": "remote_gpu", "remote": {}})

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "REMOTE_GPU_HOST" in response.json()["missing"]


def test_remote_experiment_connection_test_runs_ssh_readiness_check(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text, timeout):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"available": true, "device_count": 1, "device_names": ["Remote GPU"], '
                '"python_version": "3.12.0", "torch_version": "2.13.0", "torch_cuda": "13.2"}'
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "false"})
    client = TestClient(app)

    response = client.post(
        "/api/settings/experiment/test",
        json={
            "provider": "remote_gpu",
            "remote": {
                "host": "gpu.example.com",
                "user": "runner",
                "port": 2222,
                "ssh_key_path": "C:/Users/runner/.ssh/id_rsa",
                "project_dir": "/srv/ai-scientist",
                "python": "python",
            },
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["code"] == ""
    assert body["host"] == "gpu.example.com"
    assert body["device_names"] == ["Remote GPU"]
    assert body["python_version"] == "3.12.0"
    assert "BatchMode=yes" in calls[0]
    assert "torch.cuda.is_available" in calls[0][-1]


def test_remote_experiment_connection_test_reports_ssh_failure(tmp_path, monkeypatch):
    def fake_run(command, check, capture_output, text, timeout):
        raise subprocess.CalledProcessError(255, command, output="", stderr="permission denied")

    monkeypatch.setattr(subprocess, "run", fake_run)
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "false"})
    client = TestClient(app)

    response = client.post(
        "/api/settings/experiment/test",
        json={
            "provider": "remote_gpu",
            "remote": {"host": "gpu.example.com", "user": "runner", "project_dir": "/srv/ai-scientist"},
        },
    )

    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "REMOTE_GPU_SSH_FAILED"
    assert "permission denied" in body["stderr_tail"]


def test_local_experiment_connection_test_reports_missing_workdir_value(tmp_path):
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "false"})
    client = TestClient(app)

    response = client.post(
        "/api/settings/experiment/test",
        json={
            "provider": "local_gpu",
            "local": {"enabled": True, "workdir": ""},
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is False
    assert body["code"] == "LOCAL_EXPERIMENT_WORKDIR_INVALID"
    assert body["workdir"] == ""
    assert body["resolved_workdir"] == ""
    assert body["missing"] == ["LOCAL_EXPERIMENT_WORKDIR"]
    assert body["message"]


def test_local_experiment_connection_test_allows_generated_train_entrypoint(
    tmp_path, monkeypatch
):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"available": true, "device_count": 1, "device_names": ["RTX Test"], '
                '"python_version": "3.12.0", "torch_version": "2.13.0", "torch_cuda": "13.2"}'
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "false"})
    client = TestClient(app)
    workdir = tmp_path / "experiments"
    workdir.mkdir()

    response = client.post(
        "/api/settings/experiment/test",
        json={
            "provider": "local_gpu",
            "local": {"enabled": True, "workdir": str(workdir)},
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["code"] == ""
    assert body["entrypoint"] == str(workdir.resolve() / "train.py")
    assert body["entrypoint_exists"] is False


def test_local_experiment_connection_test_reports_valid_configured_and_resolved_workdir(
    tmp_path, monkeypatch
):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"available": true, "device_count": 1, "device_names": ["RTX Test"], '
                '"python_version": "3.12.0", "torch_version": "2.13.0", "torch_cuda": "13.2"}'
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "false"})
    client = TestClient(app)
    workdir = tmp_path / "experiments"
    workdir.mkdir()
    (workdir / "train.py").write_text("# test entrypoint\n", encoding="utf-8")

    response = client.post(
        "/api/settings/experiment/test",
        json={
            "provider": "local_gpu",
            "local": {
                "enabled": True,
                "workdir": str(workdir),
                "python": r"D:\competition\.venv\Scripts\python.exe",
                "cuda_visible_devices": "0",
            },
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["code"] == ""
    assert body["workdir"] == str(workdir)
    assert body["resolved_workdir"] == str(workdir.resolve())
    assert body["entrypoint"] == str(workdir.resolve() / "train.py")
    assert body["entrypoint_exists"] is True
    assert body["missing"] == []
    assert body["python_version"] == "3.12.0"
    assert body["torch_version"] == "2.13.0"
    assert body["cuda_available"] is True
    assert body["device_names"] == ["RTX Test"]
    assert body["dependency_status"] == "ready"
    assert calls[0][0] == r"D:\competition\.venv\Scripts\python.exe"


def test_local_experiment_connection_test_rejects_gpu_model_as_device_index(
    tmp_path, monkeypatch
):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                '{"available": true, "device_count": 1, "device_names": ["NVIDIA RTX 5070"], '
                '"python_version": "3.12.0", "torch_version": "2.13.0", "torch_cuda": "13.2"}'
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "false"})

    response = TestClient(app).post(
        "/api/settings/experiment/test",
        json={
            "provider": "local_gpu",
            "local": {
                "enabled": True,
                "workdir": str(tmp_path / "experiments"),
                "python": "configured-python",
                "cuda_visible_devices": "5070",
            },
        },
    )

    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "LOCAL_GPU_DEVICE_INDEX_INVALID"
    assert body["device_names"] == ["NVIDIA RTX 5070"]
    assert body["available_device_indexes"] == [0]


def test_api_accepts_user_hypothesis_selection_after_reasoning(tmp_path):
    client = TestClient(mock_app(tmp_path))
    run = client.post("/api/runs", json={"title": "select", "problem_input": "train compact cnn"}).json()
    run_id = run["id"]
    for step in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = client.post(f"/api/runs/{run_id}/steps/{step}/run").json()
    run = client.post(f"/api/runs/{run_id}/steps/evidence_reasoning/run").json()

    response = client.post(
        f"/api/runs/{run_id}/hypotheses/select",
        json={"candidate_index": 0},
    )

    assert response.status_code == 200
    selections = [
        artifact
        for artifact in response.json()["artifacts"]
        if artifact["type"] == "hypothesis_selection"
    ]
    assert selections[-1]["content"]["selected_indexes"] == [0]
    assert selections[-1]["content"]["selection_mode"] == "user_selected_after_evidence_reasoning"


def test_api_regenerates_hypotheses_as_new_round_without_rerunning_literature(tmp_path):
    client = TestClient(mock_app(tmp_path))
    run = client.post("/api/runs", json={"title": "regenerate", "problem_input": "train compact cnn"}).json()
    run_id = run["id"]
    for step in ["problem_understanding", "knowledge_integration", "hypothesis_generation", "evidence_reasoning"]:
        run = client.post(f"/api/runs/{run_id}/steps/{step}/run").json()

    evidence_before = [a for a in run["artifacts"] if a["type"] == "evidence"]
    response = client.post(f"/api/runs/{run_id}/hypotheses/regenerate")
    assert response.status_code == 200, response.text

    hypotheses = [a for a in response.json()["artifacts"] if a["type"] == "hypothesis"]
    assert len(hypotheses) == 2
    assert hypotheses[-1]["content"]["hypothesis_round"]["round_index"] == 2
    # Regeneration reuses the existing literature collection.
    assert len([a for a in response.json()["artifacts"] if a["type"] == "evidence"]) == len(evidence_before)


def test_api_adds_user_hypothesis_after_evidence_review(tmp_path):
    client = TestClient(mock_app(tmp_path))
    run = client.post("/api/runs", json={"title": "user hypothesis", "problem_input": "train compact cnn"}).json()
    run_id = run["id"]
    for step in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = client.post(f"/api/runs/{run_id}/steps/{step}/run").json()
    run = client.post(f"/api/runs/{run_id}/steps/evidence_reasoning/run").json()

    response = client.post(
        f"/api/runs/{run_id}/hypotheses/user",
        json={"claim": "A smaller CNN may be more reproducible under fixed seeds."},
    )

    assert response.status_code == 200
    candidates = [
        artifact for artifact in response.json()["artifacts"] if artifact["type"] == "hypothesis"
    ][-1]["content"]["candidates"]
    assert any(candidate.get("source") == "user" for candidate in candidates)
    assert not any(
        artifact["type"] == "hypothesis_selection"
        for artifact in response.json()["artifacts"]
    )


def test_api_requires_replacement_when_user_hypothesis_would_exceed_candidate_limit(tmp_path):
    client = TestClient(mock_app(tmp_path))
    run = client.post("/api/runs", json={"title": "replace hypothesis", "problem_input": "train compact cnn"}).json()
    run_id = run["id"]
    for step in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = client.post(f"/api/runs/{run_id}/steps/{step}/run").json()
    initial_candidates = [
        artifact for artifact in run["artifacts"] if artifact["type"] == "hypothesis"
    ][-1]["content"]["candidates"]
    for index in range(MAX_HYPOTHESIS_CANDIDATES - len(initial_candidates)):
        response = client.post(
            f"/api/runs/{run_id}/hypotheses/user",
            json={"claim": f"user supplement hypothesis {index}"},
        )
        assert response.status_code == 200

    full_response = client.post(
        f"/api/runs/{run_id}/hypotheses/user",
        json={"claim": "sixth candidate should require explicit replacement"},
    )
    replacement_response = client.post(
        f"/api/runs/{run_id}/hypotheses/user",
        json={"claim": "replace candidate 1", "replacement_index": 1},
    )

    assert full_response.status_code == 409
    assert full_response.json()["detail"]["code"] == "HYPOTHESIS_REPLACEMENT_REQUIRED"
    assert replacement_response.status_code == 200
    candidates = [
        artifact
        for artifact in replacement_response.json()["artifacts"]
        if artifact["type"] == "hypothesis"
    ][-1]["content"]["candidates"]
    assert len(candidates) == 5
    assert candidates[1]["claim"] == "replace candidate 1"
