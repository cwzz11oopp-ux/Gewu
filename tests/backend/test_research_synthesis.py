from backend.app.workflow.research_synthesis import (
    build_research_synthesis,
    build_gap_processing_pipeline,
    candidate_synthesis_provenance_issues,
    evaluate_literature_coverage,
    normalize_candidate_synthesis_provenance,
    representative_paper_ids,
    synthesis_prompt_context,
)


def test_synthesis_uses_every_verified_card_and_links_future_work_to_gap():
    references = [
        {
            "title": "Paper A", "url": "https://example.test/a", "verified": True,
            "retrieval_intent": "MECHANISM",
            "abstract": "The method improves the baseline. Future work should test low-resource settings.",
        },
        {
            "title": "Paper B", "url": "https://example.test/b", "verified": True,
            "retrieval_intent": "MECHANISM",
            "abstract": "A limitation is that the method has not been evaluated across domains.",
        },
        {
            "title": "Paper C", "url": "https://example.test/c", "verified": True,
            "retrieval_intent": "BENCHMARK",
            "abstract": "The benchmark records reproducible accuracy measurements.",
        },
    ]

    synthesis = build_research_synthesis(references)

    assert synthesis["source_collection"]["paper_count"] == 3
    assert len(synthesis["papers"]) == 3
    assert synthesis["future_work"]
    assert synthesis["research_gaps"]
    future_gap = next(gap for gap in synthesis["research_gaps"] if gap["source_future_work_ids"])
    assert future_gap["gap_type"] == "author_proposed"
    assert future_gap["source_paper_ids"]
    assert future_gap["source_claim_ids"]
    assert representative_paper_ids(synthesis)
    assert synthesis_prompt_context(synthesis)["source_collection"]["paper_count"] == 3


def test_synthesis_accepts_legacy_claim_text_without_losing_provenance():
    synthesis = build_research_synthesis([
        {
            "title": "Legacy evidence card",
            "url": "https://example.test/legacy",
            "verified": True,
            "claim": "A limitation remains unresolved. Future work should test sea-clutter transfer.",
        }
    ])

    assert synthesis["claims"]
    assert synthesis["limitations"]
    assert synthesis["future_work"]
    assert synthesis["claims"][0]["paper_id"] == synthesis["papers"][0]["paper_id"]


def test_candidate_provenance_is_derived_only_from_persisted_gap_ids():
    synthesis = build_research_synthesis([
        {
            "title": "Paper", "url": "https://example.test/paper", "verified": True,
            "abstract": "Future work should evaluate robustness under distribution shift.",
        }
    ])
    gap = synthesis["research_gaps"][0]

    grounded = normalize_candidate_synthesis_provenance(
        {"candidate_id": "CAND-001", "source_gap_ids": [gap["gap_id"]], "research_gap": gap["description"]},
        synthesis,
    )
    unavailable = normalize_candidate_synthesis_provenance(
        {"candidate_id": "CAND-002", "source_gap_ids": ["GAP-INVENTED"]},
        synthesis,
    )

    assert grounded["provenance_status"] == "grounded"
    assert grounded["source_paper_ids"] == gap["source_paper_ids"]
    assert grounded["source_claim_ids"] == gap["source_claim_ids"]
    assert unavailable["provenance_status"] == "unavailable"
    assert unavailable["source_paper_ids"] == []
    assert unavailable["source_claim_ids"] == []


def _synthetic_references(count: int):
    return [
        {
            "title": f"Synthetic paper {index}",
            "url": f"https://example.test/{index}",
            "verified": True,
            "retrieval_intent": "METHOD" if index % 2 else "EVALUATION",
            "abstract": (
                f"The method establishes conclusion {index}. "
                f"A limitation is that domain{index} remains unknown. "
                f"Future work should evaluate domain{index} transfer."
            ),
        }
        for index in range(1, count + 1)
    ]


