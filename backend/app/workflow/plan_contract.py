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
TRAINING_EPOCH_ALIASES = ("epochs", "max_epochs", "epochs_limit")

# This is the single authoring/reviewing/diff vocabulary for Research Plans.
# Every key is an actual normalized-plan field.  Historical semantic labels are
# accepted only at the input boundary through FIELD_ALIAS_TO_CANONICAL.
CANONICAL_PLAN_CONTRACT_FIELDS = {
    "objective": "The question and scoped population/task being tested.",
    "hypotheses": "The selected hypotheses; preserve user-owned claim text.",
    "primary_claim": "The exact claim directly tested by the selected hypothesis.",
    "original_question_link": "How the primary claim answers, narrows, or partially addresses the original question.",
    "secondary_endpoints": "Only the minimal secondary controls or endpoints needed to preserve that interpretation.",
    "method": "The intervention and its implementable mechanism.",
    "dataset": "Bound dataset contract, fingerprint, preprocessing, and loader verification.",
    "comparisons": "Executable baselines, variants, and fixed controls.",
    "evaluations": "Primary/secondary metrics, directions, and decision methods.",
    "procedure": "Reproducible execution and validation procedure.",
    "parameters": "Training budget, optimizer, and other fixed parameters.",
    "seeds": "Preregistered random seeds and seed policy.",
    "statistical_summary": "Aggregation, uncertainty, and statistical interpretation.",
    "success_criteria": "Conditions that support every required endpoint of the scoped primary claim, including preregistered minimum meaningful effects.",
    "failure_criteria": "Conditions that refute or limit any required endpoint of the scoped primary claim without leaving an undecided outcome gap.",
    "expected_artifacts": "Durable outputs required to audit the experiment.",
    "stop_conditions": "Early-stop, validation no-improvement rollback, iteration-budget, and execution-blocking conditions.",
    "primary_experiment": "The smallest primary experiment that tests the claim.",
    "optional_ablations": "Optional diagnostics kept outside the primary inference.",
    "traceability": "Claim-to-mechanism-to-every-required-metric decision traceability.",
    "resources": "Frozen compute, time, data, and runtime constraints.",
    "risks": "Known scientific and execution risks with boundaries.",
    "additional_sections": "Explicit supplementary design fields that do not fit another canonical field.",
    "diagnosis": "Readiness diagnosis emitted as a finding, never as an executable verdict.",
    "revised_hypothesis": "A scoped revision that preserves the user-owned claim boundary.",
    "mechanism_and_evidence": "Mechanism, supporting evidence, and limitations.",
    "boundary_conditions": "Conditions delimiting interpretation and generalization.",
    "alignment_contract": "Claim, dataset, control, metric, and decision-rule alignment.",
    "baseline_and_controls": "Intervention, executable control, and fixed controls.",
    "feasibility_risks": "Feasibility risks and their mitigations.",
    "staged_gates": "Static, overfit, smoke, pilot, and formal execution gates.",
    "formal_experiment_entry_conditions": "Conditions required before formal execution.",
    "positive_negative_inconclusive_rules": "Exhaustive preregistered multi-endpoint positive, negative, mixed, and uncertainty interpretation rules.",
    "remaining_unknowns": "Unknowns retained without silently becoming blockers.",
    "capacity_confounder": "Capacity confounder, control strategy, and justified claim boundary.",
    "local_dataset_loader_verification": "Loader verification procedure and failure policy.",
    "iteration_contract": "Bounded follow-up iteration contract.",
    "split_contract": "Reproducible split identity, disjointness, and test isolation.",
    "progressive_experiment": "Progressive experiment staging without changing formal inference.",
}

AUTHORITATIVE_PLAN_CONTRACT = CANONICAL_PLAN_CONTRACT_FIELDS

FIELD_ALIAS_TO_CANONICAL = {
    "research_question": "objective",
    "hypothesis": "hypotheses",
    "treatment_and_control": "baseline_and_controls",
    "dataset_identity": "dataset",
    "data_split": "split_contract",
    "split_identity": "split_contract",
    "training_and_validation_policy": "procedure",
    "metrics": "evaluations",
    "metrics_and_statistics": "evaluations",
    "baseline": "comparisons",
    "stopping_rule": "stop_conditions",
    "hypothesis_link": "original_question_link",
    "effect_size_justification": "statistical_summary",
    "execution_gates": "staged_gates",
    "outcome_rules": "positive_negative_inconclusive_rules",
}


def authoritative_plan_contract() -> dict[str, str]:
    """Return a copy safe to place in generator and reviewer prompt context."""
    return dict(AUTHORITATIVE_PLAN_CONTRACT)


def canonical_contract_field(value: object) -> str:
    field = str(value or "").strip()
    return FIELD_ALIAS_TO_CANONICAL.get(field, field)


