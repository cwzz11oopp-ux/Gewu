import pytest

from backend.app.agents.experiment import ExperimentAgent
from backend.app.agents.planner import PlanningAgent
from backend.app.providers.experiment import MockExperimentProvider
from backend.app.workflow.dataset_catalog import availability_status, dataset_card
from backend.app.workflow.engine import WorkflowEngine


class InputRecorder:
    mode = "qwen"
    fallback = False

    def __init__(self, response=None):
        self.calls = []
        self.response = response or {"ok": True}

    def generate_json(self, task, inputs, schema_hint, instructions=""):
        self.calls.append({"task": task, "inputs": inputs, "instructions": instructions})
        return self.response


def _options(**statuses):
    return [
        {"name": name, "status": status, "marker": "m", "card": dataset_card(name)}
        for name, status in statuses.items()
    ]


def test_dataset_card_describes_shape_classes_and_normalization():
    card = dataset_card("CIFAR-10")

    assert card["input_shape"] == [3, 32, 32]
    assert card["num_classes"] == 10
    assert card["train_size"] == 50000
    assert card["test_size"] == 10000
    assert card["normalization"]["mean"]
    assert "DATA_ROOT" in card["loader"]


def test_availability_status_reports_cached_downloadable_and_missing(tmp_path):
    marker = tmp_path / "cifar-10-batches-py"
    marker.mkdir()
    (marker / "data_batch_1").write_bytes(b"x")

    online = {entry["name"]: entry["status"] for entry in availability_status(tmp_path, "online")}
    local = {entry["name"]: entry["status"] for entry in availability_status(tmp_path, "local")}

    assert online["cifar-10"] == "cached"
    assert online["mnist"] == "downloadable"
    assert local["cifar-10"] == "cached"
    assert local["mnist"] == "missing"


def test_build_task_uses_preregistered_plan_seed_contract():
    seeds = [687603589, 369531869, 2110560859]
    plan = {
        "seeds": seeds,
        "experiment_constraints": {"epochs": 5},
        "dataset": {
            "canonical_name": "ipix17",
            "root": "/datasets/ipix17",
            "contract_id": "dataset_ipix17",
        },
    }

    task = ExperimentAgent(MockExperimentProvider()).build_task(plan)

    assert task["constraints"] == {
        "epochs": 5,
        "seed": seeds[0],
        "seeds": seeds,
    }
    assert task["seed"] == seeds[0]
    assert task["seeds"] == seeds
    assert task["plan"]["seeds"] == seeds


def test_build_plan_passes_dataset_options_and_contract():
    llm = InputRecorder()
    options = _options(**{"cifar-10": "cached"})

    PlanningAgent(llm).build_plan(
        {"claim": "c"}, instructions="base", dataset_options=options
    )

    call = llm.calls[0]
    assert call["inputs"]["dataset_options"] == options
    assert call["inputs"]["plan_context"] == {}
    assert call["instructions"].startswith("base")
    assert "dataset_options" in call["instructions"]
    assert "missing" in call["instructions"]


def test_build_plan_without_options_still_includes_authoritative_plan_contract():
    llm = InputRecorder()

    PlanningAgent(llm).build_plan({"claim": "c"}, instructions="base")

    assert llm.calls[0]["instructions"].startswith("base")
    assert "Authoritative Plan Contract" in llm.calls[0]["instructions"]
    assert llm.calls[0]["inputs"]["dataset_options"] == []
    assert llm.calls[0]["inputs"]["plan_context"] == {}


def test_plan_dataset_issues_rejects_missing_local_dataset():
    plan = {"dataset": {"name": "CIFAR-10"}}
    options = _options(**{"cifar-10": "missing", "mnist": "cached"})

    issues = WorkflowEngine._plan_dataset_issues(plan, options)

    assert len(issues) == 1
    assert issues[0].startswith("PLAN_DATASET_UNAVAILABLE:cifar-10")
    assert "mnist" in issues[0]


def test_plan_dataset_issues_allows_cached_and_synthetic_plans():
    options = _options(**{"cifar-10": "cached"})

    assert WorkflowEngine._plan_dataset_issues({"dataset": {"name": "CIFAR-10"}}, options) == []
    assert WorkflowEngine._plan_dataset_issues({"dataset": {"name": "合成数据"}}, options) == []
    assert WorkflowEngine._plan_dataset_issues({"dataset": {}}, options) == []


def test_attach_dataset_card_enriches_plan_dataset():
    plan = {"dataset": {"name": "CIFAR-10", "split": "train/test"}}
    options = _options(**{"cifar-10": "downloadable"})

    enriched = WorkflowEngine._attach_dataset_card(plan, options)

    dataset = enriched["dataset"]
    assert dataset["normalized_name"] == "cifar-10"
    assert dataset["availability"] == "downloadable"
    assert dataset["card"]["num_classes"] == 10
    assert dataset["split"] == "train/test"


def test_generate_bundle_receives_dataset_card_from_plan():
    response = {
        "entrypoint": "train.py",
        "files": [{"path": "train.py", "content": "print('accuracy')\n"}],
        "python_args": [],
        "requirements": [],
        "requires_gpu": False,
        "expected_metrics": ["accuracy"],
        "parameters": {},
        "seeds": [7],
    }
    llm = InputRecorder(response)
    agent = ExperimentAgent(MockExperimentProvider(), llm)
    plan = {"dataset": {"name": "CIFAR-10", "card": dataset_card("cifar-10")}}

    agent.generate_bundle("run_1", "experiment_1", plan, {"seed": 7}, "", "python")

    inputs = llm.calls[0]["inputs"]
    assert inputs["dataset_card"]["num_classes"] == 10
    assert "dataset_card" in llm.calls[0]["instructions"]


