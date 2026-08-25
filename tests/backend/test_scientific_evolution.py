import pytest

from backend.app.workflow.scientific_evolution import (
    build_working_hypothesis,
    detect_disagreement,
    evolution_decision,
    normalize_scientific_analysis,
    synthesize_scientific_conclusion,
    unavailable_secondary_review,
)


def analysis(status="SUPPORTED", **overrides):
    value = {"hypothesis_status": status, "supported_findings": ["validated test metric"], "contradicting_findings": [], "alternative_explanations": [], "confounders": [], "evidence_gaps": [], "interpretation": "bounded interpretation", "recommended_action": "KEEP_HYPOTHESIS", "proposed_hypothesis": None, "confidence": 0.8}
    value.update(overrides)
    return normalize_scientific_analysis(value, provider_id="qwen")


def test_user_hypothesis_is_immutable_and_scientific_revision_has_lineage():
    v1 = {"id": "art_hyp_1", "source": "user", "claim": "CNN wins"}
    conclusion = "art_conclusion_1"
    v2 = build_working_hypothesis(parent_hypothesis_id=v1["id"], parent_claim=v1["claim"], proposal={"claim": "CNN advantage depends on capacity"}, derived_from=["art_result_1", conclusion, "art_evidence_1"], reason="CONTRADICTED", revision=1)
    assert v1["claim"] == "CNN wins"
    assert v2["parent_hypothesis_id"] == v1["id"]
    assert conclusion in v2["derived_from"]
    assert v2["source"] == "scientific_revision"


def test_supported_agreement_keeps_hypothesis_without_duplicate_revision():
    primary, secondary = analysis(), normalize_scientific_analysis(analysis(), provider_id="deepseek")
    disagreement = detect_disagreement(primary, secondary)
    synthesis = synthesize_scientific_conclusion(primary, secondary, disagreement)
    decision = evolution_decision(synthesis, iteration=1, max_iterations=4)
    assert disagreement["status"] == "SCIENTIFIC_AGREEMENT"
    assert decision["action"] == "KEEP_HYPOTHESIS"
    assert decision["create_working_hypothesis"] is False


def test_contradicted_path_creates_refined_or_replacement_candidate():
    primary = analysis("CONTRADICTED", contradicting_findings=["CNN <= MLP"], proposed_hypothesis={"claim": "Capacity explains observed difference"})
    secondary = normalize_scientific_analysis({**primary, "provider_id": "deepseek"}, provider_id="deepseek")
    decision = evolution_decision(synthesize_scientific_conclusion(primary, secondary, detect_disagreement(primary, secondary)), iteration=1, max_iterations=4)
    assert decision["action"] == "REPLACE_HYPOTHESIS"
    assert decision["create_working_hypothesis"] is True


@pytest.mark.parametrize(
    ("raw_status", "expected_status"),
    [("FAILED", "CONTRADICTED"), ("UNSUPPORTED", "CONTRADICTED"), ("PARTIAL", "INCONCLUSIVE")],
)
def test_result_verdict_aliases_normalize_to_scientific_statuses(raw_status, expected_status):
    assert analysis(raw_status)["hypothesis_status"] == expected_status


def test_inconclusive_and_disagreement_require_more_evidence_not_model_vote():
    primary = analysis("SUPPORTED")
    secondary = normalize_scientific_analysis({**analysis("CONTRADICTED"), "contradicting_findings": ["capacity confound"]}, provider_id="deepseek")
    disagreement = detect_disagreement(primary, secondary)
    synthesis = synthesize_scientific_conclusion(primary, secondary, disagreement)
    assert disagreement["status"] == "SCIENTIFIC_DISAGREEMENT"
    assert synthesis["hypothesis_status"] == "INCONCLUSIVE"
    assert evolution_decision(synthesis, iteration=1, max_iterations=4)["action"] == "MORE_EVIDENCE"


def test_secondary_unavailable_is_explicit_and_iteration_limit_is_bounded():
    primary = analysis("INCONCLUSIVE")
    unavailable = unavailable_secondary_review("DEEPSEEK_API_KEY_MISSING")
    disagreement = detect_disagreement(primary, unavailable)
    assert disagreement["status"] == "SECONDARY_REVIEW_UNAVAILABLE"
    assert evolution_decision(synthesize_scientific_conclusion(primary, unavailable, disagreement), iteration=4, max_iterations=4)["reason"] == "RESEARCH_ITERATION_LIMIT_REACHED"


def test_revision_requires_real_evidence_provenance():
    with pytest.raises(ValueError, match="SCIENTIFIC_REVISION_EVIDENCE_REQUIRED"):
        build_working_hypothesis(parent_hypothesis_id="v1", parent_claim="claim", proposal={"claim": "v2"}, derived_from=[], reason="CONTRADICTED", revision=1)
