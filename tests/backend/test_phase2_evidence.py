import pytest

from backend.app.workflow.phase2_evidence import (
    approximate_reproduction,
    baseline_profile,
    dataset_profile,
    fair_experiment_contract,
    metric_direction,
    progressive_protocol,
    reproduction_check,
    result_evidence,
    route_result,
)


def inspected(columns):
    return {"contract_id": "dataset_test", "schemas": [{"columns": columns, "sampled_row_count": 12}], "file_count": 1, "content_fingerprint": "sha256:test"}


@pytest.mark.parametrize(("task_type", "columns", "required"), [
    ("classification", ["x", "label"], "class_distribution"),
    ("forecasting", ["timestamp", "value", "target"], "random_time_leakage_forbidden"),
    ("anomaly_detection", ["feature", "is_anomaly"], "threshold_evaluation_protocol"),
])
def test_three_task_dataset_profiles(task_type, columns, required):
    profile = dataset_profile(inspected(columns), {"task_type": task_type, "dataset_description": "fixture"})
    assert profile["dataset_profile_version"] == 2
    assert profile["task_profile"]["task_type"] == task_type
    assert required in profile["task_profile"]
    assert profile["user_description"] == "fixture"


def test_dataset_profile_keeps_observed_structure_for_the_persisted_artifact():
    observation = [{
        "relative_path": "clutter.mat",
        "filename": "clutter.mat",
        "format": "mat",
        "suffix": ".mat",
        "arrays": [{"key": "clutter", "shape": [18_000, 512], "dtype": "float32"}],
    }]
    profile = dataset_profile(
        {**inspected(["x", "label"]), "observed_structure": observation},
        {"task_type": "classification"},
    )

    assert profile["observed_structure"] == observation


def test_forecasting_order_evidence_controls_profile_without_forcing_incompatibility():
    with_column = dataset_profile(inspected(["timestamp", "value", "target"]), {"task_type": "forecasting"})
    assert with_column["task_profile"]["split_policy"] == "chronological_by_time_column"
    by_row = dataset_profile(inspected(["value", "target"]), {"task_type": "forecasting", "dataset_description": "Rows are chronological time ordered observations."})
    assert by_row["task_profile"]["split_policy"] == "chronological_by_row_order"
    unknown = dataset_profile(inspected(["value", "target"]), {"task_type": "forecasting"})
    assert unknown["task_profile"]["split_policy"] == "unknown"
    assert unknown["task_profile"]["compatibility_status"] == "needs_confirmation"
    assert unknown["task_profile"]["compatible"] is True
    assert unknown["task_profile"]["random_time_leakage_forbidden"] is True


def test_baseline_profile_priority_and_ten_percent_reproduction_logic():
    profile = baseline_profile({"baseline": {"name": "UserNet", "paper_metrics": {"accuracy": 0.80, "rmse": 2.0}}}, {}, inspected([]))
    assert profile["source"] == "user_specified"
    passing = reproduction_check(profile, {"accuracy": 0.86, "rmse": 2.18})
    assert passing["reproduction_status"] == "reproduced"
    # Accuracy uses absolute percentage points: 0.80 -> 0.71 is 9 points,
    # despite exceeding 10% relative deviation.
    percentage_points = reproduction_check(profile, {"accuracy": 0.71, "rmse": 2.18})
    accuracy = next(item for item in percentage_points["comparisons"] if item["metric"] == "accuracy")
    assert accuracy["deviation_type"] == "absolute_percentage_points"
    assert accuracy["within_10_percent"] is True
    rmse = next(item for item in percentage_points["comparisons"] if item["metric"] == "rmse")
    assert rmse["deviation_type"] == "relative_deviation"
    failing = reproduction_check(profile, {"accuracy": 0.60, "rmse": 2.5})
    assert failing["route"] == "baseline_diagnosis"
    approximate = approximate_reproduction(profile, failing, ["different preprocessing"])
    assert approximate["reproduction_status"] == "approximate_reproduction"
    assert approximate["possible_reasons"] == ["different preprocessing"]


def test_fair_contract_and_three_progressive_stages_are_independent():
    dataset = dataset_profile(inspected(["x", "label"]), {"task_type": "classification"})
    baseline = baseline_profile({"epochs": 20, "seed": [3, 5, 7]}, {}, dataset)
    contract = fair_experiment_contract(dataset, baseline, {"epochs": 20, "seed": [3, 5, 7], "primary_metrics": ["accuracy"], "secondary_metrics": ["f1"]}, {})
    smoke, small, formal = (progressive_protocol(contract, stage) for stage in ("smoke", "small_scale", "formal_validation"))
    assert smoke["stage"] == "smoke" and smoke["epochs"] == 1 and smoke["seeds"] == [3]
    assert small["stage"] == "small_scale" and small["seeds"] == [3, 5]
    assert formal["stage"] == "formal_validation" and formal["epochs"] == 20 and formal["seeds"] == [3, 5, 7]
    assert contract["evaluation_protocol"]["same_seed_pairing"] is True


def test_result_analyzer_statistics_direction_and_routes_are_deterministic():
    positive = result_evidence({1: .70, 2: .71, 3: .69, 4: .70}, {1: .80, 2: .81, 3: .79, 4: .80}, "accuracy")
    assert positive["route"] == "expand_validation"
    assert positive["positive_direction_count"] == 4
    assert positive["mean_delta"] == pytest.approx(.10)
    assert positive["confidence_interval_95"][0] > 0
    assert positive["effect_size"] > 0
    minimized = result_evidence({1: 2.0, 2: 2.1}, {1: 1.8, 2: 1.9}, "rmse")
    assert minimized["direction"] == "minimize" and minimized["mean_delta"] > 0
    ambiguous = result_evidence({1: .70, 2: .70, 3: .70, 4: .70}, {1: .71, 2: .69, 3: .71, 4: .69}, "accuracy")
    assert ambiguous["route"] == "add_seeds"
    negative = result_evidence({1: .8, 2: .8}, {1: .7, 2: .7}, "accuracy")
    assert negative["route"] == "scientific_review"
    assert route_result(ambiguous, seed_limit_reached=True) == "scientific_review"
    assert route_result(positive, anomalies=["NaN"]) == "engineering_diagnosis"
    assert metric_direction("mae") == "minimize"


def test_result_analyzer_requires_paired_seeds():
    evidence = result_evidence({1: .7}, {2: .8}, "accuracy")
    assert evidence["status"] == "not_comparable"
    assert evidence["route"] == "engineering_diagnosis"
