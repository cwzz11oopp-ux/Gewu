import time
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.storage.repository import Repository


def wait_for_status(client, run_id: str, statuses: set[str]):
    for _ in range(300):
        state = client.get(f"/api/runs/{run_id}/paper-writing").json()
        if state["status"] in statuses:
            return state
        time.sleep(0.01)
    raise AssertionError(state)


def test_interactive_paper_writing_requires_checkpoints_and_exports_word_and_latex(tmp_path):
    data_dir = str(tmp_path / "data")
    repository = Repository(data_dir)
    run = repository.create_run("验证论文写作流程", "论文写作测试")
    repository.add_artifact(
        run.id,
        "report",
        "Competition Report",
        {
            "Paper Title": "可验证论文写作",
            "Problem Statement": "验证交互式论文写作。",
            "Results": {"is_real_experiment": True, "metrics": {"accuracy": 0.9}},
            "References": [],
        },
        "report_export",
        "test",
    )
    repository.add_artifact(
        run.id,
        "experiment_result",
        "Experiment Result",
        {"is_real_experiment": True, "metrics": {"accuracy": 0.9}},
        "experiment_run_analysis",
        "test",
    )
    app = create_app(
        data_dir=data_dir,
        env={
            "COMPETITION_MODE": "false",
            "LLM_PROVIDER": "mock",
            "LITERATURE_PROVIDER": "mock",
            "EXPERIMENT_PROVIDER": "mock",
        },
    )
    client = TestClient(app)

    started = client.post(
        f"/api/runs/{run.id}/paper-writing/start",
        json={
            "venue": "测试期刊",
            "language": "zh-CN",
            "paper_type": "实验研究论文",
            "authors": "研究者",
            "notes": "保留负结果",
        },
    )
    assert started.status_code == 200
    plan_state = wait_for_status(client, run.id, {"waiting_plan_confirmation", "failed"})
    assert plan_state["status"] == "waiting_plan_confirmation", plan_state
    assert plan_state["active_skill"] == "paper-plan"
    assert plan_state["plan"]["sections"]

    confirmed = client.post(
        f"/api/runs/{run.id}/paper-writing/confirm-plan",
        json={"feedback": ""},
    )
    assert confirmed.status_code == 200
    draft_state = wait_for_status(client, run.id, {"waiting_final_confirmation", "failed"})
    assert draft_state["status"] == "waiting_final_confirmation", draft_state
    assert len(draft_state["sections"]) == len(draft_state["plan"]["sections"])
    assert draft_state["audit"]["accepted"] is True

    finalized = client.post(
        f"/api/runs/{run.id}/paper-writing/finalize",
        json={"feedback": ""},
    )
    assert finalized.status_code == 200
    assert finalized.json()["status"] == "completed"

    word = client.get(f"/api/runs/{run.id}/paper-writing/download?format=docx")
    latex = client.get(f"/api/runs/{run.id}/paper-writing/download?format=latex")
    assert word.status_code == 200
    with ZipFile(BytesIO(word.content)) as document:
        assert "word/document.xml" in document.namelist()
    assert latex.status_code == 200
    with ZipFile(BytesIO(latex.content)) as package:
        names = set(package.namelist())
        assert "main.tex" in names
        assert "references.bib" in names
        assert any(name.startswith("sections/") for name in names)
