from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.app.models.experiment import ExperimentBundle, ExperimentFile, ExperimentManifest
from backend.app.workflow.serial_iteration import (
    apply_source_edits, build_iteration_memory, continuation_stop, digest,
    direction_issues, freeze_iteration_policy, implementation_base, prompt_memory,
    trial_signature,
)


def artifact(kind, identifier, content, parent=None):
    return SimpleNamespace(type=kind, id=identifier, content=content, parent_artifact_id=parent)


def policy():
    content = {
        "schema_version": 1, "kind": "optimization", "max_rounds": 4,
        "stagnation_patience": 2,
    }
    return artifact("iteration_policy", "policy", {**content, "policy_sha256": digest(content)})


def trial(number, scores, *, baseline=None, direction="maximize", split="frozen", real=True):
    seeds = [7, 11, 19]
    baseline = baseline or [.5, .51, .49]
    plan = {
        "dataset": {"content_fingerprint": "dataset-sha"},
        "method": "classifier", "parameters": {"rate": number / 10},
        "seeds": seeds, "evaluations": [{"metric": "accuracy", "direction": direction}],
        "comparisons": [{"baseline": "baseline", "variant": "candidate"}],
    }
    protocol = {
        "primary_metric": "accuracy", "primary_metric_direction": direction,
        "split": {"source": split}, "seeds": seeds, "stage": "formal_validation",
        "training_budget": {"epochs": 3},
    }
    source = f"import math\n\ndef main():\n    rate = {number / 10}\n    return math.sqrt(rate)\n"
    bundle = ExperimentBundle(
        manifest=ExperimentManifest(run_id="run", experiment_id=f"experiment_{number}",
                                    result_id=f"experiment_{number}_result", seeds=seeds),
        files=[ExperimentFile(path="train.py", content=source)],
    ).model_dump()
    # A complete system runtime contract isn't needed by the observation helper;
    # the real model validation for implementation_base uses the manifest above.
    bundle["runtime_contract"] = None
    result = {"is_real_experiment": real, "experiment_id": f"experiment_{number}",
              "result_id": f"experiment_{number}_result", "audit": {"integrity_status": "passed"},
              "seed_results": [{"seed": seed, "metrics": {"baseline_accuracy": b, "accuracy": v}}
                               for seed, b, v in zip(seeds, baseline, scores)]}
    return [artifact("plan", f"p{number}", plan),
            artifact("experiment_task", f"t{number}", {"plan": deepcopy(plan), "phase2_protocol": protocol}, f"p{number}"),
            artifact("experiment_bundle", f"b{number}", bundle, f"t{number}"),
            artifact("experiment_result", f"r{number}", result, f"b{number}")]


def test_goal_is_normal_question_with_no_round_count_and_legacy_stays_verification():
    question = "如何提高低虚警条件下的目标检出率？"
    value = freeze_iteration_policy({"research_intent": {
        "kind": "optimization", "goal_quote": "提高低虚警条件下的目标检出率", "reason": "改善指标",
    }}, question, 4)
    assert value["kind"] == "optimization"
    assert value["max_rounds"] == 4
    assert freeze_iteration_policy({}, question, 4)["kind"] == "verification"
    assert freeze_iteration_policy({"research_intent": {"kind": "optimization", "goal_quote": "invented"}}, question, 4)["kind"] == "verification"
    assert build_iteration_memory(trial(1, [.7, .71, .69])) == {"enabled": False}


def test_keep_best_after_regression_then_promote_comparable_improvement():
    artifacts = [policy(), *trial(1, [.7, .71, .69]), *trial(2, [.65, .66, .64])]
    before = deepcopy(artifacts)
    memory = build_iteration_memory(artifacts)
    assert memory["best"]["result_id"] == "r1"
    assert memory["current"]["selection"] == "keep_incumbent"
    assert memory["stagnant_rounds"] == 1
    assert artifacts == before
    artifacts += trial(3, [.81, .80, .83])
    memory = build_iteration_memory(artifacts)
    assert memory["best"]["result_id"] == "r3"
    assert memory["current"]["incumbent_comparison"]["status"] == "positive_stable"
    assert memory["stagnant_rounds"] == 0
    assert memory["confirmation_status"] == "independent_confirmation_required"


