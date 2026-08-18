import json
from pathlib import Path

import pytest

from backend.app.agents.experiment import ExperimentAgent
from backend.app.providers.experiment import MockExperimentProvider
from backend.app.workflow.skills import SkillLoader
from backend.app.workflow.experiment_code import (
    ExperimentBundleValidationError,
    default_mock_experiment_code,
    experiment_validation_issues,
    normalize_experiment_bundle,
    normalize_experiment_code,
    smoke_data_reduction_issues,
    validate_experiment_bundle_source,
    validate_relative_file_path,
)


def _experiment_skill_instructions() -> str:
    root = Path(__file__).resolve().parents[2]
    return SkillLoader(root).load_complete("experiment-implementation").instructions


class BundleLLM:
    mode = "qwen"
    fallback = False

    def __init__(self):
        self.calls = []

    def generate_json(self, task, inputs, schema_hint, instructions=""):
        self.calls.append((task, inputs, schema_hint, instructions))
        return {
            "entrypoint": "train.py",
            "files": [
                {
                    "path": "train.py",
                    "content": (
                        "import argparse, json\nfrom pathlib import Path\nimport torch\n"
                        "p=argparse.ArgumentParser()\n"
                        "p.add_argument('--run-id', required=True)\n"
                        "p.add_argument('--experiment-id', required=True)\n"
                        "p.add_argument('--result-id', required=True)\n"
                        "p.add_argument('--output', required=True)\n"
                        "p.add_argument('--smoke-test', action='store_true')\n"
                        "a=p.parse_args()\n"
                        "smoke_test=a.smoke_test\n"
                        "device=torch.device('cuda')\n"
                        "x=torch.ones((2, 2), device=device)\n"
                        "torch.cuda.synchronize()\n"
                        "Path(a.output).parent.mkdir(parents=True, exist_ok=True)\n"
                        "Path(a.output).write_text(json.dumps({'run_id': a.run_id, "
                        "'experiment_id': a.experiment_id, 'result_id': a.result_id, "
                        "'metrics': {'accuracy': float(x.sum().detach().cpu().item())}}))\n"
                    ),
                }
            ],
            "python_args": ["--seed", "42"],
            "requirements": ["numpy", "torch", "torchvision"],
            "requires_gpu": True,
            "expected_metrics": ["accuracy"],
            "parameters": {"seed": 42},
            "seeds": [42],
            "supports_smoke_test": True,
        }


def test_validate_relative_file_path_accepts_nested_relative_path():
    assert validate_relative_file_path("src/train.py") == "src/train.py"


def test_smoke_data_reduction_check_rejects_the_legacy_ipix_head_slice():
    source = """
if args.smoke_test:
    X = X[:1000]
    y = y[:1000]
"""
    assert smoke_data_reduction_issues(source) == [
        "EXPERIMENT_SMOKE_DATA_REDUCTION_FORBIDDEN:X",
        "EXPERIMENT_SMOKE_DATA_REDUCTION_FORBIDDEN:y",
    ]


def test_normalize_experiment_code_rejects_parent_directory_file_path():
    raw = {
        "entrypoint": "train.py",
        "files": [{"path": "../train.py", "content": "print('bad')"}],
    }
    task = {"seed": 7, "metrics_path": "results/run_seed_7.json", "log_path": "logs/run_seed_7.log"}

    with pytest.raises(ValueError, match="EXPERIMENT_CODE_PATH_INVALID"):
        normalize_experiment_code(raw, task, "python")


def test_normalize_experiment_code_builds_command_and_keeps_files():
    raw = {
        "entrypoint": "train.py",
        "files": [{"path": "train.py", "content": "print('{}')"}],
        "assumptions": ["uses local toy data"],
    }
    task = {"seed": 7, "metrics_path": "results/run_seed_7.json", "log_path": "logs/run_seed_7.log"}

    code = normalize_experiment_code(raw, task, "python")

    assert code["entrypoint"] == "train.py"
    assert code["command"] == "python train.py --seed 7 --output results/run_seed_7.json"
    assert code["metrics_path"] == "results/run_seed_7.json"
    assert code["log_path"] == "logs/run_seed_7.log"
    assert code["files"] == [{"path": "train.py", "content": "print('{}')"}]


