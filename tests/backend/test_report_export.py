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
    assert report["Report Spec"]["schema_version"] == "round6.visual-report.v2"


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


def test_numeric_audit_accepts_display_precision_rounding_and_requires_derived_path():
    rounded_claim = (
        "CCFE模型在测试集上的平均ROC-AUC稳定在0.9102（标准差0.0015），"
        "而基线模型达到了0.9428（标准差0.0031）。"
    )
    difference_claim = "两者之间的平均差值为负0.0325。"
    sections = [
        {
            "id": "results",
            "paragraphs": [rounded_claim, difference_claim],
        }
    ]
    facts = {
        "final_result": {
            "metric_summary": {
                "CCFE_ROC-AUC": {"std": 0.0014661747071507423},
                "Baseline_ROC-AUC": {"mean": 0.9427692376648594},
            }
        }
    }
    failures = [
        {
            "code": "numeric_mismatch",
            "section_id": "results",
            "paragraph_index": 0,
            "claim": rounded_claim,
            "source_path": "final_result.metric_summary.CCFE_ROC-AUC.std",
            "source_fact": "0.0014661747071507423",
            "required_correction": "修正标准差。",
        },
        {
            "code": "numeric_mismatch",
            "section_id": "results",
            "paragraph_index": 1,
            "claim": difference_claim,
            "source_path": "final_result.metric_summary.Baseline_ROC-AUC.mean",
            "source_fact": "0.9427692376648594",
            "required_correction": "修正差值。",
        },
    ]

    assert WriterAgent._validated_hard_failures(
        failures,
        facts=facts,
        sections=sections,
    ) == []


def test_numeric_audit_keeps_true_scalar_mismatch():
    claim = "CCFE模型的ROC-AUC为0.9000。"
    failure = {
        "code": "numeric_mismatch",
        "section_id": "results",
        "paragraph_index": 0,
        "claim": claim,
        "source_path": "final_result.metrics.CCFE_ROC-AUC",
        "source_fact": "0.8",
        "required_correction": "将ROC-AUC改为0.8000。",
    }

    assert WriterAgent._validated_hard_failures(
        [failure],
        facts={"final_result": {"metrics": {"CCFE_ROC-AUC": 0.8}}},
        sections=[{"id": "results", "paragraphs": [claim]}],
    ) == [failure]


@pytest.mark.parametrize(
    ("claim", "source_path", "fact"),
    [
        (
            "基线准确率的跨种子标准差为0.07个百分点。",
            "final_result.metric_summary.Baseline_Test_Accuracy.std",
            0.0006928203230275387,
        ),
        (
            "基线准确率的跨种子标准差为0.07 个百分点。",
            "final_result.metric_summary.Baseline_Test_Accuracy.std",
            0.0006928203230275387,
        ),
        (
            "基线准确率的跨种子标准差为0.07%。",
            "final_result.metric_summary.Baseline_Test_Accuracy.std",
            0.0006928203230275387,
        ),
        (
            "平滑组准确率的跨种子标准差为0.28pp。",
            "final_result.metric_summary.LS_Test_Accuracy.std",
            0.002843120351538689,
        ),
    ],
)
def test_numeric_audit_accepts_equivalent_ratio_displays(claim, source_path, fact):
    failure = {
        "code": "numeric_mismatch",
        "section_id": "results",
        "paragraph_index": 0,
        "claim": claim,
        "source_path": source_path,
        "source_fact": str(fact),
        "required_correction": "修改数值。",
    }

    assert WriterAgent._validated_hard_failures(
        [failure],
        facts={
            "final_result": {
                "metric_summary": {
                    "Baseline_Test_Accuracy": {"std": 0.0006928203230275387},
                    "LS_Test_Accuracy": {"std": 0.002843120351538689},
                }
            }
        },
        sections=[{"id": "results", "paragraphs": [claim]}],
    ) == []