def test_minimization_and_uncertain_gain_do_not_promote_by_raw_score_alone():
    artifacts = [policy(), *trial(1, [.7, .71, .69], direction="minimize")]
    artifacts += trial(2, [.59, .62, .55], direction="minimize")
    assert build_iteration_memory(artifacts)["best"]["result_id"] == "r2"
    artifacts = [policy(), *trial(1, [.7, .71, .69]), *trial(2, [.69, .8, .66])]
    memory = build_iteration_memory(artifacts)
    assert memory["current"]["score"] > memory["best"]["score"]
    assert memory["best"]["result_id"] == "r1"


def test_changed_split_creates_separate_series_not_cross_protocol_gain():
    memory = build_iteration_memory([policy(), *trial(1, [.8, .81, .79]),
                                     *trial(2, [.6, .61, .59], split="group")])
    assert len(memory["best_by_protocol"]) == 2
    assert memory["best"]["result_id"] == "r2"
    assert memory["current"]["selection"] == "initial_candidate"
    assert memory["stagnant_rounds"] == 1


@pytest.mark.parametrize("defect", ["mock", "audit", "nan", "missing_seed", "fingerprint", "lineage", "baseline_drift"])
def test_invalid_measurements_never_become_best(defect):
    items = trial(2, [.9, .91, .89])
    if defect == "mock":
        items[-1].content["is_real_experiment"] = False
    elif defect == "audit":
        items[-1].content["audit"]["integrity_status"] = "failed"
    elif defect == "nan":
        items[-1].content["seed_results"][0]["metrics"]["accuracy"] = float("nan")
    elif defect == "missing_seed":
        items[-1].content["seed_results"].pop()
    elif defect == "fingerprint":
        items[1].content["plan"]["dataset"] = {}
    elif defect == "lineage":
        items[-1].parent_artifact_id = "unknown"
    else:
        items[-1].content["seed_results"][0]["metrics"]["baseline_accuracy"] = .8
    memory = build_iteration_memory([policy(), *trial(1, [.7, .71, .69]), *items])
    assert not memory["current"]["eligible"]
    assert memory["best"] is None
    assert memory["best_by_protocol"][0]["result_id"] == "r1"


def test_stagnation_and_budget_are_hard_stops_without_minimum_round_count():
    memory = build_iteration_memory([policy(), *trial(1, [.7, .71, .69]),
                                     *trial(2, [.6, .61, .59]), *trial(3, [.6, .61, .59])])
    assert continuation_stop(memory, 3, 4) == "OPTIMIZATION_STAGNATION_LIMIT"
    assert continuation_stop(memory, 4, 4) == "ITERATION_LIMIT_REACHED"
    assert continuation_stop({"policy": {}}, 1, 4) == ""


def test_best_code_reference_is_exact_immutable_snapshot():
    artifacts = [policy(), *trial(1, [.7, .71, .69]), *trial(2, [.6, .61, .59])]
    memory = build_iteration_memory(artifacts)
    base = implementation_base(artifacts, memory["best"])
    assert base["bundle_artifact_id"] == "b1"
    assert "rate = 0.1" in base["files"][0]["content"]
    reference = deepcopy(memory["best"])
    reference["bundle_id"] = "b2"
    with pytest.raises(ValueError, match="SNAPSHOT_MISMATCH"):
        implementation_base(artifacts, reference)
    artifacts[3].content["files"][0]["content"] += "# tamper"
    with pytest.raises(ValueError, match="SNAPSHOT_MISMATCH"):
        implementation_base(artifacts, memory["best"])


def test_source_edits_apply_to_base_without_mutation_or_new_files():
    artifacts = [policy(), *trial(1, [.7, .71, .69])]
    base = implementation_base(artifacts, build_iteration_memory(artifacts)["best"])
    result = apply_source_edits(base, {"edits": [{"old": "rate = 0.1", "new": "rate = 0.3"}]})
    assert "rate = 0.3" in result["files"][0]["content"]
    assert "rate = 0.1" in base["files"][0]["content"]
    assert len(result["files"]) == 1
    for invalid in ({}, {"edits": [{"old": "unknown", "new": "x"}]},
                    {"edits": [{"old": base["files"][0]["content"], "new": "pass"}]},
                    {"edits": [{"old": "rate", "new": "x"}]}):
        with pytest.raises(ValueError, match="ITERATION_PATCH_INVALID"):
            apply_source_edits(base, invalid)