def test_normalize_experiment_code_accepts_single_code_field_as_train_entrypoint():
    raw = {
        "entrypoint": "train.py",
        "code": "print('{}')",
    }
    task = {"seed": 7, "metrics_path": "results/run_seed_7.json", "log_path": "logs/run_seed_7.log"}

    code = normalize_experiment_code(raw, task, "python")

    assert code["files"] == [{"path": "train.py", "content": "print('{}')"}]


def test_default_mock_experiment_code_is_executable_contract():
    task = {"seed": 7, "metrics_path": "results/local_seed_7.json", "log_path": "logs/local_seed_7.log"}

    code = default_mock_experiment_code(task, "python")

    assert code["entrypoint"] == "train.py"
    assert code["files"][0]["path"] == "train.py"
    assert "--seed 7" in code["command"]
    assert "--output results/local_seed_7.json" in code["command"]
    assert "json.dumps(metrics)" in code["files"][0]["content"]


def test_bundle_rejects_statically_nested_metric_mapping():
    bundle = normalize_experiment_bundle(
        {
            "files": [{"path": "train.py", "content": "import json\nmetrics = {'accuracy': {'cnn': 0.9}}\noutput = json.dumps({'metrics': metrics})\n"}],
            "expected_metrics": [],
            "supports_smoke_test": True,
        },
        "run_1",
        "experiment_1",
        {},
        validate_source=False,
    )

    with pytest.raises(ExperimentBundleValidationError, match="EXPERIMENT_METRIC_VALUE_INVALID"):
        validate_experiment_bundle_source(bundle)


def test_generate_bundle_contains_manifest_code_requirements_and_stable_ids():
    llm = BundleLLM()
    agent = ExperimentAgent(MockExperimentProvider(), llm)

    bundle = agent.generate_bundle(
        run_id="run_1",
        experiment_id="experiment_1",
        plan={"objective": "test"},
        task={"seed": 42},
        instructions="Use experiment implementation.",
        python_command="python",
    )

    assert bundle.manifest.result_id == "experiment_1_result"
    assert bundle.manifest.python_args[-2:] == [
        "--output",
        "results/experiment_1_result.json",
    ]
    assert "--run-id" in bundle.manifest.python_args
    assert "--experiment-id" in bundle.manifest.python_args
    assert "--result-id" in bundle.manifest.python_args
    assert bundle.files[0].path == "train.py"
    assert bundle.requirements == ["numpy", "torch", "torchvision"]
    assert bundle.manifest.requires_gpu is True
    assert llm.calls[0][0] == "experiment.generate_bundle"
    _, inputs, schema_hint, instructions = llm.calls[0]
    assert set(inputs["task"]) <= {"name", "dataset", "seed"}
    assert "run_id" not in inputs
    assert "experiment_id" not in inputs
    assert "entrypoint" not in schema_hint
    assert "python_args" not in schema_hint
    assert "content_lines" in schema_hint["files"][0]
    assert "content_lines" in instructions


def test_bundle_generation_and_repair_receive_observed_local_structure():
    llm = BundleLLM()
    agent = ExperimentAgent(MockExperimentProvider(), llm)
    observed = [{
        "relative_path": "clutter.mat",
        "filename": "clutter.mat",
        "format": "mat",
        "suffix": ".mat",
        "arrays": [{"key": "clutter", "shape": [18_000, 512], "dtype": "float32"}],
    }]
    plan = {
        "dataset": {
            "contract_id": "dataset_1",
            "root": "D:/data/IPIX17",
            "observed_structure": observed,
        }
    }
    task = {"seed": 42, "plan": plan}

    bundle = agent.generate_bundle(
        "run_1", "experiment_1", plan, task, "Use local data.", "python", validate=False
    )
    repaired = agent.repair_bundle(
        plan, task, bundle, {"evidence": ["repair"]}, "Repair local data.", validate=False
    )

    generated = llm.calls[0]
    repaired_call = llm.calls[1]
    assert generated[1]["observed_structure"] == observed
    assert repaired_call[1]["observed_structure"] == observed
    assert "read-only inspection of the real local data files" in generated[3]
    assert "read-only inspection of the real local data files" in repaired_call[3]
    assert repaired.manifest.dataset_contract_id == "dataset_1"


