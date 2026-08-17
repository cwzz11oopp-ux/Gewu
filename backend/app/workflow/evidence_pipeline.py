"""Auditable literature-to-evidence helpers used before idea selection.

The workflow deliberately keeps a paper record separate from an evidence record:
a paper is a source; an evidence record is one source-grounded claim that can take
part in reasoning.  The routines here are deterministic fallbacks, so they remain
useful when an LLM is unavailable and never fabricate a citation or a result.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any


EVIDENCE_TYPES = {
    "METHOD",
    "MECHANISM",
    "RESULT",
    "LIMITATION",
    "RESEARCH_GAP",
    "DATASET_OBSERVATION",
    "COMPUTATIONAL_COST",
}


def extract_claim_evidence(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract conservative, sentence-level claims from abstract/available text.

    No sentence is promoted to evidence unless it is traceable to a verified paper.
    The classification is intentionally lightweight and explainable; an LLM extractor
    may enrich these records later without changing their provenance contract.
    """
    records: list[dict[str, Any]] = []
    for paper_index, paper in enumerate(papers):
        if not isinstance(paper, dict) or not paper.get("verified"):
            continue
        text = str(
            paper.get("available_text") or paper.get("abstract") or paper.get("claim") or ""
        ).strip()
        if not text:
            continue
        paper_id = _paper_id(paper, paper_index)
        for sentence_index, sentence in enumerate(_sentences(text)):
            if len(sentence) < 18:
                continue
            evidence_type = _classify(sentence)
            identifier = f"{paper_id}:{sentence_index}:{sentence.casefold()}"
            records.append(
                {
                    "evidence_id": "EVID-" + hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:12],
                    "paper_id": paper_id,
                    "claim": sentence,
                    "evidence_type": evidence_type,
                    "stance": "contradict" if _has(sentence, "however", "but", "fails", "limited", "limitation", "not") else "support",
                    "relation": "DIRECT" if evidence_type in {"RESULT", "METHOD", "DATASET_OBSERVATION"} else "INDIRECT",
                    "relevance_score": _number(paper.get("relevance")),
                    "confidence": _confidence(paper, sentence),
                    "source_quality": _number(paper.get("reliability"), default=0.5),
                    "source_title": str(paper.get("title") or ""),
                    "source_url": str(paper.get("url") or ""),
                    "verification_status": "verified",
                }
            )
    return records


