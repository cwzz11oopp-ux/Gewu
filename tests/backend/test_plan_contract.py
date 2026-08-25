from backend.app.workflow.plan_contract import (
    authoritative_plan_contract,
    merge_plan_patch,
    normalize_plan,
)


def _full_plan():
    return normalize_plan(
        {
            "objective": "compare treatment with control",
            "procedure": {"steps": ["verify loader", "run smoke"]},
            "comparisons": [{"baseline": "MLP", "variant": "CNN", "controls": []}],
            "dataset": {"name": "frozen-ds", "split": "train/val/test"},
        },
        {"selected": [{"claim": "CNN wins"}]},
    )


def test_merge_plan_patch_carries_unchanged_fields_and_applies_patch():
    base = _full_plan()
    patch = {
        "comparisons": [{"baseline": "MLP", "variant": "SE-CNN", "controls": ["width-matched"]}],
        "fix_map": {"PRI-control": ["comparisons"]},
    }
    merged = merge_plan_patch(base, patch)
    # Unchanged fields keep the candidate's exact values.
    assert merged["objective"] == base["objective"]
    assert merged["procedure"] == base["procedure"]
    assert merged["dataset"] == base["dataset"]
    # Patched fields take the new values; fix_map survives.
    assert merged["comparisons"][0]["variant"] == "SE-CNN"
    assert merged["fix_map"] == {"PRI-control": ["comparisons"]}


def test_merge_plan_patch_unwraps_plan_wrapper_and_keeps_top_level_fix_map():
    base = _full_plan()
    patch = {
        "plan": {"comparisons": [{"baseline": "MLP", "variant": "ECA-CNN", "controls": []}]},
        "fix_map": {"PRI-control": ["comparisons"]},
    }
    merged = merge_plan_patch(base, patch)
    assert merged["comparisons"][0]["variant"] == "ECA-CNN"
    assert merged["fix_map"] == {"PRI-control": ["comparisons"]}
    assert merged["objective"] == base["objective"]


def test_merge_plan_patch_drops_non_canonical_provider_metadata():
    base = _full_plan()
    patch = {
        "comparisons": [{"baseline": "MLP", "variant": "SE-CNN", "controls": []}],
        "model_used": "qwen3.7-plus",
        "thinking_enabled": True,
        "fix_map": {"PRI-control": ["comparisons"]},
    }
    merged = merge_plan_patch(base, patch)
    assert "model_used" not in merged
    assert "thinking_enabled" not in merged
    assert merged["comparisons"][0]["variant"] == "SE-CNN"


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
