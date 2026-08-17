from __future__ import annotations

import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from backend.app.models.literature import LocalDocument
from backend.app.models.provider import EvidenceCard
from backend.app.providers.literature import LiteratureProvider
from backend.app.storage.literature import LiteratureLibrary
from backend.app.storage.research_wiki import ResearchWikiStore, WikiChangeSet
from backend.app.workflow.literature_policy import (
    LiteratureQuery,
    LiteratureRetrievalPolicy,
    normalize_queries,
)
from backend.app.workflow.research_synthesis import (
    build_research_synthesis,
    evaluate_literature_coverage,
)


class KnowledgeIntegrationResult(BaseModel):
    references: list[EvidenceCard] = Field(default_factory=list)
    core_references: list[EvidenceCard] = Field(default_factory=list)
    local_only: list[EvidenceCard] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sources: dict = Field(default_factory=dict)
    literature_coverage: dict = Field(default_factory=dict)
    wiki_changes: WikiChangeSet


class KnowledgeIntegrationService:
    def __init__(
        self,
        wiki: ResearchWikiStore,
        library: LiteratureLibrary,
        external_provider: LiteratureProvider,
        external_limit: int | None = None,
        policy: LiteratureRetrievalPolicy | None = None,
    ) -> None:
        self.wiki = wiki
        self.library = library
        self.external_provider = external_provider
        self.policy = policy or LiteratureRetrievalPolicy()
        self.external_limit = external_limit or self.policy.max_results_per_query

    def collect(self, run_id: str, problem: dict) -> KnowledgeIntegrationResult:
        queries = _queries(problem)
        return self.collect_queries(run_id, queries)

    def collect_queries(
        self,
        run_id: str,
        queries: list[object],
    ) -> KnowledgeIntegrationResult:
        expanded_queries: list[object] = []
        for value in queries:
            expanded_queries.append(value)
            query = str(value.get("query") or "") if isinstance(value, dict) else str(value)
            fallback = _english_query_fallback(query)
            if fallback:
                if isinstance(value, dict):
                    expanded_queries.append({**value, "query": fallback})
                else:
                    expanded_queries.append(fallback)
        query_specs = normalize_queries(expanded_queries, self.policy)
        candidates: list[EvidenceCard] = []
        warnings: list[str] = []
        calls: list[dict[str, str]] = []
        raw_counts = {"wiki": 0, "local": 0, "external": 0}

        for spec in query_specs:
            query = spec.query
            try:
                remaining = self.policy.max_results_per_source - raw_counts["wiki"]
                wiki_limit = min(self.external_limit, max(0, remaining))
                try:
                    wiki_result = self.wiki.query(query, limit=wiki_limit)
                except TypeError:
                    wiki_result = self.wiki.query(query)
                    wiki_result.papers = wiki_result.papers[:wiki_limit]
                calls.append({"source": "wiki", "query": query, "intent": spec.intent.value})
                warnings.extend(wiki_result.warnings)
                cards = [_with_query(_wiki_card(paper), spec) for paper in wiki_result.papers]
                raw_counts["wiki"] += len(cards)
                candidates.extend(cards)
            except Exception:
                calls.append({"source": "wiki", "query": query, "intent": spec.intent.value})
                warnings.append("WIKI_DEGRADED")

            try:
                remaining = self.policy.max_results_per_source - raw_counts["local"]
                local_documents = self.library.search(
                    query, min(self.external_limit, max(0, remaining))
                )
                calls.append({"source": "local", "query": query, "intent": spec.intent.value})
                cards = [_with_query(_local_card(document), spec) for document in local_documents]
                raw_counts["local"] += len(cards)
                candidates.extend(cards)
            except Exception:
                calls.append({"source": "local", "query": query, "intent": spec.intent.value})
                warnings.append("LOCAL_LITERATURE_DEGRADED")

            try:
                remaining = self.policy.max_results_per_source - raw_counts["external"]
                external_cards = self.external_provider.search(
                    query, min(self.external_limit, max(0, remaining))
                )
                calls.append({"source": "external", "query": query, "intent": spec.intent.value})
                cards = [
                    _with_query(card.model_copy(update={"source_kind": "external"}), spec)
                    for card in external_cards
                ]
                raw_counts["external"] += len(cards)
                candidates.extend(cards)
            except Exception:
                calls.append({"source": "external", "query": query, "intent": spec.intent.value})
                warnings.append("EXTERNAL_LITERATURE_FAILED")

        merged = _merge(candidates)
        ranked_all = _rerank(merged, query_specs, self.policy)
        # The cap is a configurable safety guard.  It is deliberately recorded
        # separately from the scientific coverage decision below.
        ranked = ranked_all[: self.policy.max_candidate_count]
        core = _diverse_core(ranked, self.policy.max_core_reference_count)
        references = [card for card in ranked if card.exportable]
        local_only = [card for card in merged if not card.exportable]
        coverage = evaluate_literature_coverage(
            build_research_synthesis([card.model_dump() for card in references]),
            retrieved_count=len(ranked_all),
            verified_count=len(references), hard_cap=self.policy.max_candidate_count,
            minimum_theme_count=self.policy.minimum_theme_count,
            minimum_method_coverage=self.policy.minimum_method_coverage,
            minimum_conclusion_coverage=self.policy.minimum_conclusion_coverage,
            minimum_limitations_coverage=self.policy.minimum_limitations_coverage,
            minimum_future_work_coverage=self.policy.minimum_future_work_coverage,
            minimum_gap_stability=self.policy.minimum_gap_stability,
            saturation_new_information_rate=self.policy.saturation_new_information_rate,
        )
        proposed = [card.model_dump() for card in references[:12]]
        return KnowledgeIntegrationResult(
            references=references,
            core_references=core,
            local_only=local_only,
            warnings=list(dict.fromkeys(warnings)),
            sources={
                "calls": calls,
                "query_count": len(query_specs),
                "queries": [spec.model_dump(mode="json") for spec in query_specs],
                "raw_candidate_count": sum(raw_counts.values()),
                "raw_counts": raw_counts,
                "bounded_candidate_count": len(ranked),
                "dedup_count": len(merged),
                "core_reference_count": len(core),
                "wiki": sum(card.source_kind == "wiki" for card in merged),
                "local": sum(bool(card.local_document_id) for card in merged),
                "external": sum(card.source_kind == "external" for card in merged),
                "hard_caps": {
                    "query_count": self.policy.max_query_count,
                    "results_per_query": self.policy.max_results_per_query,
                    "results_per_source": self.policy.max_results_per_source,
                    "candidate_count": self.policy.max_candidate_count,
                    "core_reference_count": self.policy.max_core_reference_count,
                },
            },
            literature_coverage=coverage,
            wiki_changes=WikiChangeSet(
                papers=proposed,
                origin_run_id=run_id,
            ),
        )


