from backend.app.workflow.evidence_pipeline import (
    analyze_research_gaps,
    candidate_evidence_map,
    extract_claim_evidence,
)
from backend.app.workflow.knowledge import _rerank
from backend.app.workflow.literature_policy import LiteratureQuery, LiteratureRetrievalPolicy
from backend.app.models.provider import EvidenceCard


def test_claim_extraction_keeps_traceable_method_result_and_limitation_records():
    evidence = extract_claim_evidence([
        {
            "title": "Efficient Attention",
            "url": "https://doi.org/10.1/attention",
            "identifiers": {"doi": "10.1/attention"},
            "verified": True,
            "relevance": 0.9,
            "reliability": 0.8,
            "claim": (
                "We introduce a compact layer. "
                "The method improves accuracy on Fashion-MNIST. "
                "A limitation is that low-resolution transfer remains unexplored."
            ),
        }
    ])

    assert {item["evidence_type"] for item in evidence} >= {
        "METHOD", "RESULT", "LIMITATION"
    }
    assert all(item["paper_id"].startswith("PAPER-") for item in evidence)
    assert all(item["verification_status"] == "verified" for item in evidence)


def test_gap_candidate_map_supports_novel_combination_without_direct_final_result():
    evidence = extract_claim_evidence([
        {
            "title": "Component A",
            "url": "https://doi.org/10.1/a",
            "identifiers": {"doi": "10.1/a"},
            "verified": True,
            "relevance": 0.9,
            "reliability": 0.9,
            "claim": "Efficient channel attention improves channel modelling with low overhead. A limitation is that compact classifiers remain unexplored.",
        },
        {
            "title": "Component B",
            "url": "https://doi.org/10.1/b",
            "identifiers": {"doi": "10.1/b"},
            "verified": True,
            "relevance": 0.8,
            "reliability": 0.9,
            "claim": "MobileNetV2 is an efficient compact classifier for image recognition.",
        },
    ])
    gaps = analyze_research_gaps(evidence)
    mapping = candidate_evidence_map(
        {
            "candidate_id": "CAND-001",
            "claim": "Combine efficient channel attention with MobileNetV2 for Fashion-MNIST.",
            "mechanism": "channel attention can improve compact feature selection",
            "novel_inference": "the combination improves Fashion-MNIST accuracy",
        },
        evidence,
        gaps,
    )

    assert mapping["supporting_evidence"]
    # The new combination is an experimental prediction, not a required existing paper result.
    assert "novel_inference" not in mapping["missing_evidence"]


def test_relevance_reranking_demotes_obviously_unrelated_provider_records():
    policy = LiteratureRetrievalPolicy()
    related = EvidenceCard(
        title="MobileNetV2 for Fashion-MNIST image classification",
        authors=[], year=2024, source="test", claim="Lightweight CNN benchmark.",
        url="https://doi.org/10.1/related", identifiers={"doi": "10.1/related"},
        verified=True, relevance=0.0, reliability=0.8,
    )
    unrelated = EvidenceCard(
        title="Air pollutant job scheduling and software refactoring",
        authors=[], year=2024, source="test", claim="Unrelated workflow study.",
        url="https://doi.org/10.1/unrelated", identifiers={"doi": "10.1/unrelated"},
        verified=True, relevance=0.75, reliability=0.95,
    )

    ranked = _rerank(
        [unrelated, related],
        [LiteratureQuery(query="MobileNetV2 FashionMNIST lightweight CNN")],
        policy,
    )

    assert ranked[0].title == related.title
    assert ranked[0].relevance > ranked[1].relevance
