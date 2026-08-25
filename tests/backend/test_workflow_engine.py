from pathlib import Path
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from backend.app.config import Settings
from backend.app.providers.experiment import LocalGpuExperimentProvider, MockExperimentProvider
from backend.app.providers.literature import MockLiteratureProvider
from backend.app.providers.llm import LLMRequestCancelled, MockLLMProvider
from backend.app.models.provider import EvidenceCard
from backend.app.agents.critic import CriticAgent
from backend.app.agents.planner import PlanningAgent
from backend.app.agents.reviewer import ValidationDecision
from backend.app.agents.supervisor import SupervisorAgent
from backend.app.storage.repository import Repository
from backend.app.workflow.engine import WorkflowEngine
from backend.app.workflow.skills import SkillCatalog, SkillLoader, SkillRegistry
from backend.app.workflow.steps import ORDER


class RecordingLLM:
    mode = "qwen"
    fallback = False

    def __init__(self):
        self.tasks = []
        self.inputs = []

    def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
        self.tasks.append(task)
        self.inputs.append((task, inputs))
        if task == "research.structure_problem":
            return {
                "problem_statement": inputs["problem_input"],
                "constraints": ["real experiment"],
                "knowledge_gaps": ["which training change is verifiable"],
                "literature_queries": ["neural network dropout robustness"],
            }
        if task == "hypothesis.generate":
            return {
                "candidates": [
                    {
                        "claim": "Dropout may improve robustness for a compact CNN under fixed seeds.",
                        "verifiability": "compare baseline and dropout run",
                        "novelty_basis": ["Dropout: A Simple Way to Prevent Neural Networks from Overfitting"],
                        "risks": ["small dataset"],
                        "source_gap_ids": ["GAP-001"],
                    },
                    {
                        "claim": "A compact CNN with dropout may be more reproducible under fixed seeds.",
                        "verifiability": "compare seed variance for baseline and dropout runs",
                        "novelty_basis": ["verified literature on dropout robustness"],
                        "risks": ["small dataset"],
                        "source_gap_ids": ["GAP-001"],
                    },
                    {
                        "claim": "A parameter-matched compact CNN may isolate whether dropout gains come from regularization.",
                        "verifiability": "compare dropout against a parameter-matched control",
                        "novelty_basis": ["controlled mechanism comparison"],
                        "risks": ["capacity matching may be imperfect"],
                        "source_gap_ids": ["GAP-001"],
                    },
                ],
            }
        if task == "idea_selection.review":
            return {
                "evaluations": [
                    self._idea_evaluation(
                        index,
                        candidate,
                        4 if index == 1 else (5 if index == 2 else 2),
                        "EVIDENCE_INSUFFICIENT" if index == 1 else ("GO" if index == 2 else "REVISE"),
                    )
                    for index, candidate in enumerate(inputs["candidates"])
                ]
            }
        if task == "hypothesis.analyze_user_hypothesis":
            return {
                "claim": inputs["user_hypothesis"],
                "verifiability": "validated after checking verified evidence",
                "novelty_basis": ["user supplied hypothesis reviewed against evidence"],
                "risks": ["requires additional ablation"],
                "source": "user",
                "analysis": "The hypothesis is testable but needs a baseline and fixed seed.",
            }
        if task == "critic.evidence_reasoning":
            hypothesis = inputs["hypothesis"]
            decision = inputs.get("evaluation", {}).get("decision")
            if decision in {"REVISE", "PIVOT"}:
                return {
                    "status": "revised",
                    "revised_hypothesis": {
                        **hypothesis,
                        "claim": f"{hypothesis['claim']} (evidence-revised)",
                    },
                    "revision_reason": "Applied the idea review before human selection.",
                    "support": inputs["evidence"],
                    "warnings": [],
                }
            return {
                "status": (
                    "rejected"
                    if decision == "STOP"
                    else (
                        "evidence_insufficient"
                        if decision == "EVIDENCE_INSUFFICIENT"
                        else "verified"
                    )
                ),
                "selected": hypothesis,
                "support": inputs["evidence"],
                "warnings": [],
            }
        if task == "planning.build_plan":
            return {
                "methods": ["train baseline cnn", "train dropout cnn"],
                "dataset": "Fashion-MNIST",
                "baselines": ["baseline_cnn"],
                "metrics": ["accuracy"],
                "parameters": {"epochs": 1},
                "expected_result": "dropout improves or narrows claim",
            }
        if task == "planning.review_plan":
            return {
                "verdict": "ACCEPT", "issues": [], "required_changes": [],
                "suggested_fixes": [], "revised_plan_guidance": [],
                "experiment_feasibility": "FEASIBLE",
            }
        if task == "planning.refine_plan":
            return {
                **inputs["current_plan"],
                "procedure": {
                    "steps": ["run the requested ablation with fixed seeds"],
                    "repetitions": 3,
                },
            }
        if task == "experiment.generate_code":
            return {
                "entrypoint": "train.py",
                "files": [{"path": "train.py", "content": "print('{\"accuracy\": 0.9}')"}],
                "assumptions": ["test code generation"],
            }
        if task == "experiment.generate_bundle":
            return {
                "entrypoint": "train.py",
                "files": [
                    {
                        "path": "train.py",
                        "content": (
                            "import argparse, json\nfrom pathlib import Path\n"
                            "parser=argparse.ArgumentParser()\n"
                            "parser.add_argument('--run-id', required=True)\n"
                            "parser.add_argument('--experiment-id', required=True)\n"
                            "parser.add_argument('--result-id', required=True)\n"
                            "parser.add_argument('--output', required=True)\n"
                            "parser.add_argument('--smoke-test', action='store_true')\n"
                            "parser.add_argument('--seed', default='7')\n"
                            "args=parser.parse_args()\n"
                            "smoke_test=args.smoke_test\n"
                            "payload={'run_id': args.run_id, 'experiment_id': args.experiment_id, "
                            "'result_id': args.result_id, 'metrics': {'accuracy': 0.9}}\n"
                            "Path(args.output).parent.mkdir(parents=True, exist_ok=True)\n"
                            "Path(args.output).write_text(json.dumps(payload), encoding='utf-8')\n"
                        ),
                    }
                ],
                "python_args": ["--seed", "7"],
                "requirements": [],
                "requires_gpu": False,
                "expected_metrics": ["accuracy"],
                "parameters": {"seed": 7},
                "seeds": [7],
                "supports_smoke_test": True,
            }
        if task == "experiment.analyze_results":
            result = inputs["result"]
            return {
                "experiment_id": result["experiment_id"],
                "result_id": result["result_id"],
                "metrics": result["metrics"],
                "comparisons": [],
                "observations": ["Recorded validated fixture metrics."],
                "limitations": ["Development fixture is not competition evidence."],
                "verdict": "partial",
            }
        if task == "experiment.audit_result":
            return {
                "integrity_status": "passed",
                "issues": [],
                "verified_files": [
                    {"path": item["path"], "sha256": item["sha256"]}
                    for item in inputs["files"]
                ],
                "environment_summary": inputs["result"].get("environment") or {},
                "is_real_experiment": inputs["result"].get("is_real_experiment", False),
            }
        if task == "critic.review_result":
            return {
                "decision": "REVISE",
                "feedback": "Add ablation and narrow the claim.",
                "required_revision": "state seed and metric path",
            }
        if task in {"scientific.primary_result_analysis", "scientific.independent_result_review"}:
            return {
                "hypothesis_status": "INCONCLUSIVE",
                "supported_findings": [],
                "contradicting_findings": [],
                "alternative_explanations": ["fixture analysis"],
                "confounders": [],
                "evidence_gaps": ["fixture evidence gap"],
                "interpretation": "Fixture result is inconclusive.",
                "recommended_action": "MORE_EVIDENCE",
                "proposed_hypothesis": None,
                "confidence": 0.5,
            }
        if task == "critic.select_iteration_direction":
            candidate = {
                "name": "固定条件的单变量消融",
                "problem_addressed": "核对当前结果是否稳定",
                "result_basis": ["当前实验结果"],
                "evidence_basis": [],
                "changed_variable": "仅改变一个实验因素",
                "fixed_controls": ["数据", "种子", "指标"],
                "target_metrics": ["accuracy"],
                "possible_regressions": ["计算成本"],
                "information_gain": "high",
                "expected_benefit": "medium",
                "evidence_confidence": "medium",
                "compute_cost": "一次实验",
                "scientific_risk": "low",
                "success_rule": "达到原阈值",
                "failure_rule": "未达到原阈值",
                "stop_rule": "完成验证后停止",
            }
            return {
                "decision": "REVISE",
                "evidence_sufficiency": "SUFFICIENT",
                "evidence_assessment": [],
                "optimization_candidates": [
                    candidate,
                    {**candidate, "name": "增加重复实验", "changed_variable": "重复次数"},
                ],
                "selected_direction": candidate,
                "selection_reason": "优先验证单一原因。",
                "next_action": "生成下一轮受控实验。",
            }
        if task == "writer.report_outline":
            return {
                "title": "Qwen-guided Neural Network Experiment",
                "central_question": "Can the controlled experiment test the selected claim?",
                "narrative_logic": "problem, method, iteration, result, conclusion",
                "section_plans": [],
                "reference_selection": ["Dropout"],
            }
        if task in {"writer.report_section", "writer.revise_report_section"}:
            section = inputs.get("required_section") or inputs.get("section") or {}
            paragraph = (
                "本段依据已经保存的研究产物说明受控实验的研究问题、方法设置、评价依据和证据边界，"
                "并通过连续论述避免把原始字段直接拼接成报告内容。"
            )
            return {
                "id": section.get("id"),
                "title": section.get("title"),
                "paragraphs": [
                    paragraph * 7 + "本段聚焦问题界定。",
                    paragraph * 7 + "本段聚焦方法设置。",
                    paragraph * 7 + "本段聚焦结果解释。",
                    paragraph * 7 + "本段聚焦结论边界。",
                ],
                "subsections": [],
                "citations": ["Dropout"],
            }
        if task == "writer.report_abstract":
            return {
                "abstract": (
                    "本报告围绕受控神经网络实验组织研究问题、方法、迭代过程、最终结果和结论边界，"
                    "全部判断均以已经保存并通过审计的研究产物为依据。"
                ) * 6,
                "keywords": ["受控实验", "神经网络"],
            }
        if task == "writer.audit_report":
            return {
                "accepted": True,
                "issues": [],
                "revised_abstract": "",
                "section_revisions": [],
            }
        if task == "writer.build_report":
            return {"Paper Title": "Qwen-guided Neural Network Experiment"}
        raise AssertionError(task)

    @staticmethod
    def _idea_evaluation(index, candidate, score, decision):
        return {
            "candidate_index": index,
            "idea_card": {"claim": candidate["claim"]},
            "evidence_ledger": [],
            "closest_prior_work": [],
            "gates": {"testability": "PASS"},
            "scores": {
                "novelty": score,
                "scientific_soundness": score,
                "impact": score,
                "testability": score,
                "execution_feasibility": score,
                "reproducibility_compliance": score,
            },
            "mde": {},
            "risks": [],
            "decision": decision,
            "confidence": "medium",
            "unknowns": [],
        }


def _selected_hypothesis_run(engine, repository):
    run = repository.create_run("train cnn", "Skill routing")
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)
    run = engine.run_step(run.id, "evidence_reasoning")
    return engine.select_hypothesis(run.id, 0)


def make_engine_and_run(tmp_path, llm):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository, llm, MockLiteratureProvider(), MockExperimentProvider()
    )
    return engine, repository.create_run("train cnn", "automatic selection")


def test_local_dataset_resolution_binds_only_selected_fashion_directory(tmp_path):
    datasets = tmp_path / "datasets"
    fashion = datasets / "fashionmnist"
    ipix = datasets / "IPIX"
    fashion.mkdir(parents=True)
    ipix.mkdir()
    (fashion / "train.bin").write_bytes(b"fashion")
    (ipix / "clutter.mat").write_bytes(b"ipix")
    provider = LocalGpuExperimentProvider(Settings.from_env({
        "EXPERIMENT_PROVIDER": "local_gpu",
        "EXPERIMENT_DATASET_SOURCE": "local",
        "EXPERIMENT_DATASET_DIR": str(datasets),
        "LOCAL_EXPERIMENT_WORKDIR": str(tmp_path / "experiments"),
    }))
    engine = WorkflowEngine(
        Repository(data_dir=str(tmp_path / "data")), RecordingLLM(),
        MockLiteratureProvider(), provider,
    )
    run = engine.repository.create_run(
        "Improve MobileNetV2 on FashionMNIST.", "Dataset resolution"
    )

    profile = engine._inspect_configured_local_dataset(run)

    assert profile["canonical_name"] == "fashion-mnist"
    assert profile["root"] == str(fashion.resolve())
    assert profile["directory_name"] == "fashionmnist"
    assert all("IPIX" not in item["relative_path"] for item in profile["files"])


def test_run_step_honors_a_stop_requested_before_step_execution(tmp_path):
    engine, run = make_engine_and_run(tmp_path, RecordingLLM())
    engine.repository.update_workflow_state(run.id, stop_requested=True)

    with pytest.raises(LLMRequestCancelled, match="PIPELINE_STOPPED"):
        engine.run_step(run.id, "problem_understanding")

    stopped = engine.repository.get_run(run.id)
    step = next(item for item in stopped.steps if item.id == "problem_understanding")
    assert step.status == "interrupted"
    assert step.error["code"] == "PIPELINE_STOPPED"


def prepared_candidate_run(tmp_path):
    engine, run = make_engine_and_run(tmp_path, RecordingLLM())
    for step_id in [
        "problem_understanding",
        "knowledge_integration",
        "hypothesis_generation",
    ]:
        run = engine.run_step(run.id, step_id)
    return engine, run


