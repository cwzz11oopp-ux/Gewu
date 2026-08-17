from __future__ import annotations

from copy import deepcopy
from typing import Any


MIN_HYPOTHESIS_CANDIDATES = 3
MAX_HYPOTHESIS_CANDIDATES = 5

FORBIDDEN_HYPOTHESIS_KEYS = {
    "created_at",
    "created_order",
    "index",
    "order",
    "position",
    "priority",
    "rank",
    "rating",
    "recommendation",
    "recommendation_score",
    "recommended",
    "score",
    "sequence",
    "sort_key",
    "time",
    "timestamp",
    "updated_at",
}

PROVENANCE_KEYS = {"provider_mode", "fallback_used", "fallback_reason"}
CLAIM_ALIAS_KEYS = ("claim", "hypothesis")


def _candidate_claim(candidate: dict[str, Any]) -> Any:
    for key in CLAIM_ALIAS_KEYS:
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def normalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        key: deepcopy(value)
        for key, value in candidate.items()
        if key not in FORBIDDEN_HYPOTHESIS_KEYS and key != "hypothesis"
    }
    claim = _candidate_claim(candidate)
    if claim is not None:
        normalized["claim"] = deepcopy(claim)
    return normalized


def normalize_candidates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    candidates = []
    for candidate in value:
        if not isinstance(candidate, dict):
            continue
        normalized = normalize_candidate(candidate)
        if normalized.get("claim"):
            normalized.setdefault("candidate_id", f"CAND-{len(candidates) + 1:03d}")
            candidates.append(normalized)
        if len(candidates) >= MAX_HYPOTHESIS_CANDIDATES:
            break
    return candidates


def normalize_hypothesis_content(content: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "candidates": normalize_candidates(content.get("candidates")),
    }
    for key in PROVENANCE_KEYS:
        if key in content:
            normalized[key] = deepcopy(content[key])
    return normalized


def hypothesis_candidate_issues(content: dict[str, Any]) -> list[str]:
    candidates = normalize_candidates(content.get("candidates"))
    issues: list[str] = []
    if len(candidates) < MIN_HYPOTHESIS_CANDIDATES:
        issues.append(
            f"Generate {MIN_HYPOTHESIS_CANDIDATES} to {MAX_HYPOTHESIS_CANDIDATES} "
            "technically distinct candidate hypotheses; do not return a single candidate."
        )
    claims = [str(candidate.get("claim") or "").strip().casefold() for candidate in candidates]
    if len(set(claims)) != len(claims):
        issues.append("Candidate hypotheses must have distinct claims, not wording variants.")
    return issues
