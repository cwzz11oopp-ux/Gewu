from __future__ import annotations

import gzip
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.bootstrap import (
    DatasetDownloadApprovalRequired,
    GreenfieldBootstrapRequest,
    GreenfieldBootstrapService,
    inspect_local_dataset,
)
from backend.app.literature import SprintLiteratureService
from backend.app.main import create_app
from backend.app.research.ideator import BranchConstructor
from backend.app.services.v2_critic import ScientificCritic
from backend.app.services.v2_sessions import ResearchSessionService
from backend.app.storage.v2 import V2Stores


QUESTION = (
    "Can a lightweight classifier improve Fashion-MNIST accuracy through a lower-cost "
    "training strategy without increasing model size?"
)


class NoPapers:
    def search(self, query, limit):
        return []


def make_local_fashion(root: Path) -> Path:
    raw = root / "FashionMNIST" / "raw"
    raw.mkdir(parents=True)
    for name in (
        "train-images-idx3-ubyte.gz",
        "train-labels-idx1-ubyte.gz",
        "t10k-images-idx3-ubyte.gz",
        "t10k-labels-idx1-ubyte.gz",
    ):
        with gzip.open(raw / name, "wb") as stream:
            stream.write(b"local-fixture-content")
    (root / "FashionMNIST" / "values.csv").write_text(
        "x,label\n-1,0\n0.1,1\n0.8,1\n-0.4,0\n", encoding="utf-8"
    )
    return root


BASELINE_SOURCE = '''import argparse, csv, json
from pathlib import Path

THRESHOLD = 0.5

def evaluate(data_root):
    path = Path(data_root) / "FashionMNIST" / "values.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    correct = sum(int((float(row["x"]) >= THRESHOLD) == bool(int(row["label"]))) for row in rows)
    return correct / len(rows)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--output", default="")
    parser.add_argument("--protocol-fingerprint", default="")
    parser.add_argument("--seeds", nargs="*", type=int, default=[7])
    parser.add_argument("--data-root", required=True)
    args = parser.parse_args()
    metric = evaluate(args.data_root)
    if args.smoke:
        print("SMOKE_OK", metric)
        return
    Path(args.output).write_text(json.dumps({"metrics": {"accuracy": metric}, "seeds": args.seeds, "protocol_fingerprint": args.protocol_fingerprint}), encoding="utf-8")
    print("METRIC", metric)

if __name__ == "__main__":
    main()
'''


VARIANT_SOURCE = BASELINE_SOURCE.replace("THRESHOLD = 0.5", "THRESHOLD = 0.0")


class GreenfieldGateway:
    def __init__(self):
        self.calls = []

    def invoke_structured(self, task_type, messages, output_schema, context=None):
        self.calls.append(task_type)
        if task_type == "v2.greenfield.design_baseline":
            return output_schema.model_validate({
                "project_name": "local-first-classifier",
                "task_type": "binary classification",
                "method_summary": "One scalar threshold learned as a minimal reproducible baseline.",
                "entrypoint": "train.py",
                "metric_name": "accuracy",
                "metric_direction": "maximize",
                "metric_definition": "correct predictions divided by examples",
                "preprocessing": {"input": "raw scalar values"},
                "evaluation_protocol": {"evaluation": "all local values.csv rows"},
                "training_controls": {"parameter_count": 1},
                "epochs": 1,
                "seeds": [7],
                "environment_requirements": ["Python standard library"],
            })
        if task_type == "v2.greenfield.generate_repository":
            return output_schema.model_validate({
                "files": [
                    {"path": "train.py", "content": BASELINE_SOURCE, "purpose": "Executable baseline"},
                    {"path": ".gitignore", "content": "*-result.json\n__pycache__/\n", "purpose": "Keep runtime outputs untracked"},
                    {"path": "README.md", "content": "# Generated baseline\n", "purpose": "Document the project"},
                ],
                "smoke_description": "Load the local file and compute one metric.",
                "formal_run_description": "Evaluate every local row under the locked protocol.",
            })
        if task_type == "v2.ideator.construct_branches":
            common = {
                "mechanism": "Lowering the threshold corrects the positive example near zero.",
                "expected_observation": "Accuracy increases under the same local dataset fingerprint.",
                "falsification_condition": "Locked accuracy does not increase.",
                "minimal_experiment": "Change one threshold and rerun the locked command.",
                "closest_prior_work": [],
                "novelty_risk": "Fixture mechanism only.",
                "information_gain": "high",
                "scientific_potential": "medium",
                "estimated_compute_minutes": 1,
                "risk": "low",
                "initially_runnable": True,
                "required_prior_evidence": [],
            }
            return output_schema.model_validate({"proposals": [
                {**common, "research_gap": "Boundary bias", "hypothesis": "A lower threshold improves accuracy.", "proposed_change": "Lower THRESHOLD to 0.0."},
                {**common, "research_gap": "Input scaling", "hypothesis": "Normalization may stabilize decisions.", "proposed_change": "Normalize using training-only statistics."},
                {**common, "research_gap": "Seed stability", "hypothesis": "Replication may confirm stability.", "proposed_change": "Repeat locked evaluation."},
            ]})
        if task_type == "v2.repository.inspect":
            return output_schema.model_validate({"files": ["train.py"], "rationale": "Threshold is defined here."})
        if task_type == "v2.repository.implementation_plan":
            return output_schema.model_validate({
                "summary": "Lower only the threshold.",
                "edits": [{"path": "train.py", "replacement_content": VARIANT_SOURCE, "rationale": "Isolate boundary bias."}],
                "expected_effect": "The near-zero positive becomes correct.",
                "risks": ["Small local fixture"],
            })
        if task_type == "v2.critic.review_experiment":
            return output_schema.model_validate({
                "supported_claims": ["Accuracy improved under the locked local protocol."],
                "unsupported_claims": ["External generalization is untested."],
                "possible_mechanisms": ["Reduced threshold bias."],
                "alternative_explanations": ["Small dataset."],
                "methodological_issues": ["One seed."],
                "open_information_gaps": ["Does the gain replicate?"],
                "recommended_actions": ["RUN_REPLICATION"],
            })
        raise AssertionError(task_type)