def test_generate_bundle_rejects_parent_file_paths():
    llm = BundleLLM()
    original = llm.generate_json

    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        result["files"][0]["path"] = "../train.py"
        return result

    llm.generate_json = invalid

    with pytest.raises(ValueError, match="EXPERIMENT_CODE_PATH_INVALID"):
        ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
            "run_1", "experiment_1", {}, {}, "", "python"
        )


def test_generate_bundle_rejects_gpu_bundle_without_visible_cuda_execution():
    llm = BundleLLM()
    original = llm.generate_json

    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        result["files"][0]["content"] = (
            "import argparse, json, torch\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('--run-id'); "
            "p.add_argument('--experiment-id'); p.add_argument('--result-id'); "
            "p.add_argument('--output'); a=p.parse_args()\n"
            "Path(a.output).write_text(json.dumps({'run_id': a.run_id, "
            "'experiment_id': a.experiment_id, 'result_id': a.result_id, "
            "'metrics': {'accuracy': 0.9}}))\n"
        )
        return result

    llm.generate_json = invalid

    with pytest.raises(ValueError, match="EXPERIMENT_BUNDLE_CUDA_USAGE_MISSING"):
        ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
            "run_1", "experiment_1", {}, {}, "", "python"
        )


def test_generate_bundle_rejects_runtime_dataset_downloads():
    llm = BundleLLM()
    original = llm.generate_json

    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        result["files"][0]["content"] += "\n# bad\ntorchvision.datasets.CIFAR10(root='data', download=True)\n"
        return result

    llm.generate_json = invalid

    with pytest.raises(ValueError, match="EXPERIMENT_BUNDLE_RUNTIME_DOWNLOAD_FORBIDDEN"):
        ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
            "run_1", "experiment_1", {}, {}, "", "python"
        )


def test_generate_bundle_rejects_nonexistent_torch_api_before_runtime():
    llm = BundleLLM()
    original = llm.generate_json

    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        result["files"][0]["content"] += (
            "\nclass Mish:\n"
            "    def __call__(self, x):\n"
            "        return x * torch.tanh(torch.softplus(x))\n"
        )
        return result

    llm.generate_json = invalid

    with pytest.raises(ValueError, match="EXPERIMENT_CODE_API_INVALID"):
        ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
            "run_1", "experiment_1", {}, {}, "", "python"
        )


def test_generate_bundle_requires_machine_readable_epoch_progress():
    llm = BundleLLM()
    original = llm.generate_json

    def epoch_loop_without_progress(*args, **kwargs):
        result = original(*args, **kwargs)
        result["files"][0]["content"] += "\nfor epoch in range(2):\n    pass\n"
        return result

    llm.generate_json = epoch_loop_without_progress

    with pytest.raises(ValueError, match="EXPERIMENT_CODE_PROGRESS_MISSING"):
        ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
            "run_1", "experiment_1", {}, {}, "", "python"
        )


def test_repair_bundle_freezes_scientific_contract():
    llm = BundleLLM()
    agent = ExperimentAgent(MockExperimentProvider(), llm)
    plan = {
        "parameters": {"seed": 42},
        "seeds": [42],
        "variants": ["baseline", "dropout"],
    }
    original_bundle = agent.generate_bundle(
        "run_1", "experiment_1", plan, {"seed": 42}, "", "python"
    )

    def repaired(task, inputs, schema_hint, instructions=""):
        assert task == "experiment.repair_bundle"
        assert "smallest safe source change" in instructions
        assert inputs["plan"]["variants"] == ["baseline", "dropout"]
        return {
            "files": [
                {
                    "path": "train.py",
                    "content_lines": original_bundle.files[0].content.splitlines(),
                }
            ],
            "requirements": original_bundle.requirements,
            # These untrusted changes must be ignored by the repair path.
            "parameters": {"seed": 999},
            "seeds": [999],
            "dataset": "mnist",
        }

    llm.generate_json = repaired
    repaired_bundle = agent.repair_bundle(
        plan,
        {"seed": 42, "plan": plan},
        original_bundle,
        {"evidence": ["runtime traceback"]},
        _experiment_skill_instructions(),
    )

    assert repaired_bundle.manifest.parameters == original_bundle.manifest.parameters
    assert repaired_bundle.manifest.seeds == original_bundle.manifest.seeds
    assert repaired_bundle.manifest.dataset == original_bundle.manifest.dataset
    assert repaired_bundle.manifest.expected_metrics == original_bundle.manifest.expected_metrics


