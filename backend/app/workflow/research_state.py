from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from copy import deepcopy
from typing import Any, Iterable


# A research-state artifact is a snapshot of the ledger, not a scientific input.
# Excluding it prevents recursive snapshots and makes repeated builds deterministic.
LEDGER_EXCLUDED_TYPES = {"research_state"}

# Older members of these sequences remain valid historical observations. They are
# not "superseded" merely because a later round exists.
HISTORICAL_SEQUENCE_TYPES = {
    "experiment_task",
    "experiment_bundle",
    "experiment_diagnosis",
    "experiment_result",
    "iteration_analysis",
    "iteration_evidence",
    "iteration_decision",
    "revision",
}

AUTHORITY_ORDER = [
    "experiment_result",
    "experiment_bundle",
    "experiment_task",
    "plan",
    "hypothesis_selection",
    "reasoning",
    "research_synthesis",
    "evidence",
    "problem",
    "model_inference",
]


def build_research_state(artifacts: Iterable[Any]) -> dict:
    """Build an immutable, topic-independent ledger for every process artifact."""
    values = [
        artifact
        for artifact in artifacts
        if artifact.type not in LEDGER_EXCLUDED_TYPES
    ]
    latest = _latest(values)
    latest_ids = {kind: artifact.id for kind, artifact in latest.items()}
    children = _children_by_parent(values)

    plan_artifact = latest.get("plan")
    result_artifact = latest.get("experiment_result")
    revision_artifact = latest.get("revision")
    plan = deepcopy(plan_artifact.content) if plan_artifact else {}
    result = deepcopy(result_artifact.content) if result_artifact else {}
    revision = deepcopy(revision_artifact.content) if revision_artifact else {}
    original_hypothesis = _selected_hypothesis(latest)

    plan_claim = _text(plan.get("objective")) or _first_text(plan.get("hypotheses"))
    original_claim = _text(original_hypothesis.get("claim"))
    claim_before_execution = (
        plan_claim
        if plan_artifact is not None and plan_artifact.parent_artifact_id
        else (original_claim or plan_claim)
    )
    planned_parameters = deepcopy(
        plan.get("parameters") if isinstance(plan.get("parameters"), dict) else {}
    )
    executed_parameters = deepcopy(
        result.get("parameters") if isinstance(result.get("parameters"), dict) else {}
    )
    if not executed_parameters:
        executed_parameters = deepcopy(planned_parameters)

    conflicts = _parameter_conflicts(
        plan_artifact,
        result_artifact,
        planned_parameters,
        executed_parameters,
    )

    # Preserve compatibility with historical free-text Dropout hypotheses. New
    # research topics do not depend on this detector; structured parameters above
    # are the general conflict mechanism.
    original_dropout = _find_dropout_probability(original_claim)
    executed_dropout = _find_dropout_probability(executed_parameters, result)
    if (
        original_dropout is not None
        and executed_dropout is not None
        and original_dropout != executed_dropout
    ):
        conflicts.insert(
            0,
            {
                "code": "hypothesis_execution_parameter_mismatch",
                "field": "dropout_probability",
                "superseded_value": original_dropout,
                "authoritative_value": executed_dropout,
                "resolution": (
                    "Use the recorded execution parameter as the report fact; "
                    "retain the original hypothesis only as iteration history."
                ),
                "source_artifact_id": result_artifact.id if result_artifact else "",
            },
        )

    artifact_states = [
        _artifact_state(
            artifact,
            latest,
            children,
            conflicts,
        )
        for artifact in values
    ]

    verdict = _text(revision.get("verdict")) or _text(
        (result.get("analysis") or {}).get("verdict")
        if isinstance(result.get("analysis"), dict)
        else result.get("verdict")
    )
    hypothesis_status = {
        "supported": "verified",
        "failed": "rejected",
        "partial": "unverified",
    }.get(verdict, "unverified")
    if original_dropout is not None and executed_dropout is not None and original_dropout != executed_dropout:
        hypothesis_status = "superseded"

    active_claim = claim_before_execution
    if executed_dropout is not None and active_claim:
        active_claim = _replace_dropout_probability(active_claim, executed_dropout)

    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    reasoning = deepcopy(latest["reasoning"].content) if latest.get("reasoning") else {}
    targeted_artifacts = [
        deepcopy(artifact.content)
        for artifact in values
        if artifact.type == "targeted_retrieval"
    ]
    experiment_scope = _text(result.get("experiment_id")) or "current_experiment"
    claims = [
        {
            "claim_id": "original_hypothesis",
            "kind": "hypothesis",
            "content": original_claim,
            "status": (
                "superseded"
                if original_dropout is not None
                and executed_dropout is not None
                and original_dropout != executed_dropout
                else "unverified"
            ),
            "source_artifact_id": _source_id_for_hypothesis(latest),
            "scope": experiment_scope,
        },
        {
            "claim_id": "active_hypothesis",
            "kind": "hypothesis",
            "content": active_claim,
            "status": hypothesis_status,
            "source_artifact_id": plan_artifact.id if plan_artifact else "",
            "scope": experiment_scope,
        },
        {
            "claim_id": "planned_parameters",
            "kind": "plan_parameter",
            "content": planned_parameters,
            "status": "superseded" if conflicts else "verified",
            "source_artifact_id": plan_artifact.id if plan_artifact else "",
            "scope": experiment_scope,
        },
        {
            "claim_id": "executed_parameters",
            "kind": "execution_parameter",
            "content": executed_parameters,
            "status": "verified" if _is_verified_result(result) else "unverified",
            "source_artifact_id": result_artifact.id if result_artifact else "",
            "scope": experiment_scope,
        },
        {
            "claim_id": "measured_metrics",
            "kind": "experiment_fact",
            "content": deepcopy(metrics),
            "status": "verified" if _is_verified_result(result) else "unverified",
            "source_artifact_id": result_artifact.id if result_artifact else "",
            "scope": experiment_scope,
        },
        {
            "claim_id": "terminal_verdict",
            "kind": "conclusion",
            "content": verdict,
            "status": "verified" if verdict else "unverified",
            "source_artifact_id": revision_artifact.id if revision_artifact else "",
            "scope": experiment_scope,
        },
    ]
    return {
        "schema_version": 3,
        "ledger_policy": {
            "coverage": "all_process_artifacts",
            "excluded_types": sorted(LEDGER_EXCLUDED_TYPES),
            "exclusion_reason": "prevent_recursive_state_snapshots",
            "lifecycle_dimension": ["active", "superseded", "historical"],
            "validity_dimension": [
                "verified",
                "unverified",
                "invalid",
                "conflicted",
                "not_applicable",
            ],
        },
        "authority_order": AUTHORITY_ORDER,
        "active_artifacts": latest_ids,
        "artifact_states": artifact_states,
        "claims": [item for item in claims if item["content"] not in ("", {}, [])],
        "conflicts": conflicts,
        "literature_rounds": deepcopy(
            (reasoning.get("targeted_retrieval") or {}).get("history") or []
        ),
        "literature_queries": list(
            (reasoning.get("targeted_retrieval") or {}).get("queries") or []
        ),
        "literature_registry": deepcopy(reasoning.get("literature_registry") or []),
        "research_synthesis": deepcopy(latest["research_synthesis"].content) if latest.get("research_synthesis") else {},
        "evidence_registry": deepcopy(reasoning.get("evidence_registry") or []),
        "research_gaps": deepcopy(reasoning.get("research_gaps") or []),
        "candidate_evidence_maps": deepcopy(reasoning.get("candidate_evidence_maps") or []),
        "unverified_citations": deepcopy(reasoning.get("unverified_citations") or []),
        "targeted_retrieval_round": len(targeted_artifacts),
        "candidate_revision_round": sum(
            bool(item.get("was_revised"))
            for item in reasoning.get("candidate_assessments") or []
            if isinstance(item, dict)
        ),
        "critic_decisions": [
            {
                "candidate_index": item.get("candidate_index"),
                "decision": item.get("critic_decision") or item.get("recommendation"),
            }
            for item in reasoning.get("candidate_assessments") or []
            if isinstance(item, dict)
        ],
        "rejected_candidates": [
            item.get("candidate_index")
            for item in reasoning.get("candidate_assessments") or []
            if isinstance(item, dict) and item.get("status") == "rejected"
        ],
        "selection_history": targeted_artifacts,
        "canonical": {
            "active_hypothesis": active_claim,
            "original_hypothesis": original_claim,
            "planned_parameters": planned_parameters,
            "executed_parameters": executed_parameters,
            "executed_dropout_probability": executed_dropout,
            "metrics": deepcopy(metrics),
            "verdict": verdict,
            "experiment_id": _text(result.get("experiment_id")),
        },
    }


