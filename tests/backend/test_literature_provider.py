import httpx

from backend.app.config import Settings
from backend.app.models.provider import EvidenceCard
from backend.app.providers.literature import ArxivSemanticScholarProvider, MockLiteratureProvider


def test_mock_literature_returns_real_metadata_records():
    provider = MockLiteratureProvider()

    cards = provider.search("neural network robustness", limit=2)

    assert len(cards) == 2
    assert all(card.verified for card in cards)
    assert all(card.identifiers for card in cards)


def test_exportable_reference_requires_verified_identifier():
    card = EvidenceCard(
        title="Generated citation candidate",
        authors=["Model"],
        year=2026,
        source="llm",
        claim="candidate claim",
        url="",
        identifiers={},
        verified=False,
    )

    assert card.exportable is False


def test_arxiv_provider_parses_real_identifier_from_atom_feed():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/1512.03385v1</id>
        <title>Deep Residual Learning for Image Recognition</title>
        <published>2015-12-10T00:00:00Z</published>
        <author><name>Kaiming He</name></author>
        <summary>Residual learning framework.</summary>
      </entry>
    </feed>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml)

    provider = ArxivSemanticScholarProvider(
        settings=Settings.from_env({"LITERATURE_PROVIDER": "arxiv_semantic_scholar"}),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    cards = provider.search("resnet", limit=1)

    assert cards[0].identifiers["arxiv"] == "1512.03385"
    assert cards[0].verified is True
    assert cards[0].exportable is True
