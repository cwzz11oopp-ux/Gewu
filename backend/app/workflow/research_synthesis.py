"""Stable, provenance-first literature synthesis for hypothesis formation.

The synthesis is deliberately built from every verified literature card.  It is an
idea-formation record, separate from the later claim-evidence validation ledger.
No paper is promoted to a gap without preserving its source paper and claim IDs.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Any


_FUTURE_WORK = (
    "future work", "future research", "future direction", "further work",
    "further research", "in future", "outlook", "remains to be explored",
)
_LIMITATION = (
    "limitation", "limited", "limiting", "challenge", "drawback", "lack of",
    "has not been", "not been", "unexplored", "remains unknown",
)
_CONFLICT = ("however", "but", "contradict", "inconsistent", "fails", "not ")


def stable_paper_id(card: dict[str, Any], index: int = 0) -> str:
    identifiers = card.get("identifiers") if isinstance(card.get("identifiers"), dict) else {}
    anchor = (
        identifiers.get("doi") or identifiers.get("arxiv") or card.get("doi")
        or card.get("arxiv") or card.get("url") or card.get("source_url")
        or card.get("title") or str(index)
    )
    return "PAPER-" + hashlib.sha256(str(anchor).strip().casefold().encode("utf-8")).hexdigest()[:12]


def build_research_synthesis(references: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a deterministic, complete-collection synthesis with stable lineage.

    This does not claim an unverified experimental result: it only organizes source
    text into findings, limitations and author-proposed future work.  A downstream
    LLM receives this synthesis, not a silently truncated paper list.
    """
    papers: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    themes: dict[str, dict[str, Any]] = {}
    future_work: list[dict[str, Any]] = []
    limitations: list[dict[str, Any]] = []
    established: list[dict[str, Any]] = []
    conflicting: list[dict[str, Any]] = []

    for paper_index, raw_value in enumerate(references):
        # KnowledgeIntegration keeps validated cards as Pydantic models; API
        # checkpoints expose the same cards as dicts.  Both are one contract.
        raw = raw_value.model_dump() if hasattr(raw_value, "model_dump") else raw_value
        if not isinstance(raw, dict) or raw.get("verified") is False:
            continue
        paper_id = stable_paper_id(raw, paper_index)
        title = _text(raw.get("title")) or "Untitled literature record"
        paper = {
            "paper_id": paper_id,
            "title": title,
            "url": _text(raw.get("url"), raw.get("source_url")),
            "year": raw.get("year"),
            "source": _text(raw.get("source"), raw.get("venue"), raw.get("journal")),
            "retrieval_intent": _text(raw.get("retrieval_intent")) or "RELATED_APPLICATION",
        }
        papers.append(paper)
        theme_key = paper["retrieval_intent"]
        theme = themes.setdefault(theme_key, {
            "theme_id": "THEME-" + hashlib.sha256(theme_key.casefold().encode("utf-8")).hexdigest()[:8],
            "title": _theme_title(theme_key),
            "description": f"Literature retrieved for {theme_key}.",
            "source_paper_ids": [],
            "source_claim_ids": [],
        })
        theme["source_paper_ids"].append(paper_id)

        source_text = _text(raw.get("available_text"), raw.get("abstract"), raw.get("summary"))
        sentences = _sentences(source_text) or [title]
        for sentence_index, sentence in enumerate(sentences):
            if len(sentence) < 12:
                continue
            claim_id = _claim_id(paper_id, sentence_index, sentence)
            claim = {
                "claim_id": claim_id,
                "paper_id": paper_id,
                "claim": sentence,
                "locator": f"abstract.sentence.{sentence_index + 1}" if source_text else "title",
                "source_reference": {"title": title, "url": paper["url"]},
            }
            claims.append(claim)
            theme["source_claim_ids"].append(claim_id)
            lower = sentence.casefold()
            if _contains(lower, _FUTURE_WORK):
                future_work.append({**claim, "future_work_id": "FW-" + claim_id[6:], "kind": "author_proposed"})
            elif _contains(lower, _LIMITATION):
                limitations.append({**claim, "kind": "limitation"})
            elif _contains(lower, _CONFLICT):
                conflicting.append({**claim, "kind": "conflicting_finding"})
            else:
                established.append({**claim, "kind": "established_finding"})

    gaps = _gaps_from_limitations_and_future_work(limitations, future_work)
    return {
        "schema_version": 1,
        "source_collection": {
            "paper_count": len(papers),
            "selection_policy": "all_verified_references",
            "source_paper_ids": [paper["paper_id"] for paper in papers],
        },
        "papers": papers,
        "claims": claims,
        "themes": list(themes.values()),
        "established_findings": established,
        "conflicting_findings": conflicting,
        "limitations": limitations,
        "future_work": future_work,
        "research_gaps": gaps,
    }