def active_plan_for_report(plan: dict, state: dict) -> dict:
    value = deepcopy(plan)
    canonical = state.get("canonical") if isinstance(state, dict) else {}
    active_claim = _text((canonical or {}).get("active_hypothesis"))
    if active_claim:
        value["objective"] = active_claim
        value["hypotheses"] = [active_claim]
    executed = (canonical or {}).get("executed_parameters")
    if isinstance(executed, dict) and executed:
        value["parameters"] = deepcopy(executed)
    value["resolved_conflicts"] = deepcopy(state.get("conflicts") or [])
    return value


def resolve_fact_path(facts: dict, path: str) -> Any:
    parts = [part for part in str(path or "").split(".") if part]
    if parts and parts[0] == "facts":
        parts = parts[1:]
    value: Any = facts
    for part in parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def _artifact_state(
    artifact: Any,
    latest: dict[str, Any],
    children: dict[str, list[Any]],
    conflicts: list[dict],
) -> dict:
    current = latest.get(artifact.type)
    is_latest = current is not None and current.id == artifact.id
    if is_latest:
        lifecycle = "active"
    elif artifact.type in HISTORICAL_SEQUENCE_TYPES:
        lifecycle = "historical"
    else:
        lifecycle = "superseded"

    # Candidate sets become history after a selection has been made.
    if artifact.type == "hypothesis" and latest.get("hypothesis_selection"):
        lifecycle = "historical"
    if artifact.type in {"report_draft", "report_audit"} and latest.get("report"):
        lifecycle = "historical"

    validity, validity_reason = _artifact_validity(
        artifact,
        children,
        conflicts,
    )
    return {
        "artifact_id": artifact.id,
        "artifact_type": artifact.type,
        "version": artifact.version,
        # Backward-compatible alias for existing consumers.
        "status": lifecycle,
        "lifecycle_status": lifecycle,
        "validity_status": validity,
        "validity_reason": validity_reason,
        "source_step": artifact.source_step,
        "parent_artifact_id": artifact.parent_artifact_id or "",
        "superseded_by": (
            current.id
            if lifecycle == "superseded" and current is not None
            else ""
        ),
        "content_sha256": _content_fingerprint(artifact.content),
    }


