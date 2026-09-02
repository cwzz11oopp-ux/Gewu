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
from backend.app.workflow.plan_contract import execution_training_budget


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
    phase2_protocol = task.get("phase2_protocol") or {}
    protocol_stage = str(phase2_protocol.get("stage") or "formal_validation")
    protocol_seeds = list(phase2_protocol.get("seeds") or manifest.seeds)
    training_budget = execution_training_budget(plan)
    protocol_epochs = phase2_protocol.get("epochs") or (
        int(training_budget.get("epochs") or training_budget["runtime_passes"])
        if training_budget is not None
        else None
    ) or 1
    protocol_epochs = int(protocol_epochs)
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
        "stage": protocol_stage,
        "epochs": protocol_epochs,
        "seeds": protocol_seeds,
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


SCAFFOLD_SOURCE = """\
\"\"\"Deterministic runtime scaffold for GEWU experiment implementations.

The system Harness writes this module next to ``train.py`` and supplies every
protocol value through CLI arguments and environment variables.  Implementations
import it (``import gewu_runtime as gr``) instead of hand-rolling argparse,
result serialization, seed handling, or the smoke contract, so the fragile
protocol invariants are owned by deterministic code rather than generated text.
\"\"\"

from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path


def _env_json(name, default):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def get_args() -> argparse.Namespace:
    \"\"\"Parse the system-owned protocol arguments and inject frozen parameters.

    The Harness always invokes ``train.py`` with ``--run-id``,
    ``--experiment-id``, ``--result-id``, ``--output``, ``--seed`` and (during
    smoke) ``--smoke-test``.  Every frozen Plan parameter (``batch_size``,
    ``epochs``, ``lr``, ...) is also exposed as an attribute, so
    ``args.batch_size`` is always defined even when generated code never
    declared it.
    \"\"\"
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--result-id", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--smoke-test", action="store_true")
    args, _known = parser.parse_known_args()
    parameters = _env_json("GEWU_PARAMETERS_JSON", {}) or {}
    if not isinstance(parameters, dict):
        parameters = {}
    for key, value in parameters.items():
        setattr(args, key, value)
    args.parameters = parameters
    return args


def is_smoke() -> bool:
    \"\"\"True when the Harness bound this execution to one seed and one epoch.\"\"\"
    return os.environ.get("GEWU_EXECUTION_STAGE") == "smoke"


def epoch_budget() -> int:
    \"\"\"System-bound epoch count for this stage (always 1 during smoke).\"\"\"
    try:
        return max(1, int(os.environ.get("GEWU_EXECUTION_EPOCHS", "1")))
    except (TypeError, ValueError):
        return 1


def seeds() -> list:
    \"\"\"System-bound seed list (the Harness runs one seed in smoke).\"\"\"
    return list(_env_json("GEWU_SEEDS_JSON", []))


def expected_metrics() -> list:
    \"\"\"Manifest-declared aggregate metric keys that must be present.\"\"\"
    return list(_env_json("GEWU_EXPECTED_METRICS_JSON", []))


def _metric_key_token(name) -> str:
    # Compare generated metric aliases without changing their reported values.
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def data_root() -> str:
    \"\"\"Verified dataset root bound by the system for this contract.\"\"\"
    return os.environ.get("DATA_ROOT", "") or os.environ.get(
        "GEWU_VERIFIED_DATA_ROOT", ""
    )


def write_result(
    metrics,
    epoch_metrics=None,
    *,
    primary_prefix: str = "",
    seed=None,
    output=None,
) -> dict:
    \"\"\"Normalize and persist the per-seed result under the system contract.

    Every value under ``metrics`` must be one finite number.  Nested objects,
    booleans, tensors and NumPy scalars are rejected.  A unique normalized
    primary-variant alias (for example ``ECA_Standard_Overall_Test_Accuracy``)
    is promoted to its manifest key.  Ambiguous aliases remain absent so the
    result contract never guesses a scientific value.
    \"\"\"
    args = get_args()
    if not isinstance(metrics, dict):
        raise ValueError("RESULT_METRICS_INVALID")
    normalized = {}
    for name, value in metrics.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"RESULT_METRIC_NON_FINITE:{name}")
        normalized[str(name)] = float(value)
    for name in expected_metrics():
        if name in normalized:
            continue
        expected_token = _metric_key_token(name)
        direct = [
            key for key in normalized
            if _metric_key_token(key) == expected_token
        ]
        primary_token = _metric_key_token(primary_prefix)
        prefixed = [
            key for key in normalized
            if primary_token
            and _metric_key_token(key).startswith(primary_token + "_")
            and _metric_key_token(key).endswith("_" + expected_token)
        ]
        candidates = direct if len(direct) == 1 else prefixed
        if len(candidates) == 1:
            normalized[str(name)] = normalized[candidates[0]]
    payload = {
        "run_id": args.run_id,
        "experiment_id": args.experiment_id,
        "result_id": args.result_id,
        "seed": args.seed if seed is None else seed,
        "metrics": normalized,
    }
    if epoch_metrics is not None:
        rows = []
        for row in epoch_metrics:
            if not isinstance(row, dict):
                continue
            epoch = row.get("epoch")
            if (
                epoch is None
                or isinstance(epoch, bool)
                or not isinstance(epoch, (int, float))
                or not math.isfinite(float(epoch))
                or float(epoch) < 1
            ):
                continue
            cleaned = {"epoch": int(epoch)}
            for key, value in row.items():
                if key == "epoch":
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                    continue
                cleaned[str(key)] = float(value)
            rows.append(cleaned)
        if rows:
            payload["epoch_metrics"] = rows
    out = Path(output) if output else (Path(args.output) if args.output else None)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    return payload
"""


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
SCAFFOLD_SOURCE = {SCAFFOLD_SOURCE!r}


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


