from backend.app.workflow.evidence_audit import (
    build_evidence_audit,
    targeted_evidence_queries,
)


def _evidence():
    return [
        {
            "title": "A Verified Radar Study",
            "url": "https://arxiv.org/abs/2401.01234v2",
            "identifiers": {"arxiv": "2401.01234"},
            "claim": "The study reports a controlled radar benchmark.",
            "verified": True,
            "relevance": 0.9,
            "reliability": 0.95,
        }
    ]


def test_evidence_audit_matches_arxiv_versions_and_assigns_stable_ids():
    candidate = {
        "claim": "A radar representation may improve detection.",
        "evidence_basis": [
            {
                "statement": "A verified radar benchmark exists.",
                "source_title": "A Verified Radar Study",
                "source_url": "http://arxiv.org/abs/2401.01234v7",
                "evidence_type": "FACT",
            }
        ],
    }

    audit = build_evidence_audit(_evidence(), [candidate])

    candidate_audit = audit["candidate_audits"][0]
    assert audit["policy"]["mode"] == "scoped"
    assert candidate_audit["gate"] == "PASS"
    assert candidate_audit["matched_evidence_ids"] == [
        audit["registry"][0]["evidence_id"]
    ]
    assert candidate_audit["links"][0]["matched_verified_source"] is True


def test_evidence_audit_blocks_unmatched_factual_sources_but_labels_assumptions():
    candidates = [
        {
            "claim": "Unsupported factual claim.",
            "evidence_basis": [
                {
                    "statement": "An unrelated source allegedly proves the claim.",
                    "source_title": "Unknown Paper",
                    "source_url": "https://example.com/unknown",
                    "evidence_type": "FACT",
                }
            ],
        },
        {
            "claim": "Explicit assumption.",
            "evidence_basis": [
                {
                    "statement": "This mechanism remains an assumption.",
                    "source_title": "",
                    "source_url": "",
                    "evidence_type": "ASSUMPTION",
                }
            ],
        },
    ]

    audit = build_evidence_audit(_evidence(), candidates)

    factual, assumption = audit["candidate_audits"]
    assert factual["gate"] == "FAIL"
    assert "UNVERIFIED_OR_UNMATCHED_SOURCE" in factual["issues"]
    assert assumption["gate"] == "FAIL"
    assert assumption["unmatched_sources"] == []
    assert assumption["evidence_type_counts"]["ASSUMPTION"] == 1


def test_evidence_audit_keeps_legacy_candidates_visible_without_false_verification():
    audit = build_evidence_audit(
        _evidence(),
        [{"claim": "Legacy candidate without an evidence basis."}],
    )

    assert audit["policy"]["mode"] == "legacy_unscoped"
    assert audit["candidate_audits"][0]["gate"] == "UNKNOWN"


def test_targeted_queries_prefer_explicit_gaps_deduplicate_and_limit():
    assessments = [
        {
            "status": "evidence_insufficient",
            "critic_reasoning": {
                "required_evidence": ["direct radar benchmark", "direct radar benchmark"],
                "unsupported_claims": ["frequency fusion improves detection"],
            },
            "evaluation": {"unknowns": ["dataset-specific ablation"]},
        },
        {
            "status": "verified",
            "critic_reasoning": {"required_evidence": ["must not be queried"]},
        },
    ]

    assert targeted_evidence_queries(assessments, limit=2) == [
        "direct radar benchmark",
        "frequency fusion improves detection",
    ]