def analyze_research_gaps(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn limitations/contradictions into experimentable, linked gaps."""
    gaps: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in evidence:
        if record.get("evidence_type") in {"LIMITATION", "RESEARCH_GAP"} or record.get("stance") == "contradict":
            grouped[_topic_key(str(record.get("claim") or ""))].append(record)
    for index, records in enumerate(grouped.values(), start=1):
        claims = [str(item["claim"]) for item in records]
        gaps.append(
            {
                "gap_id": f"GAP-{index:03d}",
                "description": claims[0],
                "supporting_evidence_ids": [item["evidence_id"] for item in records],
                "confidence": round(sum(_number(item.get("confidence")) for item in records) / len(records), 3),
                "experimentable": True,
            }
        )
    return gaps


def candidate_evidence_map(
    candidate: dict[str, Any], evidence: list[dict[str, Any]], gaps: list[dict[str, Any]]
) -> dict[str, Any]:
    """Match evidence claims to candidate subclaims rather than paper titles."""
    candidate_id = str(candidate.get("candidate_id") or candidate.get("id") or "")
    claim = " ".join(
        str(candidate.get(key) or "")
        for key in ("motivation", "mechanism", "research_gap", "claim", "hypothesis")
    )
    claim_terms = _terms(claim)
    matches: list[dict[str, Any]] = []
    for item in evidence:
        overlap = _overlap(claim_terms, _terms(str(item.get("claim") or "")))
        if overlap <= 0:
            continue
        matches.append(
            {
                "candidate_id": candidate_id,
                "evidence_id": item["evidence_id"],
                "candidate_claim": str(candidate.get("claim") or candidate.get("hypothesis") or ""),
                "stance": item.get("stance", "neutral"),
                "relation": item.get("relation", "INDIRECT"),
                "confidence": round(min(1.0, 0.55 * overlap + 0.45 * _number(item.get("confidence"))), 3),
            }
        )
    matches.sort(key=lambda item: item["confidence"], reverse=True)
    gap_ids = set(candidate.get("supporting_gap_ids") or [])
    gap_matches = [
        evidence_id
        for gap in gaps
        if not gap_ids or gap.get("gap_id") in gap_ids
        for evidence_id in gap.get("supporting_evidence_ids") or []
    ]
    supporting = [item["evidence_id"] for item in matches if item["stance"] == "support"]
    contradicting = [item["evidence_id"] for item in matches if item["stance"] == "contradict"]
    mechanism = str(candidate.get("mechanism") or "").strip()
    missing = []
    if not supporting:
        missing.append("motivation_or_component_mechanism")
    if not gap_matches and not str(candidate.get("research_gap") or "").strip():
        missing.append("research_gap")
    if not mechanism:
        missing.append("component_mechanism")
    return {
        "candidate_id": candidate_id,
        "motivation_evidence": supporting,
        "component_evidence": supporting,
        "gap_evidence": list(dict.fromkeys(gap_matches)),
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting,
        "unverified_claims": list(candidate.get("unverified_citations") or []),
        "missing_evidence": missing,
        "matches": matches[:12],
    }


def targeted_queries(candidate: dict[str, Any], evidence_map: dict[str, Any], critic: dict[str, Any]) -> list[str]:
    """Derive queries from actual missing claims, not a fixed template."""
    values = [
        *(critic.get("recommended_queries") or []),
        *(critic.get("required_evidence") or []),
        *(critic.get("unsupported_claims") or []),
    ]
    if evidence_map.get("missing_evidence"):
        values.extend(
            filter(
                None,
                [candidate.get("mechanism"), candidate.get("research_gap"), candidate.get("claim")],
            )
        )
    queries: list[str] = []
    seen: set[str] = set()
    for value in values:
        query = re.sub(r"\s+", " ", str(value or "")).strip()[:180]
        if query and query.casefold() not in seen:
            seen.add(query.casefold())
            queries.append(query)
    return queries[:4]


def _paper_id(paper: dict[str, Any], index: int) -> str:
    identifiers = paper.get("identifiers") or {}
    stable = identifiers.get("doi") or identifiers.get("arxiv") or paper.get("url") or paper.get("title") or str(index)
    return "PAPER-" + hashlib.sha256(str(stable).encode("utf-8")).hexdigest()[:12]


def _sentences(text: str) -> list[str]:
    return [value.strip() for value in re.split(r"(?<=[.!?。！？])\s+", text) if value.strip()]


def _classify(sentence: str) -> str:
    lower = sentence.casefold()
    if _has(lower, "limitation", "limited", "however", "future work", "not been", "lack"):
        return "LIMITATION"
    if _has(lower, "gap", "unexplored", "unknown", "remains"):
        return "RESEARCH_GAP"
    if _has(lower, "improve", "outperform", "result", "accuracy", "achieve", "reduce"):
        return "RESULT"
    if _has(lower, "dataset", "benchmark", "fashion-mnist", "imagenet"):
        return "DATASET_OBSERVATION"
    if _has(lower, "cost", "latency", "flops", "parameter", "overhead", "efficient"):
        return "COMPUTATIONAL_COST"
    if _has(lower, "mechanism", "attention", "representation", "feature", "because"):
        return "MECHANISM"
    return "METHOD"


def _has(value: str, *needles: str) -> bool:
    return any(needle in value for needle in needles)


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", value.casefold()) if term not in {"with", "that", "this", "from", "into", "using", "under"}}


def _overlap(left: set[str], right: set[str]) -> float:
    return len(left & right) / max(1, len(left))


def _topic_key(value: str) -> str:
    terms = sorted(_terms(value))[:5]
    return " ".join(terms) or value[:80].casefold()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _confidence(paper: dict[str, Any], sentence: str) -> float:
    directness = 0.8 if _classify(sentence) in {"METHOD", "RESULT", "DATASET_OBSERVATION"} else 0.65
    return round(min(1.0, 0.5 * _number(paper.get("reliability"), 0.5) + 0.3 * _number(paper.get("relevance")) + 0.2 * directness), 3)