def merge_verified_evidence(
    existing: list[dict] | list[EvidenceCard],
    additional: list[EvidenceCard],
) -> list[EvidenceCard]:
    cards = [
        item if isinstance(item, EvidenceCard) else EvidenceCard.model_validate(item)
        for item in existing
    ]
    return [card for card in _merge([*cards, *additional]) if card.exportable]


ENGLISH_QUERY_TERMS = (
    ("大语言模型", "language model"),
    ("语言模型", "language model"),
    ("神经网络", "neural network"),
    ("训练", "training"),
    ("消融", "ablation study"),
    ("评估", "evaluation"),
    ("基准", "benchmark"),
    ("对齐", "alignment"),
    ("可置信", "trustworthy"),
    ("鲁棒", "robustness"),
    ("架构", "architecture"),
    ("数据集", "dataset"),
)


def evidence_key(card: EvidenceCard) -> str:
    if card.identifiers.get("doi"):
        return f"doi:{card.identifiers['doi'].lower()}"
    if card.identifiers.get("arxiv"):
        return f"arxiv:{card.identifiers['arxiv'].lower()}"
    return f"title:{_normalize_title(card.title)}"


def _queries(problem: dict) -> list[object]:
    values = problem.get("literature_queries") or [problem.get("problem_statement") or ""]
    return list(values)


def _expand_queries(values: list[str]) -> list[str]:
    queries = []
    for value in values:
        query = str(value).strip()
        if not query:
            continue
        queries.append(query)
        fallback = _english_query_fallback(query)
        if fallback:
            queries.append(fallback)
    return list(dict.fromkeys(queries))


def _english_query_fallback(query: str) -> str:
    if query.isascii():
        return ""
    terms = [
        english
        for chinese, english in ENGLISH_QUERY_TERMS
        if chinese in query
    ]
    if not terms:
        return "machine learning research"
    return " ".join(dict.fromkeys(terms))


