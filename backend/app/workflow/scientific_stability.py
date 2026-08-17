"""Domain-neutral scientific stability contracts.

This module deliberately contains no dataset, model, or metric names.  It
turns incomplete scientific knowledge into explicit state instead of allowing
an agent response to silently promote it to a blocking fact.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any

CLAIM_STATUSES = {"unknown", "provisional", "supported", "verified", "conflicted"}
SOURCE_TYPES = {"user", "dataset", "literature", "source_code", "derived", "none"}
READINESS_STATES = {
    "needs_evidence", "needs_verification", "pilot_ready", "full_experiment_ready",
    "scientifically_infeasible",
}
SCIENTIFIC_RUN_STATES = {
    "NEEDS_VERIFICATION", "NEEDS_EVIDENCE", "NEEDS_PROTOCOL_RESOLUTION",
    "NEEDS_PLAN_REVISION", "RECOVERABLE_PROVIDER_ERROR", "HYPOTHESIS_REJECTED",
    "COMPLETED_NEGATIVE", "COMPLETED_INCONCLUSIVE", "COMPLETED_WITH_BOUNDARY",
}
HARD_CONTRACT_KINDS = {
    "metric_threshold", "success_threshold", "effect_size", "significance_threshold",
    "split_protocol", "window", "stride", "seed", "resource_limit", "latency_limit",
    "parameter_growth_limit", "operating_condition", "baseline_configuration",
}


def scientific_claim(value: Any, *, kind: str, source_type: str = "none", source_ids: list[str] | None = None,
                     status: str = "unknown", derivation: Any = None, confidence: float | None = None,
                     claim_id: str | None = None) -> dict[str, Any]:
    """Create the only accepted representation for decision-bearing facts."""
    source_type = source_type if source_type in SOURCE_TYPES else "none"
    status = status if status in CLAIM_STATUSES else "unknown"
    ids = [str(item) for item in (source_ids or []) if str(item)]
    if kind in HARD_CONTRACT_KINDS and (source_type == "none" or not ids or status not in {"supported", "verified"}):
        # Unsupported numeric/protocol content stays visible but cannot gate a
        # formal experiment.
        status = "provisional" if value not in (None, "", [], {}) else "unknown"
    identity = claim_id or "SC-" + sha256(
        json.dumps({"kind": kind, "value": value, "source_type": source_type, "source_ids": ids},
                   ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return {"id": identity, "kind": kind, "value": deepcopy(value), "status": status,
            "source_type": source_type, "source_ids": ids, "derivation": deepcopy(derivation),
            "confidence": confidence}


def hard_constraint_issues(claims: list[dict[str, Any]] | None) -> list[str]:
    issues: list[str] = []
    for claim in claims or []:
        if not isinstance(claim, dict) or claim.get("kind") not in HARD_CONTRACT_KINDS:
            continue
        if claim.get("source_type") == "none" or not claim.get("source_ids"):
            issues.append(f"HARD_CONSTRAINT_PROVENANCE_REQUIRED:{claim.get('id', '')}")
        if claim.get("status") not in {"supported", "verified"}:
            issues.append(f"HARD_CONSTRAINT_NOT_ESTABLISHED:{claim.get('id', '')}")
    return issues


def infer_research_profile(problem: str, *, dataset_present: bool, evidence: dict | None = None) -> dict[str, Any]:
    """Route by research mode, never by a named benchmark or metric."""
    text = str(problem or "").casefold()
    theoretical = any(token in text for token in ("theorem", "proof", "prove", "数学", "证明", "定理"))
    synthesis = any(token in text for token in ("literature review", "综述", "文献综述", "survey"))
    simulation = any(token in text for token in ("simulate", "simulation", "仿真"))
    forecasting = any(token in text for token in ("forecast", "预测", "time series", "时序"))
    labels: list[str] = []
    if theoretical:
        labels.append("mathematical")
    elif synthesis:
        labels.append("literature_synthesis")
    elif simulation:
        labels.append("simulation")
    elif dataset_present:
        labels.append("empirical_data")
    else:
        labels.append("computational")
    if forecasting:
        labels.append("hybrid")
    applicable = {
        "dataset": not (theoretical or synthesis), "training": not (theoretical or synthesis),
        "metric_protocol": not theoretical, "experiment": not (theoretical or synthesis),
        "literature": True,
    }
    return {"profile_types": labels, "applicability": applicable,
            "source": scientific_claim("research profile", kind="research_profile", source_type="derived",
                                        source_ids=["problem_input"], status="supported"),
            "evidence_present": bool(evidence)}


def annotate_dataset_semantics(profile: dict[str, Any] | None) -> dict[str, Any]:
    """Preserve structural facts and represent missing semantics as UNKNOWN."""
    source = profile or {}
    semantic = deepcopy(source.get("semantic_facts") or {})
    for name in ("sample_unit", "label_semantics", "axis_semantics", "group_identity", "temporal_identity", "split_protocol", "leakage_relationships"):
        value = semantic.get(name)
        if not isinstance(value, dict):
            semantic[name] = scientific_claim(None, kind=f"dataset_{name}", source_type="none", status="unknown")
    structural = {
        key: scientific_claim(source.get(key), kind=f"dataset_{key}", source_type="dataset",
                              source_ids=[str(source.get("contract_id") or "dataset_profile")], status="verified")
        for key in ("files", "schemas", "file_count", "total_bytes", "file_types", "content_fingerprint") if key in source
    }
    return {**deepcopy(source), "structural_facts": structural, "semantic_facts": semantic,
            "unknown_semantics": sorted(name for name, item in semantic.items() if item.get("status") == "unknown")}


def protocol_state(*, objective: str, profile: dict[str, Any], literature: dict | None,
                   dataset: dict | None, code: dict | None, stage: str) -> dict[str, Any]:
    """Resolve generic protocol roles from evidence; never fabricate concrete metrics."""
    evidence_ids = [str(item.get("reference_id") or item.get("id") or "") for item in (literature or {}).get("references", []) if isinstance(item, dict)]
    source_type = "literature" if evidence_ids else "none"
    outcome = scientific_claim(objective, kind="primary_outcome", source_type="user", source_ids=["problem_input"], status="verified")
    roles = {"primary_outcome": outcome}
    for role in ("secondary_outcome", "operating_condition", "success_criterion", "failure_criterion", "statistical_support", "control", "baseline"):
        roles[role] = scientific_claim(None, kind=role, source_type=source_type, source_ids=evidence_ids,
                                       status="unknown")
    unknown = [name for name, claim in roles.items() if claim["status"] == "unknown"]
    return {"current_stage": stage, "roles": roles, "unknown_roles": unknown,
            "hard_constraint_issues": hard_constraint_issues(list(roles.values()))}


def readiness_state(*, assessment: dict | None, dataset: dict | None, protocol: dict | None,
                    profile: dict | None) -> dict[str, Any]:
    assessment = assessment or {}
    status = str(assessment.get("status") or "").casefold()
    recommendation = str(assessment.get("recommendation") or assessment.get("critic_decision") or "").casefold()
    mechanism_gate = str(assessment.get("mechanism_gate") or "").casefold()
    # Legacy assessments may use evidence_insufficient as a non-blocking
    # ranking label.  Only an explicit failed mechanism/revise decision closes
    # the formal-plan gate.
    if ((status in {"evidence_insufficient", "needs_evidence"} and (mechanism_gate == "fail" or recommendation in {"revise", "needs_evidence"}))
            or recommendation == "needs_evidence"):
        state, route = "needs_evidence", "targeted_literature"
    elif status in {"rejected", "scientifically_infeasible"}:
        state, route = "scientifically_infeasible", "hypothesis_revision"
    elif not (profile or {}).get("applicability", {}).get("experiment", True):
        state, route = "pilot_ready", "non_experimental_progression"
    elif (dataset or {}).get("unknown_semantics"):
        state, route = "needs_verification", "dataset_verification"
    elif (protocol or {}).get("unknown_roles"):
        state, route = "needs_verification", "protocol_resolution"
    else:
        state, route = "full_experiment_ready", "main"
    return {"state": state, "next_route": route, "blocking_unknowns": list((dataset or {}).get("unknown_semantics") or []) + list((protocol or {}).get("unknown_roles") or []),
            "assessment_status": status}


def next_research_stage(readiness: dict[str, Any], profile: dict[str, Any]) -> str:
    if not profile.get("applicability", {}).get("experiment", True):
        return "VERIFY" if readiness["state"] == "needs_verification" else "CONFIRM"
    return {"needs_evidence": "VERIFY", "needs_verification": "VERIFY", "pilot_ready": "PILOT",
            "full_experiment_ready": "MAIN", "scientifically_infeasible": "VERIFY"}[readiness["state"]]


def merge_issue_ledger(previous: list[dict[str, Any]] | None, review: dict[str, Any], *, round_index: int,
                       new_information: bool = False) -> list[dict[str, Any]]:
    """Carry review issues forward; new blocking issues require a stated reason."""
    prior = {str(item.get("issue_id")): deepcopy(item) for item in previous or [] if isinstance(item, dict)}
    incoming = review.get("issues") if isinstance(review, dict) else []
    incoming = incoming if isinstance(incoming, list) else []
    matched: set[str] = set()
    ledger: list[dict[str, Any]] = []
    for index, issue in enumerate(incoming):
        issue = issue if isinstance(issue, dict) else {"description": str(issue)}
        description = str(issue.get("description") or issue.get("reason") or "")
        fingerprint = "ISS-" + sha256(description.casefold().encode()).hexdigest()[:10]
        old = prior.get(fingerprint)
        blocking = str(issue.get("severity") or "major").casefold() in {"critical", "major"}
        reason = issue.get("new_issue_reason")
        if old is None and blocking and round_index > 1 and not (reason or new_information):
            blocking = False
            reason = "Unqualified new issue retained as non-blocking; ledger convergence contract."
        ledger.append({"issue_id": fingerprint, "introduced_round": old.get("introduced_round", round_index) if old else round_index,
                       "severity": str(issue.get("severity") or "major").casefold(), "blocking": blocking,
                       "description": description, "required_fix": str(issue.get("required_fix") or issue.get("reason") or ""),
                       "status": "open", "resolution_evidence": list(issue.get("resolution_evidence") or []),
                       "new_issue_reason": reason})
        matched.add(fingerprint)
    for key, old in prior.items():
        if key not in matched:
            old["status"] = "resolved"
            ledger.append(old)
    return ledger


def selected_hypothesis_digest(selection: dict[str, Any], reasoning: dict[str, Any], *, budget: int = 12_000) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = (selection.get("selected") or [{}])[0] if isinstance(selection, dict) else {}
    selected_id = str(selected.get("candidate_id") or "") if isinstance(selected, dict) else ""
    selected_index = (selection.get("selected_indexes") or [None])[0] if isinstance(selection, dict) else None
    assessments = reasoning.get("candidate_assessments") if isinstance(reasoning, dict) else []
    matching = [item for item in assessments or [] if isinstance(item, dict) and (item.get("candidate_id") == selected_id or item.get("candidate_index") == selected_index)]
    digest = {"selected_hypothesis": deepcopy(selected), "selected_assessment": deepcopy(matching[0]) if matching else {},
              "source_gaps": deepcopy((matching[0].get("gaps") if matching else []) or []),
              "supporting_evidence": deepcopy((matching[0].get("evidence") if matching else []) or []),
              "contradictory_evidence": deepcopy((matching[0].get("conflicts") if matching else []) or []),
              "uncertainty": deepcopy((matching[0].get("uncertainty") if matching else []) or [])}
    encoded = json.dumps(digest, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(encoded) > budget:
        # Deterministic digest retains identity and explicit overflow metadata;
        # no unreported scientific records are silently dropped.
        digest = {"selected_hypothesis": deepcopy(selected), "digest_status": "over_budget_requires_secondary_digest",
                  "source_assessment_id": selected_id, "omitted_components": ["selected_assessment_detail"]}
        encoded = json.dumps(digest, ensure_ascii=False, separators=(",", ":"))
    telemetry = context_telemetry([("selected_hypothesis_digest", digest)], budget)
    return digest, telemetry


def context_telemetry(components: list[tuple[str, Any]], budget: int) -> dict[str, Any]:
    rows = []
    for name, value in components:
        chars = len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))
        rows.append({"name": name, "records": len(value) if isinstance(value, list) else 1,
                     "chars": chars, "estimated_tokens": max(1, (chars + 3) // 4)})
    total = sum(item["chars"] for item in rows)
    return {"components": rows, "total_chars": total, "estimated_tokens": max(1, (total + 3) // 4),
            "budget": budget, "budget_status": "within_budget" if total <= budget else "over_budget"}


def build_world_state(*, run: Any, profile: dict[str, Any], dataset: dict[str, Any] | None,
                      protocol: dict[str, Any], readiness: dict[str, Any], stage: str,
                      issue_ledger: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"schema_version": 1, "research_profile": profile, "objectives": [protocol["roles"]["primary_outcome"]],
            "constraints": [], "dataset_state": deepcopy(dataset or {}), "literature_state": {}, "code_state": {},
            "hypothesis_state": {}, "protocol_state": protocol, "readiness_state": readiness,
            "current_research_stage": stage, "review_issue_ledger": deepcopy(issue_ledger or []),
            "experiment_state": {}, "scientific_boundaries": [], "run_id": getattr(run, "id", "")}


def failure_state_for(exc: Exception) -> str:
    """Classify operational failures separately from scientific outcomes."""
    text = f"{type(exc).__name__}:{exc}".casefold()
    if any(token in text for token in ("timeout", "429", "connection", "provider", "model_", "llm", "json", "schema")):
        return "RECOVERABLE_PROVIDER_ERROR"
    return "FAILED_SYSTEM"
