import pytest

from backend.app.agents.writer import WriterAgent
from backend.app.models.artifact import Artifact
from backend.app.reporting import render_report_html
from backend.app.workflow.policies import competition_export_allowed


class ReportLLM:
    mode = "qwen"
    fallback = False

    def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
        if task == "writer.report_outline":
            return {
                "title": "Verified Qwen-guided Experiment",
                "central_question": "real problem",
                "narrative_logic": "problem to result",
                "section_plans": [],
                "reference_selection": ["paper", "verified"],
                "selected_figure_ids": ["research_workflow", "main_comparison", "workflow_timeline"],
                "figure_rationale": "选择可由已保存研究事实直接支持的流程图、主要对比图和时间线。",
            }
        if task in {"writer.report_section", "writer.revise_report_section"}:
            section = inputs.get("required_section") or inputs.get("section") or {}
            return {
                "id": section.get("id"),
                "title": section.get("title"),
                "paragraphs": [
                    "本段根据已经保存的研究事实说明问题、方法和证据边界，并保持各项实验条件之间的逻辑联系。" * 8,
                    "本段继续说明受控比较的设置、评价指标及其解释方式，不补造输入产物没有记录的研究信息。" * 8,
                    "本段结合实验结果限定能够形成的判断，同时说明当前证据尚不能支持的外推范围和因果解释。" * 8,
                    "本段承接前述分析并为下一章节提供必要背景，避免把同一结论拆分成多个简短判断重复陈述。" * 8,
                ],
                "subsections": [],
                "citations": ["paper", "verified"],
            }
        if task == "writer.report_abstract":
            return {
                "abstract": "本报告围绕真实研究问题组织受控实验、结果分析和结论边界。" * 15,
                "keywords": ["受控实验", "可复现研究"],
            }
        if task == "writer.audit_report":
            return {
                "accepted": True,
                "issues": [],
                "revised_abstract": "",
                "section_revisions": [],
            }
        raise AssertionError(task)


class GenericGroundingAuditLLM(ReportLLM):
    def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
        if task == "writer.audit_report":
            return {
                "accepted": False,
                "hard_failures": [{"type": "grounding", "message": "需要进一步核对"}],
                "revision_required": [],
                "soft_style_issues": [],
                "section_scores": [],
                "revised_abstract": "",
                "section_revisions": [],
            }
        return super().generate_json(task, inputs, schema_hint, instructions)


class RepairableFactAuditLLM(ReportLLM):
    def __init__(self, *, remains_after_repair: bool = False) -> None:
        self.tasks = []
        self.remains_after_repair = remains_after_repair

    @staticmethod
    def failure() -> dict:
        return {
            "code": "numeric_mismatch",
            "section_id": "results",
            "paragraph_index": 1,
            "claim": "本段根据已经保存的研究事实说明问题",
            "source_path": "authoritative_research_state.canonical",
            "source_fact": "研究事实必须以权威状态表为准。",
            "required_correction": "删除与权威状态表冲突的表述。",
        }

    def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
        self.tasks.append(task)
        if task == "writer.audit_report":
            return {
                "accepted": False,
                "hard_failures": [self.failure()],
                "revision_required": [],
                "soft_style_issues": [],
                "section_scores": [],
                "revised_abstract": "",
                "section_revisions": [],
            }
        if task == "writer.repair_report_audit":
            retained_claim = (
                "本段根据已经保存的研究事实说明问题。"
                if self.remains_after_repair
                else ""
            )
            return {
                "revised_abstract": "",
                "section_revisions": [
                    {
                        "section_id": "results",
                        "replacement_paragraphs": [
                            retained_claim
                            + "受控实验记录的测试准确率为92.3%，本报告据此解释结果，并将结论限定在当前数据集、模型结构、参数设置与评价方法范围内。"
                            "这一结果只用于回答预先定义的研究问题，不被扩展为未经实验验证的因果判断或普遍规律。" * 2
                        ],
                    }
                ],
            }
        if task == "writer.verify_report_audit":
            return {
                "hard_failures": [self.failure()] if self.remains_after_repair else []
            }
        return super().generate_json(task, inputs, schema_hint, instructions)


def artifact(artifact_type: str, content: dict) -> Artifact:
    return Artifact(
        run_id="run_test",
        type=artifact_type,
        version=1,
        title=artifact_type,
        content=content,
        source_step=artifact_type,
        created_by="test",
    )


def test_report_has_all_competition_fields():
    report = WriterAgent(ReportLLM()).build_report([
        artifact("problem", {"problem_statement": "real problem"}),
        artifact("hypothesis", {"verifiability": "metric comparison", "novelty_basis": ["paper"]}),
        artifact("plan", {"methods": ["train"], "dataset": "Fashion-MNIST", "baselines": ["cnn"], "metrics": ["accuracy"]}),
        artifact("evidence", {"references": [{"title": "paper", "verified": True, "identifiers": {"arxiv": "1512.03385"}}]}),
        artifact("experiment_result", {"is_real_experiment": True, "provider": "remote_gpu", "metrics": {"accuracy": 0.9}}),
    ])

    required = {
        "Problem Statement", "Rationale", "Technical Details", "Datasets", "Source", "Target",
        "Paper Title", "Paper Abstract", "Methods", "Experiments", "Results", "References",
        "Narrative Sections",
    }

    assert required.issubset(report.keys())
    assert report["Report Status"]["complete"] is True
    assert report["Report Status"]["verified_reference_count"] == 1
    assert report["Reproducibility"]["provider"] == "remote_gpu"
    assert report["Iteration Summary"]["round_count"] == 1
    assert report["Report Spec"]["schema_version"] == "round6.visual-report.v1"


