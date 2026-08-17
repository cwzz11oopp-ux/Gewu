from __future__ import annotations

_PLANNING_FIELDS = (
    "dataset",
    "methods",
    "method",
    "baselines",
    "metrics",
    "comparisons",
    "evaluations",
    "success_criteria",
)
_NOT_PROVIDED = "未提供"

# This is the single authoring/reviewing vocabulary for Research Plans.  It is
# intentionally descriptive rather than Fashion-MNIST-specific: the runtime
# still validates the actual dataset, split, Bundle, Harness, and result.
AUTHORITATIVE_PLAN_CONTRACT = {
    "research_question": "The question and scoped population/task being tested.",
    "hypothesis": "The selected hypothesis; preserve user-owned claim text.",
    "treatment_and_control": "Intervention, executable control, and fixed controls.",
    "dataset_identity": "Bound dataset contract, fingerprint, preprocessing, and loader verification.",
    "split_identity": "Reproducible train/validation/test identity, seed, disjointness, and test isolation.",
    "training_and_validation_policy": "Budget, optimizer, checkpoint selection, and early-stopping policy.",
    "metrics_and_statistics": "Primary/secondary metrics, aggregation, uncertainty, and preregistered interpretation.",
    "capacity_confounder": "Capacity confounder, control strategy, and justified claim boundary.",
    "effect_size_justification": "Minimum meaningful effect/effect-size rationale and stopping criteria.",
    "execution_gates": "Loader verification and static/overfit/smoke/pilot/formal entry gates.",
    "outcome_rules": "Positive, negative, and inconclusive rules plus remaining unknowns.",
}


def authoritative_plan_contract() -> dict[str, str]:
    """Return a copy safe to place in generator and reviewer prompt context."""
    return dict(AUTHORITATIVE_PLAN_CONTRACT)


def normalize_plan(
    raw_plan: object,
    selected_hypotheses: object,
    *,
    provider_mode: str | None = None,
    fallback_used: bool | None = None,
) -> dict:
    """Convert provider planning output into the stable experiment-blueprint contract."""
    raw = _as_dict(raw_plan)
    unwrapped = isinstance(raw.get("plan"), dict) and not any(field in raw for field in _PLANNING_FIELDS)
    plan = raw["plan"] if unwrapped else raw

    hypotheses = _selected_claims(selected_hypotheses)
    objective = _first_string(raw.get("objective"), plan.get("objective"), plan.get("expected_result"))
    if not objective and hypotheses:
        objective = hypotheses[0]

    comparisons = _comparison_records(plan.get("comparisons"))
    if not comparisons:
        comparisons = _legacy_comparisons(plan.get("baselines"), plan.get("methods"))

    evaluations = _evaluation_records(plan.get("evaluations"))
    if not evaluations:
        evaluations = _legacy_evaluations(plan.get("metrics"))

    return {
        "objective": objective,
        "hypotheses": hypotheses,
        "method": _as_dict(plan.get("method")),
        "dataset": _dataset(plan.get("dataset")),
        "comparisons": comparisons,
        "evaluations": evaluations,
        "procedure": _as_dict(plan.get("procedure")),
        "parameters": _as_dict(plan.get("parameters")),
        "seeds": _integer_list(plan.get("seeds")),
        "statistical_summary": _as_dict(plan.get("statistical_summary")),
        "success_criteria": _string_list(plan.get("success_criteria")),
        "failure_criteria": _string_list(plan.get("failure_criteria")),
        "expected_artifacts": _string_list(plan.get("expected_artifacts")),
        "stop_conditions": _string_list(plan.get("stop_conditions")),
        "primary_experiment": _as_dict(plan.get("primary_experiment")),
        "optional_ablations": _record_list(plan.get("optional_ablations")),
        "traceability": _record_list(plan.get("traceability")),
        "resources": _as_dict(plan.get("resources")),
        "risks": _as_list(plan.get("risks")),
        "additional_sections": _as_dict(plan.get("additional_sections")),
        "diagnosis": _as_dict(plan.get("diagnosis")),
        "revised_hypothesis": _as_dict(plan.get("revised_hypothesis")),
        "mechanism_and_evidence": _as_dict(plan.get("mechanism_and_evidence")),
        "boundary_conditions": _as_list(plan.get("boundary_conditions")),
        "alignment_contract": _record_list(plan.get("alignment_contract")),
        "baseline_and_controls": _as_dict(plan.get("baseline_and_controls")),
        "feasibility_risks": _record_list(plan.get("feasibility_risks")),
        "staged_gates": _record_list(plan.get("staged_gates")),
        "formal_experiment_entry_conditions": _string_list(plan.get("formal_experiment_entry_conditions")),
        "positive_negative_inconclusive_rules": _as_dict(plan.get("positive_negative_inconclusive_rules")),
        "remaining_unknowns": _string_list(plan.get("remaining_unknowns")),
        "capacity_confounder": _as_dict(plan.get("capacity_confounder")),
        "local_dataset_loader_verification": _as_dict(plan.get("local_dataset_loader_verification")),
        "iteration_contract": _as_dict(plan.get("iteration_contract")),
        "split_contract": _as_dict(plan.get("split_contract")),
        "progressive_experiment": _as_dict(plan.get("progressive_experiment")),
        "provider_mode": provider_mode if provider_mode is not None else _first_string(
            raw.get("provider_mode"), plan.get("provider_mode")
        ),
        "fallback_used": fallback_used if fallback_used is not None else _first_bool(
            raw.get("fallback_used"), plan.get("fallback_used")
        ),
        "normalization": {"unwrapped_plan": unwrapped},
    }


