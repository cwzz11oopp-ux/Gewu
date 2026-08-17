from __future__ import annotations

import hashlib
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field

from backend.app.literature.paper_card import AccessLevel, PaperCard
from backend.app.models.literature import LocalDocument
from backend.app.models.provider import EvidenceCard
from backend.app.providers.literature import LiteratureProvider
from backend.app.research.evidence import (
    EvidenceRelation,
    EvidenceSourceType,
    EvidenceUnit,
)
from backend.app.storage.literature import LiteratureLibrary


class LiteratureEvidenceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queries: list[str]
    papers: list[PaperCard] = Field(default_factory=list)
    evidence_units: list[EvidenceUnit] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SprintLiteratureService:
    """Honest metadata/abstract/full-text intake using existing providers."""

    def __init__(
        self,
        provider: LiteratureProvider,
        local_library: LiteratureLibrary | None = None,
    ) -> None:
        self.provider = provider
        self.local_library = local_library

    def research(self, queries: list[str], *, limit_per_query: int = 5) -> LiteratureEvidenceResult:
        normalized = [item.strip() for item in queries if item.strip()]
        if not normalized:
            raise ValueError("LITERATURE_QUERY_REQUIRED")
        external: list[EvidenceCard] = []
        local: list[LocalDocument] = []
        warnings: list[str] = []
        for query in normalized:
            try:
                external.extend(self.provider.search(query, limit_per_query))
            except Exception as exc:
                warnings.append(
                    f"external search failed for {query!r}: {type(exc).__name__}"
                )
            if self.local_library is not None:
                local.extend(self.local_library.search(query, limit_per_query))
        papers = self._deduplicate(
            [self._from_external(card) for card in external]
            + [self._from_local(document) for document in local]
        )
        evidence = [self._evidence(card) for card in papers if card.abstract]
        return LiteratureEvidenceResult(
            queries=normalized,
            papers=papers,
            evidence_units=evidence,
            gaps=self._gaps(papers),
            warnings=warnings,
        )

    @staticmethod
    def _from_external(card: EvidenceCard) -> PaperCard:
        identifier = card.identifiers.get("doi") or card.identifiers.get("arxiv")
        stable = identifier or card.url or card.title.lower()
        paper_id = "paper_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:12]
        access = AccessLevel.ABSTRACT_ONLY if card.claim.strip() else AccessLevel.METADATA_ONLY
        return PaperCard(
            paper_id=paper_id,
            title=card.title,
            authors=card.authors,
            year=card.year,
            abstract=card.claim,
            access_level=access,
            source=card.source,
            url=card.url,
            identifiers=card.identifiers,
            verified=card.verified,
            provenance={
                "source_kind": card.source_kind,
                "relevance": card.relevance,
                "reliability": card.reliability,
                "extraction_scope": access,
            },
        )

    @staticmethod
    def _from_local(document: LocalDocument) -> PaperCard:
        has_text = "parsed" in document.statuses
        access = (
            AccessLevel.FULL_TEXT
            if has_text
            else AccessLevel.ABSTRACT_ONLY
            if document.abstract
            else AccessLevel.METADATA_ONLY
        )
        return PaperCard(
            paper_id=document.id,
            title=document.title or document.filename,
            authors=document.authors,
            year=document.year,
            abstract=document.abstract,
            access_level=access,
            source="local_upload",
            identifiers=document.identifiers,
            verified=document.verification.verified,
            provenance={
                "filename": document.filename,
                "sha256": document.sha256,
                "statuses": document.statuses,
                "extraction_scope": access,
            },
        )

    @staticmethod
    def _deduplicate(cards: Iterable[PaperCard]) -> list[PaperCard]:
        selected: dict[str, PaperCard] = {}
        access_rank = {
            AccessLevel.METADATA_ONLY: 0,
            AccessLevel.ABSTRACT_ONLY: 1,
            AccessLevel.FULL_TEXT: 2,
        }
        for card in cards:
            key = (
                card.identifiers.get("doi", "").lower()
                or card.identifiers.get("arxiv", "").lower()
                or card.url.lower()
                or card.title.lower()
            )
            current = selected.get(key)
            if current is None or access_rank[card.access_level] > access_rank[current.access_level]:
                selected[key] = card
        return sorted(selected.values(), key=lambda item: (-int(item.verified), item.title.lower()))

    @staticmethod
    def _evidence(card: PaperCard) -> EvidenceUnit:
        return EvidenceUnit(
            source_type=EvidenceSourceType.LITERATURE,
            paper_id=card.paper_id,
            claim=card.abstract,
            relation=EvidenceRelation.CONTEXT,
            strength=0.7 if card.verified else 0.4,
            verified=card.verified,
            section="abstract" if card.access_level != AccessLevel.FULL_TEXT else "available local text",
            location="abstract" if card.access_level != AccessLevel.FULL_TEXT else "local extracted text",
            access_level=card.access_level,
            provenance={"source": card.source, "identifiers": card.identifiers},
        )

    @staticmethod
    def _gaps(papers: list[PaperCard]) -> list[str]:
        if not papers:
            return ["No relevant literature evidence was retrieved."]
        gaps: list[str] = []
        if not any(item.access_level == AccessLevel.FULL_TEXT for item in papers):
            gaps.append("No full text is available; method and result details remain unverified.")
        if not any(item.datasets for item in papers):
            gaps.append("Dataset-specific evidence has not been extracted from accessible sources.")
        if not any(item.main_results for item in papers):
            gaps.append("Comparable quantitative results have not been extracted.")
        return gaps