def build_gap_processing_pipeline(
    synthesis: dict[str, Any], *, batch_max_chars: int = 3_600
) -> dict[str, Any]:
    """Process every gap deterministically before constructing an LLM payload.

    A character budget may make a *single prompt* finite, but it must never turn
    into a positional gap filter.  Each gap is placed in exactly one detailed
    batch; batch summaries then feed a secondary structural synthesis.  The
    resulting lineage is persisted with the research synthesis artifact.
    """
    gaps = _records(synthesis.get("research_gaps"))
    batches: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    used = 2
    for gap in gaps:
        detail = {
            "gap_id": str(gap.get("gap_id") or ""),
            "title": _short(str(gap.get("title") or "Research gap"), 180),
            "description": _short(str(gap.get("description") or ""), 900),
            "gap_type": str(gap.get("gap_type") or ""),
            "source_paper_ids": list(gap.get("source_paper_ids") or []),
            "source_claim_ids": list(gap.get("source_claim_ids") or []),
            "source_future_work_ids": list(gap.get("source_future_work_ids") or []),
        }
        size = len(json.dumps(detail, ensure_ascii=False, sort_keys=True))
        if current and used + size > batch_max_chars:
            batches.append(_gap_batch(len(batches) + 1, current))
            current, used = [], 2
        current.append(detail)
        used += size
    if current:
        batches.append(_gap_batch(len(batches) + 1, current))
    source_gap_ids = [str(gap.get("gap_id") or "") for gap in gaps if gap.get("gap_id")]
    processed_gap_ids = [
        gap_id for batch in batches for gap_id in batch["source_gap_ids"]
    ]
    if processed_gap_ids != source_gap_ids:
        raise ValueError("GAP_PROCESSING_COVERAGE_INCOMPLETE")
    return {
        "processing_version": 1,
        "total_gap_count": len(source_gap_ids),
        "processed_gap_count": len(processed_gap_ids),
        "batch_count": len(batches),
        "source_gap_ids": source_gap_ids,
        "gap_coverage": 1.0 if processed_gap_ids == source_gap_ids else 0.0,
        "batch_synthesis": batches,
        "secondary_synthesis": {
            "method": "deterministic_structural_secondary_synthesis",
            "source_gap_ids": source_gap_ids,
            "batch_count": len(batches),
            "summary": (
                f"All {len(source_gap_ids)} research gaps were processed across "
                f"{len(batches)} deterministic batches before hypothesis formation."
            ),
        },
    }


