"""Phase 2 deterministic experiment evidence contracts.

No model call is made here.  These functions only normalize inspected facts,
freeze a comparable protocol, and calculate auditable statistics from recorded
per-seed metrics.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite, sqrt
from statistics import mean, median, stdev
from typing import Any

from backend.app.workflow.plan_contract import canonical_training_epochs

SUPPORTED_TASK_TYPES = {"classification", "forecasting", "anomaly_detection"}
LOWER_IS_BETTER = (
    "loss", "mse", "mae", "rmse", "error", "mape", "confusion",
    "misclassification",
)
PROPORTION_METRICS = {"accuracy", "acc", "f1", "f1_score", "f1score", "auc", "roc_auc", "auroc", "pd", "probability_of_detection"}


def dataset_profile(inspection: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    """Extend the existing read-only inspection with task-specific, explicit facts."""
    profile = deepcopy(inspection)
    task_type = str(constraints.get("task_type") or "classification").strip().lower()
    task_type = task_type if task_type in SUPPORTED_TASK_TYPES else "classification"
    constraint_dataset = constraints.get("dataset") if isinstance(constraints.get("dataset"), dict) else {}
    description = str(constraints.get("dataset_description") or constraint_dataset.get("description") or "")
    schemas = profile.get("schemas") or []
    columns = next((item.get("columns") for item in schemas if item.get("columns")), []) or []
    names = [str(item).lower() for item in columns]
    target = next((str(columns[index]) for index, name in enumerate(names) if name in {"label", "target", "class", "y", "anomaly", "is_anomaly"}), "")
    time_field = next((str(columns[index]) for index, name in enumerate(names) if name in {"time", "timestamp", "date", "datetime", "ds"}), "")
    missing = {str(column): "unknown" for column in columns}
    split = {"source": "official" if any("train" in str(item.get("relative_path", "")).lower() for item in schemas) else "generated_frozen", "stratified": task_type == "classification"}
    task = {
        "task_type": task_type,
        "compatible": bool(schemas),
        "compatibility_issues": [],
        "target_or_label": target or "unknown",
        "input_columns": [str(item) for item in columns if str(item) != target],
        "missing_values": missing,
        "split": split,
    }
    if task_type == "classification":
        task.update({"class_count": "unknown", "class_distribution": "unknown", "split_policy": "stratified"})
    elif task_type == "forecasting":
        description_normalized = description.casefold()
        row_order_confirmed = bool(
            constraints.get("time_order_confirmed")
            or constraint_dataset.get("time_order_confirmed")
            or any(token in description_normalized for token in (
                "chronological", "time ordered", "time-ordered", "ordered by time",
                "按时间顺序", "时间顺序", "时间排序", "时序排列",
            ))
        )
        explicitly_incompatible = bool(
            constraints.get("forecasting_compatible") is False
            or constraint_dataset.get("forecasting_compatible") is False
        )
        if time_field:
            split_policy, time_order, compatibility_status = "chronological_by_time_column", "verified_by_time_column", "compatible"
        elif row_order_confirmed:
            split_policy, time_order, compatibility_status = "chronological_by_row_order", "confirmed_by_user_description", "compatible"
        else:
            split_policy, time_order, compatibility_status = "unknown", "needs_confirmation", "needs_confirmation"
        if explicitly_incompatible:
            task["compatible"] = False
            task["compatibility_issues"].append("FORECASTING_EXPLICITLY_INCOMPATIBLE")
            compatibility_status = "incompatible"
        task.update({"time_field": time_field or "unknown", "time_order": time_order, "compatibility_status": compatibility_status, "input_length": "user_or_plan_required", "horizon": "user_or_plan_required", "split_policy": split_policy, "random_time_leakage_forbidden": True})
    else:
        task.update({"normal_anomaly_label": target or "unknown", "label_distribution": "unknown", "training_protocol": "train_on_normal_or_document_exception", "threshold_evaluation_protocol": "frozen_before_validation"})
    profile["dataset_profile_version"] = 2
    profile["sample_count"] = sum(int(item.get("declared_record_count") or item.get("sampled_row_count") or item.get("sampled_record_count") or 0) for item in schemas) or "unknown"
    profile["input_shape_or_columns"] = columns or [item.get("shape") for item in schemas if item.get("shape")] or "unknown"
    profile["user_description"] = description
    profile["task_profile"] = task
    return profile


def baseline_profile(constraints: dict[str, Any], plan: dict[str, Any], dataset: dict[str, Any], repository_url: str | None = None) -> dict[str, Any]:
    requested = constraints.get("baseline") or {}
    if isinstance(requested, str):
        requested = {"name": requested}
    requested = dict(requested) if isinstance(requested, dict) else {}
    source = "user_specified" if requested.get("name") else "repository" if repository_url else "literature_selected"
    name = str(requested.get("name") or (plan.get("baselines") or [{}])[0].get("name") or "documented_local_baseline")
    paper_metrics = requested.get("paper_metrics") or {}
    return {
        "schema_version": 1, "name": name, "source": source,
        "paper_or_repo_reference": requested.get("reference") or repository_url or "",
        "implementation_type": requested.get("implementation_type") or ("reuse_repository" if repository_url else "planned_local_implementation"),
        "training_config": deepcopy(requested.get("training_config") or plan.get("training_config") or {}),
        "paper_metrics": deepcopy(paper_metrics), "local_metrics": {},
        "reproduction_status": "pending" if paper_metrics else "local_baseline_only",
        "dataset_contract_id": dataset.get("contract_id", ""),
    }


def reproduction_check(profile: dict[str, Any], local_metrics: dict[str, float], tolerance: float = 0.10) -> dict[str, Any]:
    reported = profile.get("paper_metrics") or {}
    comparisons = []
    for metric, paper_value in reported.items():
        local_value = local_metrics.get(metric)
        if not isinstance(paper_value, (int, float)) or not isinstance(local_value, (int, float)):
            continue
        normalized_metric = str(metric).casefold().replace("-", "_").replace(" ", "_")
        absolute_difference = abs(float(local_value) - float(paper_value))
        if normalized_metric in PROPORTION_METRICS:
            # Scores stored in [0, 1] use 0.10; percentage-form scores use 10.
            percentage_point_tolerance = tolerance if max(abs(float(paper_value)), abs(float(local_value))) <= 1 else tolerance * 100
            within = absolute_difference <= percentage_point_tolerance
            comparisons.append({"metric": metric, "paper_value": paper_value, "local_value": local_value, "deviation_type": "absolute_percentage_points", "absolute_difference": absolute_difference, "tolerance": percentage_point_tolerance, "within_10_percent": within})
        else:
            deviation = absolute_difference / max(abs(float(paper_value)), 1e-12)
            comparisons.append({"metric": metric, "paper_value": paper_value, "local_value": local_value, "deviation_type": "relative_deviation", "relative_deviation": deviation, "tolerance": tolerance, "within_10_percent": deviation <= tolerance})
    accepted = bool(comparisons) and all(item["within_10_percent"] for item in comparisons)
    status = "reproduced" if accepted else "baseline_diagnosis_required" if comparisons else profile.get("reproduction_status", "local_baseline_only")
    return {"comparisons": comparisons, "reproduction_status": status, "route": "baseline_diagnosis" if status == "baseline_diagnosis_required" else "continue"}


def approximate_reproduction(profile: dict[str, Any], check: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    result = deepcopy(profile)
    result["local_metrics"] = {item["metric"]: item["local_value"] for item in check.get("comparisons", [])}
    result["reproduction_status"] = "approximate_reproduction"
    result["reproduction_deviation"] = deepcopy(check.get("comparisons") or [])
    result["possible_reasons"] = list(reasons)
    return result


def fair_experiment_contract(dataset: dict[str, Any], baseline: dict[str, Any], constraints: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    metrics = constraints.get("primary_metrics") or constraints.get("primary_metric") or []
    if isinstance(metrics, str): metrics = [metrics]
    evaluations = [
        item for item in (plan.get("evaluations") or [])
        if isinstance(item, dict) and str(item.get("metric") or "").strip()
    ]
    primary = str(
        metrics[0] if metrics else
        (evaluations[0].get("metric") if evaluations else (plan.get("metrics") or ["accuracy"])[0])
    )
    primary_metrics = list(dict.fromkeys(
        str(metric).strip() for metric in metrics if str(metric).strip()
    )) or [primary]
    primary_metric_directions = {}
    for metric in primary_metrics:
        matching_evaluation = next(
            (item for item in evaluations if str(item.get("metric") or "").strip() == metric),
            evaluations[0] if evaluations else {},
        )
        primary_metric_directions[metric] = metric_direction(
            metric,
            constraints.get("primary_metric_direction")
            if metric == primary else matching_evaluation.get("direction"),
        )
    primary_direction = primary_metric_directions[primary]
    secondary = constraints.get("secondary_metrics") or []
    seeds = (
        constraints.get("seed")
        or constraints.get("seeds")
        or plan.get("seeds")
        or [7, 11]
    )
    if isinstance(seeds, int): seeds = [seeds]
    epochs = canonical_training_epochs(plan)
    if epochs is None:
        raise ValueError("MODEL_PLANNED_TRAINING_EPOCHS_REQUIRED")
    preprocessing = constraints.get("preprocessing") or plan.get("preprocessing") or {}
    return {"schema_version": 1, "dataset_contract_id": dataset.get("contract_id", ""), "split": deepcopy((dataset.get("task_profile") or {}).get("split") or {}), "preprocessing": deepcopy(preprocessing), "primary_metric": primary, "primary_metric_direction": primary_direction, "primary_metrics": primary_metrics, "primary_metric_directions": primary_metric_directions, "secondary_metrics": list(secondary), "seeds": list(seeds), "epochs": epochs, "training_config": deepcopy(baseline.get("training_config") or {}), "statistical_summary": deepcopy(plan.get("statistical_summary") or {}), "evaluation_protocol": {"same_seed_pairing": True, "same_baseline": baseline.get("name"), "task_type": (dataset.get("task_profile") or {}).get("task_type")}, "baseline": baseline.get("name"), "frozen": True}


def progressive_protocol(contract: dict[str, Any], stage: str) -> dict[str, Any]:
    if stage not in {"smoke", "small_scale", "formal_validation"}:
        raise ValueError(f"PHASE2_STAGE_INVALID:{stage}")
    result = deepcopy(contract)
    seeds = list(contract.get("seeds") or [7])
    epochs = int(contract.get("epochs") or 1)
    if stage == "smoke":
        result.update({"stage": stage, "seeds": seeds[:1], "epochs": min(1, epochs), "purpose": "code_data_interface_only"})
    elif stage == "small_scale":
        result.update({"stage": stage, "seeds": seeds[:max(1, min(2, len(seeds)))], "epochs": max(1, min(epochs, max(1, epochs // 4))), "purpose": "paired_baseline_vs_idea_screening"})
    else:
        result.update({"stage": stage, "seeds": seeds, "epochs": epochs, "purpose": "paired_formal_validation"})
    return result


def paired_seed_metrics(
    result: dict[str, Any],
    primary_metric: str,
    expected_metrics: list[str] | None = None,
    comparisons: list[dict[str, Any]] | None = None,
) -> tuple[dict[int, float], dict[int, float]]:
    """Derive per-seed baseline and idea measurements from a harness result.

    The harness records each seed's flat metric dict (e.g. baseline_accuracy,
    se_accuracy, and the primary metric such as Overall Accuracy).  Both sides
    must name the requested primary metric; a generic ``Baseline_`` key is not
    sufficient because a result can also contain baseline loss or resource
    metrics.  Returns ({seed: value}, {seed: value}); empty dicts when no safe
    pairing can be derived, so missing or ambiguous pairs stay explicit.
    """
    seed_results = result.get("seed_results") or []
    if not seed_results:
        return {}, {}
    first_metrics = seed_results[0].get("metrics") or {}
    keys = [str(key) for key in first_metrics.keys()]
    if not keys:
        return {}, {}
    token = lambda value: "".join(
        char for char in str(value or "").casefold() if char.isalnum()
    )
    primary = token(primary_metric or "accuracy")
    key_tokens = {key: token(key) for key in keys}
    baseline_candidates = [
        key for key in keys
        if "baseline" in key.casefold() and primary in key_tokens[key]
    ]
    baseline_key = baseline_candidates[0] if len(baseline_candidates) == 1 else None
    idea_key = None
    for name in expected_metrics or []:
        candidate = str(name)
        if (
            candidate in keys
            and primary in token(candidate)
            and candidate != baseline_key
        ):
            idea_key = candidate
            break
    exact_idea_candidates = [key for key in keys if key_tokens[key] == primary]
    if idea_key is None and len(exact_idea_candidates) == 1:
        idea_key = exact_idea_candidates[0]
    # Multi-arm experiments use named prefixes rather than literal
    # baseline_/idea_ keys. The Plan's final comparison is the primary contrast.
    for comparison in reversed(comparisons or []):
        if not isinstance(comparison, dict):
            continue
        baseline_prefix = token(comparison.get("baseline"))
        idea_prefix = token(comparison.get("variant"))
        candidate_baselines = [
            key for key in keys if key_tokens[key] == baseline_prefix + primary
        ]
        candidate_ideas = [
            key for key in keys if key_tokens[key] == idea_prefix + primary
        ]
        candidate_baseline = (
            candidate_baselines[0] if len(candidate_baselines) == 1 else None
        )
        candidate_idea = candidate_ideas[0] if len(candidate_ideas) == 1 else None
        if candidate_baseline and candidate_idea:
            baseline_key, idea_key = candidate_baseline, candidate_idea
            break
    if idea_key is None:
        fallback_idea_candidates = []
        for key in keys:
            normalized = key_tokens[key]
            if key == baseline_key or primary not in normalized:
                continue
            if any(token in normalized for token in ("params", "flops", "pairwise", "error")):
                continue
            fallback_idea_candidates.append(key)
        if len(fallback_idea_candidates) == 1:
            idea_key = fallback_idea_candidates[0]
    if not baseline_key or not idea_key or baseline_key == idea_key:
        return {}, {}
    baseline_rows: dict[int, float] = {}
    idea_rows: dict[int, float] = {}
    for item in seed_results:
        metrics = item.get("metrics") or {}
        seed = item.get("seed")
        if seed is None or baseline_key not in metrics or idea_key not in metrics:
            continue
        try:
            baseline_rows[int(seed)] = float(metrics[baseline_key])
            idea_rows[int(seed)] = float(metrics[idea_key])
        except (AttributeError, TypeError, ValueError):
            continue
    return baseline_rows, idea_rows


def metric_direction(metric: str, explicit: str | None = None) -> str:
    normalized = str(explicit or "").strip().casefold()
    if normalized in {"maximize", "max", "higher is better", "larger is better", "越大越好", "越高越好", "越多越好"}:
        return "maximize"
    if normalized in {"minimize", "min", "lower is better", "smaller is better", "越小越好", "越低越好", "越少越好"}:
        return "minimize"
    return "minimize" if any(token in metric.lower() for token in LOWER_IS_BETTER) else "maximize"


def _paired_t_test(deltas: list[float]) -> tuple[dict[str, Any], list[float]]:
    """Calculate the planned paired test after all seed results are available."""
    count = len(deltas)
    unavailable = {
        "method": "paired_t_test",
        "pairing_unit": "seed",
        "difference_orientation": "positive_favors_variant",
        "alternative": "two-sided",
        "n_pairs": count,
        "degrees_of_freedom": max(0, count - 1),
        "statistic": None,
        "p_value": None,
        "alpha": 0.05,
        "alpha_source": "system_default",
        "significant": None,
        "status": "unavailable",
    }
    if count < 2:
        return {**unavailable, "reason": "AT_LEAST_TWO_PAIRED_SEEDS_REQUIRED"}, []

    delta_mean = mean(deltas)
    delta_std = stdev(deltas)
    standard_error = delta_std / sqrt(count)
    if standard_error <= 1e-15:
        # A constant paired difference makes the usual t statistic degenerate.
        # Keep the condition explicit instead of serializing NaN or Infinity.
        return {
            **unavailable,
            "status": "degenerate_zero_variance",
            "reason": "ALL_PAIRED_DIFFERENCES_IDENTICAL",
        }, [delta_mean, delta_mean]

    statistic = delta_mean / standard_error
    try:
        from scipy.stats import t as student_t
    except ImportError:
        return {
            **unavailable,
            "statistic": statistic,
            "reason": "SCIPY_REQUIRED_FOR_STUDENT_T_DISTRIBUTION",
        }, []

    degrees_of_freedom = count - 1
    p_value = float(2 * student_t.sf(abs(statistic), degrees_of_freedom))
    critical_value = float(student_t.ppf(0.975, degrees_of_freedom))
    interval = [
        delta_mean - critical_value * standard_error,
        delta_mean + critical_value * standard_error,
    ]
    if not all(isfinite(value) for value in (statistic, p_value, *interval)):
        return {**unavailable, "reason": "NON_FINITE_TEST_RESULT"}, []
    return {
        "method": "paired_t_test",
        "pairing_unit": "seed",
        "difference_orientation": "positive_favors_variant",
        "alternative": "two-sided",
        "n_pairs": count,
        "degrees_of_freedom": degrees_of_freedom,
        "statistic": statistic,
        "p_value": p_value,
        "alpha": 0.05,
        "alpha_source": "system_default",
        "significant": p_value < 0.05,
        "status": "computed",
        "small_sample_warning": count < 5,
    }, interval


def result_evidence(baseline_seed_metrics: dict[int, float], idea_seed_metrics: dict[int, float], metric: str, direction: str | None = None) -> dict[str, Any]:
    shared = sorted(set(baseline_seed_metrics) & set(idea_seed_metrics))
    if not shared:
        return {"schema_version": 1, "metric": metric, "status": "not_comparable", "reason": "PAIRED_SEEDS_REQUIRED", "route": "engineering_diagnosis", "paired_t_test": {"method": "paired_t_test", "status": "unavailable", "reason": "PAIRED_SEEDS_REQUIRED", "n_pairs": 0}}
    baseline = [float(baseline_seed_metrics[seed]) for seed in shared]
    idea = [float(idea_seed_metrics[seed]) for seed in shared]
    orient = 1.0 if metric_direction(metric, direction) == "maximize" else -1.0
    deltas = [orient * (candidate - control) for control, candidate in zip(baseline, idea)]
    delta_mean = mean(deltas)
    delta_std = stdev(deltas) if len(deltas) > 1 else 0.0
    paired_test, ci = _paired_t_test(deltas)
    paired_test["metric"] = metric
    paired_test["paired_seed_ids"] = shared
    if not ci:
        se = delta_std / sqrt(len(deltas)) if deltas else 0.0
        ci = [delta_mean - 1.96 * se, delta_mean + 1.96 * se]
    pooled = sqrt(((stdev(baseline) if len(baseline) > 1 else 0.0) ** 2 + (stdev(idea) if len(idea) > 1 else 0.0) ** 2) / 2)
    effect = delta_mean / pooled if pooled > 1e-12 else (0.0 if abs(delta_mean) < 1e-12 else float("inf"))
    noise = max(delta_std, pooled)
    positive = sum(value > 0 for value in deltas)
    status = "positive_stable" if delta_mean > 0 and ci[0] > 0 and positive / len(deltas) >= 0.75 else "inconclusive" if abs(delta_mean) <= max(noise, 1e-12) or ci[0] <= 0 <= ci[1] else "negative"
    route = {"positive_stable": "expand_validation", "inconclusive": "add_seeds", "negative": "scientific_review"}[status]
    return {"schema_version": 1, "metric": metric, "direction": metric_direction(metric, direction), "paired_seeds": shared, "baseline": {"mean": mean(baseline), "std": stdev(baseline) if len(baseline) > 1 else 0.0}, "idea": {"mean": mean(idea), "std": stdev(idea) if len(idea) > 1 else 0.0}, "paired_delta": {str(seed): delta for seed, delta in zip(shared, deltas)}, "mean_delta": delta_mean, "median_delta": median(deltas), "delta_std": delta_std, "positive_direction_count": positive, "positive_direction_ratio": positive / len(deltas), "confidence_interval_95": ci, "confidence_interval_method": "student_t" if paired_test.get("status") == "computed" else "normal_approximation", "paired_t_test": paired_test, "effect_size": effect, "noise_magnitude": noise, "status": status, "route": route}


def route_result(evidence: dict[str, Any], *, anomalies: list[str] | None = None, seed_limit_reached: bool = False) -> str:
    if anomalies: return "engineering_diagnosis"
    route = str(evidence.get("route") or "engineering_diagnosis")
    if route == "add_seeds" and seed_limit_reached: return "scientific_review"
    return route