def _artifact_validity(
    artifact: Any,
    children: dict[str, list[Any]],
    conflicts: list[dict],
) -> tuple[str, str]:
    content = artifact.content if isinstance(artifact.content, dict) else {}
    kind = artifact.type

    if kind == "dataset_profile":
        verified = str(content.get("inspection_status") or "").lower() == "verified"
        return (
            ("verified", "dataset_inspection_verified")
            if verified
            else ("unverified", "dataset_inspection_not_verified")
        )
    if kind in {"evidence", "iteration_evidence"}:
        return _evidence_validity(content)
    if kind == "experiment_result":
        if str(content.get("status") or "").lower() == "failed":
            return "invalid", "experiment_execution_failed"
        if _is_verified_result(content):
            return "verified", "real_experiment_result_recorded"
        return "unverified", "real_experiment_not_confirmed"
    if kind in {"experiment_task", "experiment_bundle"}:
        if _has_verified_result_descendant(artifact.id, children):
            return "verified", "lineage_contains_verified_experiment_result"
        return "unverified", "no_verified_result_in_lineage"
    if kind == "experiment_diagnosis":
        return (
            ("verified", "diagnosis_recorded")
            if content
            else ("unverified", "empty_diagnosis")
        )
    if kind == "revision":
        return (
            ("verified", "verdict_recorded")
            if _text(content.get("verdict"))
            else ("unverified", "verdict_missing")
        )
    if kind == "plan" and any(
        item.get("code") == "plan_execution_parameter_mismatch"
        for item in conflicts
    ):
        return "conflicted", "planned_parameters_differ_from_execution"
    if kind in {"hypothesis_selection", "reasoning"} and any(
        item.get("code") == "hypothesis_execution_parameter_mismatch"
        for item in conflicts
    ):
        return "conflicted", "hypothesis_parameter_differs_from_execution"
    if kind == "report":
        return "verified", "report_completed"
    if kind == "report_draft":
        return "unverified", "draft_not_exportable"
    if kind == "report_audit":
        hard_failures = content.get("hard_failures") or content.get("blocking_issues") or []
        return (
            ("invalid", "audit_contains_unresolved_hard_failures")
            if hard_failures
            else ("verified", "audit_contains_no_hard_failure")
        )
    return "not_applicable", "process_record_has_no_independent_truth_verdict"