def synthesis_prompt_context(synthesis: dict[str, Any], max_chars: int = 12_000) -> dict[str, Any]:
    """Return a bounded final context without dropping any gap from processing."""
    pipeline = synthesis.get("hypothesis_gap_processing")
    if not isinstance(pipeline, dict):
        pipeline = build_gap_processing_pipeline(synthesis)
    if pipeline.get("processed_gap_count") != pipeline.get("total_gap_count"):
        raise ValueError("GAP_PROCESSING_COVERAGE_INCOMPLETE")
    themes = [
        {
            "theme_id": item.get("theme_id"),
            "title": _short(str(item.get("title") or ""), 120),
            "source_paper_ids": list(item.get("source_paper_ids") or []),
        }
        for item in _records(synthesis.get("themes"))
    ]
    # Every gap remains represented by ID in the bounded secondary synthesis.
    # Full per-gap detail is retained in ``batch_synthesis`` in the Artifact.
    secondary = pipeline.get("secondary_synthesis") or {}
    context = {
        "schema_version": synthesis.get("schema_version", 1),
        "source_collection": synthesis.get("source_collection") or {},
        "themes": themes,
        "gap_processing": {
            key: pipeline.get(key)
            for key in (
                "total_gap_count", "processed_gap_count", "batch_count",
                "source_gap_ids", "gap_coverage",
            )
        },
        "secondary_synthesis": {
            "method": secondary.get("method"),
            "batch_count": secondary.get("batch_count"),
            "summary": secondary.get("summary"),
        },
        "batch_summaries": [
            {
                "batch_id": batch.get("batch_id"),
                "processed_gap_count": batch.get("processed_gap_count"),
                "summary": batch.get("summary"),
            }
            for batch in pipeline.get("batch_synthesis") or []
        ],
        "provenance_note": "Every gap entered a detailed batch and the secondary synthesis; use only listed GAP IDs.",
        # Supplementary code evidence is never substituted for literature or
        # used to remove a gap from the all-gap processing contract.
        "code_evidence": [
            {
                key: item.get(key)
                for key in ("code_evidence_id", "repository_url", "repository_commit", "source_file", "symbol", "line_start", "line_end", "claim", "file_hash")
            }
            for item in _records(synthesis.get("code_evidence"))
        ],
    }
    # The compact representation is intentionally ID-centric, so 5, 45, or 100
    # gaps all remain in scope.  A malformed, unexpectedly huge payload is a
    # contract error rather than a reason to silently skip trailing records.
    if len(json.dumps(context, ensure_ascii=False)) > max_chars:
        context["themes"] = [
            {"theme_id": item.get("theme_id"), "title": item.get("title")}
            for item in themes
        ]
    if len(json.dumps(context, ensure_ascii=False)) > max_chars:
        raise ValueError("HYPOTHESIS_CONTEXT_BUDGET_EXCEEDED_WITH_COMPLETE_GAP_LINEAGE")
    return context


def representative_paper_ids(synthesis: dict[str, Any], limit: int = 8) -> list[str]:
    """Choose at most one representative source per theme before filling slots."""
    output: list[str] = []
    for theme in _records(synthesis.get("themes")):
        for paper_id in theme.get("source_paper_ids") or []:
            if isinstance(paper_id, str) and paper_id not in output:
                output.append(paper_id)
                break
        if len(output) >= limit:
            return output
    for paper in _records(synthesis.get("papers")):
        paper_id = paper.get("paper_id")
        if isinstance(paper_id, str) and paper_id not in output:
            output.append(paper_id)
        if len(output) >= limit:
            break
    return output


def normalize_candidate_synthesis_provenance(candidate: dict[str, Any], synthesis: dict[str, Any]) -> dict[str, Any]:
    """Validate candidate-selected gaps and derive only their real source lineage."""
    value = dict(candidate)
    gaps = {str(item.get("gap_id")): item for item in _records(synthesis.get("research_gaps"))}
    requested = [str(item) for item in value.get("source_gap_ids") or [] if str(item) in gaps]
    # Empty lineage is represented explicitly rather than invented from position.
    value["source_gap_ids"] = list(dict.fromkeys(requested))
    paper_ids: list[str] = []
    claim_ids: list[str] = []
    future_work_ids: list[str] = []
    for gap_id in value["source_gap_ids"]:
        gap = gaps[gap_id]
        paper_ids.extend(str(item) for item in gap.get("source_paper_ids") or [])
        claim_ids.extend(str(item) for item in gap.get("source_claim_ids") or [])
        future_work_ids.extend(str(item) for item in gap.get("source_future_work_ids") or [])
    value["source_paper_ids"] = list(dict.fromkeys(paper_ids))
    value["source_claim_ids"] = list(dict.fromkeys(claim_ids))
    value["source_future_work_ids"] = list(dict.fromkeys(future_work_ids))
    value["reasoning_summary"] = _text(value.get("reasoning_summary"), value.get("research_gap"), value.get("motivation"))
    value["provenance_status"] = "grounded" if value["source_gap_ids"] else "unavailable"
    return value


def candidate_synthesis_provenance_issues(
    candidates: list[dict[str, Any]], synthesis: dict[str, Any]
) -> list[str]:
    """Return repairable provenance violations before a candidate is accepted."""
    known = {str(item.get("gap_id")) for item in _records(synthesis.get("research_gaps"))}
    issues: list[str] = []
    for index, candidate in enumerate(candidates):
        candidate_id = str(candidate.get("candidate_id") or f"candidate={index}")
        requested = candidate.get("source_gap_ids")
        if not isinstance(requested, list) or not [item for item in requested if str(item).strip()]:
            issues.append(f"CANDIDATE_PROVENANCE_REQUIRED:{candidate_id}")
            continue
        unknown = [str(item) for item in requested if str(item) not in known]
        if unknown:
            issues.append(
                f"CANDIDATE_PROVENANCE_UNKNOWN_GAP:{candidate_id}:{','.join(unknown)}"
            )
    return issues