def test_numeric_audit_keeps_true_percentage_point_mismatch():
    claim = "基线准确率的跨种子标准差为0.08个百分点。"
    failure = {
        "code": "numeric_mismatch",
        "section_id": "results",
        "paragraph_index": 0,
        "claim": claim,
        "source_path": "final_result.metric_summary.Baseline_Test_Accuracy.std",
        "source_fact": "0.0006928203230275387",
        "required_correction": "修改数值。",
    }

    assert WriterAgent._validated_hard_failures(
        [failure],
        facts={"final_result": {"metric_summary": {"Baseline_Test_Accuracy": {"std": 0.0006928203230275387}}}},
        sections=[{"id": "results", "paragraphs": [claim]}],
    ) == [failure]


def test_unit_audit_accepts_percentage_points_for_spread_but_not_metric_level():
    spread_claim = "基线准确率的跨种子标准差为0.07个百分点。"
    level_claim = "基线准确率为88.3个百分点。"
    facts = {
        "final_result": {
            "metric_summary": {
                "Baseline_Test_Accuracy": {"mean": 0.883, "std": 0.0006928203230275387}
            }
        }
    }
    failures = [
        {
            "code": "unit_mismatch",
            "section_id": "results",
            "paragraph_index": 0,
            "claim": spread_claim,
            "source_path": "final_result.metric_summary.Baseline_Test_Accuracy.std",
            "source_fact": "0.0006928203230275387",
            "required_correction": "修改单位。",
        },
        {
            "code": "unit_mismatch",
            "section_id": "results",
            "paragraph_index": 2,
            "claim": level_claim,
            "source_path": "final_result.metric_summary.Baseline_Test_Accuracy.mean",
            "source_fact": "0.883",
            "required_correction": "将水平值单位改为百分比。",
        },
    ]

    assert WriterAgent._validated_hard_failures(
        failures,
        facts=facts,
        sections=[{"id": "results", "paragraphs": [spread_claim, level_claim]}],
    ) == [failures[1]]


def test_report_fact_sheet_exposes_deterministic_statistical_evidence():
    evidence = {
        "metric": "Test Accuracy",
        "paired_t_test": {
            "method": "paired_t_test",
            "statistic": 1.7214586864,
            "degrees_of_freedom": 2,
            "p_value": 0.2273085782,
            "status": "computed",
        },
    }

    facts = WriterAgent._fact_sheet(
        {}, {}, {}, {}, {}, {}, [], {}, evidence
    )

    assert facts["deterministic_result_evidence"]["paired_t_test"]["p_value"] == pytest.approx(
        0.2273085782
    )


def test_scientific_boundary_guard_rejects_null_proof_and_unobserved_convergence_claims():
    facts = {
        "deterministic_result_evidence": {
            "paired_t_test": {
                "method": "paired_t_test",
                "p_value": 0.2273085782,
                "confidence_interval_95": [-0.0041, 0.0096],
            }
        },
        "final_result": {"epoch_metrics": []},
    }
    issues = WriterAgent._scientific_boundary_issues(
        "结果呈现统计中性状态，且未进行具有足够统计功效的显著性检验。",
        [
            {
                "id": "design",
                "paragraphs": ["5个Epoch足以使基线进入收敛阶段，并为观察欠训练留出窗口。"],
            },
            {
                "id": "results",
                "paragraphs": ["实验结果支持了标签平滑无法带来统计显著提升的判断。"],
            },
            {
                "id": "conclusion",
                "paragraphs": [
                    "该结果直接否定了准确率略微降低的预测方向。",
                    "无法显著提升准确率的部分得到了数据支持，因为统计检验未能拒绝零假设。",
                    "统计检验未能拒绝零假设，因此支持标签平滑无法显著提升准确率。",
                    "实验部分证伪了准确率显著降低的预测。",
                ],
            },
        ],
        facts,
    )

    assert {(item["section_id"], item["paragraph_index"]) for item in issues} == {
        ("abstract", 0),
        ("design", 0),
        ("results", 0),
        ("conclusion", 0),
        ("conclusion", 1),
        ("conclusion", 2),
        ("conclusion", 3),
    }


