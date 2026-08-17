from backend.app.models.literature import DocumentVerification, LocalDocument
from backend.app.models.provider import EvidenceCard
from backend.app.storage.literature import LiteratureLibrary
from backend.app.storage.research_wiki import ResearchWikiStore, WikiQueryResult
from backend.app.workflow.knowledge import (
    KnowledgeIntegrationService,
    merge_verified_evidence,
)
from backend.app.workflow.literature_policy import LiteratureRetrievalPolicy


class RecordingWiki(ResearchWikiStore):
    def __init__(self, root, calls, result=None):
        super().__init__(root)
        self.calls = calls
        self.result = result

    def query(self, topic):
        self.calls.append(("wiki", topic))
        return self.result or WikiQueryResult(
            status="empty", warnings=["WIKI_EMPTY"]
        )


class RecordingLibrary(LiteratureLibrary):
    def __init__(self, root, calls, documents=None):
        super().__init__(root)
        self.calls = calls
        self.documents = documents or []

    def search(self, query, limit=10):
        self.calls.append(("local", query))
        return self.documents[:limit]


class RecordingExternal:
    provider_name = "external"

    def __init__(self, calls, cards=None):
        self.calls = calls
        self.cards = cards or []
        self.queries = []

    def search(self, query, limit):
        self.calls.append(("external", query))
        self.queries.append(query)
        return self.cards[:limit]


class QueryAwareExternal(RecordingExternal):
    def __init__(self, calls, results_by_query):
        super().__init__(calls)
        self.results_by_query = results_by_query

    def search(self, query, limit):
        self.calls.append(("external", query))
        self.queries.append(query)
        return self.results_by_query.get(query, [])[:limit]


def _verified_card(index=1, doi=None):
    return EvidenceCard(
        title=f"Robust Training {index}",
        authors=["Researcher"],
        year=2024,
        source="external",
        claim="robust training evidence",
        url=f"https://doi.org/{doi or f'10.1/{index}'}",
        identifiers={"doi": doi or f"10.1/{index}"},
        verified=True,
    )


def test_collect_continues_external_search_when_wiki_is_empty(tmp_path):
    calls = []
    external = RecordingExternal(calls, [_verified_card()])
    service = KnowledgeIntegrationService(
        RecordingWiki(tmp_path / "wiki", calls),
        RecordingLibrary(tmp_path / "library", calls),
        external,
    )

    result = service.collect(
        "run_1", {"literature_queries": ["dropout robustness"]}
    )

    assert external.queries == ["dropout robustness"]
    assert calls == [
        ("wiki", "dropout robustness"),
        ("local", "dropout robustness"),
        ("external", "dropout robustness"),
    ]
    assert "WIKI_EMPTY" in result.warnings
    assert len(result.references) == 1


def test_collect_uses_every_problem_query_in_source_order(tmp_path):
    calls = []
    service = KnowledgeIntegrationService(
        RecordingWiki(tmp_path / "wiki", calls),
        RecordingLibrary(tmp_path / "library", calls),
        RecordingExternal(calls),
    )

    service.collect("run_1", {"literature_queries": ["query one", "query two"]})

    assert calls == [
        ("wiki", "query one"),
        ("local", "query one"),
        ("external", "query one"),
        ("wiki", "query two"),
        ("local", "query two"),
        ("external", "query two"),
    ]


def test_verified_external_duplicate_preserves_local_provenance(tmp_path):
    calls = []
    local = LocalDocument(
        id="paper_local",
        filename="paper.pdf",
        media_type="application/pdf",
        sha256="a" * 64,
        size_bytes=10,
        title="Local title",
        identifiers={"doi": "10.1/shared"},
        verification=DocumentVerification(verified=False),
    )
    service = KnowledgeIntegrationService(
        RecordingWiki(tmp_path / "wiki", calls),
        RecordingLibrary(tmp_path / "library", calls, [local]),
        RecordingExternal(calls, [_verified_card(doi="10.1/shared")]),
    )

    result = service.collect("run_1", {"literature_queries": ["robust"]})

    assert len(result.references) == 1
    assert result.references[0].source_kind == "external"
    assert result.references[0].local_document_id == "paper_local"
    assert result.local_only == []


def test_collect_limits_proposed_wiki_papers_to_twelve(tmp_path):
    calls = []
    cards = [_verified_card(index) for index in range(20)]
    service = KnowledgeIntegrationService(
        RecordingWiki(tmp_path / "wiki", calls),
        RecordingLibrary(tmp_path / "library", calls),
        RecordingExternal(calls, cards),
        external_limit=20,
    )

    result = service.collect("run_1", {"literature_queries": ["robust"]})

    assert len(result.wiki_changes.papers) == 12