def service(tmp_path: Path, gateway: GreenfieldGateway) -> GreenfieldBootstrapService:
    literature = SprintLiteratureService(NoPapers())
    sessions = ResearchSessionService(
        V2Stores(str(tmp_path / "state")),
        BranchConstructor(gateway),
        literature,
        model_ready=True,
        critic=ScientificCritic(gateway),
    )
    return GreenfieldBootstrapService(str(tmp_path / "app"), sessions, gateway, literature)


def test_dataset_profile_is_stable_local_and_traceable(tmp_path):
    root = make_local_fashion(tmp_path / "datasets")
    first = inspect_local_dataset(question=QUESTION, dataset_root=str(root))
    second = inspect_local_dataset(question=QUESTION, dataset_root=str(root))

    assert first.name == "fashion-mnist"
    assert first.availability == "available"
    assert first.sample_count == 70000
    assert first.class_count == 10
    assert first.input_shape == [1, 28, 28]
    assert first.corrupted_files == []
    assert first.fingerprint == second.fingerprint
    assert first.local_path == str((root / "FashionMNIST").resolve())

    (root / "FashionMNIST" / "values.csv").write_text("x,label\n1,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="DATASET_PROTOCOL_INCOMPATIBLE"):
        GreenfieldBootstrapService._assert_dataset_unchanged(first)


def test_online_strategy_requires_explicit_approval_and_never_downloads(tmp_path):
    bootstrap = service(tmp_path, GreenfieldGateway())
    with pytest.raises(DatasetDownloadApprovalRequired) as caught:
        bootstrap.inspect_dataset(GreenfieldBootstrapRequest(
            question=QUESTION,
            dataset_strategy="online",
            dataset_root=str(tmp_path / "datasets"),
            allow_online_dataset_download=False,
        ))
    assert caught.value.detail["dataset_name"] == "fashion-mnist"
    assert not (tmp_path / "datasets").exists()


def test_greenfield_dataset_api_reports_profile_and_download_approval(tmp_path):
    dataset_root = make_local_fashion(tmp_path / "datasets")
    app = create_app(
        data_dir=str(tmp_path / "api"),
        env={"COMPETITION_MODE": "false", "LLM_PROVIDER": "mock", "LITERATURE_PROVIDER": "mock", "EXPERIMENT_PROVIDER": "mock"},
    )
    client = TestClient(app)
    payload = {
        "question": QUESTION,
        "research_mode": "greenfield",
        "dataset_strategy": "auto_local",
        "dataset_root": str(dataset_root),
    }
    response = client.post("/api/v2/research/sessions/bootstrap/datasets/inspect", json=payload)
    assert response.status_code == 200
    assert response.json()["name"] == "fashion-mnist"
    assert response.json()["source"] == "local"

    payload.update({"dataset_strategy": "online", "dataset_root": str(tmp_path / "empty")})
    approval = client.post("/api/v2/research/sessions/bootstrap/datasets/inspect", json=payload)
    assert approval.status_code == 409
    assert approval.json()["detail"]["code"] == "ONLINE_DATASET_DOWNLOAD_APPROVAL_REQUIRED"


def test_greenfield_bootstrap_creates_real_repository_baseline_and_first_experiment(tmp_path):
    dataset_root = make_local_fashion(tmp_path / "datasets")
    gateway = GreenfieldGateway()
    bootstrap = service(tmp_path, gateway)
    result = bootstrap.run(GreenfieldBootstrapRequest(
        question=QUESTION,
        dataset_root=str(dataset_root),
        dataset_strategy="auto_local",
        run_first_experiment=True,
        experiment_limit=4,
        model_call_limit=10,
    ))

    repository = Path(result.repository_path)
    assert (repository / ".git").is_dir()
    assert (repository / "train.py").is_file()
    assert result.baseline.can_be_comparison_denominator
    assert result.baseline.local_metrics == {"accuracy": 0.75}
    assert result.first_experiment is not None
    assert result.first_experiment["metrics"] == {"accuracy": 1.0}
    assert result.first_experiment["comparison"]["compatible"] is True
    assert result.first_experiment["audit_passed"] is True
    assert result.first_action is not None
    assert result.first_action["operator"] == "RUN_EXPERIMENT"
    assert result.online_download_performed is False
    assert result.dataset_profile.fingerprint == result.baseline.dataset.fingerprint
    assert bootstrap.get(result.session_id).baseline_commit == result.baseline_commit
    assert gateway.calls[:3] == [
        "v2.greenfield.design_baseline",
        "v2.greenfield.generate_repository",
        "v2.ideator.construct_branches",
    ]
