from backend.app.models.artifact import Artifact
from backend.app.workflow.research_state import (
    active_plan_for_report,
    build_research_state,
)


def artifact(kind: str, version: int, content: dict) -> Artifact:
    return Artifact(
        run_id="run_state",
        type=kind,
        version=version,
        title=kind,
        content=content,
        source_step=kind,
        created_by="test",
    )


def test_research_state_resolves_historical_hypothesis_against_execution():
    selection = artifact(
        "hypothesis_selection",
        1,
        {
            "selected": [
                {
                    "claim": "Dropout(p=0.5)应使测试准确率提升至少0.5%。",
                }
            ]
        },
    )
    old_plan = artifact(
        "plan",
        1,
        {
            "objective": "检验Dropout(p=0.5)是否有效。",
            "parameters": {"dropout_probability": 0.5},
        },
    )
    current_plan = artifact(
        "plan",
        2,
        {
            "objective": "修正后检验Dropout(p=0.3)是否有效。",
            "hypotheses": ["旧文本仍写Dropout(p=0.5)。"],
            "parameters": {"dropout_probability": 0.3},
        },
    )
    result = artifact(
        "experiment_result",
        1,
        {
            "experiment_id": "experiment_2",
            "is_real_experiment": True,
            "status": "completed",
            "parameters": {"dropout_probability": 0.3},
            "metrics": {"test_accuracy_improvement": -0.013},
        },
    )
    revision = artifact("revision", 1, {"verdict": "failed"})

    state = build_research_state(
        [selection, old_plan, current_plan, result, revision]
    )

    assert state["canonical"]["executed_dropout_probability"] == 0.3
    assert "Dropout(p=0.3)" in state["canonical"]["active_hypothesis"]
    assert state["conflicts"][0]["code"] == "hypothesis_execution_parameter_mismatch"
    plan_states = [
        item for item in state["artifact_states"] if item["artifact_type"] == "plan"
    ]
    assert plan_states[0]["status"] == "superseded"
    assert plan_states[0]["superseded_by"] == current_plan.id
    assert plan_states[1]["status"] == "active"

    report_plan = active_plan_for_report(current_plan.content, state)
    assert report_plan["parameters"]["dropout_probability"] == 0.3
    assert report_plan["hypotheses"] == [
        state["canonical"]["active_hypothesis"]
    ]


