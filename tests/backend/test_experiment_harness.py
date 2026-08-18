import json
import subprocess
import sys

import pytest

from backend.app.agents.experiment import ExperimentAgent
from backend.app.providers.experiment_runtime import validate_result_payload
from backend.app.workflow.experiment_code import normalize_experiment_bundle
from backend.app.workflow.experiment_harness import (
    compile_bundle_runtime_contract,
    compile_runtime_contract,
    harness_source,
)


def _plan() -> dict:
    return {
        "dataset": {
            "canonical_name": "fashion-mnist",
            "contract_id": "dataset_fashion",
            "content_fingerprint": "sha256:fashion",
            "root": "D:/Gewu/datasets/fashionmnist",
        },
        "seeds": [7, 11],
        "parameters": {"learning_rate": 0.01},
        "iteration_contract": {"required_metrics": ["accuracy"]},
    }


def _bundle(plan: dict | None = None):
    plan = plan or _plan()
    return normalize_experiment_bundle(
        {
            "files": [{"path": "train.py", "content": "import os\nroot = os.environ['DATA_ROOT']\n"}],
            "dataset": "fashion-mnist",
            "requirements": [],
            "expected_metrics": ["accuracy"],
            "parameters": {"learning_rate": 0.01},
            "seeds": [7, 11],
        },
        "run_1",
        "experiment_1",
        {"run_id": "run_1", "experiment_id": "experiment_1", "result_id": "experiment_1_result", "plan": plan},
    )


def test_runtime_contract_compilation_and_harness_source_are_deterministic():
    plan = _plan()
    bundle = _bundle(plan)
    first = compile_runtime_contract(plan, {"run_id": "run_1"}, bundle)
    second = compile_runtime_contract(plan, {"run_id": "run_1"}, bundle)

    assert first == second
    assert harness_source(first) == harness_source(second)
    assert first.contract_sha256
    assert "uuid" not in harness_source(first).lower()


def test_compiled_contract_owns_dataset_seed_and_parameter_bindings():
    plan = _plan()
    bundle = compile_bundle_runtime_contract(plan, {}, _bundle(plan))
    contract = bundle.runtime_contract

    assert contract is not None
    assert contract.dataset_contract_id == "dataset_fashion"
    assert contract.dataset_fingerprint == "sha256:fashion"
    assert contract.expected_data_root == "D:/Gewu/datasets/fashionmnist"
    assert contract.seeds == [7, 11]
    assert contract.parameters == {"learning_rate": 0.01}

    tampered = bundle.model_copy(
        update={"runtime_contract": contract.model_copy(update={"seeds": [999]})}
    )
    with pytest.raises(ValueError, match="EXPERIMENT_RUNTIME_CONTRACT_MISMATCH"):
        ExperimentAgent.validate_bundle(plan, tampered)


def test_harness_rejects_unverified_dataset_binding(tmp_path):
    plan = _plan()
    contract = compile_runtime_contract(plan, {}, _bundle(plan))
    harness = tmp_path / contract.harness_filename
    harness.write_text(harness_source(contract), encoding="utf-8")

    failed = subprocess.run(
        [sys.executable, str(harness)],
        cwd=tmp_path,
        env={"DATA_ROOT": "D:/other", "DATASET_CONTRACT_ID": "other", "DATASET_FINGERPRINT": "other", "GEWU_VERIFIED_DATA_ROOT": "D:/other"},
        capture_output=True,
        text=True,
    )
    assert failed.returncode != 0
    assert "HARNESS_DATASET_CONTRACT_MISMATCH" in failed.stderr


def test_local_candidate_cannot_override_data_root_or_contract():
    plan = _plan()
    bundle = compile_bundle_runtime_contract(plan, {}, _bundle(plan))
    overridden_source = "import os\nDATA_ROOT = 'D:/other'\nroot = os.environ['DATA_ROOT']\n"
    overridden = bundle.model_copy(
        update={"files": [bundle.files[0].model_copy(update={"content": overridden_source})]}
    )
    with pytest.raises(ValueError, match="EXPERIMENT_DATASET_ROOT_OVERRIDE_FORBIDDEN"):
        ExperimentAgent.validate_bundle(plan, overridden)

    contract = bundle.runtime_contract
    assert contract is not None
    tampered = bundle.model_copy(
        update={"runtime_contract": contract.model_copy(update={"dataset_contract_id": "other"})}
    )
    with pytest.raises(ValueError, match="EXPERIMENT_RUNTIME_CONTRACT_MISMATCH"):
        ExperimentAgent.validate_bundle(plan, tampered)