def test_empty_hypotheses_stop_before_selection_or_reasoning(tmp_path):
    class EmptyLLM(RecordingLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "hypothesis.generate":
                return {"candidates": []}
            return super().generate_json(task, inputs, schema_hint, instructions)

    engine, run = make_engine_and_run(tmp_path, EmptyLLM())
    engine.run_step(run.id, "problem_understanding")
    engine.run_step(run.id, "knowledge_integration")

    with pytest.raises(ValueError, match="HYPOTHESIS_CANDIDATES_EMPTY"):
        engine.run_step(run.id, "hypothesis_generation")

    types = {artifact.type for artifact in engine.repository.get_run(run.id).artifacts}
    assert not {"hypothesis", "idea_review", "hypothesis_selection", "reasoning"} & types


def test_step_reports_all_missing_input_artifacts_before_agent_execution(tmp_path):
    engine, run = make_engine_and_run(tmp_path, RecordingLLM())

    with pytest.raises(
        ValueError,
        match="STEP_INPUT_MISSING:evidence_reasoning.*problem,evidence,hypothesis",
    ):
        engine.run_step(run.id, "evidence_reasoning")


def test_single_hypothesis_is_rejected_and_regenerated_as_a_candidate_set(tmp_path):
    class OneThenManyLLM(RecordingLLM):
        def __init__(self):
            super().__init__()
            self.hypothesis_attempts = 0

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "hypothesis.generate":
                self.hypothesis_attempts += 1
                if self.hypothesis_attempts == 1:
                    return {
                        "candidates": [{
                            "claim": "A single unsupported candidate must not pass.",
                            "method": "single method",
                            "mechanism": "single mechanism",
                            "verifiability": "single test",
                        }]
                    }
            return super().generate_json(task, inputs, schema_hint, instructions)

    llm = OneThenManyLLM()
    engine, run = make_engine_and_run(tmp_path, llm)
    engine.run_step(run.id, "problem_understanding")
    engine.run_step(run.id, "knowledge_integration")

    run = engine.run_step(run.id, "hypothesis_generation")

    hypothesis = [artifact for artifact in run.artifacts if artifact.type == "hypothesis"][-1]
    assert llm.hypothesis_attempts == 2
    assert len(hypothesis.content["candidates"]) == 3


def test_evidence_reasoning_reviews_and_reasons_about_every_candidate(tmp_path):
    engine, run = prepared_candidate_run(tmp_path)

    run = engine.run_step(run.id, "evidence_reasoning")

    latest = {artifact.type: artifact for artifact in run.artifacts}
    assert "hypothesis_selection" not in latest
    assert len(latest["idea_review"].content["evaluations"]) == 3
    assert latest["idea_review"].source_step == "evidence_reasoning"
    assessments = latest["reasoning"].content["candidate_assessments"]
    assert [assessment["candidate_index"] for assessment in assessments] == [0, 1, 2]
    assert all(
        {
            "candidate_index",
            "status",
            "original_hypothesis",
            "revised_hypothesis",
            "was_revised",
            "revision_reason",
            "evaluation",
            "critic_reasoning",
            "evidence_audit",
            "claim_evidence_issues",
        } <= set(assessment)
        for assessment in assessments
        )
    critic_calls = [inputs for task, inputs in engine.llm_provider.inputs if task == "critic.evidence_reasoning"]
    assert len(critic_calls) == 3
    review_call = next(
        inputs
        for task, inputs in engine.llm_provider.inputs
        if task == "idea_selection.review"
    )
    assert review_call["evidence_audit"]["registry"]
    assert len(review_call["evidence_audit"]["candidate_audits"]) == 3
    assert latest["reasoning"].content["evidence_registry"]
    assert latest["reasoning"].content["evidence_policy"]["verified_sources_only"] is True
    assert latest["reasoning"].content["selection_required"] is True
    assert latest["reasoning"].content["selection_status"] == "awaiting_selection"


def test_evidence_reasoning_resume_reuses_completed_candidate_checkpoints(tmp_path):
    class FailFourthOnce(RecordingLLM):
        def __init__(self):
            super().__init__()
            self.critic_claims = []
            self.failed = False

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "hypothesis.generate":
                return {"candidates": [{
                        "claim": f"candidate {index}", "verifiability": "test",
                        "novelty_basis": [], "risks": [], "source_gap_ids": ["GAP-001"],
                } for index in range(3)]}
            if task == "idea_selection.review":
                return {"evaluations": [
                    self._idea_evaluation(index, candidate, 4 - index / 10, "GO")
                    for index, candidate in enumerate(inputs["candidates"])
                ]}
            if task == "critic.evidence_reasoning":
                claim = inputs["hypothesis"]["claim"]
                self.critic_claims.append(claim)
                if claim == "candidate 2" and not self.failed:
                    self.failed = True
                    raise RuntimeError("MODEL_REQUEST_TIMEOUT:provider=deepseek")
            return super().generate_json(task, inputs, schema_hint, instructions)

    llm = FailFourthOnce()
    engine, run = make_engine_and_run(tmp_path, llm)
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)

    from backend.app.workflow.orchestrator import WorkflowOrchestrator

    orchestrator = WorkflowOrchestrator(engine.repository, lambda: engine)
    orchestrator._drive(run.id)
    interrupted = engine.repository.get_run(run.id)
    # The transient provider timeout is retried in-process; the remaining
    # pause is the normal hypothesis-selection checkpoint, not a failure.
    assert interrupted.status == "paused"
    step = next(item for item in interrupted.steps if item.id == "evidence_reasoning")
    assert step.status == "completed"
    assert llm.critic_claims == ["candidate 0", "candidate 1", "candidate 2", "candidate 2"]
    checkpoints = [
        artifact for artifact in engine.repository.get_run(run.id).artifacts
        if artifact.type == "candidate_reasoning_checkpoint"
    ]
    assert [item.content["candidate_index"] for item in checkpoints] == [0, 1, 2]
    events = [item for item in interrupted.events if item.step_id == "evidence_reasoning"]
    interrupted_event = next(item for item in events if item.data.get("status") == "interrupted")
    assert interrupted_event.data == {
        "status": "interrupted", "candidate_index": 2, "candidate_id": "CAND-003",
        "completed_count": 2, "recoverable": True,
        "error_code": "MODEL_REQUEST_TIMEOUT",
    }
    assert any(
        item.data.get("automatic_retry") is True
        for item in events
    )


def test_automatic_selection_requires_all_candidates_and_uses_weighted_ranking(tmp_path):
    engine, run = prepared_candidate_run(tmp_path)
    run = engine.run_step(run.id, "evidence_reasoning")
    reasoning = next(a for a in run.artifacts if a.type == "reasoning")
    reasoning.content["candidate_assessments"] = reasoning.content["candidate_assessments"][:2]
    engine.repository.save_run(run)
    with pytest.raises(ValueError, match="HYPOTHESIS_CANDIDATE_REVIEWS_INCOMPLETE"):
        engine.auto_select_hypothesis(run.id)

    run = engine.run_step(run.id, "evidence_reasoning")
    selected = engine.auto_select_hypothesis(run.id)
    selection = [a for a in selected.artifacts if a.type == "hypothesis_selection"][-1]
    assert selection.content["selection_mode"] == "automatic"
    assert selection.content["selected_indexes"] == [2]


def test_regenerate_hypotheses_creates_new_round_without_rerunning_literature(tmp_path):
    engine, run = prepared_candidate_run(tmp_path)
    run = engine.run_step(run.id, "evidence_reasoning")
    assert len([a for a in run.artifacts if a.type == "hypothesis"]) == 1
    assert len([a for a in run.artifacts if a.type == "reasoning"]) == 1

    regenerated = engine.regenerate_hypotheses(run.id)

    hypotheses = [a for a in regenerated.artifacts if a.type == "hypothesis"]
    reasoning = [a for a in regenerated.artifacts if a.type == "reasoning"]
    assert len(hypotheses) == 2
    assert hypotheses[-1].content["hypothesis_round"]["round_index"] == 2
    assert len(reasoning) == 2
    # Literature search is not re-run: evidence and synthesis artifacts stay single.
    assert len([a for a in regenerated.artifacts if a.type == "evidence"]) == 1
    assert len([a for a in regenerated.artifacts if a.type == "research_synthesis"]) == 1


def test_automatic_selection_pauses_without_score_exceeding_threshold(tmp_path):
    engine, run = prepared_candidate_run(tmp_path)
    run = engine.run_step(run.id, "evidence_reasoning")
    reasoning = next(a for a in run.artifacts if a.type == "reasoning")
    for assessment in reasoning.content["candidate_assessments"]:
        assessment["evaluation"].pop("scores", None)
    engine.repository.save_run(run)
    selected = engine.auto_select_hypothesis(run.id)
    assert selected.status == "paused"
    assert not any(a.type == "hypothesis_selection" for a in selected.artifacts)

    run2_engine, run2 = prepared_candidate_run(tmp_path / "none")
    run2 = run2_engine.run_step(run2.id, "evidence_reasoning")
    reasoning2 = next(a for a in run2.artifacts if a.type == "reasoning")
    for assessment in reasoning2.content["candidate_assessments"]:
        assessment["status"] = "rejected"
    run2_engine.repository.save_run(run2)
    revision_required = run2_engine.auto_select_hypothesis(run2.id)
    assert revision_required.status == "hypothesis_revision_required"
    assert any(
        a.type == "hypothesis_revision_required" and a.content["code"] == "NO_SELECTABLE_HYPOTHESIS"
        for a in run2_engine.repository.get_run(run2.id).artifacts
    )


def test_evidence_reasoning_retries_and_logs_invalid_idea_review(tmp_path):
    class InvalidThenValidReviewLLM(RecordingLLM):
        def __init__(self):
            super().__init__()
            self.review_attempts = 0

        def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
            if task == "idea_selection.review":
                self.review_attempts += 1
                self.tasks.append(task)
                self.inputs.append((task, inputs, instructions))
                if self.review_attempts == 1:
                    return {
                        "evaluations": [
                            {
                                "candidate_index": 0,
                                "idea_card": "H1",
                                "evidence_ledger": "title",
                            }
                        ]
                    }
                return {
                    "evaluations": [
                        self._idea_evaluation(
                            index,
                            candidate,
                                4 if index == 1 else (3 if index == 2 else 2),
                                "EVIDENCE_INSUFFICIENT" if index == 1 else ("GO" if index == 2 else "REVISE"),
                        )
                        for index, candidate in enumerate(inputs["candidates"])
                    ]
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repo = Repository(data_dir=str(tmp_path))
    llm = InvalidThenValidReviewLLM()
    run = repo.create_run("train a compact cnn", "Retry invalid idea review")
    engine = WorkflowEngine(repo, llm, MockLiteratureProvider(), MockExperimentProvider())
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)

    run = engine.run_step(run.id, "evidence_reasoning")

    assert llm.review_attempts == 2
    latest = {artifact.type: artifact for artifact in run.artifacts}
    assert latest["idea_review"].source_step == "evidence_reasoning"
    invalid_events = [
        event for event in run.events
        if event.step_id == "evidence_reasoning"
        and event.message == "Idea selection review returned invalid output."
    ]
    assert invalid_events
    assert invalid_events[-1].data["raw_review"]["evaluations"][0]["evidence_ledger"] == "title"
    assert "IDEA_SELECTION_OUTPUT_INVALID" in invalid_events[-1].output_summary["issues"][0]


