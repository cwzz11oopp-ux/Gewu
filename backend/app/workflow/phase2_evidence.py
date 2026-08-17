"""Phase 2 deterministic experiment evidence contracts.

No model call is made here.  These functions only normalize inspected facts,
freeze a comparable protocol, and calculate auditable statistics from recorded
per-seed metrics.
"""
from __future__ import annotations

from copy import deepcopy
from math import sqrt
from statistics import mean, median, stdev
from typing import Any

SUPPORTED_TASK_TYPES = {"classification", "forecasting", "anomaly_detection"}
LOWER_IS_BETTER = ("loss", "mse", "mae", "rmse", "error", "mape")
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
    primary = str(metrics[0]) if metrics else str((plan.get("metrics") or ["accuracy"])[0])
    secondary = constraints.get("secondary_metrics") or []
    seeds = constraints.get("seed") or constraints.get("seeds") or [7, 11]
    if isinstance(seeds, int): seeds = [seeds]
    epochs = constraints.get("epochs") or plan.get("epochs") or 1
    preprocessing = constraints.get("preprocessing") or plan.get("preprocessing") or {}
    return {"schema_version": 1, "dataset_contract_id": dataset.get("contract_id", ""), "split": deepcopy((dataset.get("task_profile") or {}).get("split") or {}), "preprocessing": deepcopy(preprocessing), "primary_metric": primary, "secondary_metrics": list(secondary), "seeds": list(seeds), "epochs": epochs, "training_config": deepcopy(baseline.get("training_config") or {}), "evaluation_protocol": {"same_seed_pairing": True, "same_baseline": baseline.get("name"), "task_type": (dataset.get("task_profile") or {}).get("task_type")}, "baseline": baseline.get("name"), "frozen": True}


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


def metric_direction(metric: str, explicit: str | None = None) -> str:
    if explicit in {"maximize", "minimize"}: return explicit
    return "minimize" if any(token in metric.lower() for token in LOWER_IS_BETTER) else "maximize"


def result_evidence(baseline_seed_metrics: dict[int, float], idea_seed_metrics: dict[int, float], metric: str, direction: str | None = None) -> dict[str, Any]:
    shared = sorted(set(baseline_seed_metrics) & set(idea_seed_metrics))
    if not shared:
        return {"schema_version": 1, "metric": metric, "status": "not_comparable", "reason": "PAIRED_SEEDS_REQUIRED", "route": "engineering_diagnosis"}
    baseline = [float(baseline_seed_metrics[seed]) for seed in shared]
    idea = [float(idea_seed_metrics[seed]) for seed in shared]
    orient = 1.0 if metric_direction(metric, direction) == "maximize" else -1.0
    deltas = [orient * (candidate - control) for control, candidate in zip(baseline, idea)]
    delta_mean = mean(deltas)
    delta_std = stdev(deltas) if len(deltas) > 1 else 0.0
    se = delta_std / sqrt(len(deltas)) if deltas else 0.0
    ci = [delta_mean - 1.96 * se, delta_mean + 1.96 * se]
    pooled = sqrt(((stdev(baseline) if len(baseline) > 1 else 0.0) ** 2 + (stdev(idea) if len(idea) > 1 else 0.0) ** 2) / 2)
    effect = delta_mean / pooled if pooled > 1e-12 else (0.0 if abs(delta_mean) < 1e-12 else float("inf"))
    noise = max(delta_std, pooled)
    positive = sum(value > 0 for value in deltas)
    status = "positive_stable" if delta_mean > 0 and ci[0] > 0 and positive / len(deltas) >= 0.75 else "inconclusive" if abs(delta_mean) <= max(noise, 1e-12) or ci[0] <= 0 <= ci[1] else "negative"
    route = {"positive_stable": "expand_validation", "inconclusive": "add_seeds", "negative": "scientific_review"}[status]
    return {"schema_version": 1, "metric": metric, "direction": metric_direction(metric, direction), "paired_seeds": shared, "baseline": {"mean": mean(baseline), "std": stdev(baseline) if len(baseline) > 1 else 0.0}, "idea": {"mean": mean(idea), "std": stdev(idea) if len(idea) > 1 else 0.0}, "paired_delta": {str(seed): delta for seed, delta in zip(shared, deltas)}, "mean_delta": delta_mean, "median_delta": median(deltas), "delta_std": delta_std, "positive_direction_count": positive, "positive_direction_ratio": positive / len(deltas), "confidence_interval_95": ci, "effect_size": effect, "noise_magnitude": noise, "status": status, "route": route}


def route_result(evidence: dict[str, Any], *, anomalies: list[str] | None = None, seed_limit_reached: bool = False) -> str:
    if anomalies: return "engineering_diagnosis"
    route = str(evidence.get("route") or "engineering_diagnosis")
    if route == "add_seeds" and seed_limit_reached: return "scientific_review"
    return route