def test_generate_bundle_uses_skill_as_the_behavioral_instruction_source():
    llm = BundleLLM()
    agent = ExperimentAgent(MockExperimentProvider(), llm)

    agent.generate_bundle(
        "run_1",
        "experiment_1",
        {"objective": "test"},
        {"seed": 42},
        _experiment_skill_instructions(),
        "python",
    )

    instructions = llm.calls[0][3]
    assert "# Experiment Implementation" in instructions
    assert instructions.count("download datasets at runtime") == 1


def test_generate_bundle_rejects_external_torchvision_dataset_dependency():
    llm = BundleLLM()
    original = llm.generate_json

    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        result["files"][0]["content"] += "\n# bad\ndatasets.CIFAR10(root='data', train=True, download=False)\n"
        return result

    llm.generate_json = invalid

    with pytest.raises(ValueError, match="EXPERIMENT_BUNDLE_EXTERNAL_DATASET_FORBIDDEN"):
        ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
            "run_1", "experiment_1", {}, {}, "", "python"
        )


_DATASET_SOURCE = (
    "import argparse, json, os\nfrom pathlib import Path\nimport torch\n"
    "from torchvision import datasets\n"
    "p=argparse.ArgumentParser()\n"
    "p.add_argument('--run-id', required=True)\n"
    "p.add_argument('--experiment-id', required=True)\n"
    "p.add_argument('--result-id', required=True)\n"
    "p.add_argument('--output', required=True)\n"
    "p.add_argument('--smoke-test', action='store_true')\n"
    "a=p.parse_args()\n"
    "smoke_test=a.smoke_test\n"
    "root=os.environ['DATA_ROOT']\n"
    "data=datasets.CIFAR10(root=root, train=False, download=False)\n"
    "device=torch.device('cuda')\n"
    "x=torch.ones((2, 2), device=device)\n"
    "torch.cuda.synchronize()\n"
    "Path(a.output).parent.mkdir(parents=True, exist_ok=True)\n"
    "Path(a.output).write_text(json.dumps({'run_id': a.run_id, "
    "'experiment_id': a.experiment_id, 'result_id': a.result_id, "
    "'metrics': {'accuracy': float(x.sum().detach().cpu().item())}}))\n"
)


def test_generate_bundle_accepts_declared_dataset_loaded_from_data_root():
    llm = BundleLLM()
    original = llm.generate_json

    def with_dataset(*args, **kwargs):
        result = original(*args, **kwargs)
        result["dataset"] = "CIFAR-10"
        result["files"][0]["content"] = _DATASET_SOURCE
        return result

    llm.generate_json = with_dataset

    bundle = ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
        "run_1", "experiment_1", {}, {"seed": 42}, "", "python"
    )

    assert bundle.manifest.dataset == "cifar-10"


def test_bound_local_public_dataset_keeps_matching_name_and_contract():
    plan = {
        "dataset": {
            "canonical_name": "fashion-mnist",
            "display_name": "FashionMNIST",
            "contract_id": "dataset_verified",
            "content_fingerprint": "sha256:verified",
        }
    }
    bundle = normalize_experiment_bundle(
        {
            "files": [{"path": "train.py", "content": "print('accuracy')\n"}],
            "dataset": "fashion-mnist",
            "expected_metrics": ["accuracy"],
        },
        "run_1",
        "experiment_1",
        {"seed": 7, "plan": plan},
    )

    assert bundle.manifest.dataset == "fashion-mnist"
    assert bundle.manifest.dataset_contract_id == "dataset_verified"