def _as_dict(value: object) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    return list(value) if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    return [item for item in _as_list(value) if isinstance(item, str) and item.strip()]


def _integer_list(value: object) -> list[int]:
    values = []
    for item in _as_list(value):
        if isinstance(item, bool):
            continue
        try:
            values.append(int(item))
        except (TypeError, ValueError):
            continue
    return values


def _record_list(value: object) -> list[dict]:
    return [dict(item) for item in _as_list(value) if isinstance(item, dict)]


def _first_string(*values: object) -> str:
    return next((value for value in values if isinstance(value, str)), "")


def _first_bool(*values: object) -> bool:
    return next((value for value in values if isinstance(value, bool)), False)


def _selected_claims(selection: object) -> list[str]:
    selected = _as_dict(selection).get("selected")
    claims = []
    for candidate in _as_list(selected):
        if isinstance(candidate, dict) and isinstance(candidate.get("claim"), str):
            claims.append(candidate["claim"])
        elif isinstance(candidate, dict):
            idea_card = candidate.get("idea_card")
            if isinstance(idea_card, dict) and isinstance(idea_card.get("claim"), str):
                claims.append(idea_card["claim"])
    return claims


def _dataset(value: object) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        return {"name": value}
    return {}


def _comparison_records(value: object) -> list[dict]:
    return [dict(record) for record in _as_list(value) if isinstance(record, dict)]


def _legacy_comparisons(baselines: object, methods: object) -> list[dict]:
    baseline_names = [name for name in _as_list(baselines) if isinstance(name, str)]
    method_names = [name for name in _as_list(methods) if isinstance(name, str)]
    if not baseline_names and not method_names:
        return []
    return [
        {
            "baseline": baseline_names[index] if index < len(baseline_names) else _NOT_PROVIDED,
            "variant": method_names[index] if index < len(method_names) else _NOT_PROVIDED,
            "controls": [],
        }
        for index in range(max(len(baseline_names), len(method_names)))
    ]


def _evaluation_records(value: object) -> list[dict]:
    return [dict(record) for record in _as_list(value) if isinstance(record, dict)]


def _legacy_evaluations(metrics: object) -> list[dict]:
    return [
        {"metric": metric, "direction": _NOT_PROVIDED, "method": _NOT_PROVIDED}
        for metric in _as_list(metrics)
        if isinstance(metric, str)
    ]
