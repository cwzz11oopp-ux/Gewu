import pytest

from backend.app.agents.critic import CriticAgent
from backend.app.agents.experiment import ExperimentAgent
from backend.app.agents.hypothesis import HypothesisAgent
from backend.app.agents.idea import IdeaAgent
from backend.app.agents.planner import PlanningAgent
from backend.app.agents.research import ResearchAgent
from backend.app.agents.writer import WriterAgent
from backend.app.providers.experiment import MockExperimentProvider
from backend.app.models.experiment import ExperimentBundle, ExperimentFile, ExperimentManifest


class TaskRecorder:
    mode = "qwen"
    fallback = False

    def __init__(self):
        self.tasks = []
        self.schema_hints = []
        self.instructions = []

    def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
        self.tasks.append(task)
        self.schema_hints.append(schema_hint)
        self.instructions.append(instructions)
        if task == "writer.report_outline":
            return {
                "title": "test report",
                "section_plans": [],
                "reference_selection": [],
            }
        if task in {"writer.report_section", "writer.revise_report_section"}:
            section = inputs.get("required_section") or inputs.get("section") or {}
            paragraph = (
                "本段用于验证分章写作任务确实调用模型，并依据输入事实形成连续的中文论述，"
                "同时排除内部运行字段和没有证据支持的扩展判断。"
            )
            return {
                "id": section.get("id"),
                "title": section.get("title"),
                "paragraphs": [
                    paragraph * 8 + "本段聚焦问题界定。",
                    paragraph * 8 + "本段聚焦方法设置。",
                    paragraph * 8 + "本段聚焦结果解释。",
                    paragraph * 8 + "本段聚焦结论边界。",
                ],
                "subsections": [],
                "citations": [],
            }
        if task == "writer.report_abstract":
            return {"abstract": "本摘要用于验证报告生成调用。" * 30, "keywords": []}
        if task == "writer.audit_report":
            return {
                "accepted": True,
                "issues": [],
                "revised_abstract": "",
                "section_revisions": [],
            }
        if task == "writer.verify_report_audit":
            return {"hard_failures": []}
        return {"ok": True}


def test_reasoning_agents_call_llm_provider():
    llm = TaskRecorder()

    ResearchAgent(llm).structure_problem("train cnn")
    HypothesisAgent(llm).generate({"problem_statement": "p"}, [])
    PlanningAgent(llm).build_plan({"claim": "c"})
    CriticAgent(llm).review_result({"claim": "c"}, {"metrics": {"accuracy": 0.9}})
    WriterAgent(llm).build_report([])

    assert llm.tasks[:5] == [
        "research.structure_problem",
        "hypothesis.generate",
        "planning.build_plan",
        "critic.review_result",
        "writer.report_outline",
    ]
    assert llm.tasks.count("writer.report_section") == 8
    assert llm.tasks.count("writer.revise_report_section") == 0
    assert llm.tasks[-3:] == ["writer.report_abstract", "writer.audit_report", "writer.verify_report_audit"]


def test_feedback_schemas_separate_scientific_verdict_from_workflow_decision():
    llm = TaskRecorder()
    critic = CriticAgent(llm)

    critic.review_result({"claim": "c"}, {"metrics": {"accuracy": 0.9}})
    critic.select_iteration_direction(
        {"claim": "c"},
        {"objective": "o"},
        {"metrics": {"accuracy": 0.9}},
        {"verdict": "failed"},
        {"references": []},
    )

    review_schema, direction_schema = llm.schema_hints
    assert review_schema["verdict"] == "supported|partial|failed"
    assert "REPORT|REVISE|PIVOT" in review_schema["decision"]
    assert "REPORT|REVISE|PIVOT" in direction_schema["decision"]


def test_planning_agent_receives_real_observed_structure_separately_from_semantics():
    class CaptureLLM(TaskRecorder):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            self.inputs = inputs
            self.instructions = instructions
            return {"ok": True}

    observed = [{
        "relative_path": "clutter.mat",
        "filename": "clutter.mat",
        "format": "mat",
        "suffix": ".mat",
        "arrays": [{"key": "clutter", "shape": [18_000, 512], "dtype": "float32"}],
    }]
    llm = CaptureLLM()
    PlanningAgent(llm).build_plan(
        {"claim": "c"},
        dataset_options=[{
            "contract_id": "dataset_1",
            "card": {"observed_structure": observed},
        }],
    )

    assert llm.inputs["observed_structure"] == [
        {"contract_id": "dataset_1", "observed_structure": observed}
    ]
    assert "read-only inspection of real files" in llm.instructions