def test_research_state_covers_every_process_artifact_and_generic_parameters():
    problem = artifact("problem", 1, {"problem_statement": "比较两种优化器"})
    evidence = artifact(
        "evidence",
        1,
        {"references": [{"title": "Reference", "verified": True}]},
    )
    hypothesis = artifact(
        "hypothesis",
        1,
        {"candidates": [{"claim": "优化器 B 可能改善验证损失"}]},
    )
    review = artifact("idea_review", 1, {"evaluations": [{"decision": "GO"}]})
    selection = artifact(
        "hypothesis_selection",
        1,
        {"selected": [{"claim": "优化器 B 可能改善验证损失"}]},
    )
    old_plan = artifact(
        "plan",
        1,
        {"objective": "测试优化器 B", "parameters": {"learning_rate": 0.01}},
    )
    current_plan = artifact(
        "plan",
        2,
        {
            "objective": "测试优化器 B",
            "parameters": {"learning_rate": 0.005, "optimizer": "adamw"},
        },
    )
    task = artifact("experiment_task", 1, {"experiment_id": "experiment_1"})
    task.parent_artifact_id = current_plan.id
    bundle = artifact("experiment_bundle", 1, {"manifest": {"entrypoint": "train.py"}})
    bundle.parent_artifact_id = task.id
    result = artifact(
        "experiment_result",
        1,
        {
            "experiment_id": "experiment_1",
            "is_real_experiment": True,
            "status": "completed",
            "parameters": {"learning_rate": 0.004, "optimizer": "adamw"},
            "metrics": {"validation_loss": 0.31},
        },
    )
    result.parent_artifact_id = bundle.id
    analysis = artifact("iteration_analysis", 1, {"observations": ["损失下降"]})
    analysis.parent_artifact_id = result.id
    iteration_evidence = artifact(
        "iteration_evidence",
        1,
        {"references": [{"title": "Optimizer Study", "exportable": True}]},
    )
    iteration_evidence.parent_artifact_id = analysis.id
    decision = artifact(
        "iteration_decision",
        1,
        {"selected_direction": {"title": "保持优化器，缩小学习率范围"}},
    )
    decision.parent_artifact_id = iteration_evidence.id
    revision = artifact("revision", 1, {"verdict": "partial"})
    revision.parent_artifact_id = result.id
    future_type = artifact("future_process_artifact", 1, {"value": "retained"})
    prior_state = artifact("research_state", 1, {"schema_version": 1})
    artifacts = [
        problem,
        evidence,
        hypothesis,
        review,
        selection,
        old_plan,
        current_plan,
        task,
        bundle,
        result,
        analysis,
        iteration_evidence,
        decision,
        revision,
        future_type,
        prior_state,
    ]
    original_contents = [item.content.copy() for item in artifacts]

    state = build_research_state(artifacts)

    entries = {item["artifact_id"]: item for item in state["artifact_states"]}
    expected_ids = {item.id for item in artifacts if item.type != "research_state"}
    assert set(entries) == expected_ids
    assert prior_state.id not in entries
    assert entries[old_plan.id]["lifecycle_status"] == "superseded"
    assert entries[current_plan.id]["lifecycle_status"] == "active"
    assert entries[hypothesis.id]["lifecycle_status"] == "historical"
    assert entries[result.id]["validity_status"] == "verified"
    assert entries[task.id]["validity_status"] == "verified"
    assert entries[bundle.id]["validity_status"] == "verified"
    assert entries[future_type.id]["validity_status"] == "not_applicable"
    assert all(len(item["content_sha256"]) == 64 for item in entries.values())
    assert state["conflicts"] == [
        {
            "code": "plan_execution_parameter_mismatch",
            "field": "learning_rate",
            "superseded_value": 0.005,
            "authoritative_value": 0.004,
            "resolution": (
                "Use the recorded execution value as the report fact and retain "
                "the planned value only as historical intent."
            ),
            "source_artifact_id": result.id,
        }
    ]
    assert state["canonical"]["executed_parameters"]["learning_rate"] == 0.004
    assert [item.content for item in artifacts] == original_contents


def test_research_state_is_deterministic_when_previous_snapshots_exist():
    plan = artifact(
        "plan",
        1,
        {"objective": "验证通用方法", "parameters": {"batch_size": 32}},
    )
    result = artifact(
        "experiment_result",
        1,
        {
            "is_real_experiment": True,
            "status": "completed",
            "parameters": {"batch_size": 32},
            "metrics": {"score": 0.8},
        },
    )
    first = build_research_state([plan, result])
    snapshot = artifact("research_state", 1, first)

    assert build_research_state([plan, result, snapshot]) == first


def test_research_state_persists_targeted_retrieval_and_candidate_evidence():
    reasoning = artifact(
        "reasoning",
        1,
        {
            "literature_registry": [{"title": "Verified paper"}],
            "evidence_registry": [{"evidence_id": "EVID-001", "claim": "mechanism"}],
            "research_gaps": [{"gap_id": "GAP-001"}],
            "candidate_evidence_maps": [{"candidate_id": "CAND-001"}],
            "unverified_citations": [{"candidate_id": "CAND-001", "citations": ["candidate citation"]}],
            "targeted_retrieval": {
                "queries": ["efficient channel attention"],
                "history": [{"round": 1, "new_papers": 2, "new_evidence": 3}],
            },
            "candidate_assessments": [
                {"candidate_index": 0, "critic_decision": "GO", "was_revised": False},
                {"candidate_index": 1, "critic_decision": "REJECT", "status": "rejected", "was_revised": True},
            ],
        },
    )
    retrieval = artifact("targeted_retrieval", 1, {"round": 1, "queries": {"0": ["query"]}})

    state = build_research_state([reasoning, retrieval])

    assert state["schema_version"] == 3
    assert state["targeted_retrieval_round"] == 1
    assert state["literature_queries"] == ["efficient channel attention"]
    assert state["evidence_registry"][0]["evidence_id"] == "EVID-001"
    assert state["research_gaps"][0]["gap_id"] == "GAP-001"
    assert state["rejected_candidates"] == [1]