def test_scientific_boundary_guard_accepts_restrained_inconclusive_language():
    facts = {
        "deterministic_result_evidence": {
            "paired_t_test": {
                "method": "paired_t_test",
                "p_value": 0.2273085782,
                "confidence_interval_95": [-0.0041, 0.0096],
            }
        },
        "final_result": {"epoch_metrics": []},
    }

    assert WriterAgent._scientific_boundary_issues(
        "配对t检验已经完成，但三个种子的结果未建立显著提升或下降证据。",
        [
            {
                "id": "design",
                "paragraphs": ["5 epoch来自冻结实验计划；本次没有独立收敛预实验或完整逐epoch曲线。"],
            },
            {
                "id": "conclusion",
                "paragraphs": ["点估计为正，但置信区间跨零，不能据此判定方向或等效性。"],
            },
        ],
        facts,
    ) == []


def test_scientific_boundary_issue_identifies_reverse_null_inference_rule():
    facts = {
        "deterministic_result_evidence": {
            "paired_t_test": {
                "method": "paired_t_test",
                "p_value": 0.2273085782,
                "confidence_interval_95": [-0.0041, 0.0096],
            }
        },
        "final_result": {"epoch_metrics": []},
    }
    issues = WriterAgent._scientific_boundary_issues(
        "",
        [{
            "id": "results",
            "paragraphs": [
                "无法显著提升准确率的部分得到了数据支持，因为统计检验未能拒绝零假设。"
            ],
        }],
        facts,
    )

    assert len(issues) == 1
    assert "nonrejection_supports_no_improvement" in issues[0]["rule_id"]


def test_scientific_boundary_guard_rejects_report_wording_seen_in_export():
    facts = {
        "deterministic_result_evidence": {
            "paired_t_test": {
                "method": "paired_t_test",
                "p_value": 0.2273085782,
                "confidence_interval_95": [-0.0041, 0.0096],
            }
        },
        "final_result": {"epoch_metrics": []},
    }
    sections = [{
        "id": "results",
        "paragraphs": [
            "无法显著提升准确率的判断，实验结果提供了支持：统计检验未能拒绝零假设。",
            "实验揭示了标签平滑在严格计算约束下的中性效应。",
            "该方法并未造成系统性的性能下降。",
        ],
    }]

    issues = WriterAgent._scientific_boundary_issues("", sections, facts)

    assert {(item["section_id"], item["paragraph_index"]) for item in issues} == {
        ("results", 0),
        ("results", 1),
        ("results", 2),
    }
    assert "support_for_no_improvement_claim_broad" in issues[0]["rule_id"]
    assert issues[1]["rule_id"] == "neutral_or_balanced_effect_claim"
    assert issues[2]["rule_id"] == "unqualified_no_decline_claim"


def test_scientific_boundary_fallback_removes_only_invalid_sentences():
    facts = {
        "deterministic_result_evidence": {
            "paired_t_test": {
                "method": "paired_t_test",
                "p_value": 0.2273085782,
                "confidence_interval_95": [-0.0041, 0.0096],
            }
        },
        "final_result": {"epoch_metrics": []},
    }
    sections = [
        {
            "id": "results",
            "paragraphs": [
                "配对t检验得到p=0.227，95%置信区间跨零。"
                "实验结果支持了标签平滑无法带来统计显著提升的判断。"
                "这一判断必须保留适用边界。"
            ],
        }
    ]
    issues = WriterAgent._scientific_boundary_issues("", sections, facts)

    _, repaired = WriterAgent._apply_scientific_boundary_fallback(
        "", sections, issues
    )

    paragraph = repaired[0]["paragraphs"][0]
    assert "p=0.227" in paragraph
    assert "这一判断必须保留适用边界" in paragraph
    assert "支持了标签平滑无法" not in paragraph
    assert "尚未建立总体方向性差异证据" in paragraph
    assert WriterAgent._scientific_boundary_issues("", repaired, facts) == []


