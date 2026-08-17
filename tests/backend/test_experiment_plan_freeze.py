from backend.app.agents.experiment import ExperimentAgent


def test_rejected_candidate_cannot_poison_plan_seed_or_parameter_contract():
    plan = {
        "dataset": {"canonical_name": "fashion-mnist", "contract_id": "dataset_1", "content_fingerprint": "sha256:test"},
        "parameters": {"batch_size": 128, "epochs": 20, "learning_rate": 0.001},
        "seeds": [42, 123, 456],
    }
    task = {"run_id": "run_1", "experiment_id": "experiment_1", "result_id": "experiment_1_result"}
    frozen = ExperimentAgent.frozen_contract_from_candidate(
        plan, task, {"requires_gpu": True, "dataset": "fashion-mnist", "parameters": {}, "seeds": [42]}
    )
    assert frozen["parameters"] == plan["parameters"]
    assert frozen["seeds"] == plan["seeds"]