def test_hypothesis_schema_requests_method_mechanism_and_traceable_evidence():
    llm = TaskRecorder()

    IdeaAgent(llm).generate({"problem_statement": "p"}, [])

    candidate = llm.schema_hints[0]["candidates"][0]
    assert {"claim", "method", "mechanism", "evidence_basis"} <= set(candidate)
    basis = candidate["evidence_basis"][0]
    assert {"statement", "source_title", "source_url", "evidence_type"} <= set(basis)


def test_feedback_iteration_contract_rejects_bundle_missing_required_metric():
    bundle = ExperimentBundle(
        manifest=ExperimentManifest(
            run_id="run_1",
            experiment_id="experiment_1",
            result_id="experiment_1_result",
            expected_metrics=["test_accuracy"],
        ),
        files=[ExperimentFile(path="train.py", content="print('ok')")],
    )
    plan = {
        "dataset": {},
        "iteration_contract": {"required_metrics": ["macro_f1"]},
    }

    with pytest.raises(ValueError, match="EXPERIMENT_FEEDBACK_METRICS_MISSING:macro_f1"):
        ExperimentAgent._validate_bundle_against_plan(plan, bundle)


def test_experiment_analysis_keeps_only_comparisons_grounded_in_runtime_metrics():
    class ComparisonLLM(TaskRecorder):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            return {
                "comparisons": [
                    {
                        "baseline": "baseline",
                        "variant": "variant",
                        "metric": "custom score",
                        "baseline_value": 0.4,
                        "variant_value": 0.6,
                        "difference": 999,
                        "interpretation": "grounded",
                    },
                    {
                        "baseline": "baseline",
                        "variant": "variant",
                        "metric": "invented score",
                        "baseline_value": 0.4,
                        "variant_value": 0.99,
                        "difference": 0.59,
                    },
                ],
                "verdict": "supported",
            }

    analysis = ExperimentAgent(MockExperimentProvider(), ComparisonLLM()).analyze_result(
        {},
        {"manifest": {}},
        {
            "experiment_id": "experiment_1",
            "result_id": "experiment_1_result",
            "metrics": {"baseline_custom": 0.4, "variant_custom": 0.6},
        },
    )

    assert analysis["comparisons"] == [{
        "baseline": "baseline",
        "variant": "variant",
        "metric": "custom score",
        "baseline_value": 0.4,
        "variant_value": 0.6,
        "difference": 0.19999999999999996,
        "interpretation": "grounded",
    }]


def test_hypothesis_agent_is_a_compatibility_alias_for_idea_agent():
    assert HypothesisAgent is IdeaAgent


def test_planning_agent_forwards_skill_instructions_to_llm():
    llm = TaskRecorder()

    PlanningAgent(llm).build_plan(
        {"claim": "dropout"},
        instructions="Use the experiment-plan protocol.",
    )

    assert len(llm.instructions) == 1
    assert llm.instructions[0].startswith("Use the experiment-plan protocol.")
    assert "Authoritative Plan Contract" in llm.instructions[0]


def test_research_agent_requests_english_literature_queries_for_external_search():
    llm = TaskRecorder()

    ResearchAgent(llm).structure_problem("训练一个小型神经网络并做消融")

    schema = llm.schema_hints[0]
    schema_text = str(schema)
    assert "English" in schema_text
    assert "arXiv" in schema_text
    assert "Chinese" in schema_text


def test_planning_agent_requests_the_stable_chinese_blueprint_fields():
    llm = TaskRecorder()

    PlanningAgent(llm).build_plan({"claim": "验证 dropout"})

    schema = llm.schema_hints[0]
    assert {
        "objective",
        "hypotheses",
        "method",
        "dataset",
        "comparisons",
        "evaluations",
        "procedure",
        "parameters",
        "seeds",
        "statistical_summary",
        "success_criteria",
        "failure_criteria",
        "expected_artifacts",
        "stop_conditions",
        "primary_experiment",
        "optional_ablations",
        "traceability",
        "resources",
        "risks",
        "additional_sections",
    } <= set(schema)
    assert {"diagnosis", "alignment_contract", "capacity_confounder", "local_dataset_loader_verification"} <= set(schema)
    assert "中文" in schema["objective"]
    assert "数据集" in schema["dataset"]["name"]
    assert "评估指标" in schema["evaluations"][0]["metric"]