def test_direction_requires_real_result_reference_and_candidate_membership():
    memory = build_iteration_memory([policy(), *trial(1, [.7, .71, .69])])
    selected = {k: "test" for k in ("name", "problem_addressed", "changed_variable", "success_rule", "failure_rule", "stop_rule")}
    selected.update(result_basis=["AUC"], source_result_ids=["r1"], fixed_controls=["split"], target_metrics=["accuracy"])
    direction = {"decision": "REVISE", "selected_direction": selected, "optimization_candidates": [deepcopy(selected)]}
    assert direction_issues(direction, memory) == []
    selected["source_result_ids"] = ["invented"]
    assert "ITERATION_DIRECTION_RESULT_REFERENCE_INVALID" in direction_issues(direction, memory)
    selected["name"] = "not compared"
    assert "ITERATION_DIRECTION_NOT_IN_CANDIDATES" in direction_issues(direction, memory)
    assert direction_issues({"decision": "REPORT"}, memory) == []


def test_memory_is_bounded_and_duplicate_signature_ignores_narrative():
    artifacts = [policy()]
    for n in range(1, 13):
        artifacts += trial(n, [.7, .71, .69])
    value = prompt_memory(build_iteration_memory(artifacts))
    assert len(value["history"]) == 8
    assert "candidate_seeds" not in json.dumps(value)
    plan = trial(1, [.7, .71, .69])[0].content
    assert trial_signature(plan) == trial_signature({**plan, "objective": "reworded", "iteration_contract": {"id": 3}})
    assert trial_signature(plan) != trial_signature({**plan, "parameters": {"rate": .9}})


def test_engineering_failures_and_reanalysis_do_not_consume_scientific_patience():
    artifacts = [policy(), *trial(1, [.7, .71, .69])]
    duplicate = deepcopy(artifacts[-1])
    duplicate.id = "reanalyzed-result"
    failed = trial(2, [.6, .61, .59])
    failed[-1].content["status"] = "failed"
    memory = build_iteration_memory([*artifacts, duplicate, *failed])
    assert memory["rounds_observed"] == 1
    assert memory["stagnant_rounds"] == 0
    assert memory["best"]["result_id"] == duplicate.id


def test_changed_runtime_parameters_may_reuse_source_without_fake_edit():
    artifacts = [policy(), *trial(1, [.7, .71, .69])]
    base = implementation_base(artifacts, build_iteration_memory(artifacts)["best"])
    output = apply_source_edits(base, {"edits": []}, allow_unchanged=True)
    assert output["files"][0]["content"] == base["files"][0]["content"]
    with pytest.raises(ValueError, match="ITERATION_PATCH_INVALID"):
        apply_source_edits(base, {"edits": []})


def test_report_receives_best_separately_from_latest_and_does_not_claim_confirmation():
    from backend.app.agents.writer import WriterAgent

    memory = build_iteration_memory([policy(), *trial(1, [.7, .71, .69]), *trial(2, [.6, .61, .59])])
    facts = WriterAgent._fact_sheet({}, {}, {}, {"metrics": {"accuracy": .6}}, {}, {}, [],
                                   {"optimization": prompt_memory(memory)})
    assert facts["final_result"]["metrics"]["accuracy"] == .6
    assert facts["optimization"]["best"]["result_id"] == "r1"
    assert facts["optimization"]["confirmation_status"] == "independent_confirmation_required"


def test_frozen_policy_cannot_be_changed_or_duplicated():
    corrupted = policy()
    corrupted.content["max_rounds"] = 999
    with pytest.raises(ValueError, match="ITERATION_POLICY_INTEGRITY_INVALID"):
        build_iteration_memory([corrupted])
    with pytest.raises(ValueError, match="ITERATION_POLICY_DUPLICATED"):
        build_iteration_memory([policy(), policy()])
