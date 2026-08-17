import time
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.providers.llm import MockLLMProvider


def test_development_end_to_end_loop_marks_mock_reasoning_and_mock_experiment(tmp_path):
    app = create_app(data_dir=str(tmp_path), env={
        "COMPETITION_MODE": "false",
        "LLM_PROVIDER": "mock",
        "LITERATURE_PROVIDER": "mock",
        "EXPERIMENT_PROVIDER": "mock",
    })
    client = TestClient(app)
    run = client.post("/api/runs", json={"title": "Dev run", "problem_input": "train compact cnn"}).json()

    for step_id in [
        "problem_understanding",
        "knowledge_integration",
        "hypothesis_generation",
        "evidence_reasoning",
    ]:
        response = client.post(f"/api/runs/{run['id']}/steps/{step_id}/run")
        assert response.status_code == 200, response.text
    selected = client.post(
        f"/api/runs/{run['id']}/hypotheses/select",
        json={"candidate_index": 0},
    )
    assert selected.status_code == 200, selected.text
    for _ in range(300):
        latest_run = client.get(f"/api/runs/{run['id']}").json()
        if latest_run["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    assert latest_run["status"] == "completed", latest_run

    response = client.get(f"/api/runs/{run['id']}/report")
    body = response.json()

    assert response.status_code == 200
    assert body["Results"]["is_real_experiment"] is False
    zip_download = client.get(f"/api/runs/{run['id']}/report/download")
    docx_download = client.get(f"/api/runs/{run['id']}/report/download?format=docx")
    assert zip_download.status_code == 200
    assert zip_download.headers["content-type"].startswith("application/zip")
    with ZipFile(BytesIO(zip_download.content)) as package:
        names = set(package.namelist())
        assert "研究报告/科学假设与研究报告.docx" in names
        assert any(name.startswith("实验代码/") and name.endswith("train.py") for name in names)
        assert any(name.startswith("实验结果/") and name.endswith(".json") for name in names)
        assert not any(name.endswith((".html", ".md")) for name in names)
        assert "MANIFEST.json" not in names
        with ZipFile(BytesIO(package.read("研究报告/科学假设与研究报告.docx"))) as document:
            assert "word/document.xml" in document.namelist()
    assert docx_download.status_code == 200
    assert "wordprocessingml.document" in docx_download.headers["content-type"]
    latest = client.get(f"/api/runs/{run['id']}").json()
    assert len([artifact for artifact in latest["artifacts"] if artifact["type"] == "report"]) == 1
    final_state = [
        artifact
        for artifact in latest["artifacts"]
        if artifact["type"] == "research_state"
    ][-1]["content"]
    ledger_ids = {
        item["artifact_id"] for item in final_state["artifact_states"]
    }
    process_ids = {
        artifact["id"]
        for artifact in latest["artifacts"]
        if artifact["type"] != "research_state"
    }
    assert ledger_ids == process_ids
    assert any(
        item["artifact_type"] == "report"
        and item["lifecycle_status"] == "active"
        and item["validity_status"] == "verified"
        for item in final_state["artifact_states"]
    )
    assert any(event["fallback_used"] for event in latest["events"])


def test_qwen_mode_semantically_reviews_high_risk_candidate_before_persisting(
    tmp_path, monkeypatch
):
    class ReviewingLLM(MockLLMProvider):
        mode = "qwen"
        fallback = False

        def __init__(self):
            self.tasks = []

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            self.tasks.append(task)
            if task == "hypothesis.generate":
                result = super().generate_json(
                    task, inputs, schema_hint, instructions
                )
                source = inputs["verified_evidence"][0]
                for candidate in result["candidates"]:
                    candidate["evidence_basis"] = [
                        {
                            "statement": source["claim"],
                            "source_title": source["title"],
                            "source_url": source["url"],
                            "evidence_type": "FACT",
                        }
                    ]
                return result
            if task == "critic.evidence_reasoning":
                result = super().generate_json(
                    task, inputs, schema_hint, instructions
                )
                evidence_id = inputs["evidence_audit"]["candidate_audit"][
                    "matched_evidence_ids"
                ][0]
                result["claim_evidence_map"] = [
                    {
                        "claim": inputs["hypothesis"]["claim"],
                        "evidence_id": evidence_id,
                        "stance": "support",
                        "relation": "INDIRECT",
                        "strength": "low",
                        "limitation": "Development fixture.",
                    }
                ]
                return result
            if task == "reviewer.semantic":
                assert inputs["step_id"] == "evidence_reasoning"
                assert inputs["artifact"]["active_hypothesis"]
                return {"accepted": True, "issues": []}
            return super().generate_json(task, inputs, schema_hint, instructions)

    llm = ReviewingLLM()
    monkeypatch.setattr("backend.app.main.get_llm_provider", lambda settings: llm)
    app = create_app(
        data_dir=str(tmp_path / "data"),
        env={
            "COMPETITION_MODE": "false",
            "LLM_PROVIDER": "qwen",
            "LITERATURE_PROVIDER": "mock",
            "EXPERIMENT_PROVIDER": "mock",
            "QWEN_API_KEY": "test-only",
        },
    )
    client = TestClient(app)
    run = client.post(
        "/api/runs",
        json={"title": "Review run", "problem_input": "train compact cnn"},
    ).json()

    for step_id in [
        "problem_understanding",
        "knowledge_integration",
        "hypothesis_generation",
        "evidence_reasoning",
    ]:
        response = client.post(f"/api/runs/{run['id']}/steps/{step_id}/run")
        assert response.status_code == 200, response.text

    assert llm.tasks.count("idea_selection.review") == 1
    assert "reviewer.semantic" not in llm.tasks
    assert not (tmp_path / "data" / "staging").exists()