def normalize_candidate_code_evidence_provenance(candidate: dict[str, Any], synthesis: dict[str, Any]) -> dict[str, Any]:
    """Keep only genuine persisted code-evidence IDs, never infer by order."""
    value = dict(candidate)
    known = {str(item.get("code_evidence_id")) for item in _records(synthesis.get("code_evidence"))}
    requested = [str(item) for item in value.get("source_code_evidence_ids") or []]
    value["source_code_evidence_ids"] = list(dict.fromkeys(item for item in requested if item in known))
    return value


def candidate_code_evidence_provenance_issues(candidates: list[dict[str, Any]], synthesis: dict[str, Any]) -> list[str]:
    """Reject invented code IDs instead of masking them with a null fallback."""
    known = {str(item.get("code_evidence_id")) for item in _records(synthesis.get("code_evidence"))}
    issues: list[str] = []
    for index, candidate in enumerate(candidates):
        candidate_id = str(candidate.get("candidate_id") or f"candidate={index}")
        requested = candidate.get("source_code_evidence_ids") or []
        unknown = [str(item) for item in requested if str(item) not in known]
        if unknown:
            issues.append(f"CANDIDATE_PROVENANCE_UNKNOWN_CODE_EVIDENCE:{candidate_id}:{','.join(unknown)}")
    return issues


def evaluate_literature_coverage(
    synthesis: dict[str, Any], *, retrieved_count: int, verified_count: int,
    hard_cap: int, previous_synthesis: dict[str, Any] | None = None,
    minimum_theme_count: int = 2, minimum_method_coverage: float = 0.60,
    minimum_conclusion_coverage: float = 0.60, minimum_limitations_coverage: float = 0.50,
    minimum_future_work_coverage: float = 0.50, minimum_gap_stability: float = 0.70,
    saturation_new_information_rate: float = 0.10,
) -> dict[str, Any]:
    """Evaluate scientific coverage separately from operational resource limits."""
    papers = max(1, int(synthesis.get("source_collection", {}).get("paper_count") or 0))
    themes = _records(synthesis.get("themes"))
    findings = _records(synthesis.get("established_findings")) + _records(synthesis.get("conflicting_findings"))
    limitations = _records(synthesis.get("limitations"))
    future_work = _records(synthesis.get("future_work"))
    gaps = _records(synthesis.get("research_gaps"))
    method_coverage = min(1.0, len(themes) / max(1, minimum_theme_count))
    conclusion_coverage = min(1.0, len(findings) / papers)
    limitations_coverage = min(1.0, len(limitations) / papers)
    future_work_coverage = min(1.0, len(future_work) / papers)
    prior_gap_ids = {
        str(item.get("gap_id")) for item in _records((previous_synthesis or {}).get("research_gaps"))
    }
    current_gap_ids = {str(item.get("gap_id")) for item in gaps}
    if previous_synthesis is None:
        gap_stability, new_information_rate = 0.0, 1.0
    else:
        overlap = len(prior_gap_ids & current_gap_ids)
        gap_stability = overlap / max(1, len(prior_gap_ids | current_gap_ids))
        new_information_rate = len(current_gap_ids - prior_gap_ids) / max(1, len(current_gap_ids))
    components = [method_coverage, conclusion_coverage, limitations_coverage, future_work_coverage]
    coverage_score = round(sum(components) / len(components), 3)
    saturation_score = round((gap_stability + (1.0 - new_information_rate)) / 2, 3)
    sufficient = (
        len(themes) >= minimum_theme_count
        and method_coverage >= minimum_method_coverage
        and conclusion_coverage >= minimum_conclusion_coverage
        and limitations_coverage >= minimum_limitations_coverage
        and future_work_coverage >= minimum_future_work_coverage
    )
    saturated = sufficient and previous_synthesis is not None and gap_stability >= minimum_gap_stability and new_information_rate <= saturation_new_information_rate
    hard_cap_reached = int(verified_count) >= int(hard_cap)
    insufficient_reasons = []
    if len(themes) < minimum_theme_count:
        insufficient_reasons.append(f"themes={len(themes)}<{minimum_theme_count}")
    if method_coverage < minimum_method_coverage:
        insufficient_reasons.append(f"method_coverage={method_coverage:.2f}<{minimum_method_coverage:.2f}")
    if conclusion_coverage < minimum_conclusion_coverage:
        insufficient_reasons.append(f"conclusion_coverage={conclusion_coverage:.2f}<{minimum_conclusion_coverage:.2f}")
    if limitations_coverage < minimum_limitations_coverage:
        insufficient_reasons.append(f"limitations_coverage={limitations_coverage:.2f}<{minimum_limitations_coverage:.2f}")
    if future_work_coverage < minimum_future_work_coverage:
        insufficient_reasons.append(f"future_work_coverage={future_work_coverage:.2f}<{minimum_future_work_coverage:.2f}")
    return {
        "retrieved_count": int(retrieved_count),
        "verified_count": int(verified_count),
        "theme_count": len(themes), "gap_count": len(gaps),
        "method_coverage": round(method_coverage, 3),
        "conclusion_coverage": round(conclusion_coverage, 3),
        "limitations_coverage": round(limitations_coverage, 3),
        "future_work_coverage": round(future_work_coverage, 3),
        "gap_stability": round(gap_stability, 3),
        "new_information_rate": round(new_information_rate, 3),
        "coverage_score": coverage_score, "saturation_score": saturation_score,
        "sufficient": sufficient, "insufficient_reasons": insufficient_reasons,
        "hard_cap": int(hard_cap), "hard_cap_reached": hard_cap_reached,
        "decision": "saturated" if saturated else "hard_cap_reached" if hard_cap_reached else "continue",
    }