def test_scientific_boundary_fallback_handles_two_issue_types_in_abstract():
    facts = {
        "deterministic_result_evidence": {
            "paired_t_test": {
                "method": "paired_t_test",
                "p_value": 0.2273085782,
                "confidence_interval_95": [-0.0041, 0.0096],
            }
        },
        "final_result": {"epoch_metrics": []},
    }
    abstract = (
        "结果呈现统计中性状态。"
        "5个Epoch足以使基线进入收敛阶段。"
        "研究边界保持不变。"
    )
    issues = WriterAgent._scientific_boundary_issues(abstract, [], facts)

    repaired, _ = WriterAgent._apply_scientific_boundary_fallback(
        abstract, [], issues
    )

    assert "统计中性状态" not in repaired
    assert "足以使基线进入收敛阶段" not in repaired
    assert "研究边界保持不变" in repaired
    assert "尚未建立总体方向性差异证据" in repaired
    assert "冻结实验计划规定的训练预算" in repaired
    assert WriterAgent._scientific_boundary_issues(repaired, [], facts) == []


def test_internal_leak_audit_requires_private_value_in_reader_facing_claim():
    facts = {
        "final_result": {
            "environment": {"data_root": r"D:\Gewu\datasets\fashionmnist"}
        }
    }
    generic_claim = "实验直接加载本地路径下的原始二进制文件。"
    generic_failure = {
        "code": "internal_leak",
        "section_id": "method",
        "paragraph_index": 0,
        "claim": "直接加载本地路径下的原始二进制文件",
        "source_path": "final_result.environment.data_root",
        "source_fact": r"D:\Gewu\datasets\fashionmnist",
        "required_correction": "删除具体路径。",
    }

    assert WriterAgent._validated_hard_failures(
        [generic_failure],
        facts=facts,
        sections=[{"id": "method", "paragraphs": [generic_claim]}],
    ) == []

    leaked_claim = r"数据从 D:\Gewu\datasets\fashionmnist 直接读取。"
    leaked_failure = {
        **generic_failure,
        "claim": leaked_claim,
    }
    assert WriterAgent._validated_hard_failures(
        [leaked_failure],
        facts=facts,
        sections=[{"id": "method", "paragraphs": [leaked_claim]}],
    ) == [leaked_failure]


def test_reader_text_redaction_removes_runtime_paths_ids_and_hashes():
    value = (
        r"数据位于 D:\Gewu\datasets\fashionmnist，记录为 run_0a7d0876b742 / "
        r"art_76813d81eef9，content_fingerprint=sha256:0123456789abcdef0123456789abcdef。"
    )

    redacted = WriterAgent._redact_reader_text(value)

    assert r"D:\Gewu" not in redacted
    assert "run_0a7d0876b742" not in redacted
    assert "art_76813d81eef9" not in redacted
    assert "content_fingerprint" not in redacted
    assert "0123456789abcdef" not in redacted
    assert "本地数据目录" in redacted
    assert "内部记录" in redacted


def test_reader_text_redaction_keeps_generic_local_path_wording():
    claim = "实验直接加载本地路径下的原始二进制文件。"
    assert WriterAgent._redact_reader_text(claim) == claim


def test_unit_audit_keeps_relative_percent_for_an_absolute_delta_as_mismatch():
    claim = "相对提升了10%。"
    failure = {
        "code": "unit_mismatch",
        "section_id": "results",
        "paragraph_index": 0,
        "claim": claim,
        "source_path": "deterministic_result_evidence.mean_delta",
        "source_fact": "0.1",
        "required_correction": "区分相对提升与绝对差值。",
    }

    assert WriterAgent._validated_hard_failures(
        [failure],
        facts={"deterministic_result_evidence": {"mean_delta": 0.1}},
        sections=[{"id": "results", "paragraphs": [claim]}],
    ) == [failure]


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
