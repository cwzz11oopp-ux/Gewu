"""Deterministic runtime contract compiler and system-owned Bundle harness."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from backend.app.models.experiment import (
    ExperimentBundle,
    ExperimentRuntimeContract,
)


def compile_runtime_contract(
    plan: dict[str, Any], task: dict[str, Any], bundle: ExperimentBundle
) -> ExperimentRuntimeContract:
    """Compile trusted upstream state into a canonical, hash-addressed contract.

    The candidate Bundle supplies only the scientific implementation.  Values in
    this contract are bound to the task/accepted plan and are later injected by
    the system harness rather than redefined by generated source.
    """
    manifest = bundle.manifest
    dataset = plan.get("dataset") or {}
    iteration = plan.get("iteration_contract") or {}
    expected_metrics = list(manifest.expected_metrics)
    required_metrics = [
        str(metric).strip()
        for metric in iteration.get("required_metrics") or []
        if str(metric).strip()
    ]
    implementation_output = f"results/{manifest.result_id}.implementation.json"
    payload = {
        "schema_version": 1,
        "run_id": str(task.get("run_id") or manifest.run_id),
        "experiment_id": str(task.get("experiment_id") or manifest.experiment_id),
        "result_id": str(task.get("result_id") or manifest.result_id),
        "dataset": manifest.dataset,
        "dataset_contract_id": str(dataset.get("contract_id") or manifest.dataset_contract_id),
        "dataset_fingerprint": str(dataset.get("content_fingerprint") or manifest.dataset_fingerprint),
        "expected_data_root": str(dataset.get("root") or task.get("dataset_root") or ""),
        "requires_gpu": bool(manifest.requires_gpu),
        "seeds": list(manifest.seeds),
        "parameters": dict(manifest.parameters),
        "expected_metrics": expected_metrics,
        "iteration_required_metrics": required_metrics,
        "supports_smoke_test": bool(manifest.supports_smoke_test),
        "implementation_entrypoint": manifest.entrypoint,
        "implementation_output_path": implementation_output,
        "result_output_path": f"results/{manifest.result_id}.json",
        "harness_filename": ".gewu_harness.py",
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ExperimentRuntimeContract(
        **payload,
        contract_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def compile_bundle_runtime_contract(
    plan: dict[str, Any], task: dict[str, Any], bundle: ExperimentBundle
) -> ExperimentBundle:
    return bundle.model_copy(update={"runtime_contract": compile_runtime_contract(plan, task, bundle)})


def canonical_contract_json(contract: ExperimentRuntimeContract) -> str:
    return json.dumps(
        contract.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def harness_source(contract: ExperimentRuntimeContract) -> str:
    """Return byte-stable harness source. No UUID, clock, or model text is used."""
    payload = canonical_contract_json(contract)
    return f'''from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys

CONTRACT = json.loads({payload!r})


def _require_verified_context() -> None:
    contract_id = CONTRACT["dataset_contract_id"]
    if contract_id:
        if os.environ.get("DATASET_CONTRACT_ID") != contract_id:
            raise RuntimeError("HARNESS_DATASET_CONTRACT_MISMATCH")
        if os.environ.get("DATASET_FINGERPRINT") != CONTRACT["dataset_fingerprint"]:
            raise RuntimeError("HARNESS_DATASET_FINGERPRINT_MISMATCH")
        if os.environ.get("GEWU_VERIFIED_DATA_ROOT") != CONTRACT["expected_data_root"]:
            raise RuntimeError("HARNESS_DATA_ROOT_BINDING_MISMATCH")
    if CONTRACT["dataset"] and not os.environ.get("DATA_ROOT"):
        raise RuntimeError("HARNESS_DATA_ROOT_MISSING")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    _require_verified_context()
    final_output = Path(CONTRACT["result_output_path"])
    final_output.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["GEWU_RUNTIME_CONTRACT_SHA256"] = CONTRACT["contract_sha256"]
    environment["GEWU_SEEDS_JSON"] = json.dumps(CONTRACT["seeds"], sort_keys=True)
    environment["GEWU_PARAMETERS_JSON"] = json.dumps(CONTRACT["parameters"], sort_keys=True)
    command = [
        sys.executable,
        CONTRACT["implementation_entrypoint"],
        "--run-id", CONTRACT["run_id"],
        "--experiment-id", CONTRACT["experiment_id"],
        "--result-id", CONTRACT["result_id"],
    ]
    requested_seeds = list(CONTRACT["seeds"])
    # Smoke is intentionally a one-seed preflight.  A formal execution must
    # execute every contract seed and preserve each raw seed result so that
    # downstream statistics cannot mistake a pilot for the full experiment.
    execution_seeds = requested_seeds[:1] if args.smoke_test else requested_seeds
    if not execution_seeds:
        raise RuntimeError("HARNESS_SEEDS_REQUIRED")
    seed_results = []
    for seed in execution_seeds:
        implementation_output = Path(
            f"results/{{CONTRACT['result_id']}}.implementation.seed_{{seed}}.json"
        )
        implementation_output.parent.mkdir(parents=True, exist_ok=True)
        seed_command = [
            *command,
            "--output", str(implementation_output),
            "--seed", str(seed),
        ]
        if args.smoke_test:
            seed_command.append("--smoke-test")
        completed = subprocess.run(seed_command, check=False, env=environment)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        try:
            candidate = json.loads(implementation_output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("HARNESS_IMPLEMENTATION_RESULT_INVALID") from exc
        if not isinstance(candidate, dict) or candidate.get("seed") != seed:
            raise RuntimeError("HARNESS_IMPLEMENTATION_SEED_MISMATCH")
        metrics = candidate.get("metrics")
        if not isinstance(metrics, dict):
            raise RuntimeError("HARNESS_IMPLEMENTATION_METRICS_INVALID")
        numeric_metrics = {{}}
        for name, value in metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise RuntimeError(f"HARNESS_IMPLEMENTATION_METRIC_INVALID:{{name}}")
            numeric_metrics[str(name)] = float(value)
        seed_results.append({{"seed": seed, "metrics": numeric_metrics}})
    metric_names = set(seed_results[0]["metrics"])
    if any(set(item["metrics"]) != metric_names for item in seed_results[1:]):
        raise RuntimeError("HARNESS_IMPLEMENTATION_METRIC_SET_MISMATCH")
    metrics = {{
        name: statistics.fmean(item["metrics"][name] for item in seed_results)
        for name in sorted(metric_names)
    }}
    metric_summary = {{
        name: {{
            "mean": metrics[name],
            "std": (statistics.stdev(item["metrics"][name] for item in seed_results) if len(seed_results) > 1 else 0.0),
        }}
        for name in sorted(metric_names)
    }}
    envelope = {{
        "run_id": CONTRACT["run_id"],
        "experiment_id": CONTRACT["experiment_id"],
        "result_id": CONTRACT["result_id"],
        "metrics": metrics,
        "seeds": execution_seeds,
        "seed_results": seed_results,
        "metric_summary": metric_summary,
        "runtime": {{
            "contract_sha256": CONTRACT["contract_sha256"],
            "mode": "smoke" if args.smoke_test else "full",
        }},
    }}
    final_output.write_text(json.dumps(envelope, ensure_ascii=False, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
'''


def harness_file_path(contract: ExperimentRuntimeContract) -> str:
    path = PurePosixPath(contract.harness_filename)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("HARNESS_PATH_INVALID")
    return path.as_posix()