def test_collect_adds_english_external_query_for_chinese_literature_terms(tmp_path):
    calls = []
    fallback_query = "neural network training ablation study evaluation"
    external = QueryAwareExternal(calls, {fallback_query: [_verified_card()]})
    service = KnowledgeIntegrationService(
        RecordingWiki(tmp_path / "wiki", calls),
        RecordingLibrary(tmp_path / "library", calls),
        external,
    )

    result = service.collect(
        "run_1", {"literature_queries": ["训练神经网络并做消融实验评估"]}
    )

    assert external.queries == [
        "训练神经网络并做消融实验评估",
        fallback_query,
    ]
    assert len(result.references) == 1
    assert result.references[0].title == "Robust Training 1"


def test_collect_queries_and_merge_add_only_new_verified_evidence(tmp_path):
    calls = []
    existing = _verified_card(doi="10.1/existing")
    new = _verified_card(doi="10.1/new")
    external = QueryAwareExternal(
        calls,
        {"targeted mechanism evidence": [existing, new]},
    )
    service = KnowledgeIntegrationService(
        RecordingWiki(tmp_path / "wiki", calls),
        RecordingLibrary(tmp_path / "library", calls),
        external,
    )

    targeted = service.collect_queries(
        "run_1", ["targeted mechanism evidence"]
    )
    merged = merge_verified_evidence(
        [existing.model_dump()], targeted.references
    )

    assert external.queries == ["targeted mechanism evidence"]
    assert [card.identifiers["doi"] for card in merged] == [
        "10.1/existing",
        "10.1/new",
    ]


def test_initial_retrieval_is_bounded_and_preserves_full_store(tmp_path):
    calls = []
    results = {
        "robust training baseline": [_verified_card(index) for index in range(1, 21)],
        "robust training mechanism": [_verified_card(index) for index in range(21, 41)],
        "robust training benchmark": [_verified_card(index) for index in range(41, 61)],
    }
    policy = LiteratureRetrievalPolicy(
        max_query_count=3,
        max_results_per_query=20,
        max_results_per_source=40,
        max_candidate_count=30,
        max_core_reference_count=6,
    )
    service = KnowledgeIntegrationService(
        RecordingWiki(tmp_path / "wiki", calls),
        RecordingLibrary(tmp_path / "library", calls),
        QueryAwareExternal(calls, results),
        external_limit=20,
        policy=policy,
    )
    result = service.collect("run_1", {"literature_queries": [
        {"query": "robust training baseline", "intent": "BASELINE"},
        {"query": "robust training baseline", "intent": "BASELINE"},
        {"query": "robust training mechanism", "intent": "MECHANISM"},
        {"query": "robust training benchmark", "intent": "BENCHMARK"},
        {"query": "unused extra query", "intent": "EVALUATION"},
    ]})

    assert result.sources["query_count"] == 3
    assert result.sources["bounded_candidate_count"] <= 30
    assert len(result.references) > len(result.core_references)
    assert len(result.core_references) == 6
    assert {card.retrieval_intent for card in result.core_references} >= {
        "BASELINE", "MECHANISM"
    }


def test_dynamic_collection_sizes_and_hard_cap_are_persisted_as_separate_contracts(tmp_path):
    for count in (12, 45, 80):
        calls = []
        policy = LiteratureRetrievalPolicy(
            max_query_count=1, max_results_per_query=100,
            max_results_per_source=100, max_candidate_count=100,
        )
        service = KnowledgeIntegrationService(
            RecordingWiki(tmp_path / f"wiki-{count}", calls),
            RecordingLibrary(tmp_path / f"library-{count}", calls),
            RecordingExternal(calls, [_verified_card(index) for index in range(1, count + 1)]),
            external_limit=100, policy=policy,
        )
        result = service.collect_queries("synthetic-run", ["dynamic literature coverage"])
        assert len(result.references) == count
        assert result.literature_coverage["verified_count"] == count
        assert result.literature_coverage["hard_cap_reached"] is False
        assert result.sources["hard_caps"]["candidate_count"] == 100

    calls = []
    capped = KnowledgeIntegrationService(
        RecordingWiki(tmp_path / "wiki-capped", calls),
        RecordingLibrary(tmp_path / "library-capped", calls),
        RecordingExternal(calls, [_verified_card(index) for index in range(1, 81)]),
        external_limit=100,
        policy=LiteratureRetrievalPolicy(max_query_count=1, max_results_per_query=100, max_results_per_source=100, max_candidate_count=45),
    ).collect_queries("synthetic-run", ["dynamic literature hard cap"])
    assert len(capped.references) == 45
    assert capped.literature_coverage["decision"] == "hard_cap_reached"
    assert capped.literature_coverage["hard_cap_reached"] is True
