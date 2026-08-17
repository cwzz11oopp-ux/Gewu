"""Append-only Phase 3 scientific Idea evolution decisions (no runtime/model calls)."""
from __future__ import annotations
from copy import deepcopy
from typing import Any

IDEA_FIELDS = ("baseline_problem", "modification", "mechanism", "evidence_ids", "minimal_experiment", "expected_observation")
MAX_IDEA_VERSIONS = 3

def candidate_issues(ideas: list[dict[str, Any]], constraints: dict[str, Any]) -> list[str]:
    issues = []
    if len(ideas) != 4: issues.append("PHASE3_EXACTLY_FOUR_IDEAS_REQUIRED")
    frozen = " ".join(str(x) for x in (constraints.get("frozen") or []))
    for index, idea in enumerate(ideas):
        for field in IDEA_FIELDS:
            if not idea.get(field): issues.append(f"IDEA_{index + 1}_{field.upper()}_REQUIRED")
        if frozen and any(token.casefold() in str(idea).casefold() for token in frozen.split() if token): issues.append(f"IDEA_{index + 1}_VIOLATES_FROZEN_CONSTRAINT")
    return issues

def rank_ideas(ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked=[]
    for index, raw in enumerate(ideas):
        idea=deepcopy(raw); idea.setdefault("idea_id", f"IDEA-{index+1:02d}")
        idea["innovation_score"] = float(idea.get("innovation_score") or 0)
        idea["positive_improvement_probability"] = float(idea.get("positive_improvement_probability") or 0)
        idea["feasible"] = bool(idea.get("feasible", True))
        idea["ranking_reason"] = str(idea.get("ranking_reason") or "Ranked by innovation and positive-improvement probability after feasibility admission.")
        ranked.append(idea)
    ranked.sort(key=lambda item: (item["feasible"], item["innovation_score"], item["positive_improvement_probability"]), reverse=True)
    for rank, idea in enumerate(ranked, 1): idea["rank"] = rank
    return ranked

def next_scientific_action(*, evidence_route: str, stage: str, version: int, engineering_error: bool = False, formal_positive: bool = False, continue_remaining_ideas: bool = False) -> dict[str, Any]:
    if engineering_error: return {"action": "engineering_diagnosis", "consume_version": False}
    if evidence_route == "expand_validation" and stage == "small_scale": return {"action": "formal_validation", "consume_version": False}
    if formal_positive and not continue_remaining_ideas: return {"action": "selected_idea", "consume_version": False}
    if evidence_route == "scientific_review" or (stage == "formal_validation" and not formal_positive):
        return {"action": "archive_and_next_idea" if version >= MAX_IDEA_VERSIONS else "scientific_diagnosis_and_revision", "consume_version": version < MAX_IDEA_VERSIONS}
    return {"action": "await_result", "consume_version": False}

def revision_payload(current: dict[str, Any], diagnosis: dict[str, Any], new_evidence_ids: list[str], version: int) -> dict[str, Any]:
    if version > MAX_IDEA_VERSIONS: raise ValueError("PHASE3_IDEA_VERSION_LIMIT")
    return {"idea_id": current["idea_id"], "version": version, "parent_version": version - 1, "why_modified": diagnosis.get("why_result_differs", "Scientific diagnosis required revision."), "modification": diagnosis.get("recommended_modification", current.get("modification")), "new_evidence_ids": list(new_evidence_ids), "new_expected_mechanism": diagnosis.get("revised_mechanism", current.get("mechanism")), "new_minimal_experiment": diagnosis.get("minimal_experiment", current.get("minimal_experiment")), "result_evidence_id": diagnosis.get("result_evidence_id", "")}

def outcome_for_archives(archived_idea_ids: list[str], total_ideas: int = 4) -> str | None:
    return "completed_negative" if len(set(archived_idea_ids)) >= total_ideas else None