def _evidence_validity(content: dict) -> tuple[str, str]:
    references = content.get("references")
    if not isinstance(references, list) or not references:
        return "unverified", "no_reference_records"
    statuses = [
        bool(item.get("verified") or item.get("exportable"))
        for item in references
        if isinstance(item, dict)
    ]
    if statuses and all(statuses) and len(statuses) == len(references):
        return "verified", "all_reference_records_verified"
    return "unverified", "one_or_more_references_unverified"


def _parameter_conflicts(
    plan_artifact: Any | None,
    result_artifact: Any | None,
    planned: dict,
    executed: dict,
) -> list[dict]:
    if not plan_artifact or not result_artifact:
        return []
    planned_values = _flatten_scalars(planned)
    executed_values = _flatten_scalars(executed)
    conflicts = []
    for field in sorted(planned_values.keys() & executed_values.keys()):
        if _equivalent(planned_values[field], executed_values[field]):
            continue
        conflicts.append(
            {
                "code": "plan_execution_parameter_mismatch",
                "field": field,
                "superseded_value": deepcopy(planned_values[field]),
                "authoritative_value": deepcopy(executed_values[field]),
                "resolution": (
                    "Use the recorded execution value as the report fact and retain "
                    "the planned value only as historical intent."
                ),
                "source_artifact_id": result_artifact.id,
            }
        )
    return conflicts


def _flatten_scalars(value: Any, prefix: str = "") -> dict[str, Any]:
    output: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            output.update(_flatten_scalars(item, child))
    elif isinstance(value, (list, tuple)):
        output[prefix] = deepcopy(list(value))
    elif not isinstance(value, set):
        output[prefix] = value
    return output


def _equivalent(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= 1e-12
    return left == right


def _children_by_parent(artifacts: list[Any]) -> dict[str, list[Any]]:
    children: dict[str, list[Any]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.parent_artifact_id:
            children[artifact.parent_artifact_id].append(artifact)
    return dict(children)


def _has_verified_result_descendant(
    artifact_id: str,
    children: dict[str, list[Any]],
    visited: set[str] | None = None,
) -> bool:
    visited = set(visited or ())
    if artifact_id in visited:
        return False
    visited.add(artifact_id)
    for child in children.get(artifact_id, []):
        if child.type == "experiment_result" and _is_verified_result(child.content):
            return True
        if _has_verified_result_descendant(child.id, children, visited):
            return True
    return False


def _is_verified_result(content: dict) -> bool:
    return bool(content.get("is_real_experiment")) and str(
        content.get("status") or "completed"
    ).lower() not in {"failed", "error"}


def _content_fingerprint(content: Any) -> str:
    serialized = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _latest(artifacts: list[Any]) -> dict[str, Any]:
    values = {}
    for artifact in artifacts:
        values[artifact.type] = artifact
    return values


def _selected_hypothesis(latest: dict[str, Any]) -> dict:
    reasoning = latest.get("reasoning")
    if reasoning:
        active = reasoning.content.get("active_hypothesis")
        if isinstance(active, dict):
            return deepcopy(active)
    selection = latest.get("hypothesis_selection")
    if selection:
        selected = selection.content.get("selected")
        if isinstance(selected, list) and selected and isinstance(selected[0], dict):
            return deepcopy(selected[0])
    return {}


def _source_id_for_hypothesis(latest: dict[str, Any]) -> str:
    for kind in ("reasoning", "hypothesis_selection", "hypothesis"):
        if latest.get(kind):
            return latest[kind].id
    return ""


def _find_dropout_probability(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key).lower()
                if "dropout" in key_text and isinstance(item, (int, float)):
                    return float(item)
                found = _find_dropout_probability(item)
                if found is not None:
                    return found
        elif isinstance(value, list):
            found = _find_dropout_probability(*value)
            if found is not None:
                return found
        else:
            match = re.search(
                r"dropout\s*\(\s*p\s*=\s*(0(?:\.\d+)?|1(?:\.0+)?)\s*\)",
                str(value or ""),
                flags=re.IGNORECASE,
            )
            if match:
                return float(match.group(1))
    return None


def _replace_dropout_probability(text: str, probability: float) -> str:
    replacement = f"Dropout(p={probability:g})"
    return re.sub(
        r"dropout\s*\(\s*p\s*=\s*(?:0(?:\.\d+)?|1(?:\.0+)?)\s*\)",
        replacement,
        text,
        flags=re.IGNORECASE,
    )


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            text = _text(item)
            if text:
                return text
    return ""


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
