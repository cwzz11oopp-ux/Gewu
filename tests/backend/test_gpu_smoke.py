import os
import sys

import pytest

from backend.app.config import Settings
from backend.app.models.experiment import ExperimentBundle, ExperimentFile, ExperimentManifest
from backend.app.providers.experiment import LocalGpuExperimentProvider


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_GPU_SMOKE") != "1",
    reason="Set RUN_GPU_SMOKE=1 to run the real CUDA smoke test.",
)


def test_real_cuda_tensor_operation():
    torch = pytest.importorskip("torch")
    assert torch.cuda.is_available(), "Configured PyTorch cannot access CUDA"
    assert torch.cuda.device_count() >= 1

    value = torch.tensor([2.0, 3.0], device="cuda")
    result = (value * value).sum()

    assert result.item() == pytest.approx(13.0)
    assert torch.cuda.get_device_name(0)


def test_local_gpu_provider_executes_real_bundle_and_validates_result(tmp_path):
    source = """from pathlib import Path
import argparse
import json
import torch

parser = argparse.ArgumentParser()
parser.add_argument('--run-id', required=True)
parser.add_argument('--experiment-id', required=True)
parser.add_argument('--result-id', required=True)
parser.add_argument('--output', required=True)
args = parser.parse_args()
value = torch.tensor([2.0, 3.0], device='cuda')
payload = {
    'run_id': args.run_id,
    'experiment_id': args.experiment_id,
    'result_id': args.result_id,
    'metrics': {'sum_squares': float((value * value).sum().item())},
}
path = Path(args.output)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload), encoding='utf-8')
"""
    bundle = ExperimentBundle(
        manifest=ExperimentManifest(
            run_id="gpu_smoke",
            experiment_id="experiment_1",
            result_id="experiment_1_result",
            python_args=[
                "--run-id", "gpu_smoke",
                "--experiment-id", "experiment_1",
                "--result-id", "experiment_1_result",
                "--output", "results/experiment_1_result.json",
            ],
            requires_gpu=True,
            expected_metrics=["sum_squares"],
            parameters={"operation": "sum_squares"},
            seeds=[7],
        ),
        files=[ExperimentFile(path="train.py", content=source)],
        requirements=["torch"],
    )
    settings = Settings.from_env(
        {
            "EXPERIMENT_PROVIDER": "local_gpu",
            "LOCAL_EXPERIMENT_WORKDIR": str(tmp_path / "experiments"),
            "LOCAL_GPU_PYTHON": sys.executable,
            "LOCAL_GPU_CUDA_VISIBLE_DEVICES": "0",
        }
    )

    result = LocalGpuExperimentProvider(settings).run(
        {"run_id": "gpu_smoke"},
        bundle,
    )

    assert result["provider"] == "local_gpu"
    assert result["experiment_id"] == "experiment_1"
    assert result["result_id"] == "experiment_1_result"
    assert result["metrics"]["sum_squares"] == pytest.approx(13.0)
    assert result["environment"]["cuda_available"] is True