def _gap_batch(index: int, details: list[dict[str, Any]]) -> dict[str, Any]:
    source_gap_ids = [item["gap_id"] for item in details]
    return {
        "batch_id": f"GAP-BATCH-{index:03d}",
        "source_gap_ids": source_gap_ids,
        "processed_gap_count": len(source_gap_ids),
        "gaps": details,
        "summary": f"Processed {len(source_gap_ids)} detailed research-gap records.",
    }


def _gaps_from_limitations_and_future_work(limitations: list[dict[str, Any]], future_work: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for kind, records in (("synthesized", limitations), ("author_proposed", future_work)):
        for item in records:
            grouped[_topic_key(str(item.get("claim") or ""))].append((kind, item))
    gaps: list[dict[str, Any]] = []
    for index, entries in enumerate(grouped.values(), start=1):
        kinds = {kind for kind, _ in entries}
        first = entries[0][1]
        papers = list(dict.fromkeys(str(item.get("paper_id")) for _, item in entries if item.get("paper_id")))
        claims = list(dict.fromkeys(str(item.get("claim_id")) for _, item in entries if item.get("claim_id")))
        future_ids = list(dict.fromkeys(str(item.get("future_work_id")) for kind, item in entries if kind == "author_proposed" and item.get("future_work_id")))
        gaps.append({
            "gap_id": f"GAP-{index:03d}",
            "title": _short(str(first.get("claim") or "Research gap"), 96),
            "description": str(first.get("claim") or ""),
            "gap_type": "mixed" if len(kinds) > 1 else next(iter(kinds)),
            "source_paper_ids": papers,
            "source_claim_ids": claims,
            "source_future_work_ids": future_ids,
            "confidence": round(min(1.0, 0.5 + 0.1 * len(papers)), 3),
        })
    return gaps


def _claim_id(paper_id: str, index: int, text: str) -> str:
    raw = f"{paper_id}:{index}:{text.casefold()}"
    return "CLAIM-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _sentences(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", value) if part.strip()]


def _contains(value: str, words: tuple[str, ...]) -> bool:
    return any(word in value for word in words)


def _theme_title(intent: str) -> str:
    return intent.replace("_", " ").title() if intent else "Related literature"


def _topic_key(value: str) -> str:
    terms = re.findall(r"[a-z0-9][a-z0-9_-]{2,}", value.casefold())
    return " ".join(sorted(set(terms))[:6]) or value.casefold()[:96]


def _records(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return re.sub(r"\s+", " ", value).strip()
    return ""


def _short(value: str, length: int) -> str:
    return value if len(value) <= length else value[: length - 1] + "…"