def test_bound_local_dataset_rejects_a_different_declared_name():
    plan = {
        "dataset": {
            "canonical_name": "fashion-mnist",
            "contract_id": "dataset_verified",
        }
    }

    with pytest.raises(
        ValueError,
        match="EXPERIMENT_LOCAL_DATASET_SUBSTITUTION_FORBIDDEN",
    ):
        normalize_experiment_bundle(
            {
                "files": [{"path": "train.py", "content": "print('accuracy')\n"}],
                "dataset": "cifar-10",
                "expected_metrics": ["accuracy"],
            },
            "run_1",
            "experiment_1",
            {"seed": 7, "plan": plan},
        )


@pytest.mark.parametrize("declared", ["FashionMNIST", "fashionmnist", "fashion-mnist", "Fashion-MNIST"])
def test_bound_local_dataset_accepts_canonical_aliases(declared):
    bundle = normalize_experiment_bundle(
        {
            "files": [{"path": "train.py", "content": "print('accuracy')\n"}],
            "dataset": declared,
            "expected_metrics": ["accuracy"],
        },
        "run_1",
        "experiment_1",
        {"seed": 7, "plan": {"dataset": {"canonical_name": "fashion-mnist", "contract_id": "dataset_a"}}},
    )

    assert bundle.manifest.dataset == "fashion-mnist"
    assert bundle.manifest.dataset_contract_id == "dataset_a"


def test_generic_local_contract_allows_empty_dataset_name():
    bundle = normalize_experiment_bundle(
        {
            "files": [{"path": "train.py", "content": "print('accuracy')\n"}],
            "dataset": "",
            "expected_metrics": ["accuracy"],
        },
        "run_1",
        "experiment_1",
        {"seed": 7, "plan": {"dataset": {"contract_id": "dataset_private", "content_fingerprint": "sha256:x"}}},
    )

    assert bundle.manifest.dataset == ""
    assert bundle.manifest.dataset_contract_id == "dataset_private"


def test_generate_bundle_rejects_declared_dataset_without_data_root_usage():
    llm = BundleLLM()
    original = llm.generate_json

    def with_dataset(*args, **kwargs):
        result = original(*args, **kwargs)
        result["dataset"] = "CIFAR-10"
        result["files"][0]["content"] = _DATASET_SOURCE.replace(
            "root=os.environ['DATA_ROOT']", "root='./data'"
        ).replace("root=root", "root='./data'")
        return result

    llm.generate_json = with_dataset

    with pytest.raises(ValueError, match="EXPERIMENT_BUNDLE_DATASET_ROOT_INVALID"):
        ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
            "run_1", "experiment_1", {}, {"seed": 42}, "", "python"
        )


def test_generate_bundle_rejects_unsupported_declared_dataset():
    llm = BundleLLM()
    original = llm.generate_json

    def with_dataset(*args, **kwargs):
        result = original(*args, **kwargs)
        result["dataset"] = "ImageNet"
        return result

    llm.generate_json = with_dataset

    with pytest.raises(ValueError, match="EXPERIMENT_DATASET_UNSUPPORTED"):
        ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
            "run_1", "experiment_1", {}, {"seed": 42}, "", "python"
        )


def test_generate_bundle_inherits_dataset_from_plan_when_source_uses_torchvision():
    llm = BundleLLM()
    original = llm.generate_json

    def with_dataset(*args, **kwargs):
        result = original(*args, **kwargs)
        result["files"][0]["content"] = _DATASET_SOURCE
        return result

    llm.generate_json = with_dataset

    bundle = ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
        "run_1",
        "experiment_1",
        {},
        {"seed": 42, "plan": {"dataset": {"name": "CIFAR-10"}}},
        "",
        "python",
    )

    assert bundle.manifest.dataset == "cifar-10"


def test_generate_bundle_rejects_imports_missing_from_requirements():
    llm = BundleLLM()
    original = llm.generate_json

    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        result["files"][0]["content"] = "import scipy\n" + result["files"][0]["content"]
        return result

    llm.generate_json = invalid

    with pytest.raises(ValueError, match="EXPERIMENT_REQUIREMENT_MISSING:scipy"):
        ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
            "run_1", "experiment_1", {}, {}, "", "python"
        )