def test_dynamic_literature_coverage_distinguishes_saturation_from_hard_cap():
    for count in (12, 45, 80):
        synthesis = build_research_synthesis(_synthetic_references(count))
        assert synthesis["source_collection"]["paper_count"] == count
        assert len(synthesis["papers"]) == count
        initial = evaluate_literature_coverage(
            synthesis, retrieved_count=count, verified_count=count, hard_cap=100
        )
        assert initial["decision"] == "continue"
        saturated = evaluate_literature_coverage(
            synthesis, retrieved_count=count, verified_count=count, hard_cap=100,
            previous_synthesis=synthesis,
        )
        assert saturated["decision"] == "saturated"
        assert saturated["hard_cap_reached"] is False

    hard_cap = evaluate_literature_coverage(
        build_research_synthesis(_synthetic_references(80)),
        retrieved_count=120, verified_count=80, hard_cap=80,
        previous_synthesis=build_research_synthesis(_synthetic_references(12)),
    )
    assert hard_cap["decision"] == "hard_cap_reached"
    assert hard_cap["hard_cap_reached"] is True


def test_every_gap_enters_deterministic_batch_and_secondary_synthesis():
    for count in (6, 45, 90):
        synthesis = build_research_synthesis([
            {
                "title": f"Gap paper {index}", "url": f"https://example.test/gap/{index}",
                "verified": True, "retrieval_intent": "METHOD",
                "abstract": f"Future work should evaluate transferdomain{index}.",
            }
            for index in range(1, count + 1)
        ])
        pipeline = build_gap_processing_pipeline(synthesis, batch_max_chars=1_100)
        synthesis["hypothesis_gap_processing"] = pipeline
        context = synthesis_prompt_context(synthesis)
        assert pipeline["total_gap_count"] == count
        assert pipeline["processed_gap_count"] == count
        assert pipeline["gap_coverage"] == 1.0
        assert len(context["gap_processing"]["source_gap_ids"]) == count
        assert context["gap_processing"]["processed_gap_count"] == context["gap_processing"]["total_gap_count"]


def test_provenance_requires_real_gaps_and_derives_every_lineage_type():
    synthesis = build_research_synthesis(_synthetic_references(1))
    gap = synthesis["research_gaps"][0]
    valid = {"candidate_id": "CAND-VALID", "source_gap_ids": [gap["gap_id"]]}
    missing = {"candidate_id": "CAND-MISSING", "source_gap_ids": []}
    unknown = {"candidate_id": "CAND-UNKNOWN", "source_gap_ids": ["GAP-NOT-REAL"]}
    assert candidate_synthesis_provenance_issues([valid], synthesis) == []
    assert candidate_synthesis_provenance_issues([missing], synthesis) == ["CANDIDATE_PROVENANCE_REQUIRED:CAND-MISSING"]
    assert candidate_synthesis_provenance_issues([unknown], synthesis) == ["CANDIDATE_PROVENANCE_UNKNOWN_GAP:CAND-UNKNOWN:GAP-NOT-REAL"]
    normalized = normalize_candidate_synthesis_provenance(valid, synthesis)
    assert normalized["source_paper_ids"] == gap["source_paper_ids"]
    assert normalized["source_claim_ids"] == gap["source_claim_ids"]
    assert normalized["source_future_work_ids"] == gap["source_future_work_ids"]


def test_legacy_synthesis_without_round_or_coverage_contract_remains_unavailable():
    legacy = {
        "schema_version": 1,
        "source_collection": {"paper_count": 1},
        "papers": [{"paper_id": "PAPER-LEGACY", "title": "Legacy paper"}],
        "themes": [], "research_gaps": [],
    }
    context = synthesis_prompt_context(legacy)
    candidate = normalize_candidate_synthesis_provenance(
        {"candidate_id": "CAND-LEGACY"}, legacy
    )
    assert context["gap_processing"]["total_gap_count"] == 0
    assert candidate["provenance_status"] == "unavailable"
    assert candidate["source_paper_ids"] == []


def test_claim_extraction_and_synthesis_share_canonical_paper_ids():
    from backend.app.workflow.evidence_pipeline import _paper_id
    from backend.app.workflow.research_synthesis import stable_paper_id
    paper = {"title": "A source", "identifiers": {"doi": " 10.1000/ABC "}}
    normalized = {"title": "A source", "identifiers": {"doi": "10.1000/abc"}}
    assert _paper_id(paper, 0) == stable_paper_id(paper) == stable_paper_id(normalized)
