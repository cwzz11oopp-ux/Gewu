from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


@dataclass(frozen=True)
class PromptContextBudget:
    max_total_chars: int = 24_000
    max_problem_chars: int = 2_500
    max_hypothesis_chars: int = 8_000
    max_reference_chars: int = 7_000
    max_skill_chars: int = 4_000
    max_previous_artifact_chars: int = 1_500


def literature_card(card: dict[str, Any]) -> dict[str, Any]:
    identifiers = card.get("identifiers") or {}
    verified_identifier = identifiers.get("doi") or identifiers.get("arxiv") or ""
    return {
        "reference_id": f"doi:{identifiers['doi']}" if identifiers.get("doi") else (
            f"arxiv:{identifiers['arxiv']}" if identifiers.get("arxiv") else card.get("title", "")
        ),
        "title": str(card.get("title") or ""),
        "year": card.get("year"),
        "source": str(card.get("source") or card.get("source_kind") or ""),
        "verified_identifier": str(verified_identifier),
        "intent": str(card.get("retrieval_intent") or "RELATED_APPLICATION"),
        "relevance": float(card.get("relevance") or 0.0),
        "evidence_summary": str(card.get("claim") or ""),
        "claim": str(card.get("claim") or ""),
        "url": str(card.get("url") or ""),
        "method_summary": "",
        "supports_or_limits": list(card.get("conflict_notes") or []),
        "key_metric_or_result": "",
    }


def compact_problem(problem: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: problem[key]
        for key in ("problem_statement", "constraints", "knowledge_gaps")
        if key in problem
    }
    profile = problem.get("dataset_profile")
    if isinstance(profile, dict):
        compact["dataset_profile"] = {
            key: profile[key]
            for key in (
                "contract_id", "source_type", "inspection_status", "root", "name",
                "content_fingerprint", "file_count", "total_bytes", "file_types", "limitations",
            )
            if key in profile
        }
    return compact


def select_units(units: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used = 2
    for unit in units:
        size = len(json.dumps(unit, ensure_ascii=False, separators=(",", ":"))) + 1
        if used + size > max_chars:
            continue
        selected.append(unit)
        used += size
    return selected


def select_units_bounded(units: list[dict[str, Any]], max_chars: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bound context with explicit accounting instead of silent record loss."""
    selected = select_units(units, max_chars)
    selected_ids = {str(item.get("reference_id") or index) for index, item in enumerate(selected)}
    omitted = [
        str(item.get("reference_id") or index)
        for index, item in enumerate(units)
        if str(item.get("reference_id") or index) not in selected_ids
    ]
    total_chars = sum(len(json.dumps(item, ensure_ascii=False, separators=(",", ":"))) + 1 for item in selected) + 2
    return selected, {
        "components": [{"name": "units", "records": len(selected), "chars": total_chars,
                        "estimated_tokens": max(1, (total_chars + 3) // 4)}],
        "total_chars": total_chars, "estimated_tokens": max(1, (total_chars + 3) // 4),
        "budget": max_chars, "budget_status": "within_budget" if not omitted else "secondary_digest_required",
        "omitted_record_ids": omitted,
    }


def budget_instructions(instructions: str, budget: PromptContextBudget) -> str:
    if len(instructions) <= budget.max_skill_chars:
        return instructions
    sections = [part for part in instructions.split("\n\n") if part.strip()]
    selected: list[str] = []
    used = 0
    for section in sections:
        if used + len(section) + 2 > budget.max_skill_chars:
            continue
        selected.append(section)
        used += len(section) + 2
    return "\n\n".join(selected)