def _epoch_metrics_from(candidate: object) -> list | None:
    """Return validated observational epoch history, or None when absent/invalid.

    Per-epoch arrays are observational only; they never block the scientific
    result.  A malformed history is dropped, never promoted to an error.
    """
    history = candidate.get("epoch_metrics") if isinstance(candidate, dict) else None
    if not isinstance(history, list) or not history:
        return None
    by_epoch = {{}}
    previous_epoch = 0
    for item in history:
        if not isinstance(item, dict) or "epoch" not in item:
            return None
        epoch = item.get("epoch")
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            return None
        if epoch < previous_epoch:
            return None
        previous_epoch = epoch
        row = by_epoch.setdefault(epoch, {{"epoch": epoch, "_values": {{}}}})
        for name, value in item.items():
            if name == "epoch":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                return None
            row["_values"].setdefault(str(name), []).append(float(value))
    epochs = sorted(by_epoch)
    return [
        {{"epoch": epoch, **{{name: statistics.fmean(values) for name, values in by_epoch[epoch]["_values"].items()}}}}
        for epoch in epochs
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    _require_verified_context()
    # The system owns the deterministic protocol surface: ship the scaffold
    # module next to train.py so generated code can import it instead of
    # hand-rolling argparse, seed handling, or result serialization.
    Path("gewu_runtime.py").write_text(SCAFFOLD_SOURCE, encoding="utf-8")
    final_output = Path(CONTRACT["result_output_path"])
    final_output.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["GEWU_RUNTIME_CONTRACT_SHA256"] = CONTRACT["contract_sha256"]
    environment["GEWU_SEEDS_JSON"] = json.dumps(CONTRACT["seeds"], sort_keys=True)
    environment["GEWU_EXPECTED_METRICS_JSON"] = json.dumps(
        CONTRACT["expected_metrics"], sort_keys=True
    )
    environment["GEWU_EXECUTION_STAGE"] = (
        "smoke" if args.smoke_test else CONTRACT["stage"]
    )
    environment["GEWU_EXECUTION_EPOCHS"] = str(
        1 if args.smoke_test else CONTRACT["epochs"]
    )
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
        # Deterministic contract-key completion (system-owned, no LLM): when an
        # aggregate expected-metric key is missing but exactly one variant-key
        # suffix is unambiguous, promote it.  Ambiguous prefixes (Baseline_ vs
        # intervention_) are left for the scaffold's primary_prefix path or for
        # repair; never guess here.
        for name in CONTRACT["expected_metrics"]:
            if name in numeric_metrics:
                continue
            candidates = {{
                key: value
                for key, value in numeric_metrics.items()
                if key != name and key.endswith(name)
            }}
            if len(candidates) == 1:
                numeric_metrics[str(name)] = float(next(iter(candidates.values())))
        seed_entry = {{"seed": seed, "metrics": numeric_metrics}}
        history = _epoch_metrics_from(candidate)
        if history is not None:
            seed_entry["epoch_metrics"] = history
        seed_results.append(seed_entry)
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
    epoch_histories = [item["epoch_metrics"] for item in seed_results if "epoch_metrics" in item]
    epoch_metrics = None
    if len(epoch_histories) == len(seed_results) and epoch_histories:
        # Every seed recorded epoch history of identical length; aggregate a
        # mean curve so the result carries one comparable per-epoch series.
        width = len(epoch_histories[0])
        if all(len(item) == width for item in epoch_histories):
            epoch_keys = sorted({{
                key
                for item in epoch_histories
                for row in item
                for key in row
                if key != "epoch"
            }})
            rows = []
            for index in range(width):
                row = {{"epoch": epoch_histories[0][index]["epoch"]}}
                for key in epoch_keys:
                    values = [
                        float(item[index][key])
                        for item in epoch_histories
                        if key in item[index]
                    ]
                    if values:
                        row[key] = statistics.fmean(values)
                rows.append(row)
            epoch_metrics = rows
    envelope = {{
        "run_id": CONTRACT["run_id"],
        "experiment_id": CONTRACT["experiment_id"],
        "result_id": CONTRACT["result_id"],
        "metrics": metrics,
        "seeds": execution_seeds,
        "seed_results": seed_results,
        "metric_summary": metric_summary,
        "epoch_metrics": epoch_metrics,
        "runtime": {{
            "contract_sha256": CONTRACT["contract_sha256"],
            "mode": "smoke" if args.smoke_test else "full",
            "stage": "smoke" if args.smoke_test else CONTRACT["stage"],
            "epochs": 1 if args.smoke_test else CONTRACT["epochs"],
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