def test_evidence_reasoning_recovers_with_targeted_retrieval(tmp_path):
    initial = EvidenceCard(
        title="Adjacent Task Evidence",
        authors=["Researcher"],
        year=2024,
        source="external",
        claim="An adjacent task uses the mechanism. Future work should evaluate direct mechanism evidence.",
        url="https://doi.org/10.1/adjacent",
        identifiers={"doi": "10.1/adjacent"},
        verified=True,
    )
    targeted = EvidenceCard(
        title="Direct Mechanism Evidence",
        authors=["Researcher"],
        year=2025,
        source="external",
        claim="A controlled benchmark directly evaluates the mechanism.",
        url="https://doi.org/10.1/direct",
        identifiers={"doi": "10.1/direct"},
        verified=True,
    )

    class TargetedLiterature:
        provider_name = "targeted"

        def __init__(self):
            self.queries = []

        def search(self, query, limit):
            self.queries.append(query)
            if query == "direct mechanism evidence":
                return [targeted]
            return [initial]

        def verify(self, card):
            return card

        def verify_identifier(self, identifiers):
            return None

    class GapThenEvidenceLLM(RecordingLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "hypothesis.generate":
                return {
                    "candidates": [
                        {
                            "claim": f"candidate {index}",
                            "evidence_basis": [
                                {
                                    "statement": initial.claim,
                                    "source_title": initial.title,
                                    "source_url": initial.url,
                                    "evidence_type": "INFERENCE",
                                }
                            ],
                                "verifiability": "controlled test",
                                "novelty_basis": [],
                                "risks": [],
                                "source_gap_ids": ["GAP-001"],
                        }
                        for index in range(3)
                    ]
                }
            if task == "idea_selection.review":
                return {
                    "evaluations": [
                        self._idea_evaluation(index, candidate, 3 + index, "GO")
                        for index, candidate in enumerate(inputs["candidates"])
                    ]
                }
            if task == "critic.evidence_reasoning":
                registry = inputs["evidence_audit"]["registry"]
                direct = next(
                    (
                        item
                        for item in registry
                        if item["title"] == "Direct Mechanism Evidence"
                    ),
                    None,
                )
                source = direct or registry[0]
                return {
                    "status": "verified" if direct else "evidence_insufficient",
                    "selected": inputs["hypothesis"],
                    "claim_evidence_map": [
                        {
                            "claim": inputs["hypothesis"]["claim"],
                            "evidence_id": source["evidence_id"],
                            "stance": "support",
                            "relation": "DIRECT" if direct else "ANALOGY",
                            "strength": "high" if direct else "low",
                            "limitation": "" if direct else "Adjacent task only.",
                        }
                    ],
                    "required_evidence": [] if direct else ["direct mechanism evidence"],
                    "unsupported_claims": [],
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    provider = TargetedLiterature()
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        GapThenEvidenceLLM(),
        provider,
        MockExperimentProvider(),
    )
    run = repository.create_run("train cnn", "targeted retrieval")
    for step_id in [
        "problem_understanding",
        "knowledge_integration",
        "hypothesis_generation",
        "evidence_reasoning",
    ]:
        run = engine.run_step(run.id, step_id)

    latest = {artifact.type: artifact for artifact in run.artifacts}
    retrieval = latest["reasoning"].content["targeted_retrieval"]
    assert retrieval["attempted"] is True
    assert "direct mechanism evidence" in retrieval["queries"]
    assert retrieval["new_evidence_count"] > 0
    assert (
        latest["reasoning"].content["candidate_assessments"][2]["status"]
        == "verified"
    )
    assert provider.queries.count("direct mechanism evidence") >= 1


def test_all_recovery_rounds_exhaust_before_no_selectable_hypothesis(tmp_path):
    initial = EvidenceCard(
        title="Initial adjacent evidence", authors=["Researcher"], year=2024,
            source="external", claim="An adjacent task uses the mechanism. Future work should evaluate missing direct evidence.",
        url="https://doi.org/10.1/initial", identifiers={"doi": "10.1/initial"}, verified=True,
    )

    class NoNewEvidenceLiterature:
        provider_name = "targeted"

        def search(self, query, limit):
            return [] if query == "missing direct evidence" else [initial]

        def verify(self, card):
            return card

        def verify_identifier(self, identifiers):
            return None

    class AlwaysInsufficientLLM(RecordingLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "hypothesis.generate":
                return {"candidates": [
                        {"claim": f"candidate {index}", "verifiability": "controlled test", "novelty_basis": [], "risks": [], "source_gap_ids": ["GAP-001"]}
                    for index in range(3)
                ]}
            if task == "idea_selection.review":
                return {"evaluations": [
                    self._idea_evaluation(index, candidate, 3, "GO")
                    for index, candidate in enumerate(inputs["candidates"])
                ]}
            if task == "critic.evidence_reasoning":
                return {
                    "status": "evidence_insufficient",
                    "selected": inputs["hypothesis"],
                    "claim_evidence_map": [],
                    "required_evidence": ["missing direct evidence"],
                    "unsupported_claims": ["missing direct evidence"],
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository, AlwaysInsufficientLLM(), NoNewEvidenceLiterature(), MockExperimentProvider()
    )
    run = repository.create_run("train cnn", "recovery exhaustion")
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation", "evidence_reasoning"]:
        run = engine.run_step(run.id, step_id)

    reasoning = [artifact for artifact in run.artifacts if artifact.type == "reasoning"][-1].content
    assert reasoning["targeted_retrieval"]["attempted"] is True
    assert len(reasoning["targeted_retrieval"]["history"]) == 2
    revision_required = engine.auto_select_hypothesis(run.id)
    assert revision_required.status == "hypothesis_revision_required"
    assert any(
        artifact.type == "hypothesis_revision_required"
        and artifact.content["code"] == "NO_SELECTABLE_HYPOTHESIS"
        for artifact in revision_required.artifacts
    )


def test_auto_selection_continues_after_one_candidate_is_exhausted(tmp_path):
    engine, run = prepared_candidate_run(tmp_path)
    run = engine.run_step(run.id, "evidence_reasoning")
    reasoning = [artifact for artifact in run.artifacts if artifact.type == "reasoning"][-1]
    assessments = reasoning.content["candidate_assessments"]
    assessments[0]["status"] = "rejected"
    assessments[0]["recommendation"] = "REJECTED_EVIDENCE_UNAVAILABLE"
    assessments[1]["status"] = "verified"
    assessments[1]["recommendation"] = "GO"
    for key in list(assessments[1]["evaluation"]["scores"]):
        assessments[1]["evaluation"]["scores"][key] = 5
    for assessment in assessments[2:]:
        assessment["status"] = "rejected"
        assessment["recommendation"] = "REJECT"
    engine.repository.save_run(run)

    selected = engine.auto_select_hypothesis(run.id)

    selection = [artifact for artifact in selected.artifacts if artifact.type == "hypothesis_selection"][-1]
    assert selection.content["selected_indexes"] == [1]


def test_removed_idea_selection_step_is_not_runnable(tmp_path):
    engine, run = prepared_candidate_run(tmp_path)
    legacy = engine.repository.add_artifact(
        run.id,
        "hypothesis_selection",
        "Locked Legacy Selection",
        {"selected": [{"claim": "legacy"}]},
        "idea_selection",
        "legacy",
    )
    engine.repository.lock_artifact(run.id, legacy.id, True)

    assert "idea_selection" not in ORDER
    with pytest.raises(ValueError, match="UNKNOWN_WORKFLOW_STEP:idea_selection"):
        engine.run_step(run.id, "idea_selection")


@pytest.mark.parametrize(
    ("selection_mode", "source_step"),
    [
        ("multi", "evidence_reasoning"),
        ("automatic_weighted_review", "idea_selection"),
        ("evidence_reasoned_weighted_review", "injected_selection"),
    ],
)
def test_research_plan_only_accepts_selection_created_by_evidence_reasoning(
    tmp_path, selection_mode, source_step
):
    engine, run = prepared_candidate_run(tmp_path)
    engine.repository.add_artifact(
        run.id,
        "hypothesis_selection",
        "Injected Manual Selection",
        {
            "selected": [{"claim": "manually injected candidate"}],
            "selected_indexes": [0],
            "selection_mode": selection_mode,
        },
        source_step,
        "test",
    )

    with pytest.raises(ValueError, match="HYPOTHESIS_SELECTION_REQUIRED"):
        engine.run_step(run.id, "research_plan")


def test_evidence_reasoned_selection_allows_research_plan(tmp_path):
    engine, run = prepared_candidate_run(tmp_path)

    run = engine.run_step(run.id, "evidence_reasoning")
    run = engine.select_hypothesis(run.id, 1)
    run = engine.run_step(run.id, "research_plan")

    assert {artifact.type for artifact in run.artifacts} >= {"reasoning", "plan"}


def test_locked_legacy_reasoning_does_not_skip_evidence_reasoned_selection(tmp_path):
    engine, run = prepared_candidate_run(tmp_path)
    legacy_selection = engine.repository.add_artifact(
        run.id,
        "hypothesis_selection",
        "Legacy Selection",
        {"selected": [{"claim": "legacy"}], "selection_mode": "automatic_weighted_review"},
        "idea_selection",
        "legacy",
    )
    legacy_reasoning = engine.repository.add_artifact(
        run.id,
        "reasoning",
        "Locked Legacy Reasoning",
        {"active_hypothesis": {"claim": "legacy"}},
        "evidence_reasoning",
        "legacy",
    )
    engine.repository.lock_artifact(run.id, legacy_reasoning.id, True)

    resumed = engine.run_step(run.id, "evidence_reasoning")

    selections = [artifact for artifact in resumed.artifacts if artifact.type == "hypothesis_selection"]
    assert selections[0].id == legacy_selection.id
    assert len(selections) == 1
    assert resumed.artifacts[-1].type == "reasoning"


def test_evidence_reasoning_maps_statuses_and_normalizes_revised_claim(tmp_path):
    class StatusLLM(RecordingLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "hypothesis.generate":
                self.tasks.append(task)
                self.inputs.append((task, inputs))
                return {
                    "candidates": [
                            {"claim": f"candidate {index}", "verifiability": "test", "novelty_basis": [], "risks": [], "source_gap_ids": ["GAP-001"]}
                        for index in range(4)
                    ]
                }
            if task == "idea_selection.review":
                self.tasks.append(task)
                self.inputs.append((task, inputs))
                decisions = ["GO", "REVISE", "EVIDENCE_INSUFFICIENT", "STOP"]
                scores = [4, 5, 2, 1]
                return {
                    "evaluations": [
                        self._idea_evaluation(index, candidate, scores[index], decisions[index])
                        for index, candidate in enumerate(inputs["candidates"])
                    ]
                }
            if task == "critic.evidence_reasoning":
                self.tasks.append(task)
                self.inputs.append((task, inputs))
                claim = inputs["hypothesis"]["claim"]
                if claim == "candidate 1":
                    return {
                        "revised_hypothesis": {
                            **inputs["hypothesis"],
                            "claim": "candidate 1 revised",
                            "rank": 99,
                        },
                        "revision_reason": "verified evidence narrows the claim",
                        "support": inputs["evidence"],
                        "warnings": [],
                    }
                return {"selected": inputs["hypothesis"], "support": inputs["evidence"], "warnings": []}
            return super().generate_json(task, inputs, schema_hint, instructions)

    engine, run = make_engine_and_run(tmp_path, StatusLLM())
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)

    run = engine.run_step(run.id, "evidence_reasoning")

    reasoning = [artifact for artifact in run.artifacts if artifact.type == "reasoning"][-1].content
    assessments = reasoning["candidate_assessments"]
    assert [assessment["status"] for assessment in assessments] == [
        "verified",
        "revised",
        "evidence_insufficient",
        "rejected",
    ]
    assert assessments[1]["was_revised"] is True
    assert assessments[1]["revised_hypothesis"]["claim"] == "candidate 1 revised"
    assert "rank" not in assessments[1]["revised_hypothesis"]
    assert reasoning["selection_required"] is True


def test_evidence_reasoning_retries_when_required_revision_is_empty(tmp_path):
    class EmptyRevisionLLM(RecordingLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if (
                task == "critic.evidence_reasoning"
                and inputs["hypothesis"]["claim"]
                == "Dropout may improve robustness for a compact CNN under fixed seeds."
                and not any(
                    recorded_task == task
                    and recorded_inputs["hypothesis"]["claim"]
                    == inputs["hypothesis"]["claim"]
                    for recorded_task, recorded_inputs in self.inputs
                )
            ):
                self.tasks.append(task)
                self.inputs.append((task, inputs))
                return {
                    "revised_hypothesis": {"claim": ""},
                    "revision_reason": "provider emitted an empty claim",
                    "support": inputs["evidence"],
                    "warnings": [],
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    engine, run = make_engine_and_run(tmp_path, EmptyRevisionLLM())
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)

    run = engine.run_step(run.id, "evidence_reasoning")

    reasoning = [artifact for artifact in run.artifacts if artifact.type == "reasoning"][-1].content
    winner = reasoning["candidate_assessments"][0]
    assert winner["was_revised"] is True
    assert winner["revised_hypothesis"]["claim"].endswith("(evidence-revised)")
    assert reasoning["selection_status"] == "awaiting_selection"
    critic_calls = [
        inputs
        for task, inputs in engine.llm_provider.inputs
        if task == "critic.evidence_reasoning"
        and inputs["hypothesis"]["claim"]
        == "Dropout may improve robustness for a compact CNN under fixed seeds."
    ]
    assert len(critic_calls) >= 2


def test_candidate_assessment_rejects_analogy_only_or_invented_evidence_links():
    evaluation = RecordingLLM._idea_evaluation(
        0,
        {"claim": "candidate"},
        5,
        "GO",
    )
    evidence_audit = {
        "gate": "PASS",
        "matched_evidence_ids": ["E1-known"],
    }

    analogy_only = WorkflowEngine._candidate_assessment(
        0,
        {"claim": "candidate"},
        evaluation,
        {
            "status": "verified",
            "claim_evidence_map": [
                {
                    "claim": "candidate",
                    "evidence_id": "E1-known",
                    "stance": "support",
                    "relation": "ANALOGY",
                }
            ],
        },
        evidence_audit,
        True,
    )
    invented_id = WorkflowEngine._candidate_assessment(
        0,
        {"claim": "candidate"},
        evaluation,
        {
            "status": "verified",
            "claim_evidence_map": [
                {
                    "claim": "candidate",
                    "evidence_id": "E9-invented",
                    "stance": "support",
                    "relation": "DIRECT",
                }
            ],
        },
        evidence_audit,
        True,
    )

    assert analogy_only["status"] == "evidence_insufficient"
    assert "DIRECT_OR_INDIRECT_SUPPORT_MISSING" in analogy_only["claim_evidence_issues"]
    assert invented_id["status"] == "evidence_insufficient"
    assert any(
        issue.startswith("CLAIM_EVIDENCE_ID_INVALID")
        for issue in invented_id["claim_evidence_issues"]
    )


class RecordingSupervisor(SupervisorAgent):
    def __init__(self, registry):
        super().__init__(registry)
        self.validated_steps = []

    def validate(self, step_id, content, artifact_path=None, wiki_paths=()):
        self.validated_steps.append(step_id)
        return ValidationDecision(True)


def test_engine_validates_every_workflow_step_before_persisting_output(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    loader = SkillLoader(Path(__file__).resolve().parents[2])
    registry = SkillRegistry()
    supervisor = RecordingSupervisor(registry)
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
        loader,
        registry,
        supervisor_agent=supervisor,
    )
    run = repository.create_run("train cnn", "Supervisor validation")

    for step_id in ORDER[:4]:
        run = engine.run_step(run.id, step_id)
    run = engine.select_hypothesis(run.id, 0)
    for step_id in ORDER[4:-1]:
        run = engine.run_step(run.id, step_id)
    revision = [artifact for artifact in run.artifacts if artifact.type == "revision"][-1]
    if revision.content["requires_follow_up"]:
        run = engine.run_step(run.id, "research_plan")
    while revision.content["requires_follow_up"]:
        for step_id in ["experiment_task", "experiment_run_analysis", "feedback_revision"]:
            run = engine.run_step(run.id, step_id)
        revision = [artifact for artifact in run.artifacts if artifact.type == "revision"][-1]
        if revision.content["requires_follow_up"]:
            # Feedback produces an unaccepted plan proposal.  The proposal must
            # pass the same frozen governance path before another experiment.
            run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "report_export")

    assert set(supervisor.validated_steps) == set(ORDER)


def test_supervisor_retries_invalid_agent_output_without_persisting_rejected_candidate(tmp_path):
    class RevisingProblemLLM(RecordingLLM):
        def __init__(self):
            super().__init__()
            self.problem_attempts = 0
            self.problem_instructions = []

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "research.structure_problem":
                self.problem_attempts += 1
                self.problem_instructions.append(instructions)
                if self.problem_attempts == 1:
                    return {"problem_statement": inputs["problem_input"]}
            return super().generate_json(task, inputs, schema_hint, instructions)

    repository = Repository(data_dir=str(tmp_path / "data"))
    llm = RevisingProblemLLM()
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = repository.create_run("train cnn", "Revision loop")

    run = engine.run_step(run.id, "problem_understanding")

    problems = [artifact for artifact in run.artifacts if artifact.type == "problem"]
    assert llm.problem_attempts == 2
    assert "constraints is required" in llm.problem_instructions[-1]
    assert len(problems) == 1
    assert problems[0].content["constraints"] == ["real experiment"]


def test_research_plan_trace_records_routed_skills(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
        SkillLoader(Path(__file__).resolve().parents[2]),
        SkillRegistry(),
    )
    run = _selected_hypothesis_run(engine, repository)

    run = engine.run_step(run.id, "research_plan")

    supervisor_call = next(
        call for call in run.events[-1].tool_calls if call["provider"] == "supervisor_agent"
    )
    runtime_call = next(
        call for call in run.events[-1].tool_calls if call["provider"] == "skill_runtime"
    )
    expected_skills = [
        "research-refine",
        "hypothesis-experiment-gate",
        "experiment-plan",
        "plan-review-governance",
    ]
    assert supervisor_call["skills"] == expected_skills
    assert runtime_call["skills"] == expected_skills
    assert runtime_call["agent_id"] == "planning"
    assert len(runtime_call["instruction_sha256"]) == 64


def test_missing_skill_stops_before_creating_a_plan_artifact(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
        SkillLoader(Path(__file__).resolve().parents[2]),
        SkillRegistry(),
    )
    run = _selected_hypothesis_run(engine, repository)
    engine.skill_loader = SkillLoader(tmp_path / "missing-skills-root")

    with pytest.raises(ValueError, match="SKILL_NOT_FOUND:research-refine"):
        engine.run_step(run.id, "research_plan")

    assert "plan" not in {artifact.type for artifact in repository.get_run(run.id).artifacts}


def test_experiment_task_trace_records_catalog_selection(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    loader = SkillLoader(Path(__file__).resolve().parents[2])
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
        loader,
        SkillRegistry(),
        SkillCatalog(loader),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")

    run = engine.run_step(run.id, "experiment_task")

    route = next(item for item in run.events[-1].tool_calls if item["provider"] == "skill_runtime")
    assert route["skills"] == ["experiment-implementation"]
    assert route["agent_id"] == "experiment"


def test_engine_records_one_supervisor_and_runtime_call_per_step(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = repository.create_run("train cnn", "Runtime audit")

    run = engine.run_step(run.id, "problem_understanding")

    calls = run.events[-1].tool_calls
    assert sum(call["provider"] == "supervisor_agent" for call in calls) == 1
    assert sum(call["provider"] == "skill_runtime" for call in calls) == 1


def test_engine_blocks_provider_call_when_skill_runtime_denies_required_tool(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = repository.create_run("train cnn", "Denied literature tool")
    run = engine.run_step(run.id, "problem_understanding")
    original_prepare = engine.skill_runtime.prepare

    def deny_tools(*args, **kwargs):
        return replace(original_prepare(*args, **kwargs), authorized_tools=())

    engine.skill_runtime.prepare = deny_tools

    with pytest.raises(
        ValueError,
        match="SKILL_TOOL_UNAUTHORIZED:knowledge_integration:query_wiki",
    ):
        engine.run_step(run.id, "knowledge_integration")

    assert not any(
        artifact.type == "evidence"
        for artifact in repository.get_run(run.id).artifacts
    )


def test_knowledge_integration_uses_all_queries_and_records_source_status(tmp_path):
    class TwoQueryLLM(RecordingLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            result = super().generate_json(task, inputs, schema_hint, instructions)
            if task == "research.structure_problem":
                result["literature_queries"] = ["dropout robustness", "fixed seed variance"]
            return result

    class RecordingLiterature(MockLiteratureProvider):
        def __init__(self):
            super().__init__()
            self.queries = []

        def search(self, query, limit):
            self.queries.append(query)
            return super().search(query, limit)

    repository = Repository(data_dir=str(tmp_path / "data"))
    literature = RecordingLiterature()
    engine = WorkflowEngine(
        repository,
        TwoQueryLLM(),
        literature,
        MockExperimentProvider(),
    )
    run = repository.create_run("train cnn", "Literature sources")
    run = engine.run_step(run.id, "problem_understanding")

    run = engine.run_step(run.id, "knowledge_integration")

    evidence = [item for item in run.artifacts if item.type == "evidence"][-1].content
    assert literature.queries == ["dropout robustness", "fixed seed variance"]
    assert "WIKI_EMPTY" in evidence["warnings"]
    assert [call["source"] for call in evidence["sources"]["calls"]] == [
        "wiki",
        "local",
        "external",
        "wiki",
        "local",
        "external",
    ]


def test_experiment_task_creates_stable_experiment_bundle_artifact(tmp_path):
    class CodegenLLM(RecordingLLM):
        def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
            if task == "experiment.generate_bundle":
                self.tasks.append(task)
                self.inputs.append((task, inputs))
                assert "Experiment Implementation" in instructions
                assert "provider-neutral experiment bundle" in instructions
                return {
                    "entrypoint": "train.py",
                    "files": [{"path": "train.py", "content": (
                        "import argparse, json\nfrom pathlib import Path\n"
                        "p=argparse.ArgumentParser()\n"
                        "p.add_argument('--run-id', required=True)\n"
                        "p.add_argument('--experiment-id', required=True)\n"
                        "p.add_argument('--result-id', required=True)\n"
                        "p.add_argument('--output', required=True)\n"
                        "p.add_argument('--smoke-test', action='store_true')\n"
                        "a=p.parse_args()\nsmoke_test=a.smoke_test\n"
                        "Path(a.output).parent.mkdir(parents=True, exist_ok=True)\n"
                        "Path(a.output).write_text(json.dumps({'run_id': a.run_id, "
                        "'experiment_id': a.experiment_id, 'result_id': a.result_id, "
                        "'metrics': {'accuracy': 1.0}}), encoding='utf-8')\n"
                    )}],
                    "python_args": ["--seed", "7"],
                    "requirements": [],
                    "requires_gpu": False,
                    "expected_metrics": ["accuracy"],
                    "parameters": {"seed": 7},
                    "seeds": [7],
                    "supports_smoke_test": True,
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repository = Repository(data_dir=str(tmp_path / "data"))
    loader = SkillLoader(Path(__file__).resolve().parents[2])
    llm = CodegenLLM()
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
        loader,
        SkillRegistry(),
        SkillCatalog(loader),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")

    run = engine.run_step(run.id, "experiment_task")

    latest = {artifact.type: artifact for artifact in run.artifacts}
    assert latest["experiment_task"].content["experiment_id"] == "experiment_1"
    assert latest["experiment_bundle"].content["manifest"]["experiment_id"] == "experiment_1"
    assert latest["experiment_bundle"].content["manifest"]["result_id"] == (
        "experiment_1_result"
    )
    assert latest["experiment_bundle"].parent_artifact_id == latest["experiment_task"].id
    assert "experiment.generate_bundle" in llm.tasks


def test_experiment_task_retries_invalid_generated_bundle(tmp_path):
    class InvalidThenValidBundleLLM(RecordingLLM):
        def __init__(self):
            super().__init__()
            self.generate_attempts = 0
            self.repair_inputs = []

        def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
            if task == "experiment.generate_bundle":
                self.generate_attempts += 1
                self.tasks.append(task)
                self.inputs.append((task, inputs))
                result = super().generate_json(task, inputs, schema_hint, instructions)
                result["files"][0]["content"] += "\ntorchvision.datasets.CIFAR10(root='data', download=True)\n"
                return result
            if task == "experiment.repair_bundle":
                self.repair_inputs.append((inputs, instructions))
                result = super().generate_json("experiment.generate_bundle", inputs, schema_hint, instructions)
                return {
                    "files": [{
                        "path": "train.py",
                        "content_lines": result["files"][0]["content"].splitlines(),
                    }],
                    "requirements": [],
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repository = Repository(data_dir=str(tmp_path / "data"))
    llm = InvalidThenValidBundleLLM()
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")

    run = engine.run_step(run.id, "experiment_task")

    assert llm.generate_attempts == 1
    assert len(llm.repair_inputs) == 1
    repair_inputs, repair_instructions = llm.repair_inputs[0]
    assert "CIFAR10" in repair_inputs["files"][0]["content"]
    assert repair_inputs["validation_feedback"] == [
        "EXPERIMENT_BUNDLE_RUNTIME_DOWNLOAD_FORBIDDEN",
        "EXPERIMENT_BUNDLE_DATASET_ROOT_INVALID: load the declared dataset with "
        "root=os.environ['DATA_ROOT'] and download=False; the runtime provisions "
        "the dataset under DATA_ROOT before execution.",
    ]
    assert repair_inputs["repair_history"] == [{
        "attempt": 1,
        "issues": [
            "EXPERIMENT_BUNDLE_RUNTIME_DOWNLOAD_FORBIDDEN",
            "EXPERIMENT_BUNDLE_DATASET_ROOT_INVALID: load the declared dataset with "
            "root=os.environ['DATA_ROOT'] and download=False; the runtime provisions "
            "the dataset under DATA_ROOT before execution.",
        ],
    }]
    assert "EXPERIMENT_BUNDLE_RUNTIME_DOWNLOAD_FORBIDDEN" in repair_instructions
    latest = {artifact.type: artifact for artifact in run.artifacts}
    assert latest["experiment_task"].content["experiment_id"] == "experiment_1"
    attempts = [
        artifact for artifact in run.artifacts
        if artifact.type == "experiment_candidate_attempt"
    ]
    assert len(attempts) == 2
    rejected = attempts[0]
    assert rejected.content["accepted"] is False
    assert rejected.content["raw_model_output"]["files"]
    assert rejected.content["normalized_bundle"]["manifest"]
    assert rejected.content["manifest"] == rejected.content["normalized_bundle"]["manifest"]
    assert rejected.content["files"] == rejected.content["normalized_bundle"]["files"]
    assert rejected.content["requirements"] == rejected.content["normalized_bundle"]["requirements"]
    assert rejected.content["validation_issues"] == [
        "EXPERIMENT_BUNDLE_RUNTIME_DOWNLOAD_FORBIDDEN",
        "EXPERIMENT_BUNDLE_DATASET_ROOT_INVALID: load the declared dataset with "
        "root=os.environ['DATA_ROOT'] and download=False; the runtime provisions "
        "the dataset under DATA_ROOT before execution.",
    ]
    assert rejected.content["attempt_id"] == rejected.id
    assert rejected.content["parent_attempt_id"] == ""
    assert rejected.content["skill_hash"]
    assert rejected.content["plan_artifact_id"]
    assert "contract_id" in rejected.content["dataset_contract_reference"]
    repaired = attempts[1]
    assert repaired.content["parent_attempt_id"] == rejected.id
    assert repaired.parent_artifact_id == rejected.id
    assert (
        rejected.content["normalized_bundle"]["runtime_contract"]
        == repaired.content["normalized_bundle"]["runtime_contract"]
    )
    rejected_events = [
        event for event in run.events
        if event.step_id == "experiment_task"
        and event.message == "Rejected candidate output and requested revision."
    ]
    assert rejected_events
    assert rejected_events[-1].output_summary["issues"] == [
        "EXPERIMENT_BUNDLE_RUNTIME_DOWNLOAD_FORBIDDEN",
        "EXPERIMENT_BUNDLE_DATASET_ROOT_INVALID: load the declared dataset with "
        "root=os.environ['DATA_ROOT'] and download=False; the runtime provisions "
        "the dataset under DATA_ROOT before execution.",
    ]


def test_experiment_task_second_repair_receives_previous_candidate_and_full_history(tmp_path):
    class TwoRepairLLM(RecordingLLM):
        def __init__(self):
            super().__init__()
            self.generate_attempts = 0
            self.repair_inputs = []

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "experiment.generate_bundle":
                self.generate_attempts += 1
                result = super().generate_json(task, inputs, schema_hint, instructions)
                result["files"][0]["content"] += (
                    "\ntorchvision.datasets.CIFAR10(root='data', download=True)\n"
                )
                return result
            if task == "experiment.repair_bundle":
                self.repair_inputs.append(inputs)
                result = super().generate_json(
                    "experiment.generate_bundle", inputs, schema_hint, instructions
                )
                source = result["files"][0]["content"]
                if len(self.repair_inputs) == 1:
                    source += "\nimport torch\ntorch.softplus(1)\n"
                    requirements = ["torch"]
                else:
                    requirements = []
                return {
                    "files": [{"path": "train.py", "content_lines": source.splitlines()}],
                    "requirements": requirements,
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repository = Repository(data_dir=str(tmp_path / "data"))
    llm = TwoRepairLLM()
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")

    assert llm.generate_attempts == 1
    assert len(llm.repair_inputs) == 2
    second = llm.repair_inputs[1]
    assert "torch.softplus(1)" in second["files"][0]["content"]
    assert second["validation_feedback"] == [
        "EXPERIMENT_CODE_API_INVALID:train.py:torch.softplus does not exist; use torch.nn.functional.softplus"
    ]
    assert second["repair_history"] == [
        {
            "attempt": 1,
            "issues": [
                "EXPERIMENT_BUNDLE_RUNTIME_DOWNLOAD_FORBIDDEN",
                "EXPERIMENT_BUNDLE_DATASET_ROOT_INVALID: load the declared dataset with "
                "root=os.environ['DATA_ROOT'] and download=False; the runtime provisions "
                "the dataset under DATA_ROOT before execution.",
            ],
        },
        {
            "attempt": 2,
            "issues": [
                "EXPERIMENT_CODE_API_INVALID:train.py:torch.softplus does not exist; use torch.nn.functional.softplus"
            ],
        },
    ]
    task = next(artifact for artifact in run.artifacts if artifact.type == "experiment_task")
    assert task.content["repair_history"] == second["repair_history"]


def test_experiment_task_malformed_repair_keeps_last_complete_bundle_as_next_base(tmp_path):
    class MalformedRepairThenValidLLM(RecordingLLM):
        def __init__(self):
            super().__init__()
            self.repair_inputs = []

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "experiment.generate_bundle":
                result = super().generate_json(task, inputs, schema_hint, instructions)
                result["files"][0]["content"] += (
                    "\ntorchvision.datasets.CIFAR10(root='data', download=True)\n"
                )
                return result
            if task == "experiment.repair_bundle":
                self.repair_inputs.append(inputs)
                if len(self.repair_inputs) == 1:
                    return {"files": [], "requirements": []}
                result = super().generate_json(
                    "experiment.generate_bundle", inputs, schema_hint, instructions
                )
                return {
                    "files": [{
                        "path": "train.py",
                        "content_lines": result["files"][0]["content"].splitlines(),
                    }],
                    "requirements": [],
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repository = Repository(data_dir=str(tmp_path / "data"))
    llm = MalformedRepairThenValidLLM()
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")

    assert len(llm.repair_inputs) == 2
    assert "torchvision.datasets.CIFAR10" in llm.repair_inputs[1]["files"][0]["content"]
    assert llm.repair_inputs[1]["previous_candidate"] == {}
    attempts = [
        artifact for artifact in run.artifacts
        if artifact.type == "experiment_candidate_attempt"
    ]
    malformed_repair = attempts[1]
    assert malformed_repair.content["accepted"] is False
    assert malformed_repair.content["raw_model_output"]["files"] == []
    assert malformed_repair.content["normalized_bundle"] is None


def test_experiment_task_records_final_issue_before_revision_limit_error(tmp_path):
    class AlwaysInvalidBundleLLM(RecordingLLM):
        def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
            if task == "experiment.repair_bundle":
                result = super().generate_json(
                    "experiment.generate_bundle", inputs, schema_hint, instructions
                )
                result = {
                    "files": [],
                    "requirements": result["requirements"],
                }
                return result
            result = super().generate_json(task, inputs, schema_hint, instructions)
            if task == "experiment.generate_bundle":
                result["files"] = []
            return result

    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        AlwaysInvalidBundleLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")

    with pytest.raises(ValueError, match="SUPERVISOR_REVISION_LIMIT:experiment_task:5"):
        engine.run_step(run.id, "experiment_task")

    failed_run = repository.get_run(run.id)
    final = failed_run.events[-1]
    assert final.message == "Rejected candidate output after revision limit."
    assert final.data["attempt"] == 6
    assert final.data["status"] == "revision_limit_exceeded"
    assert final.output_summary["issues"][0].startswith("EXPERIMENT_CODE_FILES_MISSING")
    assert len(final.data["repair_history"]) == 6
    assert final.data["repair_history"][0]["issues"][0].startswith(
        "EXPERIMENT_CODE_FILES_MISSING"
    )


def test_experiment_task_recovers_when_bundle_files_are_missing(tmp_path):
    class MissingFilesThenValidLLM(RecordingLLM):
        def __init__(self):
            super().__init__()
            self.generate_attempts = 0
            self.repair_inputs = []

        def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
            if task == "experiment.generate_bundle":
                self.generate_attempts += 1
                result = super().generate_json(task, inputs, schema_hint, instructions)
                result["files"] = []
                return result
            if task == "experiment.repair_bundle":
                self.repair_inputs.append(inputs)
                result = super().generate_json("experiment.generate_bundle", inputs, schema_hint, instructions)
                return {
                    "files": [{
                        "path": "train.py",
                        "content_lines": result["files"][0]["content"].splitlines(),
                    }],
                    "requirements": [],
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repository = Repository(data_dir=str(tmp_path / "data"))
    llm = MissingFilesThenValidLLM()
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")

    run = engine.run_step(run.id, "experiment_task")

    assert llm.generate_attempts == 1
    assert len(llm.repair_inputs) == 1
    assert llm.repair_inputs[0]["previous_candidate"]["files"] == []
    assert llm.repair_inputs[0]["validation_feedback"] == [
        "EXPERIMENT_CODE_FILES_MISSING: return a files array containing an object with path 'train.py' and the complete Python source as a content_lines array."
    ]
    latest = {artifact.type: artifact for artifact in run.artifacts}
    assert latest["experiment_task"].content["experiment_id"] == "experiment_1"


def test_workflow_links_experiment_result_to_bundle_with_stable_ids(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")
    bundle_artifact = [
        artifact for artifact in run.artifacts if artifact.type == "experiment_bundle"
    ][-1]

    run = engine.run_step(run.id, "experiment_run_analysis")

    result_artifact = [
        artifact for artifact in run.artifacts if artifact.type == "experiment_result"
    ][-1]
    assert result_artifact.content["run_id"] == run.id
    assert result_artifact.content["experiment_id"] == "experiment_1"
    assert result_artifact.content["result_id"] == "experiment_1_result"
    assert result_artifact.content["analysis"]["metrics"] == result_artifact.content["metrics"]
    assert result_artifact.content["analysis"]["verdict"] == "partial"
    assert result_artifact.content["audit"]["integrity_status"] == "passed"
    assert result_artifact.content["audit"]["verified_files"][0]["sha256"]
    assert result_artifact.parent_artifact_id == bundle_artifact.id


def test_workflow_imports_completed_provider_result_instead_of_retraining(tmp_path):
    class RecoverableExperimentProvider(MockExperimentProvider):
        def __init__(self):
            self.recovery_calls = 0
            self.run_calls = 0

        def recover_completed_result(self, task, bundle):
            self.recovery_calls += 1
            return {
                **MockExperimentProvider.run(self, task, bundle),
                "attempt_id": "attempt_20260720T094858_7ceb00ef",
                "start_time": "2026-07-20T09:49:00+00:00",
                "end_time": "2026-07-20T10:57:38+00:00",
                "recovered_from_completed_attempt": True,
            }

        def run(self, task, code=None):
            self.run_calls += 1
            raise AssertionError("completed attempt must be imported, not rerun")

    repository = Repository(data_dir=str(tmp_path / "data"))
    provider = RecoverableExperimentProvider()
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        provider,
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")

    run = engine.run_step(run.id, "experiment_run_analysis")

    result = [
        artifact for artifact in run.artifacts if artifact.type == "experiment_result"
    ][-1].content
    assert provider.recovery_calls == 1
    assert provider.run_calls == 0
    assert result["recovered_from_completed_attempt"] is True
    assert result["attempts"][-1]["recovered"] is True


def test_rerunning_analysis_reuses_bundle_and_appends_attempt(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")
    run = engine.run_step(run.id, "experiment_run_analysis")

    rerun = engine.rerun_from(run.id, "experiment_run_analysis")

    results = [
        artifact for artifact in rerun.artifacts if artifact.type == "experiment_result"
    ]
    assert len(results) == 2
    assert results[-1].content["experiment_id"] == "experiment_1"
    assert results[-1].content["result_id"] == "experiment_1_result"
    assert [attempt["attempt"] for attempt in results[-1].content["attempts"]] == [1, 2]


def test_rerunning_analysis_keeps_the_latest_feedback_iteration_lineage(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    for step_id in [
        "research_plan",
        "experiment_task",
        "experiment_run_analysis",
        "feedback_revision",
        "research_plan",
        "experiment_task",
        "experiment_run_analysis",
    ]:
        run = engine.run_step(run.id, step_id)

    tasks_before = [
        artifact for artifact in run.artifacts if artifact.type == "experiment_task"
    ]
    bundles_before = [
        artifact for artifact in run.artifacts if artifact.type == "experiment_bundle"
    ]
    revisions_before = [
        artifact for artifact in run.artifacts if artifact.type == "revision"
    ]
    plans_before = [artifact for artifact in run.artifacts if artifact.type == "plan"]
    active_task = tasks_before[-1]
    active_bundle = bundles_before[-1]

    rerun = engine.rerun_from(run.id, "experiment_run_analysis")

    tasks_after = [
        artifact for artifact in rerun.artifacts if artifact.type == "experiment_task"
    ]
    bundles_after = [
        artifact for artifact in rerun.artifacts if artifact.type == "experiment_bundle"
    ]
    results_after = [
        artifact for artifact in rerun.artifacts if artifact.type == "experiment_result"
    ]
    assert [artifact.id for artifact in tasks_after] == [
        artifact.id for artifact in tasks_before
    ]
    assert active_task.id == tasks_after[-1].id
    assert active_bundle.id == bundles_after[-1].id
    assert len(
        [artifact for artifact in rerun.artifacts if artifact.type == "revision"]
    ) == len(revisions_before)
    assert len([artifact for artifact in rerun.artifacts if artifact.type == "plan"]) == len(
        plans_before
    )
    assert results_after[-1].parent_artifact_id == active_bundle.id
    assert [attempt["attempt"] for attempt in results_after[-1].content["attempts"]] == [
        1,
        2,
    ]
    retry_event = next(
        event
        for event in reversed(rerun.events)
        if event.message
        == "Retrying the current experiment iteration with a new attempt."
    )
    assert retry_event.data["retry_mode"] == "new_attempt_same_iteration"
    assert retry_event.data["task_artifact_id"] == active_task.id
    assert retry_event.data["bundle_artifact_id"] == active_bundle.id


def test_persisted_retry_recovers_nullable_runtime_repair_candidate_lineage(tmp_path):
    """A cancelled repair candidate must not poison a later same-task retry."""
    class RetryRepairLLM(RecordingLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "experiment.repair_bundle":
                return {
                    "files": [{
                        "path": "train.py",
                        "content_lines": inputs["files"][0]["content"].splitlines(),
                    }],
                    "requirements": inputs["requirements"],
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    class FailsOnlyDuringRetry(MockExperimentProvider):
        def __init__(self):
            self.run_calls = 0

        def run(self, task, code=None):
            self.run_calls += 1
            if self.run_calls == 2:
                raise RuntimeError("LOCAL_EXPERIMENT_RUN_FAILED: simulated retry failure")
            return super().run(task, code)

    data_dir = tmp_path / "data"
    repository = Repository(data_dir=str(data_dir))
    provider = FailsOnlyDuringRetry()
    engine = WorkflowEngine(
        repository,
        RetryRepairLLM(),
        MockLiteratureProvider(),
        provider,
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")
    run = engine.run_step(run.id, "experiment_run_analysis")
    task = next(artifact for artifact in reversed(run.artifacts) if artifact.type == "experiment_task")

    # This is the persisted shape written when the earlier repair was cancelled
    # before a Bundle could be normalized.  It previously triggered
    # ``None.get(...)`` while resolving the next repair parent.
    cancelled = repository.add_artifact(
        run.id,
        "experiment_candidate_attempt",
        "Cancelled Runtime Repair Candidate",
        {
            "candidate_origin": "runtime_repair",
            "normalized_bundle": None,
            "manifest": {},
            "files": [],
            "requirements": [],
            "plan_artifact_id": task.parent_artifact_id,
            "accepted": False,
            "validation_issues": ["PIPELINE_STOPPED: user requested cancellation"],
        },
        "experiment_run_analysis",
        "Experiment Skill",
        parent_artifact_id=task.id,
    )

    # Use a fresh repository/engine to exercise the on-disk checkpoint path.
    resumed = WorkflowEngine(
        Repository(data_dir=str(data_dir)),
        RetryRepairLLM(),
        MockLiteratureProvider(),
        provider,
    ).rerun_from(run.id, "experiment_run_analysis")

    result = [artifact for artifact in resumed.artifacts if artifact.type == "experiment_result"][-1]
    repaired_candidate = [
        artifact for artifact in resumed.artifacts
        if artifact.type == "experiment_candidate_attempt"
    ][-1]
    assert provider.run_calls == 3
    assert result.content["attempts"][-1]["status"] == "completed"
    assert repaired_candidate.content["parent_attempt_id"] == cancelled.id
    assert repaired_candidate.content["normalization_status"] == "normalized"


def test_failed_experiment_attempt_is_persisted_and_returned_for_feedback(tmp_path):
    class FailingExperimentProvider(MockExperimentProvider):
        def run(self, task, code=None):
            raise RuntimeError("LOCAL_EXPERIMENT_CUDA_UNAVAILABLE")

    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        FailingExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")

    run = engine.run_step(run.id, "experiment_run_analysis")

    latest = {
        artifact.type: artifact for artifact in repository.get_run(run.id).artifacts
    }
    failure = latest["experiment_result"]
    assert failure.content["experiment_id"] == "experiment_1"
    assert failure.content["result_id"] == "experiment_1_result"
    assert failure.content["metrics"] == {}
    assert failure.content["status"] == "failed"
    assert failure.content["verdict"] == "failed"
    assert failure.content["attempts"][-1]["status"] == "failed"
    assert failure.content["attempts"][-1]["error_code"] == (
        "LOCAL_EXPERIMENT_CUDA_UNAVAILABLE"
    )
    assert failure.parent_artifact_id == latest["experiment_bundle"].id


def test_dataset_download_failure_is_diagnosed_repaired_and_retried(tmp_path):
    class RecoveringExperimentProvider(MockExperimentProvider):
        def __init__(self):
            self.run_count = 0
            self.repair_count = 0

        def run(self, task, code=None):
            self.run_count += 1
            if self.run_count == 1:
                raise RuntimeError(
                    "EXPERIMENT_DATASET_DOWNLOAD_FAILED:cifar-10. "
                    "File not found or corrupted."
                )
            return super().run(task, code)

        def quarantine_failed_dataset_download(self, name):
            self.repair_count += 1
            return {
                "status": "completed",
                "dataset": name,
                "moved": [{"from": "partial", "to": "quarantine/partial"}],
            }

    repository = Repository(data_dir=str(tmp_path / "data"))
    provider = RecoveringExperimentProvider()
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        provider,
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")

    run = engine.run_step(run.id, "experiment_run_analysis")

    latest = {
        artifact.type: artifact for artifact in repository.get_run(run.id).artifacts
    }
    result = latest["experiment_result"].content
    diagnosis = latest["experiment_diagnosis"].content
    assert provider.run_count == 2
    assert provider.repair_count == 1
    assert [attempt["status"] for attempt in result["attempts"]] == [
        "failed",
        "completed",
    ]
    assert result["metrics"]
    assert result.get("status") != "failed"
    assert diagnosis["category"] == "dataset"
    assert diagnosis["repair_action"] == "quarantine_corrupt_dataset_download"
    assert diagnosis["repair_result"]["status"] == "completed"
    assert diagnosis["resolved"] is True
    diagnostic_events = [
        event for event in run.events if event.actor == "Experiment Diagnostic Agent"
    ]
    assert len(diagnostic_events) == 2
    assert diagnostic_events[-1].message == (
        "Verified automatic repair with a successful retry."
    )


def test_generated_code_failure_repairs_source_preserves_contract_and_retries(tmp_path):
    class RepairingLLM(RecordingLLM):
        def __init__(self):
            super().__init__()
            self.repair_calls = 0

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "experiment.repair_bundle":
                self.repair_calls += 1
                source = inputs["files"][0]["content"]
                if self.repair_calls == 1:
                    source += (
                        "\nclass Mish:\n"
                        "    def __call__(self, x):\n"
                        "        return x * torch.tanh(torch.softplus(x))\n"
                    )
                else:
                    assert "EXPERIMENT_CODE_API_INVALID" in inputs["validation_feedback"][0]
                return {
                    "files": [{"path": "train.py", "content_lines": source.splitlines()}],
                    "requirements": inputs["requirements"],
                    "parameters": {"forbidden_change": True},
                    "seeds": [999],
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    class RecoveringCodeProvider(MockExperimentProvider):
        def __init__(self):
            self.run_count = 0

        def run(self, task, code=None):
            self.run_count += 1
            if self.run_count == 1:
                raise RuntimeError(
                    "LOCAL_EXPERIMENT_RUN_FAILED: AttributeError: module 'torch' "
                    "has no attribute 'softplus'"
                )
            return super().run(task, code)

    repository = Repository(data_dir=str(tmp_path / "data"))
    llm = RepairingLLM()
    provider = RecoveringCodeProvider()
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        provider,
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")
    original_bundle = [
        artifact for artifact in run.artifacts if artifact.type == "experiment_bundle"
    ][-1].content

    run = engine.run_step(run.id, "experiment_run_analysis")

    latest = {artifact.type: artifact for artifact in run.artifacts}
    repaired_bundle = latest["experiment_bundle"].content
    diagnosis = latest["experiment_diagnosis"].content
    assert provider.run_count == 2
    assert llm.repair_calls == 2
    assert repaired_bundle["manifest"]["parameters"] == original_bundle["manifest"]["parameters"]
    assert repaired_bundle["manifest"]["seeds"] == original_bundle["manifest"]["seeds"]
    assert diagnosis["repair_action"] == "repair_experiment_code"
    assert diagnosis["repair_result"]["scientific_contract_preserved"] is True
    assert diagnosis["repair_result"]["candidate_attempts"] == 2
    assert diagnosis["resolved"] is True
    candidates = [
        artifact for artifact in run.artifacts
        if artifact.type == "experiment_candidate_attempt"
    ]
    rejected_repair, accepted_repair = candidates[-2:]
    assert rejected_repair.content["candidate_origin"] == "runtime_repair"
    assert rejected_repair.content["accepted"] is False
    assert rejected_repair.content["raw_model_output"]["files"]
    assert "EXPERIMENT_CODE_API_INVALID" in rejected_repair.content["validation_issues"][0]
    assert accepted_repair.content["parent_attempt_id"] == rejected_repair.id


def test_failed_engineering_result_does_not_reach_scientific_feedback(tmp_path):
    class FailingExperimentProvider(MockExperimentProvider):
        def run(self, task, code=None):
            raise RuntimeError("LOCAL_EXPERIMENT_CUDA_UNAVAILABLE")

    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        FailingExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")
    run = engine.run_step(run.id, "experiment_run_analysis")

    failure = [artifact for artifact in run.artifacts if artifact.type == "experiment_result"][-1]
    assert failure.content["status"] == "failed"

    # An engineering failure must not be turned into a scientific revision or
    # negative; feedback_revision skips it without producing a revision artifact.
    run = engine.run_step(run.id, "feedback_revision")
    assert not any(artifact.type == "revision" for artifact in run.artifacts)


def test_rerun_from_locked_experiment_task_preserves_its_bundle(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")
    task = next(artifact for artifact in run.artifacts if artifact.type == "experiment_task")
    bundle = next(artifact for artifact in run.artifacts if artifact.type == "experiment_bundle")
    repository.lock_artifact(run.id, task.id, True)
    engine.run_step(run.id, "experiment_run_analysis")

    rerun = engine.rerun_from(run.id, "experiment_task")

    artifact_ids = {artifact.id for artifact in rerun.artifacts}
    assert task.id in artifact_ids
    assert bundle.id in artifact_ids
    assert any(artifact.type == "experiment_result" for artifact in rerun.artifacts)
    assert any(
        event.data.get("mode") == "append_only"
        and any(item.type == "experiment_result" and item.id in event.data.get("superseded_artifact_ids", []) for item in rerun.artifacts)
        for event in rerun.events
    )


def test_experiment_analysis_refuses_legacy_artifact_without_bundle(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")
    run.artifacts = [
        artifact for artifact in run.artifacts if artifact.type != "experiment_bundle"
    ]
    repository.save_run(run)

    with pytest.raises(ValueError, match="EXPERIMENT_BUNDLE_REQUIRED"):
        engine.run_step(run.id, "experiment_run_analysis")


def test_supervisor_delegates_reasoning_to_llm_agents(tmp_path):
    repo = Repository(data_dir=str(tmp_path))
    llm = RecordingLLM()
    run = repo.create_run("train a compact cnn with dropout ablation", "Dropout ablation")
    engine = WorkflowEngine(
        repository=repo,
        llm_provider=llm,
        literature_provider=MockLiteratureProvider(),
        experiment_provider=MockExperimentProvider(),
    )

    for step_id in [
        "problem_understanding",
        "knowledge_integration",
        "hypothesis_generation",
        "evidence_reasoning",
    ]:
        run = engine.run_step(run.id, step_id)
    run = engine.select_hypothesis(run.id, 0)
    for step_id in ["research_plan", "experiment_task", "experiment_run_analysis", "feedback_revision"]:
        run = engine.run_step(run.id, step_id)

    assert "research.structure_problem" in llm.tasks
    assert "hypothesis.generate" in llm.tasks
    assert "planning.build_plan" in llm.tasks
    assert "experiment.analyze_results" in llm.tasks
    assert "experiment.audit_result" in llm.tasks
    assert "planning.refine_plan" in llm.tasks
    assert "critic.review_result" in llm.tasks
    assert run.events[-1].actor == "Critic Skill"
    assert run.events[-1].provider_mode == "qwen"


def test_planning_agent_refinement_uses_full_plan_schema_and_inputs():
    class SchemaRecordingLLM:
        mode = "qwen"
        fallback = False

        def __init__(self):
            self.calls = []

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            self.calls.append((task, inputs, schema_hint, instructions))
            return {}

    llm = SchemaRecordingLLM()
    agent = PlanningAgent(llm)
    selection = {"selected": [{"claim": "fixed-seed claim"}]}
    current_plan = {"objective": "test the claim"}
    experiment_result = {"metrics": {"accuracy": 0.5}}
    feedback = {"verdict": "partial", "required_revision": "add ablation"}

    agent.build_plan(selection, instructions="initial")
    agent.refine_plan(
        selection,
        current_plan,
        experiment_result,
        feedback,
        instructions="refine",
    )

    build_call, refine_call = llm.calls
    assert refine_call[0] == "planning.refine_plan"
    assert refine_call[1] == {
        "selection": selection,
        "current_plan": current_plan,
        "experiment_result": experiment_result,
            "feedback": feedback,
            "dataset_options": [],
            "observed_structure": [],
            "plan_context": {},
        }
    assert refine_call[2] == build_call[2]
    assert refine_call[3].startswith("refine\n\nAuthoritative Plan Contract")
    assert "capacity_confounder" in refine_call[2]
    assert "local_dataset_loader_verification" in refine_call[2]


def test_critic_result_review_schema_requests_a_normalized_verdict():
    class SchemaRecordingLLM:
        mode = "qwen"
        fallback = False

        def __init__(self):
            self.schema_hint = None

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            self.schema_hint = schema_hint
            return {"verdict": "supported"}

    llm = SchemaRecordingLLM()

    CriticAgent(llm).review_result(
        {"claim": "fixed-seed claim"},
        {"metrics": {"accuracy": 0.5}},
    )

    assert llm.schema_hint["verdict"] == "supported|partial|failed"


def test_mock_llm_has_deterministic_plan_refinement_fallback():
    current_plan = {
        "objective": "test the selected claim",
        "hypotheses": ["fixed-seed claim"],
        "dataset": {"name": "Fashion-MNIST"},
        "comparisons": [],
        "evaluations": [],
        "procedure": {"steps": ["train baseline"]},
        "resources": {},
        "risks": [],
        "additional_sections": {},
    }
    provider = MockLLMProvider()

    first = provider.generate_json(
        "planning.refine_plan",
        {
            "current_plan": current_plan,
            "feedback": {"required_revision": "add a fixed-seed ablation"},
        },
        {},
    )
    second = provider.generate_json(
        "planning.refine_plan",
        {
            "current_plan": current_plan,
            "feedback": {"required_revision": "add a fixed-seed ablation"},
        },
        {},
    )

    assert first == second
    assert first["objective"] == current_plan["objective"]
    assert first["provider_mode"] == "mock"
    assert first["fallback_used"] is True


class FeedbackIterationLLM(RecordingLLM):
    def __init__(self, verdicts, decisions=None):
        super().__init__()
        self.verdicts = list(verdicts)
        self.decisions = list(decisions or [])
        self.review_count = 0

    def generate_json(self, task, inputs, schema_hint, instructions=""):
        if task == "critic.review_result":
            self.tasks.append(task)
            self.inputs.append((task, inputs))
            verdict = self.verdicts[self.review_count]
            decision = (
                self.decisions[self.review_count]
                if self.review_count < len(self.decisions)
                else (
                    "REPORT"
                    if str(verdict).strip().lower()
                    in {"passed", "pass", "success", "supported"}
                    else "REVISE"
                )
            )
            self.review_count += 1
            return {
                "verdict": verdict,
                "decision": decision,
                "feedback": f"feedback round {self.review_count}",
                "required_revision": "add a fixed-seed ablation",
            }
        return super().generate_json(task, inputs, schema_hint, instructions)


class ResultDrivenIterationLLM(RecordingLLM):
    def generate_json(self, task, inputs, schema_hint, instructions=""):
        if task == "critic.review_result":
            self.tasks.append(task)
            self.inputs.append((task, inputs))
            return {
                "verdict": "partial",
                "decision": "REVISE",
                "feedback": "The accuracy target was not met.",
                "required_revision": "Test one evidence-grounded change.",
                "supported_claims": [],
                "unsupported_claims": ["The planned accuracy gain was not observed."],
                "revisions": ["Compare bounded alternatives before another run."],
                "next_action": "Retrieve evidence about the observed failure.",
                "evidence_links": [],
                "overclaim_risks": ["Do not claim improvement."],
                "result_analysis": {
                    "measured_facts": ["Accuracy remained below the target."],
                    "failed_criteria": ["The planned accuracy gain was not met."],
                    "improved_metrics": [],
                    "degraded_metrics": ["accuracy"],
                    "uncertainties": ["The causal mechanism is unresolved."],
                    "methodological_issues": [],
                    "causal_hypotheses": ["The intervention may over-regularize."],
                    "knowledge_gaps": [
                        "Which bounded regularization change best distinguishes over-regularization?"
                    ],
                },
                "literature_queries": [{
                    "question": "Which bounded change tests over-regularization?",
                    "query": "dropout over regularization small convolutional network ablation",
                    "trigger_metric": "accuracy",
                    "observed_value": 0.9,
                    "reason": "Select one informative follow-up instead of blind tuning.",
                }],
            }
        if task == "critic.select_iteration_direction":
            self.tasks.append(task)
            self.inputs.append((task, inputs))
            return {
                "decision": "REVISE",
                "evidence_sufficiency": "SUFFICIENT",
                "evidence_assessment": [{
                    "statement": "Verified literature motivates a bounded dropout ablation.",
                    "type": "FACT",
                    "evidence_id": "iteration-evidence-1",
                    "limitation": "The published architecture differs.",
                }],
                "optimization_candidates": [
                    {
                        "name": "Lower dropout ablation",
                        "problem_addressed": "Possible over-regularization",
                        "result_basis": ["Accuracy missed the target."],
                        "evidence_basis": ["iteration-evidence-1"],
                        "changed_variable": "dropout probability",
                        "fixed_controls": ["dataset", "seeds", "optimizer"],
                        "target_metrics": ["accuracy"],
                        "possible_regressions": ["generalization gap"],
                        "information_gain": "high",
                        "expected_benefit": "medium",
                        "evidence_confidence": "medium",
                        "compute_cost": "one bounded run",
                        "scientific_risk": "low",
                        "success_rule": "Accuracy exceeds the registered threshold.",
                        "failure_rule": "Accuracy remains below the threshold.",
                        "stop_rule": "Stop after the registered comparison.",
                    },
                    {
                        "name": "Capacity-matched control",
                        "problem_addressed": "Separate regularization from capacity",
                        "result_basis": ["The mechanism remains unresolved."],
                        "evidence_basis": ["iteration-evidence-1"],
                        "changed_variable": "control capacity",
                        "fixed_controls": ["dataset", "seeds", "optimizer"],
                        "target_metrics": ["accuracy"],
                        "possible_regressions": ["compute cost"],
                        "information_gain": "medium",
                        "expected_benefit": "low",
                        "evidence_confidence": "medium",
                        "compute_cost": "one bounded run",
                        "scientific_risk": "low",
                        "success_rule": "The mechanism is distinguishable.",
                        "failure_rule": "The comparison remains inconclusive.",
                        "stop_rule": "Stop after the registered comparison.",
                    },
                ],
                "selected_direction": {
                    "name": "Lower dropout ablation",
                    "changed_variable": "dropout probability",
                    "fixed_controls": ["dataset", "seeds", "optimizer"],
                    "target_metrics": ["accuracy"],
                    "success_rule": "Accuracy exceeds the registered threshold.",
                    "failure_rule": "Accuracy remains below the threshold.",
                    "stop_rule": "Stop after the registered comparison.",
                },
                "selection_reason": "It has the highest information gain at bounded cost.",
                "next_action": "Run the lower-dropout ablation with frozen controls.",
            }
        return super().generate_json(task, inputs, schema_hint, instructions)


class InsufficientDirectionIterationLLM(ResultDrivenIterationLLM):
    def generate_json(self, task, inputs, schema_hint, instructions=""):
        if task == "critic.select_iteration_direction":
            self.tasks.append(task)
            self.inputs.append((task, inputs))
            return {
                "decision": "REPORT",
                "evidence_sufficiency": "EVIDENCE_INSUFFICIENT",
                "evidence_assessment": [],
                "optimization_candidates": [],
                "selected_direction": {},
                "selection_reason": "No external direction is sufficiently supported.",
                "next_action": "",
            }
        return super().generate_json(task, inputs, schema_hint, instructions)


class PivotIterationLLM(ResultDrivenIterationLLM):
    def generate_json(self, task, inputs, schema_hint, instructions=""):
        if task == "scientific.primary_result_analysis":
            return {
                "hypothesis_status": "CONTRADICTED",
                "supported_findings": [],
                "contradicting_findings": ["The registered criterion was not met."],
                "alternative_explanations": ["The current protocol is saturated."],
                "confounders": [],
                "evidence_gaps": [],
                "interpretation": "The current claim is contradicted.",
                "recommended_action": "PIVOT",
                "proposed_hypothesis": {
                    "claim": "A bounded lower-dropout experiment may avoid saturation."
                },
                "confidence": 0.8,
            }
        value = super().generate_json(task, inputs, schema_hint, instructions)
        if task == "critic.review_result":
            return {**value, "verdict": "failed", "decision": "PIVOT"}
        if task == "critic.select_iteration_direction":
            return {**value, "decision": "PIVOT"}
        return value


class NoMaterialPivotIterationLLM(PivotIterationLLM):
    def generate_json(self, task, inputs, schema_hint, instructions=""):
        if task == "planning.refine_plan":
            self.tasks.append(task)
            self.inputs.append((task, inputs))
            return dict(inputs["current_plan"])
        return super().generate_json(task, inputs, schema_hint, instructions)


class MissingLineagePivotIterationLLM(ResultDrivenIterationLLM):
    def generate_json(self, task, inputs, schema_hint, instructions=""):
        value = super().generate_json(task, inputs, schema_hint, instructions)
        if task in {"critic.review_result", "critic.select_iteration_direction"}:
            return {**value, "decision": "PIVOT"}
        return value


def _run_through_experiment(engine, run):
    for step_id in ["research_plan", "experiment_task", "experiment_run_analysis"]:
        run = engine.run_step(run.id, step_id)
    return run


def test_first_partial_feedback_refines_plan_and_preserves_history(tmp_path):
    llm = FeedbackIterationLLM([" PARTIAL "])
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = _run_through_experiment(engine, run)
    original_plan = next(artifact for artifact in run.artifacts if artifact.type == "plan")
    result = [artifact for artifact in run.artifacts if artifact.type == "experiment_result"][-1]

    run = engine.run_step(run.id, "feedback_revision")
    feedback_event = run.events[-1]

    revisions = [artifact for artifact in run.artifacts if artifact.type == "revision"]
    plans = [artifact for artifact in run.artifacts if artifact.type == "plan"]
    proposals = [
        artifact
        for artifact in run.artifacts
        if artifact.type == "plan_refinement_proposal"
    ]
    feedback = revisions[-1].content
    assert feedback["verdict"] == "partial"
    assert feedback["iteration"] == 1
    assert feedback["requires_follow_up"] is True
    assert feedback["feedback"] == "feedback round 1"
    assert feedback["required_revision"] == "add a fixed-seed ablation"
    assert feedback["supported_claims"] == []
    assert feedback["unsupported_claims"] == []
    assert feedback["next_action"] == "add a fixed-seed ablation"
    assert feedback["evidence_links"] == []
    assert feedback["revised_plan"] == proposals[-1].content["normalized_plan"]
    assert revisions[-1].parent_artifact_id == result.id
    assert len(plans) == 1
    assert plans[0].id == original_plan.id
    assert proposals[-1].source_step == "research_plan"
    assert proposals[-1].parent_artifact_id == revisions[-1].id
    assert len([artifact for artifact in run.artifacts if artifact.type == "experiment_task"]) == 1
    assert len([artifact for artifact in run.artifacts if artifact.type == "experiment_bundle"]) == 1
    assert len([artifact for artifact in run.artifacts if artifact.type == "experiment_result"]) == 1

    run = engine.run_step(run.id, "research_plan")
    plans = [artifact for artifact in run.artifacts if artifact.type == "plan"]
    assert len(plans) == 2
    assert plans[-1].parent_artifact_id == plans[-1].content["plan_candidate_id"]

    refine_inputs = next(inputs for task, inputs in llm.inputs if task == "planning.refine_plan")
    selection = [
        artifact for artifact in run.artifacts if artifact.type == "hypothesis_selection"
    ][-1]
    assert refine_inputs["selection"] == selection.content
    assert refine_inputs["current_plan"] == original_plan.content
    assert refine_inputs["experiment_result"] == result.content
    assert refine_inputs["feedback"]["iteration"] == 1
    claim_inputs = next(inputs for task, inputs in llm.inputs if task == "critic.review_result")
    assert claim_inputs["plan"] == original_plan.content
    for key, value in result.content["analysis"].items():
        assert claim_inputs["analysis"][key] == value
    assert claim_inputs["analysis"]["deterministic_metric_evidence"]
    assert claim_inputs["audit"] == result.content["audit"]
    route = [
        call for call in feedback_event.tool_calls if call["provider"] == "skill_runtime"
    ][-1]
    assert route["skills"] == [
        "experiment-iteration",
        "result-to-claim",
        "research-refine",
        "experiment-plan",
        "ablation-planner",
    ]
    assert feedback_event.output_summary["iteration"] == 1
    assert feedback_event.output_summary["requires_follow_up"] is True


def test_feedback_iteration_retrieves_evidence_and_selects_a_direction(tmp_path):
    llm = ResultDrivenIterationLLM()
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _run_through_experiment(
        engine, _selected_hypothesis_run(engine, repository)
    )

    run = engine.run_step(run.id, "feedback_revision")

    latest = {artifact.type: artifact for artifact in run.artifacts}
    assert latest["iteration_analysis"].parent_artifact_id == latest[
        "experiment_result"
    ].id
    assert latest["iteration_evidence"].content["references"]
    assert (
        latest["iteration_evidence"].content["query_specs"][0]["trigger_metric"]
        == "accuracy"
    )
    assert latest["iteration_decision"].content["evidence_sufficiency"] == "SUFFICIENT"
    assert len(latest["iteration_decision"].content["optimization_candidates"]) == 2
    revision = latest["revision"].content
    assert revision["selected_direction"]["changed_variable"] == "dropout probability"
    assert revision["next_action"] == (
        "Run the lower-dropout ablation with frozen controls."
    )
    direction_inputs = next(
        inputs
        for task, inputs in llm.inputs
        if task == "critic.select_iteration_direction"
    )
    assert direction_inputs["iteration_evidence"]["references"]
    refine_inputs = next(
        inputs for task, inputs in llm.inputs if task == "planning.refine_plan"
    )
    assert refine_inputs["feedback"]["selected_direction"]["name"] == (
        "Lower dropout ablation"
    )


def test_failed_feedback_report_decision_skips_follow_up_and_routes_to_report(tmp_path):
    from backend.app.workflow.orchestrator import WorkflowOrchestrator

    llm = FeedbackIterationLLM(["failed"], ["REPORT"])
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _run_through_experiment(
        engine, _selected_hypothesis_run(engine, repository)
    )

    run = engine.run_step(run.id, "feedback_revision")

    revision = [
        artifact for artifact in run.artifacts if artifact.type == "revision"
    ][-1].content
    assert revision["verdict"] == "failed"
    assert revision["decision"] == "REPORT"
    assert revision["requires_follow_up"] is False
    assert "revised_plan" not in revision
    assert "critic.select_iteration_direction" not in llm.tasks
    assert "planning.refine_plan" not in llm.tasks
    assert not any(
        artifact.type in {"working_hypothesis", "idea_revision"}
        for artifact in run.artifacts
    )
    assert not any(
        artifact.type == "plan_refinement_proposal" for artifact in run.artifacts
    )
    assert WorkflowOrchestrator._next_step(run) == "report_export"


def test_failed_feedback_pivot_decision_creates_one_bounded_proposal(tmp_path):
    llm = PivotIterationLLM()
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _run_through_experiment(
        engine, _selected_hypothesis_run(engine, repository)
    )

    run = engine.run_step(run.id, "feedback_revision")

    revision = [
        artifact for artifact in run.artifacts if artifact.type == "revision"
    ][-1].content
    proposals = [
        artifact
        for artifact in run.artifacts
        if artifact.type == "plan_refinement_proposal"
    ]
    assert revision["verdict"] == "failed"
    assert revision["decision"] == "PIVOT"
    assert revision["requires_follow_up"] is True
    assert revision["revised_plan"] == proposals[0].content["normalized_plan"]
    assert len(proposals) == 1
    assert llm.tasks.count("critic.select_iteration_direction") == 1
    assert llm.tasks.count("planning.refine_plan") == 1
    assert any(
        artifact.type == "working_hypothesis" for artifact in run.artifacts
    )
    assert revision["revised_plan"]["iteration_contract"][
        "hypothesis_lineage"
    ]["kind"] == "PIVOT"


def test_pivot_without_hypothesis_lineage_fails_closed_to_report(tmp_path):
    llm = MissingLineagePivotIterationLLM()
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _run_through_experiment(
        engine, _selected_hypothesis_run(engine, repository)
    )

    run = engine.run_step(run.id, "feedback_revision")

    revision = [
        artifact for artifact in run.artifacts if artifact.type == "revision"
    ][-1].content
    assert revision["decision"] == "REPORT"
    assert revision["route_reason"] == "PIVOT_HYPOTHESIS_LINEAGE_MISSING"
    assert revision["requires_follow_up"] is False
    assert revision["required_revision"] == ""
    assert revision["revisions"] == []
    assert "report" in revision["next_action"].lower()
    assert "planning.refine_plan" not in llm.tasks
    assert not any(
        artifact.type in {
            "working_hypothesis",
            "idea_revision",
            "plan_refinement_proposal",
        }
        for artifact in run.artifacts
    )


def test_insufficient_direction_without_safe_experiment_routes_to_report(tmp_path):
    llm = InsufficientDirectionIterationLLM()
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
        max_feedback_iterations=3,
    )
    run = _run_through_experiment(
        engine, _selected_hypothesis_run(engine, repository)
    )

    run = engine.run_step(run.id, "feedback_revision")

    revision = [
        artifact for artifact in run.artifacts if artifact.type == "revision"
    ][-1].content
    assert revision["verdict"] == "partial"
    assert revision["iteration"] == 1
    assert revision["decision"] == "REPORT"
    assert revision["route_reason"] == "DIRECTION_REPORT"
    assert revision["requires_follow_up"] is False
    assert "revised_plan" not in revision
    assert "planning.refine_plan" not in llm.tasks
    assert not any(
        artifact.type in {"working_hypothesis", "idea_revision"}
        for artifact in run.artifacts
    )
    assert not any(
        artifact.type == "plan_refinement_proposal" for artifact in run.artifacts
    )


def test_no_material_pivot_revision_routes_to_report_without_proposal(tmp_path):
    llm = NoMaterialPivotIterationLLM()
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _run_through_experiment(
        engine, _selected_hypothesis_run(engine, repository)
    )

    run = engine.run_step(run.id, "feedback_revision")

    revision = [
        artifact for artifact in run.artifacts if artifact.type == "revision"
    ][-1].content
    assert revision["decision"] == "REPORT"
    assert revision["route_reason"] == "NO_MATERIAL_PLAN_CHANGE"
    assert revision["requires_follow_up"] is False
    assert revision["required_revision"] == ""
    assert revision["revisions"] == []
    assert "report" in revision["next_action"].lower()
    assert "revised_plan" not in revision
    assert llm.tasks.count("planning.refine_plan") == 1
    assert not any(
        artifact.type in {"working_hypothesis", "idea_revision"}
        for artifact in run.artifacts
    )
    assert not any(
        artifact.type == "plan_refinement_proposal" for artifact in run.artifacts
    )


def test_second_partial_feedback_stops_without_creating_third_plan(tmp_path):
    llm = FeedbackIterationLLM(["partial", "partial"])
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
        max_feedback_iterations=2,
    )
    run = _selected_hypothesis_run(engine, repository)
    run = _run_through_experiment(engine, run)
    run = engine.run_step(run.id, "feedback_revision")
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")
    run = engine.run_step(run.id, "experiment_run_analysis")

    run = engine.run_step(run.id, "feedback_revision")

    revisions = [artifact for artifact in run.artifacts if artifact.type == "revision"]
    plans = [artifact for artifact in run.artifacts if artifact.type == "plan"]
    latest_feedback = revisions[-1].content
    assert [revision.content["iteration"] for revision in revisions] == [1, 2]
    assert len({revision.parent_artifact_id for revision in revisions}) == 2
    assert latest_feedback["requires_follow_up"] is False
    assert "revised_plan" not in latest_feedback
    assert len(plans) == 2
    assert llm.tasks.count("planning.refine_plan") == 1
    assert len([artifact for artifact in run.artifacts if artifact.type == "experiment_task"]) == 2
    assert len([artifact for artifact in run.artifacts if artifact.type == "experiment_bundle"]) == 2
    assert len([artifact for artifact in run.artifacts if artifact.type == "experiment_result"]) == 2
    assert run.events[-1].output_summary["iteration"] == 2
    assert run.events[-1].output_summary["requires_follow_up"] is False
    supervisor_route = [
        call for call in run.events[-1].tool_calls if call["provider"] == "supervisor_agent"
    ][-1]
    runtime_route = [
        call for call in run.events[-1].tool_calls if call["provider"] == "skill_runtime"
    ][-1]
    expected_skills = ["experiment-iteration", "result-to-claim"]
    assert supervisor_route["skills"] == expected_skills
    assert runtime_route["skills"] == expected_skills


def test_passed_feedback_does_not_refine_plan(tmp_path):
    llm = FeedbackIterationLLM([" PASSED "])
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = _run_through_experiment(engine, run)

    run = engine.run_step(run.id, "feedback_revision")

    feedback = [artifact for artifact in run.artifacts if artifact.type == "revision"][-1].content
    assert feedback["verdict"] == "supported"
    assert feedback["iteration"] == 1
    assert feedback["requires_follow_up"] is False
    assert "revised_plan" not in feedback
    assert len([artifact for artifact in run.artifacts if artifact.type == "plan"]) == 1
    assert "planning.refine_plan" not in llm.tasks


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" PASSED ", "supported"),
        ("pass", "supported"),
        ("Success", "supported"),
        ("supported", "supported"),
        (" FAILED ", "failed"),
        ("failure", "failed"),
        ("error", "failed"),
        ("Partial", "partial"),
        ("inconclusive", "partial"),
        (None, "partial"),
    ],
)
def test_normalize_feedback_verdict(raw, expected):
    from backend.app.workflow.policies import normalize_feedback_verdict

    assert normalize_feedback_verdict(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("REPORT", "REPORT"),
        ("stop", "REPORT"),
        (" REVISE ", "REVISE"),
        ("pivot", "PIVOT"),
        (None, "REPORT"),
        ("continue because the text says so", "REPORT"),
    ],
)
def test_normalize_feedback_decision(raw, expected):
    from backend.app.workflow.policies import normalize_feedback_decision

    assert normalize_feedback_decision(raw) == expected


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({"decision": "REPORT", "requires_follow_up": True}, False),
        ({"decision": "PIVOT", "requires_follow_up": False}, True),
        ({"decision": "INVALID", "requires_follow_up": True}, False),
        ({"requires_follow_up": True}, True),
        ({"requires_follow_up": False}, False),
    ],
)
def test_feedback_requires_follow_up_prefers_decision_and_supports_legacy(
    content, expected
):
    from backend.app.workflow.policies import feedback_requires_follow_up

    assert feedback_requires_follow_up(content) is expected


@pytest.mark.parametrize(
    "field",
    [
        "primary_experiment",
        "baseline_and_controls",
        "split_contract",
        "staged_gates",
        "progressive_experiment",
    ],
)
def test_iteration_contract_treats_execution_contract_fields_as_material(field):
    previous = {field: {"version": 1}}
    revised = {field: {"version": 2}}

    contract = WorkflowEngine._build_iteration_contract(
        1,
        previous,
        revised,
        {},
    )

    assert contract["changed_fields"] == [field]
    assert contract["contract_status"] == "changed"


def test_duplicate_feedback_for_same_result_is_idempotent(tmp_path):
    llm = FeedbackIterationLLM(["partial"])
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _run_through_experiment(engine, _selected_hypothesis_run(engine, repository))
    first = engine.run_step(run.id, "feedback_revision")

    duplicate = engine.run_step(run.id, "feedback_revision")

    assert len([artifact for artifact in duplicate.artifacts if artifact.type == "revision"]) == 1
    assert len([artifact for artifact in duplicate.artifacts if artifact.type == "plan"]) == 1
    assert len(
        [artifact for artifact in duplicate.artifacts if artifact.type == "plan_refinement_proposal"]
    ) == 1
    assert llm.review_count == 1
    assert duplicate.updated_at == first.updated_at


def test_locked_feedback_for_previous_result_does_not_skip_new_result(tmp_path):
    llm = FeedbackIterationLLM(["partial", "supported"])
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(repository, llm, MockLiteratureProvider(), MockExperimentProvider())
    run = _run_through_experiment(engine, _selected_hypothesis_run(engine, repository))
    run = engine.run_step(run.id, "feedback_revision")
    first_revision = [artifact for artifact in run.artifacts if artifact.type == "revision"][-1]
    repository.lock_artifact(run.id, first_revision.id, True)
    run = engine.run_step(run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")
    run = engine.run_step(run.id, "experiment_run_analysis")

    reviewed = engine.run_step(run.id, "feedback_revision")

    revisions = [artifact for artifact in reviewed.artifacts if artifact.type == "revision"]
    results = [artifact for artifact in reviewed.artifacts if artifact.type == "experiment_result"]
    assert len(revisions) == 2
    assert revisions[-1].parent_artifact_id == results[-1].id
    assert revisions[-1].content["iteration"] == 2


def test_feedback_iteration_counts_distinct_result_parents_and_legacy_rounds(tmp_path):
    llm = FeedbackIterationLLM(["partial"])
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
        max_feedback_iterations=3,
    )
    run = _run_through_experiment(engine, _selected_hypothesis_run(engine, repository))
    result = [artifact for artifact in run.artifacts if artifact.type == "experiment_result"][-1]
    repository.add_artifact(
        run.id,
        "revision",
        "Legacy Feedback",
        {"verdict": "partial", "iteration": 1, "requires_follow_up": True},
        "feedback_revision",
        "legacy",
    )
    repository.add_artifact(
        run.id,
        "revision",
        "Duplicate Historical Feedback",
        {"verdict": "partial", "iteration": 2, "requires_follow_up": True},
        "feedback_revision",
        "legacy",
        parent_artifact_id="old-result-id",
    )
    repository.add_artifact(
        run.id,
        "revision",
        "Duplicate Historical Feedback Again",
        {"verdict": "partial", "iteration": 2, "requires_follow_up": True},
        "feedback_revision",
        "legacy",
        parent_artifact_id="old-result-id",
    )

    reviewed = engine.run_step(run.id, "feedback_revision")
    latest = [artifact for artifact in reviewed.artifacts if artifact.type == "revision"][-1]

    assert latest.parent_artifact_id == result.id
    assert latest.content["iteration"] == 3
    assert latest.content["requires_follow_up"] is False


def test_feedback_reviews_latest_reasoned_active_hypothesis(tmp_path):
    llm = FeedbackIterationLLM(["supported"])
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(repository, llm, MockLiteratureProvider(), MockExperimentProvider())
    run = _run_through_experiment(engine, _selected_hypothesis_run(engine, repository))
    revised = {"claim": "the evidence-revised active claim", "source": "critic"}
    repository.add_artifact(
        run.id,
        "reasoning",
        "Latest Evidence Reasoning",
        {"active_hypothesis": revised},
        "evidence_reasoning",
        "critic",
    )

    engine.run_step(run.id, "feedback_revision")

    review_inputs = [inputs for task, inputs in llm.inputs if task == "critic.review_result"][-1]
    assert review_inputs["hypothesis"] == revised


def test_report_export_requires_feedback_and_follow_up_completion(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
            RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = _run_through_experiment(engine, _selected_hypothesis_run(engine, repository))

    with pytest.raises(ValueError, match="REPORT_EXPORT_NOT_READY:feedback_revision"):
        engine.run_step(run.id, "report_export")
    assert not any(artifact.type == "report" for artifact in repository.get_run(run.id).artifacts)

    run = engine.run_step(run.id, "feedback_revision")
    assert [artifact for artifact in run.artifacts if artifact.type == "revision"][-1].content["requires_follow_up"] is True
    with pytest.raises(ValueError, match="REPORT_EXPORT_NOT_READY:feedback_follow_up"):
        engine.run_step(run.id, "report_export")
    assert not any(artifact.type == "report" for artifact in repository.get_run(run.id).artifacts)


def test_report_export_requires_feedback_bound_to_latest_result(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        MockLLMProvider(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = repository.create_run("latest result", "latest result")
    repository.add_artifact(
        run.id,
        "evidence",
        "Verified Evidence",
        {"references": [{"title": "verified", "verified": True}]},
        "knowledge_integration",
        "test",
    )
    first_result = repository.add_artifact(
        run.id,
        "experiment_result",
        "First Result",
        {"is_real_experiment": False, "metrics": {"accuracy": 0.8}},
        "experiment_run_analysis",
        "test",
    )
    repository.add_artifact(
        run.id,
        "revision",
        "Completed Feedback",
        {"verdict": "supported", "requires_follow_up": False},
        "feedback_revision",
        "test",
        parent_artifact_id=first_result.id,
    )
    repository.add_artifact(
        run.id,
        "experiment_result",
        "New Result",
        {"is_real_experiment": False, "metrics": {"accuracy": 0.9}},
        "experiment_run_analysis",
        "test",
    )

    with pytest.raises(ValueError, match="REPORT_EXPORT_NOT_READY:feedback_revision"):
        engine.run_step(run.id, "report_export")

    assert not any(artifact.type == "report" for artifact in repository.get_run(run.id).artifacts)


def test_report_readiness_accepts_result_from_repaired_bundle_lineage(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        MockLLMProvider(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = repository.create_run("negative result", "negative result")
    repository.add_artifact(
        run.id,
        "evidence",
        "Verified Evidence",
        {"references": [{"title": "verified", "verified": True}]},
        "knowledge_integration",
        "test",
    )
    task = repository.add_artifact(
        run.id,
        "experiment_task",
        "Experiment Task",
        {"experiment_id": "experiment_1"},
        "experiment_task",
        "test",
    )
    original_bundle = repository.add_artifact(
        run.id,
        "experiment_bundle",
        "Original Bundle",
        {"experiment_id": "experiment_1"},
        "experiment_task",
        "test",
        parent_artifact_id=task.id,
    )
    diagnosis = repository.add_artifact(
        run.id,
        "experiment_diagnosis",
        "Diagnosis",
        {"resolved": False},
        "experiment_run_analysis",
        "test",
        parent_artifact_id=original_bundle.id,
    )
    repaired_bundle = repository.add_artifact(
        run.id,
        "experiment_bundle",
        "Repaired Bundle",
        {"experiment_id": "experiment_1"},
        "experiment_run_analysis",
        "test",
        parent_artifact_id=diagnosis.id,
    )
    result = repository.add_artifact(
        run.id,
        "experiment_result",
        "Negative Result",
        {
            "experiment_id": "experiment_1",
            "is_real_experiment": True,
            "metrics": {"accuracy_delta": -0.01},
        },
        "experiment_run_analysis",
        "test",
        parent_artifact_id=repaired_bundle.id,
    )
    repository.add_artifact(
        run.id,
        "revision",
        "Terminal Negative Feedback",
        {
            "verdict": "failed",
            "iteration": 4,
            "requires_follow_up": False,
        },
        "feedback_revision",
        "test",
        parent_artifact_id=result.id,
    )
    persisted = repository.get_run(run.id)
    latest = engine._latest_by_type(persisted.artifacts)

    engine._require_report_readiness(persisted.artifacts, latest)


def test_noncompetition_report_allows_mixed_evidence_when_verified_evidence_exists(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        MockLLMProvider(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = repository.create_run("mixed evidence", "mixed evidence")
    references = [
        {"title": "verified", "verified": True},
        {"title": "working note", "verified": False},
    ]
    repository.add_artifact(
        run.id,
        "evidence",
        "Mixed Evidence",
        {"references": references},
        "knowledge_integration",
        "test",
    )
    result = repository.add_artifact(
        run.id,
        "experiment_result",
        "Result",
        {"is_real_experiment": False, "metrics": {"accuracy": 0.5}},
        "experiment_run_analysis",
        "test",
    )
    repository.add_artifact(
        run.id,
        "revision",
        "Feedback",
        {"verdict": "supported", "requires_follow_up": False},
        "feedback_revision",
        "test",
        parent_artifact_id=result.id,
    )

    exported = engine.run_step(run.id, "report_export")

    assert len([artifact for artifact in exported.artifacts if artifact.type == "report"]) == 1


def test_competition_report_policy_blocks_before_persistence(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    engine = WorkflowEngine(
        repository,
        RecordingLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
        competition_mode=True,
    )
    run = repository.create_run("test export", "competition export")
    repository.add_artifact(
        run.id,
        "evidence",
        "Verified Evidence",
        {"references": [{"title": "paper", "verified": True}]},
        "knowledge_integration",
        "test",
    )
    result = repository.add_artifact(
        run.id,
        "experiment_result",
        "Real Result",
        {"is_real_experiment": True, "provider": "remote_gpu", "metrics": {"accuracy": 0.9}},
        "experiment_run_analysis",
        "test",
    )
    repository.add_artifact(
        run.id,
        "revision",
        "Feedback",
        {"verdict": "supported", "requires_follow_up": False},
        "feedback_revision",
        "test",
        parent_artifact_id=result.id,
    )
    engine.writer_agent.build_report = lambda artifacts, instructions="": {
        "Problem Statement": "problem",
        "Rationale": "rationale",
        "Technical Details": ["details"],
        "Datasets": "dataset",
        "Source": ["source"],
        "Target": "target",
        "Paper Title": "title",
        "Paper Abstract": "abstract",
        "Methods": ["method"],
        "Experiments": {},
        "Results": {"is_real_experiment": True, "provider": "remote_gpu"},
        "References": [{"title": "paper", "verified": True}],
    }

    with pytest.raises(ValueError, match="COMPETITION_REPORT_BLOCKED:.*DOI or arXiv"):
        engine.run_step(run.id, "report_export")

    assert not any(artifact.type == "report" for artifact in repository.get_run(run.id).artifacts)


def test_same_run_step_mutations_are_serialized(tmp_path):
    class ConcurrencyRecordingLLM(RecordingLLM):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.max_active = 0
            self.guard = threading.Lock()

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "research.structure_problem":
                with self.guard:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.1)
                try:
                    return super().generate_json(task, inputs, schema_hint, instructions)
                finally:
                    with self.guard:
                        self.active -= 1
            return super().generate_json(task, inputs, schema_hint, instructions)

    repository = Repository(data_dir=str(tmp_path / "data"))
    llm = ConcurrencyRecordingLLM()
    engine = WorkflowEngine(repository, llm, MockLiteratureProvider(), MockExperimentProvider())
    run = repository.create_run("concurrent", "concurrent")
    start = threading.Barrier(2)

    def mutate():
        start.wait()
        return engine.run_step(run.id, "problem_understanding")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result(timeout=5) for future in [executor.submit(mutate), executor.submit(mutate)]]

    assert llm.max_active == 1
    persisted = repository.get_run(run.id)
    assert [artifact.version for artifact in persisted.artifacts if artifact.type == "problem"] == [1, 2]
    assert all(result.id == run.id for result in results)


def test_research_plan_requires_selected_hypotheses(tmp_path):
    repo = Repository(data_dir=str(tmp_path))
    llm = RecordingLLM()
    run = repo.create_run("train a compact cnn with dropout ablation", "Selection required")
    engine = WorkflowEngine(repo, llm, MockLiteratureProvider(), MockExperimentProvider())
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)

    try:
      engine.run_step(run.id, "research_plan")
    except ValueError as exc:
      assert "HYPOTHESIS_SELECTION_REQUIRED" in str(exc)
    else:
      raise AssertionError("research_plan should require explicit hypothesis selection")


def test_research_plan_normalizes_nested_legacy_plan_for_artifact_and_trace(tmp_path):
    class NestedPlanLLM(RecordingLLM):
        def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
            if task == "planning.build_plan":
                self.tasks.append(task)
                self.inputs.append((task, inputs))
                return {
                    "plan": {
                        "dataset": "Fashion-MNIST",
                        "methods": ["dropout cnn"],
                        "baselines": ["baseline cnn"],
                        "metrics": ["accuracy"],
                        "parameters": {"epochs": 5},
                    }
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repo = Repository(data_dir=str(tmp_path))
    llm = NestedPlanLLM()
    run = repo.create_run("train a compact cnn", "Nested plan")
    engine = WorkflowEngine(repo, llm, MockLiteratureProvider(), MockExperimentProvider())
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)
    run = engine.run_step(run.id, "evidence_reasoning")
    run = engine.select_hypothesis(run.id, 2)
    run = engine.run_step(run.id, "research_plan")

    plan = [artifact for artifact in run.artifacts if artifact.type == "plan"][-1].content
    assert plan["dataset"]["name"] == "Fashion-MNIST"
    assert plan["dataset"]["normalized_name"] == "fashion-mnist"
    assert plan["dataset"]["availability"] == "downloadable"
    assert plan["dataset"]["card"]["input_shape"] == [1, 28, 28]
    assert plan["hypotheses"] == [
        "A parameter-matched compact CNN may isolate whether dropout gains come from regularization."
    ]
    assert plan["comparisons"] == [{"baseline": "baseline cnn", "variant": "dropout cnn", "controls": []}]
    assert plan["evaluations"] == [{"metric": "accuracy", "direction": "未提供", "method": "未提供"}]
    assert plan["normalization"]["unwrapped_plan"] is True
    assert run.events[-1].output_summary == plan


def test_research_plan_uses_engine_provenance_over_raw_provider_values(tmp_path):
    class SpoofedPlanLLM(RecordingLLM):
        def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
            if task == "planning.build_plan":
                return {
                    "provider_mode": "spoofed",
                    "fallback_used": True,
                    "parameters": {"epochs": 5},
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repo = Repository(data_dir=str(tmp_path))
    llm = SpoofedPlanLLM()
    run = repo.create_run("train a compact cnn", "Authoritative provenance")
    engine = WorkflowEngine(repo, llm, MockLiteratureProvider(), MockExperimentProvider())
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)
    run = engine.run_step(run.id, "evidence_reasoning")
    run = engine.select_hypothesis(run.id, 0)
    run = engine.run_step(run.id, "research_plan")

    plan = [artifact for artifact in run.artifacts if artifact.type == "plan"][-1].content
    assert plan["provider_mode"] == run.events[-1].provider_mode == "qwen"
    assert plan["fallback_used"] is run.events[-1].fallback_used is False


def test_user_hypothesis_is_analyzed_and_automatically_reevaluated(tmp_path):
    repo = Repository(data_dir=str(tmp_path))
    llm = RecordingLLM()
    run = repo.create_run("train a compact cnn with dropout ablation", "User hypothesis")
    engine = WorkflowEngine(repo, llm, MockLiteratureProvider(), MockExperimentProvider())
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)

    run = engine.run_step(run.id, "evidence_reasoning")
    run = engine.select_hypothesis(run.id, 0)
    run = engine.run_step(run.id, "research_plan")
    old_reasoning = [artifact for artifact in run.artifacts if artifact.type == "reasoning"][-1]
    repo.lock_artifact(run.id, old_reasoning.id, True)
    run = engine.add_user_hypothesis(run.id, "A smaller CNN may improve reproducibility under fixed seeds.")

    assert any(
        artifact.type == "hypothesis_selection" for artifact in run.artifacts
    )
    assert [
        artifact
        for artifact in run.artifacts
        if artifact.type == "reasoning"
    ][-1].content["selection_status"] == "awaiting_selection"
    assert len([artifact for artifact in run.artifacts if artifact.type == "idea_review"]) == 2
    reasonings = [artifact for artifact in run.artifacts if artifact.type == "reasoning"]
    assert len(reasonings) == 2
    assert old_reasoning.id in {artifact.id for artifact in reasonings}
    assert reasonings[-1].id != old_reasoning.id
    assert any(artifact.type == "plan" for artifact in run.artifacts)
    assert "hypothesis.analyze_user_hypothesis" in llm.tasks


def test_user_hypothesis_recovers_a_stopped_run_before_re_evaluation(tmp_path):
    repo = Repository(data_dir=str(tmp_path))
    llm = RecordingLLM()
    run = repo.create_run("train a compact cnn", "Stopped user-hypothesis recovery")
    engine = WorkflowEngine(repo, llm, MockLiteratureProvider(), MockExperimentProvider())
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)
    run = engine.run_step(run.id, "evidence_reasoning")
    repo.update_workflow_state(run.id, status="paused", stop_requested=True)

    recovered = engine.add_user_hypothesis(
        run.id,
        "A compact CNN has higher held-out accuracy than an MLP under a fixed split.",
    )

    assert recovered.stop_requested is False
    assert [
        artifact for artifact in recovered.artifacts if artifact.type == "reasoning"
    ][-1].content["selection_status"] == "awaiting_selection"
    assert any(
        candidate.get("source") == "user"
        for candidate in [
            artifact for artifact in recovered.artifacts if artifact.type == "hypothesis"
        ][-1].content["candidates"]
    )


def test_user_hypothesis_claim_is_immutable_through_analysis_and_selection(tmp_path):
    class RewritingUserHypothesisLLM(RecordingLLM):
        def generate_json(self, task, inputs, schema_hint, instructions=""):
            if task == "hypothesis.analyze_user_hypothesis":
                return {
                    "claim": "model-authored replacement claim",
                    "verifiability": "fixed-seed comparison",
                    "novelty_basis": ["reviewed"],
                    "risks": [],
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    claim = "A compact CNN has higher held-out accuracy than an MLP under a fixed split."
    repo = Repository(data_dir=str(tmp_path))
    engine = WorkflowEngine(
        repo,
        RewritingUserHypothesisLLM(),
        MockLiteratureProvider(),
        MockExperimentProvider(),
    )
    run = repo.create_run("train a compact cnn", "User claim anchor")
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)
    run = engine.add_user_hypothesis(run.id, claim, replacement_index=0)
    candidates = [
        artifact for artifact in run.artifacts if artifact.type == "hypothesis"
    ][-1].content["candidates"]

    assert candidates[0]["claim"] == claim
    assert candidates[0]["source"] == "user"

    selected = engine.select_hypothesis(run.id, 0)
    selection = [
        artifact for artifact in selected.artifacts if artifact.type == "hypothesis_selection"
    ][-1]
    assert selection.content["selected"][0]["claim"] == claim


def test_hypothesis_generation_caps_candidates_and_strips_ranking_metadata(tmp_path):
    class NoisyHypothesisLLM(RecordingLLM):
        def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
            if task == "hypothesis.generate":
                self.tasks.append(task)
                self.inputs.append((task, inputs))
                return {
                    "candidates": [
                        {
                            "claim": f"候选假设 {index}",
                            "verifiability": "固定随机种子并比较指标。",
                            "novelty_basis": ["verified evidence"],
                                "risks": ["small dataset"],
                                "source_gap_ids": ["GAP-001"],
                            "score": 0.9,
                            "rank": index,
                            "recommendation": "recommended",
                            "index": index,
                            "order": index,
                            "timestamp": "2026-07-11T00:00:00Z",
                        }
                        for index in range(6)
                    ],
                    "active": {"claim": "should not be stored as an active recommendation"},
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repo = Repository(data_dir=str(tmp_path))
    llm = NoisyHypothesisLLM()
    run = repo.create_run("train a compact cnn", "Candidate contract")
    engine = WorkflowEngine(repo, llm, MockLiteratureProvider(), MockExperimentProvider())

    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)

    hypothesis = [artifact for artifact in run.artifacts if artifact.type == "hypothesis"][-1].content
    assert "active" not in hypothesis
    assert [candidate["claim"] for candidate in hypothesis["candidates"]] == [f"候选假设 {index}" for index in range(5)]
    forbidden = {"score", "rank", "recommendation", "index", "order", "timestamp"}
    assert all(forbidden.isdisjoint(candidate) for candidate in hypothesis["candidates"])


def test_full_candidate_list_requires_explicit_replacement_and_replacement_succeeds(tmp_path):
    class FullCandidateLLM(RecordingLLM):
        def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
            if task == "hypothesis.generate":
                self.tasks.append(task)
                self.inputs.append((task, inputs))
                return {
                    "candidates": [
                        {
                            "claim": f"候选假设 {index}",
                            "verifiability": "固定随机种子并比较指标。",
                            "novelty_basis": ["verified evidence"],
                                "risks": ["small dataset"],
                                "source_gap_ids": ["GAP-001"],
                        }
                        for index in range(5)
                    ]
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repo = Repository(data_dir=str(tmp_path))
    llm = FullCandidateLLM()
    run = repo.create_run("train a compact cnn", "Full list")
    engine = WorkflowEngine(repo, llm, MockLiteratureProvider(), MockExperimentProvider())
    for step_id in ["problem_understanding", "knowledge_integration", "hypothesis_generation"]:
        run = engine.run_step(run.id, step_id)

    try:
        engine.add_user_hypothesis(run.id, "用户假设需要替换一个已有候选。")
    except ValueError as exc:
        assert "HYPOTHESIS_REPLACEMENT_REQUIRED" in str(exc)
    else:
        raise AssertionError("full candidate list should require an explicit replacement target")

    run = engine.add_user_hypothesis(run.id, "用户假设需要替换一个已有候选。", replacement_index=2)
    candidates = [artifact for artifact in run.artifacts if artifact.type == "hypothesis"][-1].content["candidates"]

    assert len(candidates) == 5
    assert candidates[2]["claim"] == "用户假设需要替换一个已有候选。"
    assert candidates[2]["source"] == "user"
    assert candidates[0]["claim"] == "候选假设 0"
    assert candidates[4]["claim"] == "候选假设 4"


def test_rerun_from_preserves_locked_artifacts(tmp_path):
    repo = Repository(data_dir=str(tmp_path))
    llm = RecordingLLM()
    run = repo.create_run("train a cnn", "Locked evidence")
    engine = WorkflowEngine(repo, llm, MockLiteratureProvider(), MockExperimentProvider())
    run = engine.run_step(run.id, "problem_understanding")
    run = engine.run_step(run.id, "knowledge_integration")
    evidence = [artifact for artifact in run.artifacts if artifact.type == "evidence"][0]
    repo.lock_artifact(run.id, evidence.id, True)

    rerun = engine.rerun_from(run.id, "knowledge_integration")
    evidence_artifacts = [artifact for artifact in rerun.artifacts if artifact.type == "evidence"]

    assert len(evidence_artifacts) == 1
    assert evidence_artifacts[0].locked is True


def test_hypothesis_revision_is_append_only_across_persisted_checkpoint(tmp_path, monkeypatch):
    """A revision route must never use cleanup that deletes Round 1 evidence."""
    repository = Repository(data_dir=str(tmp_path / "checkpoint"))
    engine = WorkflowEngine(repository, RecordingLLM(), MockLiteratureProvider(), MockExperimentProvider())
    run = repository.create_run("synthetic revision", "mock only")
    h1 = repository.add_artifact(
        run.id, "hypothesis", "Round 1", {
            "candidates": [{"candidate_id": "H1", "claim": "old hypothesis", "source_gap_ids": ["GAP-001"]}],
            "hypothesis_round": {"round_id": "HYPOTHESIS-ROUND-001", "round_index": 1, "parent_round_id": "", "revision_reason": "initial", "scientific_feedback": [], "created_candidate_ids": ["H1"]},
        }, "hypothesis_generation", "fixture",
    )
    evidence = repository.add_artifact(run.id, "evidence", "Evidence", {"references": []}, "knowledge_integration", "fixture")
    reasoning = repository.add_artifact(
        run.id, "reasoning", "Round 1 reasoning", {"candidate_assessments": [{"candidate_index": 0, "candidate_id": "H1", "status": "evidence_insufficient", "reasoning": "missing evidence"}]},
        "evidence_reasoning", "fixture", parent_artifact_id=h1.id,
    )
    repository.add_artifact(run.id, "evidence_review", "Round 1 evidence", {"round": 1}, "evidence_reasoning", "fixture", parent_artifact_id=reasoning.id)
    repository.add_artifact(run.id, "hypothesis_revision_required", "Revision required", {"code": "NO_SELECTABLE_HYPOTHESIS"}, "evidence_reasoning", "fixture", parent_artifact_id=reasoning.id)
    repository.update_workflow_state(run.id, status="hypothesis_revision_required", current_step="evidence_reasoning")

    # Reload the persisted checkpoint before recovery; no real model/provider is called.
    repository.save_run(repository.get_run(run.id))
    calls = []
    def synthetic_round_step(run_id, step_id):
        calls.append(step_id)
        if step_id == "hypothesis_generation":
            repository.add_artifact(run_id, "hypothesis", "Round 2", {
                "candidates": [{"candidate_id": "H5", "claim": "new hypothesis", "source_gap_ids": ["GAP-002"]}],
                "hypothesis_round": {"round_id": "HYPOTHESIS-ROUND-002", "round_index": 2, "parent_round_id": "HYPOTHESIS-ROUND-001", "revision_reason": "NO_SELECTABLE_HYPOTHESIS", "scientific_feedback": [{"candidate_id": "H1", "status": "evidence_insufficient"}], "created_candidate_ids": ["H5"]},
            }, "hypothesis_generation", "fixture")
        elif step_id == "evidence_reasoning":
            latest_hypothesis = [item for item in repository.get_run(run_id).artifacts if item.type == "hypothesis"][-1]
            repository.add_artifact(run_id, "reasoning", "Round 2 reasoning", {"candidate_assessments": [{"candidate_index": 0, "candidate_id": "H5", "status": "evidence_insufficient"}]}, "evidence_reasoning", "fixture", parent_artifact_id=latest_hypothesis.id)
            repository.add_artifact(run_id, "evidence_review", "Round 2 evidence", {"round": 2}, "evidence_reasoning", "fixture")
        return repository.get_run(run_id)

    monkeypatch.setattr(engine, "run_step", synthetic_round_step)
    recovered = engine.rerun_from(run.id, "hypothesis_generation")
    artifacts = recovered.artifacts
    assert calls == ["hypothesis_generation", "evidence_reasoning"]
    assert h1.id in {item.id for item in artifacts}
    assert reasoning.id in {item.id for item in artifacts}
    assert evidence.id in {item.id for item in artifacts}
    assert [item.content["hypothesis_round"]["round_index"] for item in artifacts if item.type == "hypothesis"] == [1, 2]
    assert any(item.type == "evidence_review" and item.content.get("round") == 1 for item in artifacts)
    assert any(item.type == "evidence_review" and item.content.get("round") == 2 for item in artifacts)
