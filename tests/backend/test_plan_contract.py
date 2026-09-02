from backend.app.workflow.plan_contract import (
    authoritative_plan_contract,
    execution_training_budget,
    merge_plan_patch,
    normalize_plan,
)
from backend.app.agents.planner import (
    CLAIM_COHERENCE_FIELDS,
    PLAN_COHERENCE_INSTRUCTIONS,
    PLAN_REVIEW_FIXED_INSTRUCTIONS,
    PLAN_REVISION_FIXED_INSTRUCTIONS,
    PlanningAgent,
    plan_revision_patch_schema,
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


def test_logistic_regression_uses_single_fit_budget_without_epoch_loop():
    plan = {
        "method": {"components": ["LogisticRegression(solver='lbfgs')"]},
        "parameters": {"solver": "lbfgs", "max_iter": 500},
    }

    assert execution_training_budget(plan) == {
        "mode": "single_fit",
        "fit_count": 1,
        "max_iter": 500,
        "runtime_passes": 1,
    }


def test_svc_uses_single_fit_budget_without_epoch_loop():
    plan = {
        "method": {"name": "SVC(kernel='rbf')"},
        "parameters": {"max_iter": 2_000},
    }

    assert execution_training_budget(plan) == {
        "mode": "single_fit",
        "fit_count": 1,
        "max_iter": 2_000,
        "runtime_passes": 1,
    }


def test_claim_mismatch_revision_schema_exposes_full_semantic_closure():
    schema = plan_revision_patch_schema([
        {
            "issue_id": "B1",
            "blocker_class": "CLAIM_PLAN_MISMATCH",
            "contract_fields": ["primary_claim"],
        }
    ])

    assert set(CLAIM_COHERENCE_FIELDS) <= set(schema)
    assert "fix_map" in schema


def test_planning_contract_covers_multi_endpoint_outcomes_and_iteration_isolation():
    class RecordingProvider:
        def __init__(self):
            self.instructions = ""

        def generate_json(self, task, inputs, schema_hint, instructions=""):
            self.instructions = instructions
            return {}

    provider = RecordingProvider()
    PlanningAgent(provider).build_plan({"claim": "improve both endpoints"})

    prompt = provider.instructions
    for required in (
        "minimum meaningful improvement for every endpoint",
        "mixed directions",
        "reject and roll back",
        "validation data",
        "test set only once",
        "never invent provenance IDs",
    ):
        assert required in prompt
    assert PLAN_COHERENCE_INSTRUCTIONS in prompt


def test_review_and_revision_contracts_require_atomic_claim_repair():
    assert "name every affected canonical top-level field" in PLAN_REVIEW_FIXED_INSTRUCTIONS
    assert "CLAIM_PLAN_MISMATCH repair is atomic" in PLAN_REVISION_FIXED_INSTRUCTIONS
    assert "mixed/inconclusive" in PLAN_REVISION_FIXED_INSTRUCTIONS
    assert "rollback" in PLAN_REVISION_FIXED_INSTRUCTIONS