def canonical_contract_fields(values: object) -> list[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    allowed = set(CANONICAL_PLAN_CONTRACT_FIELDS)
    return list(
        dict.fromkeys(
            canonical
            for item in values
            if (canonical := canonical_contract_field(item)) in allowed
        )
    )


def merge_plan_patch(base, patch_payload):
    """Overlay a patch-only revision output onto the full normalized candidate.

    The provider may wrap the patch in a top-level ``plan`` object; unwrap it, then
    overlay only canonical Plan Contract fields plus ``fix_map`` so no unrelated or
    provider-metadata key leaks into the merge.  Every untouched field keeps the
    candidate's exact value, so ``changed_contract_fields`` sees only the true patch.
    """
    payload = dict(patch_payload) if isinstance(patch_payload, dict) else {}
    inner = (
        payload["plan"]
        if isinstance(payload.get("plan"), dict)
        and not any(field in payload for field in _PLANNING_FIELDS)
        else payload
    )
    # fix_map may sit beside the "plan" wrapper; take it from either level.
    combined = dict(inner)
    if "fix_map" in payload:
        combined["fix_map"] = payload["fix_map"]
    allowed = set(CANONICAL_PLAN_CONTRACT_FIELDS) | {"fix_map"}
    overlay = {key: value for key, value in combined.items() if key in allowed}
    return {**dict(base or {}), **overlay}


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

    parameters = _as_dict(plan.get("parameters"))
    planned_epochs = canonical_training_epochs({"epochs": plan.get("epochs"), "parameters": parameters})
    if planned_epochs is not None:
        parameters["epochs"] = planned_epochs
        for alias in TRAINING_EPOCH_ALIASES[1:]:
            parameters.pop(alias, None)

    return {
        "objective": objective,
        "hypotheses": hypotheses,
        "primary_claim": _first_string(plan.get("primary_claim"), hypotheses[0] if hypotheses else ""),
        "original_question_link": _first_string(plan.get("original_question_link")),
        "secondary_endpoints": _string_list(plan.get("secondary_endpoints")),
        "method": _as_dict(plan.get("method")),
        "dataset": _dataset(plan.get("dataset")),
        "comparisons": comparisons,
        "evaluations": evaluations,
        "procedure": _as_dict(plan.get("procedure")),
        "parameters": parameters,
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
        "fix_map": _as_dict(plan.get("fix_map")),
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


def canonical_training_epochs(plan: object) -> int | None:
    """Return the model-authored formal epoch budget in canonical form.

    Historical providers used ``max_epochs`` or ``epochs_limit``.  Accept those
    spellings at the normalization boundary, but keep only ``parameters.epochs``
    in the durable Plan Contract so execution has one unambiguous source.
    """
    candidate = _as_dict(plan)
    parameters = _as_dict(candidate.get("parameters"))
    raw = candidate.get("epochs")
    if raw in (None, ""):
        raw = next(
            (parameters.get(name) for name in TRAINING_EPOCH_ALIASES if parameters.get(name) not in (None, "")),
            None,
        )
    if raw in (None, ""):
        return None
    if isinstance(raw, bool):
        raise ValueError("PLAN_TRAINING_EPOCHS_INVALID")
    try:
        numeric = float(raw)
        epochs = int(numeric)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("PLAN_TRAINING_EPOCHS_INVALID") from exc
    if numeric != epochs or epochs < 1 or epochs > 100_000:
        raise ValueError("PLAN_TRAINING_EPOCHS_INVALID")
    return epochs


def execution_training_budget(plan: object) -> dict[str, int | str] | None:
    """Return an executable budget without inventing epoch-loop semantics.

    Epoch-trained methods must declare ``parameters.epochs``.  A converged
    Single-fit sklearn estimators such as LogisticRegression and SVC instead
    have an iteration cap (``max_iter``), so they get a ``single_fit`` contract
    and one runtime pass without claiming that the estimator runs an epoch loop.
    """
    epochs = canonical_training_epochs(plan)
    if epochs is not None:
        return {"mode": "epochs", "epochs": epochs}

    candidate = _as_dict(plan)
    parameters = _as_dict(candidate.get("parameters"))
    max_iter = _positive_int(parameters.get("max_iter"))
    method_text = " ".join(_strings(candidate.get("method"))).casefold()
    compact_method = method_text.replace(" ", "")
    single_fit_method = any(
        name in compact_method
        for name in ("logisticregression", "linearsvc", "svc", "supportvectormachine", "svm")
    )
    if max_iter and single_fit_method:
        return {
            "mode": "single_fit",
            "fit_count": 1,
            "max_iter": max_iter,
            "runtime_passes": 1,
        }
    return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def _strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [str(item) for item in value.values()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value]
    return [str(value or "")]


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
