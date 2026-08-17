from backend.app.workflow.scientific_integrity import (
    compile_scientific_contract, progressive_decision, scientific_feedback,
    validate_coverage, validate_split_contract,
)


def _plan(traceability=None, evaluations=None, comparisons=None):
    return {"traceability": traceability or [], "evaluations": evaluations or [], "comparisons": comparisons or []}


def test_coverage_detects_missing_question_claim_and_hypothesis_evidence():
    contract = compile_scientific_contract("Question", ["alpha effect", "beta effect"], _plan([
        {"claim": "alpha effect", "metric": "accuracy", "decision_rule": "higher supports"},
    ], [{"metric": "accuracy"}]))
    codes = {issue["code"] for issue in validate_coverage(contract)}
    assert "HYPOTHESIS_CLAIM_WITHOUT_EVIDENCE" in codes


def test_coverage_detects_metric_and_baseline_mismatch():
    contract = compile_scientific_contract("Question", ["method improves accuracy versus baseline"], _plan([
        {"claim": "method improves accuracy versus baseline", "metric": "accuracy", "decision_rule": "higher supports"},
    ], [{"metric": "training_loss"}]))
    codes = {issue["code"] for issue in validate_coverage(contract)}
    assert {"CLAIM_METRIC_UNCOVERED", "CLAIM_BASELINE_MISSING"} <= codes


def test_valid_complete_coverage_passes():
    plan = _plan([
        {"claim": "method improves accuracy versus baseline", "metric": "accuracy", "decision_rule": "higher supports"},
    ], [{"metric": "accuracy"}], [{"baseline": "base", "variant": "method", "controls": ["seed"]}])
    contract = compile_scientific_contract("Question", ["method improves accuracy versus baseline"], plan)
    assert validate_coverage(contract) == []


def test_split_integrity_detects_overlap_and_test_tuning():
    issues = validate_split_contract({"train": {"ids": ["1", "2"]}, "validation": {"ids": ["3"]}, "test": {"ids": ["2", "3"]}, "selection_sources": ["test"], "final_metric_source": "train", "seed": 7})
    codes = {issue["code"] for issue in issues}
    assert {"TRAIN_TEST_OVERLAP", "VALIDATION_TEST_OVERLAP", "TEST_USED_FOR_SELECTION", "FINAL_METRIC_FROM_TRAIN"} <= codes


def test_split_identity_is_reproducible_and_structure_risk_is_warning():
    split = {"train": {"ids": ["1"]}, "validation": {"ids": ["2"]}, "test": {"ids": ["3"]}, "identity": "split:abc", "seed": 7, "structure": "group"}
    assert validate_split_contract(split) == validate_split_contract(dict(split))
    assert any(issue["level"] == "WARNING" for issue in validate_split_contract(split))


def test_progressive_contract_stops_scientific_failure_without_code_repair():
    contract = {"progressive_experiment": {"stages": [{"name": "sanity", "stop_criteria": ["unsupported"]}, {"name": "full"}]}}
    assert progressive_decision(contract, "code_failure")["action"] == "repair_code"
    decision = progressive_decision(contract, "unsupported")
    assert decision == {"action": "scientific_feedback", "stage": "sanity", "escalate": False}


def test_scientific_feedback_is_persistent_safe_state_not_code_repair():
    feedback = scientific_feedback({"claims": ["claim"], "contract_sha256": "abc"}, {"metrics": {"accuracy": 0.5}, "result_id": "r"}, "unsupported")
    assert feedback["verdict"] == "unsupported"
    assert feedback["code_repair_allowed"] is False
    assert feedback["evidence"]["metrics"] == {"accuracy": 0.5}