def test_normalize_bundle_accepts_python_standard_library_imports_without_requirements():
    bundle = normalize_experiment_bundle(
        {
            "files": [
                {
                    "path": "train.py",
                    "content": (
                        "import gzip\n"
                        "import logging\n"
                        "import tempfile\n"
                        "print('accuracy')\n"
                    ),
                }
            ],
            "expected_metrics": ["accuracy"],
        },
        "run_1",
        "experiment_1",
        {"seed": 7},
    )

    assert bundle.requirements == []


def test_normalize_bundle_rejects_compact_unique_probabilities_indexed_as_full_bins():
    source = """
import numpy as np
counts_h = np.unique(values, return_counts=True)[1]
p_h = counts_h.astype(float) / len(values)
for i_h in range(10):
    print(p_h[i_h])
print("accuracy")
"""

    with pytest.raises(
        ValueError,
        match="EXPERIMENT_CODE_PROBABILITY_SHAPE_UNSAFE",
    ):
        normalize_experiment_bundle(
            {
                "files": [{"path": "train.py", "content": source}],
                "requirements": ["numpy"],
                "expected_metrics": ["accuracy"],
            },
            "run_1",
            "experiment_1",
            {"seed": 7},
        )


def test_normalize_bundle_rejects_reconstructed_model_clone_before_loading_state():
    source = """
import torch
masked_model = SimpleNet(model.fc1.in_features, model.fc2.out_features)
masked_model.load_state_dict(model.state_dict())
print("accuracy")
"""

    with pytest.raises(
        ValueError,
        match="EXPERIMENT_CODE_MODEL_CLONE_UNSAFE",
    ):
        normalize_experiment_bundle(
            {
                "files": [{"path": "train.py", "content": source}],
                "requirements": ["torch"],
                "expected_metrics": ["accuracy"],
            },
            "run_1",
            "experiment_1",
            {"seed": 7},
        )


def test_normalize_bundle_rejects_tensor_numpy_conversion_without_detach():
    source = """
import torch
weight_norms = torch.norm(model.fc1.weight, dim=0).cpu().numpy()
print("accuracy")
"""

    with pytest.raises(
        ValueError,
        match="EXPERIMENT_CODE_TENSOR_NUMPY_UNSAFE",
    ):
        normalize_experiment_bundle(
            {
                "files": [{"path": "train.py", "content": source}],
                "requirements": ["torch"],
                "expected_metrics": ["accuracy"],
            },
            "run_1",
            "experiment_1",
            {"seed": 7},
        )


def test_static_bundle_validation_aggregates_independent_issues():
    source = """
import torch
torch.softplus(1)
values = tensor.cpu().numpy()
"""
    with pytest.raises(ExperimentBundleValidationError) as exc_info:
        normalize_experiment_bundle(
            {
                "files": [{"path": "train.py", "content": source}],
                "requirements": [],
                "expected_metrics": ["accuracy"],
            },
            "run_1",
            "experiment_1",
            {"seed": 7},
        )

    assert exc_info.value.issues == [
        "EXPERIMENT_CODE_API_INVALID:train.py:torch.softplus does not exist; use torch.nn.functional.softplus",
        "EXPERIMENT_CODE_TENSOR_NUMPY_UNSAFE:train.py:line=4: detach tensors before NumPy conversion with tensor.detach().cpu().numpy()",
        "EXPERIMENT_REQUIREMENT_MISSING:torch",
    ]


def test_normalize_bundle_accepts_equivalent_detached_tensor_numpy_temporary():
    source = """
import torch
detached = tensor.detach()
on_cpu = detached.cpu()
values = on_cpu.numpy()
"""
    bundle = normalize_experiment_bundle(
        {
            "files": [{"path": "train.py", "content": source}],
            "requirements": ["torch"],
            "expected_metrics": ["accuracy"],
        },
        "run_1",
        "experiment_1",
        {"seed": 7},
    )
    assert bundle.files[0].path == "train.py"