def _wiki_card(paper: dict) -> EvidenceCard:
    return EvidenceCard(
        title=str(paper.get("title") or paper.get("id") or "Wiki paper"),
        authors=[str(item) for item in paper.get("authors") or []],
        year=paper.get("year"),
        source=str(paper.get("source") or "research_wiki"),
        source_kind="wiki",
        local_document_id=paper.get("local_document_id"),
        claim=str(paper.get("abstract") or ""),
        url=str(paper.get("url") or ""),
        identifiers=dict(paper.get("identifiers") or {}),
        verified=bool(paper.get("verified")),
    )


def _local_card(document: LocalDocument) -> EvidenceCard:
    return EvidenceCard(
        title=document.title or document.filename,
        authors=document.authors,
        year=document.year,
        source=document.source,
        source_kind="local",
        local_document_id=document.id,
        claim=document.abstract,
        url=(
            f"https://doi.org/{document.identifiers['doi']}"
            if document.identifiers.get("doi")
            else ""
        ),
        identifiers=document.identifiers,
        verified=document.verification.verified,
    )


def _merge(candidates: list[EvidenceCard]) -> list[EvidenceCard]:
    merged: dict[str, EvidenceCard] = {}
    for card in candidates:
        key = evidence_key(card)
        existing = merged.get(key)
        if existing is None:
            merged[key] = card
            continue
        local_document_id = existing.local_document_id or card.local_document_id
        preferred = card if _quality(card) > _quality(existing) else existing
        if local_document_id and preferred.local_document_id != local_document_id:
            preferred = preferred.model_copy(update={"local_document_id": local_document_id})
        merged[key] = preferred
    return list(merged.values())


def _quality(card: EvidenceCard) -> tuple[int, int, float]:
    source_priority = {"external": 3, "wiki": 2, "local": 1}[card.source_kind]
    return (1 if card.exportable else 0, source_priority, card.reliability)


def _with_query(card: EvidenceCard, spec: LiteratureQuery) -> EvidenceCard:
    return card.model_copy(update={
        "retrieval_intent": spec.intent.value,
        "target_gap": spec.target_gap,
    })


def _rerank(
    cards: list[EvidenceCard],
    queries: list[LiteratureQuery],
    policy: LiteratureRetrievalPolicy,
) -> list[EvidenceCard]:
    query_terms = set(
        re.findall(r"[a-z0-9]+", " ".join(item.query for item in queries).casefold())
    )
    current_year = datetime.now(timezone.utc).year

    def score(card: EvidenceCard) -> tuple[float, str, float]:
        text_terms = set(re.findall(r"[a-z0-9]+", f"{card.title} {card.claim}".casefold()))
        lexical = len(query_terms & text_terms) / max(1, len(query_terms))
        # Provider relevance is only one signal, never a blanket default.  The
        # query/document match is calculated for every card and persisted below.
        relevance = min(1.0, 0.65 * lexical + 0.35 * float(card.relevance))
        quality = max(float(card.reliability), 1.0 if card.exportable else 0.0)
        recency = max(0.0, 1.0 - max(0, current_year - (card.year or current_year)) / 20)
        source_bonus = {"external": 1.0, "wiki": 0.7, "local": 0.6}[card.source_kind]
        value = (
            policy.relevance_weight * relevance
            + policy.quality_weight * quality
            + policy.recency_weight * recency
            + policy.diversity_weight * source_bonus
        )
        return (-value, card.title.casefold(), relevance)

    ranked = sorted(cards, key=score)
    return [
        card.model_copy(update={"relevance": round(score(card)[2], 4)})
        for card in ranked
    ]


def _diverse_core(cards: list[EvidenceCard], limit: int) -> list[EvidenceCard]:
    exportable = [card for card in cards if card.exportable]
    groups: dict[str, list[EvidenceCard]] = {}
    for card in exportable:
        groups.setdefault(card.retrieval_intent, []).append(card)
    selected: list[EvidenceCard] = []
    while groups and len(selected) < limit:
        for intent in list(groups):
            if groups[intent]:
                selected.append(groups[intent].pop(0))
                if len(selected) >= limit:
                    break
            if not groups[intent]:
                groups.pop(intent)
    return selected


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