def test_report_uses_only_verified_references_and_grounded_iteration_metrics():
    result = artifact(
        "experiment_result",
        {
            "run_id": "run_test",
            "experiment_id": "experiment_1",
            "result_id": "experiment_1_result",
            "is_real_experiment": True,
            "provider": "local_gpu",
            "metrics": {
                "baseline_accuracy": 0.81,
                "improved_accuracy": 0.86,
            },
        },
    )
    revision = artifact(
        "revision",
        {
            "iteration": 1,
            "verdict": "supported",
            "requires_follow_up": False,
        },
    )
    revision.parent_artifact_id = result.id
    report = WriterAgent(ReportLLM()).build_report(
        [
            artifact(
                "evidence",
                {
                    "references": [
                        {
                            "title": "verified",
                            "verified": True,
                            "identifiers": {"doi": "10.1/test"},
                        },
                        {"title": "draft", "verified": False},
                    ]
                },
            ),
            result,
            revision,
        ]
    )

    assert [item["title"] for item in report["References"]] == ["verified"]
    assert report["Results"]["metrics"]["improved_accuracy"] == 0.86
    assert report["Iteration Summary"]["rounds"][0]["feedback_verdict"] == "supported"


def test_html_report_is_complete_printable_and_escapes_artifact_content():
    html = render_report_html(
        {
            "Paper Title": "<script>alert(1)</script>",
            "Paper Abstract": "摘要",
            "Problem Statement": "问题",
            "Results": {"metrics": {"accuracy": 0.9}},
            "Report Status": {
                "real_experiment": True,
                "verified_reference_count": 2,
                "feedback_iteration": 2,
            },
        },
        run_id="run_test",
        run_title="fallback",
    )

    assert "<!doctype html>" in html
    assert "@media print" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html
    assert "真实实验" in html


def test_conventional_transition_is_a_soft_issue_not_a_hard_export_failure():
    paragraph = (
        "值得注意的是，本轮实验必须结合基线结果、评价阈值和方法修正共同解释，"
        "不能因为单个指标发生变化就扩大研究结论。" * 5
    )
    section = {
        "paragraphs": [paragraph],
        "subsections": [],
    }

    soft_issues = WriterAgent._section_quality_issues(
        section,
        {"paragraphs": "1-2", "min_chars": 200},
    )

    assert "第1段包含模板化AI表达" in soft_issues
    assert WriterAgent._section_hard_quality_issues(section) == []


def test_internal_path_remains_a_hard_export_failure():
    section = {
        "paragraphs": [
            "实验结果来自D:\\竞赛\\experiments\\run_test路径，其他研究说明均省略。" * 4
        ],
        "subsections": [],
    }

    assert WriterAgent._section_hard_quality_issues(section) == [
        "第1段包含内部运行字段或路径"
    ]


def test_underspecified_grounding_label_does_not_block_report_export():
    report = WriterAgent(GenericGroundingAuditLLM()).build_report([])

    assert report["Report Status"]["complete"] is True


def test_evidence_complete_fact_failure_is_repaired_and_verified():
    llm = RepairableFactAuditLLM()

    report = WriterAgent(llm).build_report([])

    assert report["Report Status"]["complete"] is True
    assert llm.tasks[-2:] == [
        "writer.repair_report_audit",
        "writer.verify_report_audit",
    ]
    results = next(
        section for section in report["Narrative Sections"] if section["id"] == "results"
    )
    assert "92.3%" in results["paragraphs"][0]


def test_only_evidence_complete_failure_remaining_after_repair_blocks_export():
    llm = RepairableFactAuditLLM(remains_after_repair=True)

    with pytest.raises(ValueError, match="REPORT_FACT_AUDIT_FAILED:numeric_mismatch"):
        WriterAgent(llm).build_report([])


def test_competition_export_blocks_simulated_results():
    allowed, reason = competition_export_allowed({
        "Results": {"is_real_experiment": False},
        "References": [{"verified": True}],
    })

    assert allowed is False
    assert "remote_gpu or local_gpu" in reason


def test_competition_export_blocks_unverified_references():
    allowed, reason = competition_export_allowed({
        "Results": {"is_real_experiment": True},
        "References": [{"verified": False}],
    })

    assert allowed is False
    assert "verified" in reason


def test_competition_export_blocks_verified_reference_without_doi_or_arxiv():
    allowed, reason = competition_export_allowed({
        "Results": {"is_real_experiment": True},
        "References": [
            {
                "verified": True,
                "url": "https://example.com/paper",
                "identifiers": {"internal": "paper-1"},
            }
        ],
    })

    assert allowed is False
    assert "DOI or arXiv" in reason
