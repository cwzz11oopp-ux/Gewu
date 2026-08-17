from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit


EVIDENCE_TYPES = {"FACT", "INFERENCE", "ASSUMPTION"}
MAX_TARGETED_QUERIES = 3


def build_evidence_audit(
    evidence: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    registry = [_registry_entry(index, card) for index, card in enumerate(evidence)]
    by_url = {
        entry["normalized_url"]: entry
        for entry in registry
        if entry["normalized_url"]
    }
    by_title = {
        entry["normalized_title"]: entry
        for entry in registry
        if entry["normalized_title"]
    }
    scoped_mode = any(
        isinstance(candidate.get("evidence_basis"), list)
        and bool(candidate["evidence_basis"])
        for candidate in candidates
    )
    candidate_audits = [
        _audit_candidate(index, candidate, by_url, by_title, scoped_mode)
        for index, candidate in enumerate(candidates)
    ]
    return {
        "policy": {
            "mode": "scoped" if scoped_mode else "legacy_unscoped",
            "verified_sources_only": True,
            "unmatched_sources_block_verification": scoped_mode,
            "missing_evidence_basis_blocks_verification": scoped_mode,
        },
        "registry": registry,
        "candidate_audits": candidate_audits,
    }


def targeted_evidence_queries(
    assessments: list[dict[str, Any]],
    *,
    limit: int = MAX_TARGETED_QUERIES,
) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for assessment in assessments:
        if assessment.get("status") not in {"evidence_insufficient", "rejected"}:
            continue
        reasoning = assessment.get("critic_reasoning") or {}
        evaluation = assessment.get("evaluation") or {}
        pools = (
            reasoning.get("required_evidence"),
            reasoning.get("unsupported_claims"),
            evaluation.get("unknowns"),
        )
        for pool in pools:
            if not isinstance(pool, list):
                continue
            for value in pool:
                query = re.sub(r"\s+", " ", str(value)).strip()
                if not query:
                    continue
                query = query[:180]
                key = query.casefold()
                if key in seen:
                    continue
                seen.add(key)
                queries.append(query)
                if len(queries) >= max(1, limit):
                    return queries
    if queries:
        return queries
    for assessment in assessments:
        if assessment.get("status") not in {"evidence_insufficient", "rejected"}:
            continue
        hypothesis = assessment.get("revised_hypothesis") or assessment.get(
            "original_hypothesis"
        ) or {}
        query = re.sub(r"\s+", " ", str(hypothesis.get("claim") or "")).strip()[:180]
        if query and query.casefold() not in seen:
            seen.add(query.casefold())
            queries.append(query)
        if len(queries) >= max(1, limit):
            break
    return queries


def _registry_entry(index: int, card: dict[str, Any]) -> dict[str, Any]:
    identifiers = {
        str(key): str(value)
        for key, value in (card.get("identifiers") or {}).items()
        if str(value).strip()
    }
    reference_id = str(card.get("reference_id") or "")
    if not identifiers and reference_id.startswith("doi:"):
        identifiers["doi"] = reference_id[4:]
    if not identifiers and reference_id.startswith("arxiv:"):
        identifiers["arxiv"] = reference_id[6:]
    title = str(card.get("title") or "").strip()
    url = str(card.get("url") or "").strip()
    source_key = (
        f"doi:{identifiers['doi'].casefold()}"
        if identifiers.get("doi")
        else (
            f"arxiv:{_normalize_arxiv(identifiers['arxiv'])}"
            if identifiers.get("arxiv")
            else f"title:{_normalize_title(title)}"
        )
    )
    evidence_id = f"E{index + 1}-{hashlib.sha256(source_key.encode('utf-8')).hexdigest()[:8]}"
    return {
        "evidence_id": evidence_id,
        "title": title,
        "url": url,
        "identifiers": identifiers,
        "verified": bool(card.get("verified", True)),
        "relevance": float(card.get("relevance") or 0.0),
        "reliability": float(card.get("reliability") or 0.0),
        "conflict_notes": [str(item) for item in (
            card.get("conflict_notes") or card.get("supports_or_limits") or []
        ) if str(item).strip()],
        "normalized_url": _normalize_url(url),
        "normalized_title": _normalize_title(title),
    }


def _audit_candidate(
    candidate_index: int,
    candidate: dict[str, Any],
    by_url: dict[str, dict[str, Any]],
    by_title: dict[str, dict[str, Any]],
    scoped_mode: bool,
) -> dict[str, Any]:
    raw_basis = candidate.get("evidence_basis")
    basis = raw_basis if isinstance(raw_basis, list) else []
    links = []
    matched_ids: list[str] = []
    unmatched_sources = []
    type_counts = {key: 0 for key in sorted(EVIDENCE_TYPES)}

    for item in basis:
        if not isinstance(item, dict):
            unmatched_sources.append({"reason": "INVALID_EVIDENCE_BASIS_ENTRY"})
            continue
        source_url = str(item.get("source_url") or "").strip()
        source_title = str(item.get("source_title") or "").strip()
        evidence_type = str(item.get("evidence_type") or "ASSUMPTION").upper()
        if evidence_type not in EVIDENCE_TYPES:
            evidence_type = "ASSUMPTION"
        type_counts[evidence_type] += 1
        matched = (
            by_url.get(_normalize_url(source_url))
            if source_url
            else None
        ) or (
            by_title.get(_normalize_title(source_title))
            if source_title
            else None
        )
        link = {
            "statement": str(item.get("statement") or "").strip(),
            "source_title": source_title,
            "source_url": source_url,
            "evidence_type": evidence_type,
            "evidence_id": matched["evidence_id"] if matched else "",
            "matched_verified_source": bool(matched and matched["verified"]),
        }
        links.append(link)
        if matched and matched["verified"]:
            matched_ids.append(matched["evidence_id"])
        elif evidence_type != "ASSUMPTION" or source_title or source_url:
            unmatched_sources.append(
                {
                    "source_title": source_title,
                    "source_url": source_url,
                    "reason": "SOURCE_NOT_IN_VERIFIED_EVIDENCE",
                }
            )

    if not scoped_mode:
        gate = "UNKNOWN"
        issues = ["LEGACY_CANDIDATES_HAVE_NO_SCOPED_EVIDENCE_BASIS"]
    else:
        issues = []
        if not basis:
            issues.append("EVIDENCE_BASIS_MISSING")
        if unmatched_sources:
            issues.append("UNVERIFIED_OR_UNMATCHED_SOURCE")
        if not matched_ids:
            issues.append("NO_VERIFIED_SOURCE_LINK")
        gate = "PASS" if not issues else "FAIL"

    return {
        "candidate_index": candidate_index,
        "gate": gate,
        "matched_evidence_ids": list(dict.fromkeys(matched_ids)),
        "links": links,
        "unmatched_sources": unmatched_sources,
        "evidence_type_counts": type_counts,
        "issues": issues,
    }


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())


def _normalize_arxiv(value: str) -> str:
    return re.sub(r"v\d+$", "", value.strip().casefold())


def _normalize_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.casefold().rstrip("/")
    host = parts.netloc.casefold()
    path = re.sub(r"v\d+$", "", parts.path.rstrip("/").casefold())
    if host in {"arxiv.org", "www.arxiv.org"}:
        path = re.sub(r"^/(pdf|abs)/", "/abs/", path)
        if path.endswith(".pdf"):
            path = path[:-4]
        host = "arxiv.org"
    return urlunsplit(("", host, path, "", ""))