def test_harness_owns_final_result_identity_and_output_location(tmp_path):
    plan = {
        "seeds": [7],
        "parameters": {"learning_rate": 0.01},
        "iteration_contract": {"required_metrics": ["test_accuracy"]},
    }
    source = (
        "import argparse\nimport json\nfrom pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--run-id')\np.add_argument('--experiment-id')\n"
        "p.add_argument('--result-id')\np.add_argument('--output')\n"
        "p.add_argument('--seed', type=int)\np.add_argument('--smoke-test', action='store_true')\n"
        "a = p.parse_args()\nif a.smoke_test:\n    pass\nPath(a.output).parent.mkdir(parents=True, exist_ok=True)\n"
        "Path(a.output).write_text(json.dumps({'run_id': 'forged', 'experiment_id': 'forged', 'result_id': 'forged', 'seed': a.seed, 'metrics': {'test_' + 'accuracy': 0.9}}), encoding='utf-8')\n"
    )
    bundle = normalize_experiment_bundle(
        {
            "files": [{"path": "train.py", "content": source}],
            "expected_metrics": ["test_accuracy"],
            "parameters": {"learning_rate": 0.01},
            "seeds": [7],
            "python_args": ["--output", "attacker.json"],
            "supports_smoke_test": True,
        },
        "run_1",
        "experiment_1",
        {"plan": plan},
    )
    contract = compile_runtime_contract(plan, {}, bundle)
    (tmp_path / "train.py").write_text(source, encoding="utf-8")
    (tmp_path / contract.harness_filename).write_text(harness_source(contract), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, contract.harness_filename, "--smoke-test"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    final_path = tmp_path / contract.result_output_path
    payload = json.loads(final_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run_1"
    assert payload["experiment_id"] == "experiment_1"
    assert payload["result_id"] == "experiment_1_result"
    assert payload["runtime"]["mode"] == "smoke"
    assert not (tmp_path / "attacker.json").exists()
    assert validate_result_payload(payload, bundle.manifest)["metrics"] == {"test_accuracy": 0.9}


def test_harness_executes_and_aggregates_every_formal_seed(tmp_path):
    plan = {
        "seeds": [3, 5, 7],
        "parameters": {"learning_rate": 0.01},
        "iteration_contract": {"required_metrics": ["accuracy"]},
    }
    source = (
        "import argparse\nimport json\nfrom pathlib import Path\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--run-id')\np.add_argument('--experiment-id')\n"
        "p.add_argument('--result-id')\np.add_argument('--output')\n"
        "p.add_argument('--seed', type=int)\np.add_argument('--smoke-test', action='store_true')\n"
        "a = p.parse_args()\nif a.smoke_test:\n    pass\nPath(a.output).parent.mkdir(parents=True, exist_ok=True)\n"
        "Path(a.output).write_text(json.dumps({'seed': a.seed, 'metrics': {'accuracy': a.seed / 10}}), encoding='utf-8')\n"
    )
    bundle = normalize_experiment_bundle(
        {
            "files": [{"path": "train.py", "content": source}],
            "expected_metrics": ["accuracy"],
            "parameters": {"learning_rate": 0.01},
            "seeds": [3, 5, 7],
            "python_args": [],
            "supports_smoke_test": True,
        },
        "run_1",
        "experiment_1",
        {"plan": plan},
    )
    contract = compile_runtime_contract(plan, {}, bundle)
    (tmp_path / "train.py").write_text(source, encoding="utf-8")
    (tmp_path / contract.harness_filename).write_text(harness_source(contract), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, contract.harness_filename], cwd=tmp_path, capture_output=True, text=True
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads((tmp_path / contract.result_output_path).read_text(encoding="utf-8"))
    assert payload["runtime"]["mode"] == "full"
    assert payload["seeds"] == [3, 5, 7]
    assert [item["seed"] for item in payload["seed_results"]] == [3, 5, 7]
    assert payload["metrics"]["accuracy"] == pytest.approx(0.5)
    assert payload["metric_summary"]["accuracy"]["std"] == pytest.approx(0.2)


def test_harness_binds_smoke_small_scale_and_formal_budgets(tmp_path):
    plan = {
        "seeds": [3, 5, 7],
        "epochs": 20,
        "parameters": {"learning_rate": 0.01},
    }
    source = (
        "import argparse\nimport json\nimport os\nfrom pathlib import Path\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--run-id')\np.add_argument('--experiment-id')\n"
        "p.add_argument('--result-id')\np.add_argument('--output')\n"
        "p.add_argument('--seed', type=int)\np.add_argument('--smoke-test', action='store_true')\n"
        "a=p.parse_args()\n"
        "record={'seed': a.seed, 'smoke': a.smoke_test, 'stage': os.environ['GEWU_EXECUTION_STAGE'], 'epochs': os.environ['GEWU_EXECUTION_EPOCHS']}\n"
        "with Path('calls.jsonl').open('a', encoding='utf-8') as f: f.write(json.dumps(record) + '\\n')\n"
        "Path(a.output).parent.mkdir(parents=True, exist_ok=True)\n"
        "Path(a.output).write_text(json.dumps({'seed': a.seed, 'metrics': {'accuracy': 0.9}}), encoding='utf-8')\n"
    )
    bundle = normalize_experiment_bundle(
        {
            "files": [{"path": "train.py", "content": source}],
            "expected_metrics": ["accuracy"],
            "parameters": {"learning_rate": 0.01},
            "seeds": [3, 5, 7],
            "supports_smoke_test": True,
        },
        "run_1",
        "experiment_1",
        {"plan": plan},
    )
    small_task = {"phase2_protocol": {"stage": "small_scale", "seeds": [3, 5], "epochs": 5}}
    small = compile_runtime_contract(plan, small_task, bundle)
    assert small.stage == "small_scale" and small.seeds == [3, 5] and small.epochs == 5
    (tmp_path / "train.py").write_text(source, encoding="utf-8")
    (tmp_path / small.harness_filename).write_text(harness_source(small), encoding="utf-8")

    smoke = subprocess.run([sys.executable, small.harness_filename, "--smoke-test"], cwd=tmp_path, capture_output=True, text=True)
    small_run = subprocess.run([sys.executable, small.harness_filename], cwd=tmp_path, capture_output=True, text=True)
    assert smoke.returncode == 0, smoke.stderr
    assert small_run.returncode == 0, small_run.stderr
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert calls == [
        {"seed": 3, "smoke": True, "stage": "smoke", "epochs": "1"},
        {"seed": 3, "smoke": False, "stage": "small_scale", "epochs": "5"},
        {"seed": 5, "smoke": False, "stage": "small_scale", "epochs": "5"},
    ]

    formal_task = {"phase2_protocol": {"stage": "formal_validation", "seeds": [3, 5, 7], "epochs": 20}}
    formal = compile_runtime_contract(plan, formal_task, bundle)
    (tmp_path / formal.harness_filename).write_text(harness_source(formal), encoding="utf-8")
    formal_run = subprocess.run([sys.executable, formal.harness_filename], cwd=tmp_path, capture_output=True, text=True)
    assert formal_run.returncode == 0, formal_run.stderr
    formal_calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()][3:]
    assert formal_calls == [
        {"seed": seed, "smoke": False, "stage": "formal_validation", "epochs": "20"}
        for seed in [3, 5, 7]
    ]


def test_runtime_audit_rejects_single_seed_result_for_multiseed_contract():
    plan = _plan()
    bundle = compile_bundle_runtime_contract(plan, {}, _bundle(plan))
    result = {
        "run_id": "run_1",
        "experiment_id": "experiment_1",
        "result_id": "experiment_1_result",
        "metrics": {"accuracy": 0.9},
        "runtime": {"mode": "full"},
        "seeds": [7],
        "seed_results": [{"seed": 7, "metrics": {"accuracy": 0.9}}],
        "environment": {"cuda_available": True},
        "attempts": [{"status": "completed"}],
        "is_real_experiment": True,
    }

    audit = ExperimentAgent(None).audit_result(bundle, result)

    assert audit["integrity_status"] == "failed"
    assert "FORMAL_SEED_SET_MISMATCH" in audit["issues"]
    assert "FORMAL_SEED_RESULT_LINEAGE_MISMATCH" in audit["issues"]


def test_runtime_result_validation_still_rejects_missing_or_nonfinite_metrics():
    manifest = _bundle(_plan()).manifest
    with pytest.raises(RuntimeError, match="EXPERIMENT_METRIC_MISSING:accuracy"):
        validate_result_payload(
            {"run_id": "run_1", "experiment_id": "experiment_1", "result_id": "experiment_1_result", "metrics": {}},
            manifest,
        )
    with pytest.raises(RuntimeError, match="EXPERIMENT_METRIC_NON_FINITE:accuracy"):
        validate_result_payload(
            {"run_id": "run_1", "experiment_id": "experiment_1", "result_id": "experiment_1_result", "metrics": {"accuracy": float("nan")}},
            manifest,
        )
