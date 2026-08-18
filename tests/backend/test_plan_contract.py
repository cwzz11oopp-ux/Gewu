from backend.app.workflow.plan_contract import authoritative_plan_contract, normalize_plan


def test_plan_contract_is_shared_and_preserves_scientific_gate_fields():
    raw = {
        "objective": "compare treatment with control",
        "procedure": {"steps": ["verify loader", "run smoke"]},
        "diagnosis": {"status": "ready"},
        "mechanism_and_evidence": {"mechanism": "local structure", "evidence": ["E1"]},
        "boundary_conditions": ["frozen split"],
        "alignment_contract": [{"claim": "CNN wins", "metric": "accuracy"}],
        "baseline_and_controls": {"treatment": "CNN", "control": "MLP"},
        "feasibility_risks": [{"risk": "loader", "mitigation": "verify"}],
        "staged_gates": [{"name": "smoke", "pass_criteria": ["finite metrics"]}],
        "formal_experiment_entry_conditions": ["smoke pass"],
        "positive_negative_inconclusive_rules": {"positive": ["CI above zero"]},
        "remaining_unknowns": ["capacity"],
        "capacity_confounder": {"control_strategy": "parameter matched"},
        "local_dataset_loader_verification": {"procedure": "load one batch"},
    }
    plan = normalize_plan(raw, {"selected": [{"claim": "CNN wins"}]})
    assert {"dataset", "split_contract", "evaluations", "comparisons", "procedure"} <= set(
        authoritative_plan_contract()
    )
    assert plan["diagnosis"]["status"] == "ready"
    assert plan["capacity_confounder"]["control_strategy"] == "parameter matched"
    assert plan["local_dataset_loader_verification"]["procedure"] == "load one batch"
