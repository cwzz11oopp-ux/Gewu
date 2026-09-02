from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from backend.app.workflow.literature_contract import literature_summary_text
from backend.app.workflow.research_synthesis import stable_paper_id


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
    summary = literature_summary_text(card)
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
        "evidence_summary": summary,
        "claim": summary,
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


# ---------------------------------------------------------------------------
# Hypothesis literature input: relevance filter + dedup + Compact Paper Cards.
#
# The hypothesis step previously collapsed the collection to a few
# "representative" papers and then silently char-budgeted the rest.  That
# violated the contract that every valid paper participates in hypothesis
# formation.  This pipeline instead (1) drops only papers that are clearly
# unrelated to the research question, (2) merges exact duplicates, and (3)
# compacts each remaining paper into a fixed Compact Paper Card.  Every paper
# that survives filter+dedup enters hypothesis.generate -- no representative
# sampling and no character-budget truncation.
# ---------------------------------------------------------------------------

# Relevance floor used only when a paper shares no vocabulary with the research
# question at all.  Observed retrieval relevance is noisy -- relevant and
# irrelevant papers overlap across almost the whole score range (on real data
# relevant papers score 0.065-0.361 while unrelated noise scores 0.065-0.29),
# so relevance alone is not a decision.  Question-overlap alone keeps a paper,
# and this floor removes only the "essentially rejected" band (below the lowest
# score a relevant paper was ever observed at) when there is zero overlap.  The
# removal bar stays "clearly unrelated", per the standing spec.
_HYPOTHESIS_RELEVANCE_FLOOR = 0.06


def _question_terms(research_question: str) -> tuple[str, ...]:
    """Deterministic English terms derived from the CURRENT research question.

    No hardcoded domain vocabulary: the terms are whatever English tokens the
    question text actually contains (e.g. "Fashion", "MNIST", "SE", "ECA"), so
    the filter adapts to any question.  Tokens of length >= 2 are kept and are
    matched word-boundary-safe, so "SE" matches the SE block but not the "se"
    inside "sentence".  A question with no English tokens yields no terms, and
    then the filter removes nothing.
    """
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9-]*", research_question or ""):
        for part in re.split(r"[^A-Za-z0-9]", token):
            part = part.strip().casefold()
            if len(part) >= 2:
                terms.add(part)
    return tuple(sorted(terms))


def _first_sentence(text: str, limit: int = 240) -> str:
    """Deterministic first-sentence extractor for the paper's headline aim."""
    text = (text or "").strip()
    if not text:
        return ""
    first = re.split(r"(?<=[.!?。！？])\s+", text, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit]


def _clearly_irrelevant(card: dict[str, Any], question_terms: tuple[str, ...]) -> bool:
    """True only when a paper shares NO question vocabulary AND is low-scored.

    Either signal alone keeps the paper, because the removal bar is literature
    that is obviously unrelated to the research question.  With no question
    terms (e.g. a question with no English tokens) the filter removes nothing.
    """
    if not question_terms:
        return False
    lowered = " ".join(
        str(card.get(key) or "") for key in (
            "title", "claim", "research_goal", "problem", "abstract", "summary",
            "available_text",
        )
    ).casefold()
    if any(
        re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", lowered)
        for term in question_terms
    ):
        return False
    return float(card.get("relevance") or 0.0) < _HYPOTHESIS_RELEVANCE_FLOOR


def _hypothesis_dedup_key(card: dict[str, Any]) -> str:
    """doi -> arxiv -> normalized title; a paper with no anchor is never merged."""
    identifiers = card.get("identifiers") if isinstance(card.get("identifiers"), dict) else {}
    doi = str(identifiers.get("doi") or card.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi
    arxiv = str(identifiers.get("arxiv") or card.get("arxiv") or "").strip().lower()
    if arxiv:
        return "arxiv:" + arxiv
    title = re.sub(r"[^0-9a-z]+", " ", str(card.get("title") or "").casefold()).strip()
    if title:
        return "title:" + title
    return ""


def filter_and_dedupe_hypothesis_cards(
    cards: list[Any], research_question: str = ""
) -> dict[str, Any]:
    """Filter clearly-irrelevant and duplicate cards, preserving source order.

    The relevance decision is deterministic and question-driven: English terms
    are derived from the current research question (no hardcoded domain words),
    and a card is removed only when it shares none of them AND its existing
    retrieval relevance is below the conservative floor.  Returns the surviving
    cards plus the A/B/C/N accounting the user asked for: raw_count (A) ->
    irrelevant_removed (B) -> duplicate_merged (C) -> valid_count (N).  All N
    cards are expected to enter hypothesis.generate.
    """
    question_terms = _question_terms(research_question)
    raw_count = 0
    irrelevant_removed = 0
    duplicate_merged = 0
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for card in cards:
        raw = card.model_dump() if hasattr(card, "model_dump") else card
        if not isinstance(raw, dict):
            continue
        raw_count += 1
        if _clearly_irrelevant(raw, question_terms):
            irrelevant_removed += 1
            continue
        key = _hypothesis_dedup_key(raw)
        if not key:
            kept.append(raw)
            continue
        if key in seen:
            duplicate_merged += 1
            continue
        seen.add(key)
        kept.append(raw)
    return {
        "cards": kept,
        "counts": {
            "raw_count": raw_count,
            "irrelevant_removed": irrelevant_removed,
            "duplicate_merged": duplicate_merged,
            "valid_count": len(kept),
        },
    }


def build_hypothesis_context(card: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """Convert one evidence card into a Compact Paper Card.

    Fields that the source card does not carry are left empty (or empty lists)
    rather than invented.  No LLM is called to fill them.  The card keeps its
    stable paper_id so downstream provenance still resolves.
    """
    abstract = literature_summary_text(card)
    identifiers = card.get("identifiers") if isinstance(card.get("identifiers"), dict) else {}
    identifier = str(
        identifiers.get("doi") or identifiers.get("arxiv")
        or card.get("doi") or card.get("arxiv") or ""
    )
    return {
        "paper_id": stable_paper_id(card, index),
        "title": str(card.get("title") or ""),
        "year": card.get("year"),
        "research_goal": _first_sentence(abstract),
        "core_method": "",
        "contribution": "",
        # retrieval_intent is the closest equivalent available for "task": it
        # records which scientific role the paper plays (direct method,
        # contradictory evidence, mechanism, benchmark).
        "task": str(card.get("retrieval_intent") or ""),
        "dataset": "",
        "baseline": "",
        "protocol": "",
        "metrics": [],
        "key_results": "",
        "improvement": "",
        "ablation": "",
        "limitations_gap": str(card.get("target_gap") or ""),
        "future_work": [],
        "evidence": abstract,
        "provenance": {
            "source": str(card.get("source") or card.get("source_kind") or ""),
            "identifier": identifier,
            "url": str(card.get("url") or ""),
        },
    }
