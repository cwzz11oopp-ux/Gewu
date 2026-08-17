from docx import Document

from backend.app.models.artifact import Artifact
from backend.app.report_visualization import build_report_spec, render_figure_png
from backend.app.reporting import build_report_docx


def artifact(kind: str, content: dict, identifier: str) -> Artifact:
    return Artifact(
        id=identifier,
        run_id="run_visual",
        type=kind,
        version=1,
        title=kind,
        content=content,
        source_step=kind,
        created_by="test",
    )


def test_report_spec_uses_persisted_values_and_never_invents_training_curve():
    plan = artifact("plan", {"parameters": {"learning_rate": 0.001, "batch_size": 64}, "seeds": [1, 2]}, "art_plan")
    result = artifact(
        "experiment_result",
        {
            "is_real_experiment": True,
            "metrics": {"CNN Test Accuracy": 91.362, "MLP Test Accuracy": 88.888},
            "seed_results": [
                {"seed": 1, "CNN Test Accuracy": 91.0, "MLP Test Accuracy": 88.1},
                {"seed": 2, "CNN Test Accuracy": 91.7, "MLP Test Accuracy": 89.7},
            ],
        },
        "art_result",
    )
    spec = build_report_spec(
        [plan, result],
        selected_figure_ids=["research_workflow", "control_variables", "seed_comparison", "main_comparison", "training_curve", "workflow_timeline"],
        decision_rationale="仅选择已保存数据支持的图。",
    )

    figures = {item["figure_id"]: item for item in spec["figures"]}
    assert figures["main_comparison"]["source_artifact_ids"] == ["art_result"]
    assert figures["main_comparison"]["chart"]["series"][0]["values"] == [91.362, 88.888]
    assert figures["seed_comparison"]["chart"]["labels"] == ["1", "2"]
    assert "training_curve" not in figures
    assert any(item["figure_id"] == "training_curve" and "epoch/step" in item["reason"] for item in spec["omitted_figures"])
    assert render_figure_png(figures["main_comparison"]).startswith(b"\x89PNG")


def test_docx_embeds_only_report_spec_figures_and_lists_missing_data_reason():
    result = artifact("experiment_result", {"metrics": {"A": 0.8, "B": 0.9}}, "art_result")
    spec = build_report_spec([result], selected_figure_ids=["main_comparison"], decision_rationale="主比较图即可。")
    payload = build_report_docx(
        {"Paper Title": "可视化报告", "Paper Abstract": "摘要", "Report Spec": spec},
        run_id="run_visual",
        run_title="可视化报告",
    )
    document = Document(__import__("io").BytesIO(payload))
    assert len(document.inline_shapes) == 1
    assert any("未生成 training_curve" in paragraph.text for paragraph in document.paragraphs)
