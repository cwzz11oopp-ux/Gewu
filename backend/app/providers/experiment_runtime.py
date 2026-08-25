from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import BaseModel, Field

from backend.app.models.experiment import ExperimentBundle, ExperimentManifest


class CudaProbe(BaseModel):
    available: bool
    device_count: int
    device_names: list[str] = Field(default_factory=list)
    python_version: str = ""
    torch_version: str = ""
    torch_cuda: str | None = None


def build_python_command(python: str, bundle: ExperimentBundle) -> list[str]:
    if bundle.runtime_contract is not None:
        return [python, bundle.runtime_contract.harness_filename]
    return [python, bundle.manifest.entrypoint, *bundle.manifest.python_args]


def validate_result_file(path: Path, manifest: ExperimentManifest) -> dict:
    if not path.is_file():
        raise RuntimeError(f"EXPERIMENT_RESULT_MISSING:{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("EXPERIMENT_RESULT_INVALID_JSON") from exc
    return validate_result_payload(payload, manifest)


def validate_result_payload(payload: object, manifest: ExperimentManifest) -> dict:
    if not isinstance(payload, dict):
        raise RuntimeError("EXPERIMENT_RESULT_INVALID_JSON")
    if (
        payload.get("run_id") != manifest.run_id
        or payload.get("experiment_id") != manifest.experiment_id
        or payload.get("result_id") != manifest.result_id
    ):
        raise RuntimeError("EXPERIMENT_RESULT_ID_MISMATCH")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        raise RuntimeError("EXPERIMENT_METRICS_INVALID")
    for metric in manifest.expected_metrics:
        if metric in metrics:
            continue
        # Runtime metrics are intentionally scalar-only.  A plan can declare a
        # decomposed measure such as "per latent dimension"; in that case the
        # implementation must flatten it into uniquely named scalar entries
        # instead of overwriting one dictionary key with every component.
        derived_scalar_metrics = (
            " per " in metric.lower()
            and any(name.startswith(f"{metric}_") for name in metrics)
        )
        if not derived_scalar_metrics:
            raise RuntimeError(f"EXPERIMENT_METRIC_MISSING:{metric}")
    for name, value in metrics.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(f"EXPERIMENT_METRIC_INVALID:{name}")
        if not math.isfinite(float(value)):
            raise RuntimeError(f"EXPERIMENT_METRIC_NON_FINITE:{name}")
    return _lenient_epoch_history(payload)


def _lenient_epoch_history(payload: dict) -> dict:
    """Pass through a validated observational epoch history, dropping bad rows.

    Per-epoch arrays are observational only and never gate a scientific result.
    A malformed history is sanitized here rather than failing the experiment.
    """
    history = payload.get("epoch_metrics")
    if history is None:
        return payload
    if not isinstance(history, list) or not history:
        return _without_epoch_history(payload)
    cleaned: list[dict[str, object]] = []
    for item in history:
        if not isinstance(item, dict) or "epoch" not in item:
            continue
        epoch = item.get("epoch")
        if (
            isinstance(epoch, bool)
            or not isinstance(epoch, (int, float))
            or not math.isfinite(float(epoch))
            or float(epoch) < 1
        ):
            continue
        row: dict[str, object] = {"epoch": int(epoch)}
        for name, value in item.items():
            if name == "epoch":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                continue
            row[str(name)] = float(value)
        cleaned.append(row)
    if not cleaned:
        return _without_epoch_history(payload)
    return {**payload, "epoch_metrics": cleaned}


def _without_epoch_history(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "epoch_metrics"}


def cuda_probe_command(python: str) -> list[str]:
    script = (
        "import json, platform, torch; "
        "available=bool(torch.cuda.is_available()); "
        "count=int(torch.cuda.device_count()) if available else 0; "
        "print(json.dumps({'available': available, 'device_count': count, "
        "'device_names': [torch.cuda.get_device_name(i) for i in range(count)], "
        "'python_version': platform.python_version(), "
        "'torch_version': str(torch.__version__), 'torch_cuda': torch.version.cuda}))"
    )
    return [python, "-c", script]


def parse_cuda_probe(stdout: str) -> CudaProbe:
    try:
        payload = json.loads(stdout.strip())
        return CudaProbe.model_validate(payload)
    except Exception as exc:
        raise RuntimeError("EXPERIMENT_CUDA_PROBE_FAILED") from exc
