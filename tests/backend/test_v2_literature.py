from __future__ import annotations

import io

from backend.app.literature import AccessLevel, SprintLiteratureService
from backend.app.models.provider import EvidenceCard
from backend.app.storage.literature import LiteratureLibrary


class FixtureLiteratureProvider:
    def search(self, query: str, limit: int):
        return [
            EvidenceCard(
                title="Auditable Research Search",
                authors=["A. Researcher"],
                year=2026,
                source="arxiv",
                claim="The abstract describes branch search with protocol-aware experiments.",
                url="https://arxiv.org/abs/2601.00001",
                identifiers={"arxiv": "2601.00001"},
                verified=True,
                relevance=0.9,
                reliability=0.9,
            ),
            EvidenceCard(
                title="Auditable Research Search",
                authors=["A. Researcher"],
                year=2026,
                source="arxiv",
                claim="Duplicate provider record.",
                url="https://arxiv.org/abs/2601.00001v2",
                identifiers={"arxiv": "2601.00001"},
                verified=True,
            ),
        ][:limit]

    def verify(self, card):
        return card

    def verify_identifier(self, identifiers):
        return None


def test_literature_service_deduplicates_and_never_invents_full_text_fields(tmp_path):
    result = SprintLiteratureService(FixtureLiteratureProvider()).research(
        ["research search"], limit_per_query=5
    )
    assert len(result.papers) == 1
    paper = result.papers[0]
    assert paper.access_level == AccessLevel.ABSTRACT_ONLY
    assert paper.method == ""
    assert paper.datasets == []
    assert paper.main_results == []
    assert result.evidence_units[0].location == "abstract"
    assert "No full text" in result.gaps[0]


def test_accessible_local_text_is_labeled_full_text_and_wins_deduplication(tmp_path):
    library = LiteratureLibrary(tmp_path / "literature")
    library.upload(
        io.BytesIO(b"research search full text with a methods section"),
        "paper.txt",
        "text/plain",
        {
            "title": "Auditable Research Search",
            "authors": ["A. Researcher"],
            "year": 2026,
            "abstract": "Local abstract.",
            "arxiv": "2601.00001",
        },
    )
    result = SprintLiteratureService(
        FixtureLiteratureProvider(), local_library=library
    ).research(["research search"])
    assert len(result.papers) == 1
    assert result.papers[0].access_level == AccessLevel.FULL_TEXT
    assert result.papers[0].source == "local_upload"
    assert not any("No full text" in gap for gap in result.gaps)