def test_generate_bundle_derives_dataset_card_from_task_dataset():
    response = {
        "entrypoint": "train.py",
        "files": [{"path": "train.py", "content": "print('accuracy')\n"}],
        "python_args": [],
        "requirements": [],
        "requires_gpu": False,
        "expected_metrics": ["accuracy"],
        "parameters": {},
        "seeds": [7],
    }
    llm = InputRecorder(response)
    agent = ExperimentAgent(MockExperimentProvider(), llm)

    agent.generate_bundle(
        "run_1", "experiment_1", {}, {"seed": 7, "dataset": "Fashion-MNIST"}, "", "python"
    )

    assert llm.calls[0]["inputs"]["dataset_card"]["input_shape"] == [1, 28, 28]


def test_generate_bundle_rejects_dataset_substitution_against_accepted_plan():
    response = {
        "files": [{"path": "train.py", "content": "print('accuracy')\n"}],
        "requirements": [],
        "requires_gpu": False,
        "dataset": "fashion-mnist",
        "expected_metrics": ["accuracy"],
        "parameters": {},
        "seeds": [7],
    }
    agent = ExperimentAgent(MockExperimentProvider(), InputRecorder(response))
    plan = {"dataset": {"name": "CIFAR-10"}}

    with pytest.raises(ValueError, match="EXPERIMENT_PLAN_DATASET_MISMATCH"):
        agent.generate_bundle(
            "run_1", "experiment_1", plan, {"seed": 7, "plan": plan}, "", "python"
        )


def test_task_and_bundle_inherit_the_locked_contract_not_provider_defaults():
    response = {
        "files": [{"path": "train.py", "content": "import os\nimport json\nimport argparse\np=argparse.ArgumentParser()\np.add_argument('--smoke-test', action='store_true')\na=p.parse_args()\nif a.smoke_test:\n    print('smoke')\nmetrics = {'accuracy': 1.0}\noutput = json.dumps({'metrics': metrics})\nprint(os.environ['DATA_ROOT'])\nprint(output)\n"}],
        "requirements": [],
        "requires_gpu": False,
        "dataset": "fashionmnist",
        "expected_metrics": ["accuracy"],
        "parameters": {},
        "seeds": [7],
        "supports_smoke_test": True,
    }
    plan = {
        "dataset": {
            "canonical_name": "fashion-mnist",
            "display_name": "FashionMNIST",
            "root": "/datasets/fashionmnist",
            "contract_id": "dataset_fashion",
            "content_fingerprint": "sha256:fashion",
        }
    }
    agent = ExperimentAgent(MockExperimentProvider(), InputRecorder(response))

    task = agent.build_task(plan)
    bundle = agent.generate_bundle(
        "run_1", "experiment_1", plan, task, "", "python", require_smoke_test=True
    )

    assert task["dataset"] == "fashion-mnist"
    assert task["dataset_contract_id"] == "dataset_fashion"
    assert task["dataset_root"] == "/datasets/fashionmnist"
    assert bundle.manifest.dataset == "fashion-mnist"
    assert bundle.manifest.dataset_contract_id == "dataset_fashion"
    assert bundle.manifest.dataset_fingerprint == "sha256:fashion"
    generated = agent.llm_provider.calls[0]
    assert generated["inputs"]["locked_dataset"] == {
        "canonical_name": "fashion-mnist",
        "display_name": "FashionMNIST",
        "contract_id": "dataset_fashion",
        "data_root": "/datasets/fashionmnist",
        "content_fingerprint": "sha256:fashion",
    }
    assert "Locked Dataset" in generated["instructions"]


def test_flat_local_fashion_mnist_contract_requires_idx_loader_and_instructs_repair():
    plan = {
        "dataset": {
            "canonical_name": "fashion-mnist",
            "contract_id": "dataset_fashion",
            "content_fingerprint": "sha256:fashion",
            "root": "/datasets/fashionmnist",
            "files": [
                {"relative_path": name}
                for name in (
                    "train-images-idx3-ubyte.gz",
                    "train-labels-idx1-ubyte.gz",
                    "t10k-images-idx3-ubyte.gz",
                    "t10k-labels-idx1-ubyte.gz",
                )
            ],
        }
    }
    response = {
        "files": [{"path": "train.py", "content": "import os\nimport torchvision\nroot = os.environ['DATA_ROOT']\ndataset = torchvision.datasets.FashionMNIST(root=root, download=False)\n"}],
        "requirements": ["torchvision"],
        "requires_gpu": False,
        "dataset": "fashion-mnist",
        "expected_metrics": [],
        "parameters": {},
        "seeds": [],
    }
    agent = ExperimentAgent(MockExperimentProvider(), InputRecorder(response))

    with pytest.raises(ValueError, match="EXPERIMENT_LOCAL_DATASET_LAYOUT_UNSUPPORTED"):
        agent.generate_bundle(
            "run_1", "experiment_1", plan, {"plan": plan}, "", "python"
        )

    instructions = agent._local_dataset_layout_instructions(plan)
    assert "IDX gzip" in instructions
    assert "torchvision.datasets.FashionMNIST" in instructions