def test_normalize_bundle_rejects_live_tensor_numpy_even_with_other_safe_conversion():
    source = """
import torch
safe = tensor.detach().cpu().numpy()
unsafe = tensor.cpu().numpy()
"""
    with pytest.raises(ValueError, match="EXPERIMENT_CODE_TENSOR_NUMPY_UNSAFE"):
        normalize_experiment_bundle(
            {
                "files": [{"path": "train.py", "content": source}],
                "requirements": ["torch"],
                "expected_metrics": ["accuracy"],
            },
            "run_1",
            "experiment_1",
            {"seed": 7},
        )


def test_local_dataset_contract_still_validates_seeds_and_parameters():
    plan = {
        "dataset": {
            "canonical_name": "fashion-mnist",
            "contract_id": "fashion_local",
            "content_fingerprint": "fingerprint",
            "root": "D:/Gewu/datasets/fashionmnist",
        },
        "seeds": [7],
        "parameters": {"learning_rate": 0.01},
    }
    bundle = normalize_experiment_bundle(
        {
            "files": [{"path": "train.py", "content": "import os\nroot = os.environ['DATA_ROOT']\n"}],
            "dataset": "fashion-mnist",
            "requirements": [],
            "expected_metrics": ["accuracy"],
            "seeds": [8],
            "parameters": {"learning_rate": 0.02},
        },
        "run_1",
        "experiment_1",
        {"seed": 7, "plan": plan},
    )

    with pytest.raises(ExperimentBundleValidationError) as exc_info:
        ExperimentAgent.validate_bundle(plan, bundle)

    assert "EXPERIMENT_PLAN_SEEDS_MISMATCH:planned=[7]:bundle=[8]" in exc_info.value.issues
    assert "EXPERIMENT_PLAN_PARAMETERS_MISMATCH:learning_rate" in exc_info.value.issues


def test_normalize_bundle_rejects_noncontiguous_reversed_argsort_indices():
    source = """
import numpy as np
order = np.argsort(scores)[::-1]
print("accuracy")
"""
    with pytest.raises(
        ValueError,
        match="EXPERIMENT_CODE_NUMPY_STRIDE_UNSAFE",
    ):
        normalize_experiment_bundle(
            {
                "files": [{"path": "train.py", "content": source}],
                "requirements": ["numpy"],
                "expected_metrics": ["accuracy"],
            },
            "run_1",
            "experiment_1",
            {"seed": 7},
        )


def test_normalize_bundle_rejects_missing_files_with_actionable_message():
    with pytest.raises(ValueError, match="EXPERIMENT_CODE_FILES_MISSING"):
        normalize_experiment_bundle(
            {"entrypoint": "train.py", "files": []},
            "run_1",
            "experiment_1",
            {"seed": 7},
        )


def test_normalize_bundle_accepts_files_as_path_to_content_mapping():
    bundle = normalize_experiment_bundle(
        {
            "entrypoint": "train.py",
            "files": {"train.py": "print('accuracy')\n"},
            "expected_metrics": ["accuracy"],
        },
        "run_1",
        "experiment_1",
        {"seed": 7},
    )

    assert bundle.files[0].path == "train.py"


def test_experiment_validation_issues_extracts_pydantic_bundle_errors():
    try:
        normalize_experiment_bundle(
            {
                "entrypoint": "train.py",
                "files": [{"path": "helper.py", "content": "print('x')\n"}],
            },
            "run_1",
            "experiment_1",
            {"seed": 7},
        )
    except ValueError as exc:
        issues = experiment_validation_issues(exc)
    else:
        pytest.fail("expected bundle validation to fail")

    assert issues
    assert issues[0].startswith("EXPERIMENT_CODE_ENTRYPOINT_MISSING")


def test_experiment_validation_issues_covers_plain_and_json_errors():
    assert experiment_validation_issues(ValueError("EXPERIMENT_DATASET_UNSUPPORTED:x")) == [
        "EXPERIMENT_DATASET_UNSUPPORTED:x"
    ]
    json_issues = experiment_validation_issues(
        json.JSONDecodeError("Expecting value", doc="", pos=0)
    )
    assert json_issues and json_issues[0].startswith("EXPERIMENT_CODE_GENERATION_INVALID")
    assert experiment_validation_issues(ValueError("boom")) == []


