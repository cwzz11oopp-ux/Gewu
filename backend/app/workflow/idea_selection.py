from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any

from backend.app.workflow.hypothesis_contract import normalize_candidates


WEIGHTS = {
    "novelty": 0.20,
    "scientific_soundness": 0.20,
    "impact": 0.15,
    "testability": 0.20,
    "execution_feasibility": 0.20,
    "reproducibility_compliance": 0.05,
}

DECISIONS = {"GO", "REVISE", "PIVOT", "STOP", "EVIDENCE_INSUFFICIENT"}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
_AUDIT_FIELD_TYPES = {
    "evidence_ledger": list,
    "closest_prior_work": list,
    "gates": dict,
    "mde": dict,
    "risks": list,
    "unknowns": list,
}


def weighted_score(scores: dict) -> float:
    return sum(float(scores[key]) * weight for key, weight in WEIGHTS.items())


def select_top_evaluation(evaluations: list[dict]) -> dict:
    viable = [
        evaluation
        for evaluation in evaluations
        if evaluation.get("decision") in {"GO", "REVISE"}
    ]
    if not viable:
        raise ValueError(
            "NO_VIABLE_HYPOTHESIS: no candidate passed the GO/REVISE decision gate"
        )
    winner = sorted(
        viable,
        key=lambda value: (-weighted_score(value["scores"]), value["candidate_index"]),
    )[0]
    return {
        "selected_index": winner["candidate_index"],
        "selected": winner,
        "weighted_score": weighted_score(winner["scores"]),
        "selection_reason": "Highest server-computed weighted idea score.",
    }


def normalize_idea_review(raw: dict, original_candidates: list[dict]) -> dict:
    """Validate provider output and remove provider-selected winner claims."""
    candidates = normalize_candidates(original_candidates)
    if not isinstance(original_candidates, list) or len(candidates) != len(original_candidates):
        _invalid("original_candidates must contain normalized candidate objects")
    candidate_count = len(candidates)
    if not isinstance(raw, dict):
        _invalid("review must be an object")

    evaluations = raw.get("evaluations")
    if not isinstance(evaluations, list) or len(evaluations) != candidate_count:
        _invalid("review must contain one evaluation per candidate")

    normalized = [
        _validate_evaluation(evaluation, candidates)
        for evaluation in evaluations
    ]
    indices = [evaluation["candidate_index"] for evaluation in normalized]
    if set(indices) != set(range(candidate_count)) or len(set(indices)) != len(indices):
        _invalid("candidate indices must be unique and cover every candidate")

    return {"evaluations": sorted(normalized, key=lambda evaluation: evaluation["candidate_index"])}


def _validate_evaluation(evaluation: Any, candidates: list[dict]) -> dict:
    if not isinstance(evaluation, dict):
        _invalid("evaluation must be an object")
    candidate_index = evaluation.get("candidate_index")
    if not _is_index(candidate_index):
        _invalid("candidate_index must be an integer")
    if candidate_index < 0 or candidate_index >= len(candidates):
        _invalid("candidate_index is out of range")

    normalized = deepcopy(evaluation)
    if not isinstance(normalized.get("idea_card"), dict):
        normalized["idea_card"] = deepcopy(candidates[candidate_index])

    for field, expected_type in _AUDIT_FIELD_TYPES.items():
        if not isinstance(normalized.get(field), expected_type):
            _invalid(f"{field} has an invalid shape")

    scores = normalized.get("scores")
    if not isinstance(scores, dict):
        _invalid("scores must be an object")
    if set(scores) != set(WEIGHTS):
        _invalid("scores must contain exactly the required score keys")
    for score_name in WEIGHTS:
        score = scores.get(score_name)
        if not _is_score(score):
            _invalid(f"{score_name} must be a finite number from 0 to 5")

    if normalized.get("decision") not in DECISIONS:
        _invalid("decision is invalid")
    if normalized.get("confidence") not in CONFIDENCE_LEVELS:
        _invalid("confidence is invalid")
    return normalized


def _is_index(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_score(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and 0 <= value <= 5
    )


def _invalid(detail: str) -> None:
    raise ValueError(f"IDEA_SELECTION_OUTPUT_INVALID: {detail}")
