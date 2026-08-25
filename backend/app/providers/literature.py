from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx

from backend.app.config import Settings
from backend.app.models.provider import EvidenceCard


class LiteratureProvider(Protocol):
    provider_name: str

    def search(self, query: str, limit: int) -> list[EvidenceCard]: ...
    def verify(self, card: EvidenceCard) -> EvidenceCard: ...
    def verify_identifier(self, identifiers: dict[str, str]) -> EvidenceCard | None: ...


class MockLiteratureProvider:
    provider_name = "mock_literature"

    def __init__(self, path: str | None = None) -> None:
        self.path = Path(path or Path(__file__).parent / "mock_data" / "papers.json")

    def search(self, query: str, limit: int) -> list[EvidenceCard]:
        records = json.loads(self.path.read_text(encoding="utf-8"))
        return [EvidenceCard.model_validate(record) for record in records[:limit]]

    def verify(self, card: EvidenceCard) -> EvidenceCard:
        return card.model_copy(update={"verified": card.exportable})

    def verify_identifier(self, identifiers: dict[str, str]) -> EvidenceCard | None:
        records = json.loads(self.path.read_text(encoding="utf-8"))
        for record in records:
            card = EvidenceCard.model_validate(record)
            if any(
                value and card.identifiers.get(key, "").lower() == value.lower()
                for key, value in identifiers.items()
            ):
                return card.model_copy(update={"verified": True})
        return None


class ArxivCrossrefProvider:
    provider_name = "arxiv_crossref"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.Client(timeout=20.0)

    def search(self, query: str, limit: int) -> list[EvidenceCard]:
        response = self.client.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": min(limit, self.settings.arxiv_max_results),
            },
        )
        response.raise_for_status()
        return self._parse_arxiv(response.text)[:limit]

    def verify(self, card: EvidenceCard) -> EvidenceCard:
        verified = self.verify_identifier(card.identifiers)
        return verified or card.model_copy(update={"verified": False})

    def verify_identifier(self, identifiers: dict[str, str]) -> EvidenceCard | None:
        arxiv_id = identifiers.get("arxiv", "").strip()
        if arxiv_id:
            response = self.client.get(
                "https://export.arxiv.org/api/query",
                params={"id_list": arxiv_id, "max_results": 1},
            )
            response.raise_for_status()
            cards = self._parse_arxiv(response.text)
            if cards and cards[0].identifiers.get("arxiv", "").lower() == arxiv_id.lower():
                return cards[0].model_copy(update={"verified": True})

        doi = identifiers.get("doi", "").strip()
        if doi:
            response = self.client.get(f"https://api.crossref.org/works/{quote(doi, safe='')}")
            response.raise_for_status()
            message = response.json().get("message", {})
            returned_doi = str(message.get("DOI") or "")
            if returned_doi.lower() != doi.lower():
                return None
            authors = [
                " ".join(
                    part for part in (str(item.get("given") or ""), str(item.get("family") or "")) if part
                )
                for item in message.get("author", [])
            ]
            date_parts = message.get("published", {}).get("date-parts", [[None]])
            year = date_parts[0][0] if date_parts and date_parts[0] else None
            titles = message.get("title") or []
            return EvidenceCard(
                title=str(titles[0]) if titles else doi,
                authors=[author for author in authors if author],
                year=year,
                source="crossref",
                abstract=str(message.get("abstract") or "")[:500],
                url=str(message.get("URL") or f"https://doi.org/{doi}"),
                identifiers={"doi": returned_doi},
                verified=True,
                relevance=0.0,
                reliability=0.95,
            )
        return None

    def _parse_arxiv(self, text: str) -> list[EvidenceCard]:
        namespace = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(text)
        cards: list[EvidenceCard] = []
        for entry in root.findall("a:entry", namespace):
            raw_id = entry.findtext("a:id", default="", namespaces=namespace)
            title = " ".join(entry.findtext("a:title", default="", namespaces=namespace).split())
            summary = " ".join(entry.findtext("a:summary", default="", namespaces=namespace).split())
            authors = [
                node.findtext("a:name", default="", namespaces=namespace)
                for node in entry.findall("a:author", namespace)
            ]
            published = entry.findtext("a:published", default="1900", namespaces=namespace)
            arxiv_id = self._clean_arxiv_id(raw_id)
            cards.append(
                EvidenceCard(
                    title=title,
                    authors=[author for author in authors if author],
                    year=int(published[:4]),
                    source="arxiv",
                    abstract=summary[:500],
                    url=raw_id,
                    identifiers={"arxiv": arxiv_id} if arxiv_id else {},
                    verified=bool(arxiv_id),
                    relevance=0.0,
                    reliability=0.9,
                )
            )
        return cards

    def _clean_arxiv_id(self, raw_id: str) -> str:
        match = re.search(r"/abs/([^/]+)$", raw_id)
        if not match:
            return ""
        return re.sub(r"v\d+$", "", match.group(1))


def get_literature_provider(settings: Settings, client: httpx.Client | None = None) -> LiteratureProvider:
    if settings.literature_provider == "mock":
        return MockLiteratureProvider()
    return ArxivCrossrefProvider(settings=settings, client=client)


# Backward-compatible import name.  This provider has never queried Semantic
# Scholar; the concrete name above now accurately describes its capabilities.
ArxivSemanticScholarProvider = ArxivCrossrefProvider