def test_generate_bundle_allows_dynamically_constructed_metric_key():
    llm = BundleLLM()
    original = llm.generate_json

    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        result["expected_metrics"] = ["test_accuracy"]
        return result

    llm.generate_json = invalid

    bundle = ExperimentAgent(MockExperimentProvider(), llm).generate_bundle(
        "run_1", "experiment_1", {}, {}, "", "python"
    )
    assert bundle.manifest.expected_metrics == ["test_accuracy"]


@pytest.mark.parametrize("marker", ["\ufffd", r"\ufffd"])
def test_normalize_bundle_rejects_unicode_replacement_markers(marker):
    with pytest.raises(ValueError, match="EXPERIMENT_CODE_ENCODING_INVALID:train.py"):
        normalize_experiment_bundle(
            {
                "entrypoint": "train.py",
                "files": [{"path": "train.py", "content": f"# damaged {marker}\nprint('ok')\n"}],
            },
            "run_1",
            "experiment_1",
            {"seed": 7},
        )


def test_normalize_bundle_rejects_double_escaped_line_separators():
    with pytest.raises(ValueError, match="EXPERIMENT_CODE_ENCODING_INVALID:train.py"):
        normalize_experiment_bundle(
            {
                "entrypoint": "train.py",
                "files": [
                    {
                        "path": "train.py",
                        "content": r"import argparse\nimport json\nprint('broken')",
                    }
                ],
            },
            "run_1",
            "experiment_1",
            {"seed": 7},
        )


def test_normalize_bundle_rejects_python_syntax_error():
    with pytest.raises(ValueError, match="EXPERIMENT_CODE_SYNTAX_INVALID:train.py"):
        normalize_experiment_bundle(
            {
                "entrypoint": "train.py",
                "files": [{"path": "train.py", "content": "def broken(:\n    pass\n"}],
            },
            "run_1",
            "experiment_1",
            {"seed": 7},
        )


def test_normalize_bundle_allows_valid_one_line_string_with_literal_newline_escape():
    bundle = normalize_experiment_bundle(
        {
            "entrypoint": "train.py",
            "files": [{"path": "train.py", "content": r"value = '\n'"}],
        },
        "run_1",
        "experiment_1",
        {"seed": 7},
    )

    assert bundle.files[0].content == r"value = '\n'"


def test_normalize_bundle_joins_content_lines_with_real_newlines():
    bundle = normalize_experiment_bundle(
        {
            "files": [
                {
                    "path": "train.py",
                    "content_lines": ["value = r'\\n'", "", "print(value)"],
                }
            ],
        },
        "run_1",
        "experiment_1",
        {"seed": 7},
    )

    assert bundle.files[0].content == "value = r'\\n'\n\nprint(value)\n"
    assert bundle.manifest.entrypoint == "train.py"
    assert bundle.manifest.python_args[-8:] == [
        "--run-id",
        "run_1",
        "--experiment-id",
        "experiment_1",
        "--result-id",
        "experiment_1_result",
        "--output",
        "results/experiment_1_result.json",
    ]


@pytest.mark.parametrize(
    "file_payload",
    [
        {"path": "train.py", "content_lines": "print('bad')"},
        {"path": "train.py", "content_lines": []},
        {"path": "train.py", "content_lines": ["first\nsecond"]},
        {"path": "train.py", "content_lines": ["ok", 2]},
    ],
)
def test_normalize_bundle_rejects_invalid_content_lines(file_payload):
    with pytest.raises(ValueError, match="EXPERIMENT_CODE_LINES_INVALID"):
        normalize_experiment_bundle(
            {"files": [file_payload]},
            "run_1",
            "experiment_1",
            {"seed": 7},
        )


def test_normalize_bundle_rejects_ambiguous_content_and_content_lines():
    with pytest.raises(ValueError, match="EXPERIMENT_CODE_CONTENT_AMBIGUOUS"):
        normalize_experiment_bundle(
            {
                "files": [
                    {
                        "path": "train.py",
                        "content": "print('old')\n",
                        "content_lines": ["print('new')"],
                    }
                ]
            },
            "run_1",
            "experiment_1",
            {"seed": 7},
        )
