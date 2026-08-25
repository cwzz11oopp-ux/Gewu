from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any


HYPOTHESIS_STATUSES = frozenset({"SUPPORTED", "CONTRADICTED", "INCONCLUSIVE", "REFINEMENT_REQUIRED"})
EVOLUTION_ACTIONS = frozenset({"KEEP_HYPOTHESIS", "REFINE_HYPOTHESIS", "REPLACE_HYPOTHESIS", "GENERATE_ALTERNATIVE_HYPOTHESES", "MORE_EVIDENCE", "ANSWER_RESEARCH_QUESTION"})


def normalize_scientific_analysis(value: dict[str, Any], *, provider_id: str, model_identity: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("SCIENTIFIC_ANALYSIS_INVALID")
    status = str(value.get("hypothesis_status") or "").upper()
    status = {
        "FAILED": "CONTRADICTED",
        "UNSUPPORTED": "CONTRADICTED",
        "PARTIAL": "INCONCLUSIVE",
    }.get(status, status)
    if status not in HYPOTHESIS_STATUSES:
        raise ValueError(f"SCIENTIFIC_ANALYSIS_STATUS_INVALID:{status or 'EMPTY'}")
    confidence = value.get("confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("SCIENTIFIC_ANALYSIS_CONFIDENCE_INVALID")
    normalized = deepcopy(value)
    for field in ("supported_findings", "contradicting_findings", "alternative_explanations", "confounders", "evidence_gaps"):
        if not isinstance(normalized.get(field, []), list):
            raise ValueError(f"SCIENTIFIC_ANALYSIS_{field.upper()}_INVALID")
        normalized.setdefault(field, [])
    if not isinstance(normalized.get("interpretation"), str):
        raise ValueError("SCIENTIFIC_ANALYSIS_INTERPRETATION_INVALID")
    normalized.setdefault("recommended_action", "")
    normalized.setdefault("proposed_hypothesis", None)
    normalized["hypothesis_status"] = status
    normalized["confidence"] = float(confidence)
    normalized["provider_id"] = provider_id
    normalized["model_identity"] = model_identity
    return normalized


def unavailable_secondary_review(reason: str) -> dict[str, Any]:
    return {"status": "SECONDARY_REVIEW_UNAVAILABLE", "provider_id": "deepseek", "reason": reason}


def detect_disagreement(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    if secondary.get("status") == "SECONDARY_REVIEW_UNAVAILABLE":
        return {"status": "SECONDARY_REVIEW_UNAVAILABLE", "qwen_status": primary["hypothesis_status"], "deepseek_status": "UNAVAILABLE", "disagreement_dimensions": [], "requires_resolution": False}
    dimensions: list[str] = []
    if primary["hypothesis_status"] != secondary["hypothesis_status"]:
        dimensions.append("hypothesis_status")
    if str(primary.get("recommended_action") or "") != str(secondary.get("recommended_action") or ""):
        dimensions.append("recommended_action")
    for field in ("confounders", "evidence_gaps"):
        if set(map(str, primary.get(field, []))) != set(map(str, secondary.get(field, []))):
            dimensions.append(field)
    return {"status": "SCIENTIFIC_DISAGREEMENT" if dimensions else "SCIENTIFIC_AGREEMENT", "qwen_status": primary["hypothesis_status"], "deepseek_status": secondary["hypothesis_status"], "disagreement_dimensions": dimensions, "requires_resolution": bool(dimensions)}


def synthesize_scientific_conclusion(primary: dict[str, Any], secondary: dict[str, Any], disagreement: dict[str, Any]) -> dict[str, Any]:
    analyses = [primary] + ([] if secondary.get("status") == "SECONDARY_REVIEW_UNAVAILABLE" else [secondary])
    statuses = {item["hypothesis_status"] for item in analyses}
    status = primary["hypothesis_status"] if len(statuses) == 1 else "INCONCLUSIVE"
    confounders = sorted({str(value) for item in analyses for value in item.get("confounders", [])})
    gaps = sorted({str(value) for item in analyses for value in item.get("evidence_gaps", [])})
    findings_for = sorted({str(value) for item in analyses for value in item.get("supported_findings", [])})
    findings_against = sorted({str(value) for item in analyses for value in item.get("contradicting_findings", [])})
    return {"hypothesis_status": status, "agreement_level": "HIGH" if disagreement["status"] == "SCIENTIFIC_AGREEMENT" else ("UNAVAILABLE" if disagreement["status"] == "SECONDARY_REVIEW_UNAVAILABLE" else "LOW"), "supported_claims": findings_for, "unsupported_claims": findings_against, "confounders": confounders, "remaining_uncertainties": gaps, "current_conclusion": primary["interpretation"] if status == primary["hypothesis_status"] else "Independent scientific analyses disagree; do not select a model as the conclusion.", "next_action": "MORE_EVIDENCE" if disagreement.get("requires_resolution") else str(primary.get("recommended_action") or ""), "proposed_hypothesis": primary.get("proposed_hypothesis") if status in {"CONTRADICTED", "REFINEMENT_REQUIRED"} else None, "confidence": min(item["confidence"] for item in analyses)}


def evolution_decision(synthesis: dict[str, Any], *, iteration: int, max_iterations: int) -> dict[str, Any]:
    if iteration >= max_iterations:
        return {"action": "MORE_EVIDENCE", "reason": "RESEARCH_ITERATION_LIMIT_REACHED", "research_iteration": iteration, "create_working_hypothesis": False}
    if synthesis["agreement_level"] == "LOW":
        return {"action": "MORE_EVIDENCE", "reason": "SCIENTIFIC_DISAGREEMENT", "research_iteration": iteration, "create_working_hypothesis": False}
    status = synthesis["hypothesis_status"]
    action = {"SUPPORTED": "KEEP_HYPOTHESIS", "CONTRADICTED": "REPLACE_HYPOTHESIS", "REFINEMENT_REQUIRED": "REFINE_HYPOTHESIS", "INCONCLUSIVE": "MORE_EVIDENCE"}[status]
    return {"action": action, "reason": status, "research_iteration": iteration, "create_working_hypothesis": action in {"REFINE_HYPOTHESIS", "REPLACE_HYPOTHESIS"} and isinstance(synthesis.get("proposed_hypothesis"), dict)}


def build_working_hypothesis(*, parent_hypothesis_id: str, parent_claim: str, proposal: dict[str, Any], derived_from: list[str], reason: str, revision: int) -> dict[str, Any]:
    if not derived_from:
        raise ValueError("SCIENTIFIC_REVISION_EVIDENCE_REQUIRED")
    claim = str(proposal.get("claim") or "").strip()
    if not claim:
        raise ValueError("SCIENTIFIC_REVISION_CLAIM_REQUIRED")
    return {**deepcopy(proposal), "claim": claim, "source": "scientific_revision", "immutable": False, "parent_hypothesis_id": parent_hypothesis_id, "parent_claim": parent_claim, "derived_from": list(dict.fromkeys(derived_from)), "revision_reason": reason, "hypothesis_revision": revision}
