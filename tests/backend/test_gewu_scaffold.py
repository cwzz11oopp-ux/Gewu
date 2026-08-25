"""Regression tests for the deterministic Gewu runtime scaffold (Tier A).

Covers the four observed repair-loop failure classes:
  1. ``Namespace has no attribute 'batch_size'`` -- parameters not defined on args
  2. Missing aggregate ``expected_metrics`` keys in the seed result
  3. Hard-coded seed diverging from the harness-provided ``--seed``
  4. Smoke mode not honoring the one-epoch budget
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

from backend.app.providers.experiment_runtime import validate_result_payload
from backend.app.workflow.experiment_code import normalize_experiment_bundle
from backend.app.workflow.experiment_harness import (
    SCAFFOLD_SOURCE,
    compile_bundle_runtime_contract,
    compile_runtime_contract,
    harness_source,
)

PARAMS = {"batch_size": 128, "learning_rate": 0.01}

# Legacy codegen that hand-rolls argparse must still accept every protocol flag
# the Harness passes on the seed command line.
_LEGACY_TRAIN = (
    "import argparse\nimport json\nfrom pathlib import Path\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--run-id')\np.add_argument('--experiment-id')\n"
    "p.add_argument('--result-id')\np.add_argument('--output')\n"
    "p.add_argument('--seed', type=int)\np.add_argument('--smoke-test', action='store_true')\n"
    "a = p.parse_args()\nif a.smoke_test:\n    pass\n"
    "Path(a.output).parent.mkdir(parents=True, exist_ok=True)\n"
    "Path(a.output).write_text(json.dumps({{'seed': a.seed, 'metrics': {{{metrics_body}}}}}), encoding='utf-8')\n"
)


def _bundle(plan: dict, expected_metrics: list[str], source: str):
    return normalize_experiment_bundle(
        {
            "files": [{"path": "train.py", "content": source}],
            "dataset": "",
            "requirements": [],
            "expected_metrics": expected_metrics,
            "parameters": dict(PARAMS),
            "seeds": list(plan["seeds"]),
            "supports_smoke_test": True,
        },
        "run_1",
        "experiment_1",
        {"run_id": "run_1", "experiment_id": "experiment_1", "result_id": "experiment_1_result", "plan": plan},
    )


def _run_harness(tmp_path, plan: dict, expected_metrics: list[str], source: str, smoke: bool = False):
    bundle = _bundle(plan, expected_metrics, source)
    contract = compile_runtime_contract(plan, {}, bundle)
    (tmp_path / "train.py").write_text(source, encoding="utf-8")
    (tmp_path / contract.harness_filename).write_text(harness_source(contract), encoding="utf-8")
    argv = [sys.executable, contract.harness_filename]
    if smoke:
        argv.append("--smoke-test")
    completed = subprocess.run(argv, cwd=tmp_path, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    payload = json.loads((tmp_path / contract.result_output_path).read_text(encoding="utf-8"))
    return payload, contract, bundle


def _scaffold_module(tmp_path):
    path = tmp_path / "gewu_runtime.py"
    path.write_text(SCAFFOLD_SOURCE, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("gewu_runtime_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scaffold_source_is_deterministic_and_self_consistent():
    assert "uuid" not in SCAFFOLD_SOURCE.lower()
    # The embedded scaffold must itself be valid standalone Python.
    compile(SCAFFOLD_SOURCE, "gewu_runtime.py", "exec")
    for required in (
        "def get_args",
        "def is_smoke",
        "def epoch_budget",
        "def write_result",
        "def data_root",
    ):
        assert required in SCAFFOLD_SOURCE


# --- Failure 1: 'Namespace' object has no attribute 'batch_size' -------------


def test_scaffold_get_args_injects_frozen_parameters(tmp_path, monkeypatch):
    gr = _scaffold_module(tmp_path)
    monkeypatch.setenv("GEWU_PARAMETERS_JSON", json.dumps(PARAMS, sort_keys=True))
    args = gr.get_args()
    assert args.batch_size == 128
    assert args.learning_rate == 0.01
    assert args.parameters == PARAMS


def test_train_script_using_scaffold_reads_batch_size_without_crash(tmp_path):
    plan = {"seeds": [7], "epochs": 2, "parameters": dict(PARAMS)}
    source = (
        "import json\nimport gewu_runtime as gr\n"
        "args = gr.get_args()\n"
        "gr.write_result({'accuracy': 0.5 if args.batch_size == 128 else 0.0})\n"
    )
    payload, contract, bundle = _run_harness(tmp_path, plan, ["accuracy"], source, smoke=True)
    assert payload["metrics"]["accuracy"] == 0.5
    assert validate_result_payload(payload, bundle.manifest)["metrics"] == {"accuracy": 0.5}


# --- Failure 2: aggregate expected_metrics keys missing ----------------------


def test_scaffold_write_result_completes_aggregate_keys_from_primary_prefix(tmp_path):
    plan = {
        "seeds": [7],
        "epochs": 2,
        "parameters": dict(PARAMS),
    }
    expected = ["Top-1 Accuracy", "Total FLOPs"]
    source = (
        "import gewu_runtime as gr\n"
        "metrics = {'ECA_Top-1 Accuracy': 0.91, 'ECA_Total FLOPs': 456770176.0}\n"
        "gr.write_result(metrics, primary_prefix='ECA_')\n"
    )
    payload, _contract, bundle = _run_harness(tmp_path, plan, expected, source, smoke=True)
    assert payload["metrics"]["Top-1 Accuracy"] == pytest.approx(0.91)
    assert payload["metrics"]["ECA_Top-1 Accuracy"] == pytest.approx(0.91)
    assert payload["metrics"]["Total FLOPs"] == pytest.approx(456770176.0)
    assert validate_result_payload(payload, bundle.manifest)["metrics"]["Top-1 Accuracy"] == pytest.approx(0.91)


def test_harness_completes_unambiguous_aggregate_key_without_scaffold(tmp_path):
    """Legacy codegen: single variant prefix only -- harness promotes it."""
    plan = {"seeds": [7], "epochs": 2, "parameters": dict(PARAMS)}
    expected = ["accuracy"]
    source = _LEGACY_TRAIN.format(metrics_body="'ECA_accuracy': 0.9")
    payload, _contract, bundle = _run_harness(tmp_path, plan, expected, source, smoke=True)
    assert payload["metrics"]["accuracy"] == pytest.approx(0.9)
    assert payload["metrics"]["ECA_accuracy"] == pytest.approx(0.9)


def test_harness_does_not_guess_ambiguous_aggregate_key(tmp_path):
    """Baseline_ and ECA_ both present: no deterministic promotion, stays missing."""
    plan = {"seeds": [7], "epochs": 2, "parameters": dict(PARAMS)}
    source = _LEGACY_TRAIN.format(
        metrics_body="'Baseline_accuracy': 0.8, 'ECA_accuracy': 0.9"
    )
    bundle = _bundle(plan, ["accuracy"], source)
    contract = compile_runtime_contract(plan, {}, bundle)
    (tmp_path / "train.py").write_text(source, encoding="utf-8")
    (tmp_path / contract.harness_filename).write_text(harness_source(contract), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, contract.harness_filename, "--smoke-test"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    payload = json.loads((tmp_path / contract.result_output_path).read_text(encoding="utf-8"))
    assert completed.returncode == 0, completed.stderr
    assert "accuracy" not in payload["metrics"]  # ambiguous -> not silently guessed
    with pytest.raises(RuntimeError, match="EXPERIMENT_METRIC_MISSING"):
        validate_result_payload(payload, bundle.manifest)


# --- Failure 3: hard-coded seed diverging from --seed ------------------------


def test_scaffold_write_result_uses_harness_provided_seed(tmp_path):
    plan = {"seeds": [42], "epochs": 2, "parameters": dict(PARAMS)}
    source = (
        "import gewu_runtime as gr\n"
        "args = gr.get_args()\n"
        "metrics = {'accuracy': args.seed / 100.0}\n"
        "gr.write_result(metrics)\n"
    )
    payload, _contract, _bundle = _run_harness(tmp_path, plan, ["accuracy"], source, smoke=True)
    assert payload["seed_results"][0]["seed"] == 42
    assert payload["seed_results"][0]["metrics"]["accuracy"] == pytest.approx(0.42)


# --- Failure 4: smoke mode honoring the one-epoch budget ---------------------


def test_scaffold_smoke_budget_is_one_epoch(tmp_path):
    plan = {"seeds": [3, 5], "epochs": 20, "parameters": dict(PARAMS)}
    source = (
        "import json\nimport gewu_runtime as gr\n"
        "budget = gr.epoch_budget()\n"
        "records = [{'smoke': gr.is_smoke(), 'budget': budget, 'epochs': gr.epoch_budget()}]\n"
        "metrics = {'accuracy': 0.9}\n"
        "gr.write_result(metrics, epoch_metrics=[{'epoch': e, 'accuracy': 0.1 * e} for e in range(1, budget + 1)])\n"
    )
    smoke_payload, contract, bundle = _run_harness(tmp_path, plan, ["accuracy"], source, smoke=True)
    assert smoke_payload["runtime"]["mode"] == "smoke"
    assert smoke_payload["seeds"] == [3]
    assert smoke_payload["seed_results"][0]["epoch_metrics"] == [
        {"epoch": 1, "accuracy": pytest.approx(0.1)}
    ]

    formal_payload, _contract, _bundle = _run_harness(tmp_path, plan, ["accuracy"], source, smoke=False)
    assert formal_payload["runtime"]["mode"] == "full"
    assert formal_payload["seeds"] == [3, 5]
    # Every seed honored the formal 20-epoch budget for its own curve.
    for item in formal_payload["seed_results"]:
        assert len(item["epoch_metrics"]) == 20


def test_scaffold_write_result_rejects_non_finite_metrics(tmp_path, monkeypatch):
    gr = _scaffold_module(tmp_path)
    monkeypatch.setenv("GEWU_PARAMETERS_JSON", json.dumps(PARAMS, sort_keys=True))
    monkeypatch.setenv("GEWU_EXPECTED_METRICS_JSON", json.dumps(["accuracy"]))
    monkeypatch.setenv("GEWU_SEEDS_JSON", json.dumps([7]))
    monkeypatch.setattr(sys, "argv", ["train.py", "--seed", "7", "--output", "out.json"])
    with pytest.raises(ValueError, match="RESULT_METRIC_NON_FINITE"):
        gr.write_result({"accuracy": float("nan")})


def test_scaffold_epoch_metrics_sanitized_deterministically(tmp_path, monkeypatch):
    gr = _scaffold_module(tmp_path)
    monkeypatch.setenv("GEWU_PARAMETERS_JSON", json.dumps(PARAMS, sort_keys=True))
    monkeypatch.setenv("GEWU_EXPECTED_METRICS_JSON", json.dumps(["accuracy"]))
    monkeypatch.setattr(sys, "argv", ["train.py", "--seed", "7", "--output", str(tmp_path / "r.json")])
    payload = gr.write_result(
        {"accuracy": 0.9},
        epoch_metrics=[
            {"epoch": 1.0, "accuracy": 0.1, "bad": float("nan")},
            {"epoch": 2, "accuracy": 0.2},
            {"bad_row": True},
        ],
    )
    assert payload["epoch_metrics"] == [
        {"epoch": 1, "accuracy": 0.1},
        {"epoch": 2, "accuracy": 0.2},
    ]
