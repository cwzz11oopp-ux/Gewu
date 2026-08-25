import pytest

from backend.app.workflow.research_constraints import normalize_constraints


def test_normalize_constraints_accepts_scalar_shorthand_from_api_clients():
    normalized = normalize_constraints({
        "task_type": "classification",
        "dataset": "FashionMNIST",
        "primary_metrics": "accuracy",
        "seed_policy": "3 fixed seeds",
        "split_policy": "official test split",
        "preprocessing_policy": "training statistics only",
    })

    assert normalized["dataset"] == {"name": "FashionMNIST"}
    assert normalized["primary_metrics"] == ["accuracy"]
    assert normalized["seed_policy"] == {"description": "3 fixed seeds"}
    assert normalized["split_policy"] == {"description": "official test split"}
    assert normalized["preprocessing_policy"] == {
        "description": "training statistics only"
    }


def test_normalize_constraints_rejects_ambiguous_non_collection_values():
    with pytest.raises(ValueError, match="RESEARCH_CONSTRAINT_SEED_POLICY_INVALID"):
        normalize_constraints({"seed_policy": 3})

