"""Structured scientific coverage, split integrity, and escalation contracts."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def compile_scientific_contract(question: str, hypotheses: list[dict] | list[str], plan: dict, task: dict | None = None) -> dict:
    claims = [str(item.get("claim") if isinstance(item, dict) else item).strip() for item in hypotheses if str(item.get("claim") if isinstance(item, dict) else item).strip()]
    trace = [dict(item) for item in plan.get("traceability") or [] if isinstance(item, dict)]
    evaluations = [dict(item) for item in plan.get("evaluations") or [] if isinstance(item, dict)]
    comparisons = [dict(item) for item in plan.get("comparisons") or [] if isinstance(item, dict)]
    split = dict((plan.get("dataset") or {}).get("split_contract") or plan.get("split_contract") or {})
    progressive = dict(plan.get("progressive_experiment") or {})
    if not progressive:
        progressive = {"stages": [{"name": "full", "evidence_target": "formal claim evaluation", "escalation_criteria": "not_applicable", "stop_criteria": list(plan.get("stop_conditions") or [])}]}
    payload = {"question": question, "claims": claims, "traceability": trace, "evaluations": evaluations, "comparisons": comparisons, "task": dict(task or {}), "split_contract": split, "progressive_experiment": progressive}
    payload["contract_sha256"] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload


def validate_coverage(contract: dict) -> list[dict]:
    issues: list[dict] = []
    claims = list(contract.get("claims") or [])
    trace = list(contract.get("traceability") or [])
    task = contract.get("task") or {}
    metrics = set(task.get("expected_metrics") or [x.get("metric") for x in contract.get("evaluations") or [] if x.get("metric")])
    comparisons = task.get("comparisons") or contract.get("comparisons") or []
    for claim in claims:
        matching = [item for item in trace if _related(claim, str(item.get("claim") or ""))]
        if not matching:
            issues.append(_issue("ERROR", "HYPOTHESIS_CLAIM_WITHOUT_EVIDENCE", claim))
            continue
        for item in matching:
            metric = str(item.get("metric") or "").strip()
            if not metric or metric not in metrics:
                issues.append(_issue("ERROR", "CLAIM_METRIC_UNCOVERED", claim, metric=metric))
            if _requires_comparison(item) and not comparisons:
                issues.append(_issue("ERROR", "CLAIM_BASELINE_MISSING", claim))
            if not str(item.get("decision_rule") or "").strip():
                issues.append(_issue("ERROR", "CLAIM_INTERPRETATION_MISSING", claim))
    if claims and not contract.get("question"):
        issues.append(_issue("ERROR", "RESEARCH_QUESTION_MISSING"))
    return issues


def validate_split_contract(split: dict) -> list[dict]:
    issues: list[dict] = []
    if not split:
        return [_issue("ERROR", "SPLIT_CONTRACT_MISSING")]
    partitions = {name: set(map(str, split.get(name, {}).get("ids", []) if isinstance(split.get(name), dict) else [])) for name in ("train", "validation", "test")}
    if not partitions["train"] or not partitions["test"]:
        issues.append(_issue("ERROR", "HELD_OUT_PARTITION_MISSING"))
    for left, right, code in (("train", "test", "TRAIN_TEST_OVERLAP"), ("validation", "test", "VALIDATION_TEST_OVERLAP")):
        if partitions[left] & partitions[right]: issues.append(_issue("ERROR", code))
    groups = split.get("groups") or {}
    for group, assigned in groups.items():
        if isinstance(assigned, (list, tuple, set)) and len(set(assigned)) > 1: issues.append(_issue("ERROR", "GROUP_CROSS_SPLIT", group=str(group)))
    uses = {str(value).casefold() for value in split.get("selection_sources") or []}
    if uses & {"test", "held_out_test"}: issues.append(_issue("ERROR", "TEST_USED_FOR_SELECTION"))
    if str(split.get("final_metric_source") or "").casefold() == "train": issues.append(_issue("ERROR", "FINAL_METRIC_FROM_TRAIN"))
    if not split.get("seed") and not split.get("identity"): issues.append(_issue("WARNING", "SPLIT_REPRODUCIBILITY_REVIEW"))
    if split.get("structure") in {"group", "time_series", "spatial", "duplicate_sensitive"} and not split.get("strategy"):
        issues.append(_issue("WARNING", "STRUCTURE_AWARE_SPLIT_REVIEW"))
    return issues


def progressive_decision(contract: dict, outcome: str) -> dict:
    stages = list((contract.get("progressive_experiment") or {}).get("stages") or [])
    current = stages[0] if stages else {"name": "full"}
    if outcome == "code_failure": return {"action": "repair_code", "stage": current.get("name")}
    if outcome in {"unsupported", "inconclusive"}: return {"action": "scientific_feedback", "stage": current.get("name"), "escalate": False}
    stop = set(current.get("stop_criteria") or [])
    return {"action": "stop" if stop else "evaluate_escalation", "stage": current.get("name"), "escalate": False}


def scientific_feedback(contract: dict, result: dict, verdict: str) -> dict:
    return {"tested_claims": list(contract.get("claims") or []), "evidence": {"metrics": dict(result.get("metrics") or {}), "result_id": result.get("result_id", "")}, "verdict": verdict if verdict in {"supported", "unsupported", "inconclusive"} else "inconclusive", "limitations": list(result.get("limitations") or []), "recommended_next_action": "scientific_review", "code_repair_allowed": False, "contract_sha256": contract.get("contract_sha256", "")}


def _related(left: str, right: str) -> bool:
    generic = {"effect", "method", "model", "result", "performance", "improves", "improve"}
    a = {word.casefold() for word in left.replace("_", " ").split() if len(word) > 2 and word.casefold() not in generic}
    b = {word.casefold() for word in right.replace("_", " ").split() if len(word) > 2 and word.casefold() not in generic}
    return bool(a & b) or left.casefold() == right.casefold()

def _requires_comparison(item: dict) -> bool:
    text = " ".join(str(item.get(key) or "") for key in ("claim", "decision_rule", "mechanism")).casefold()
    return any(word in text for word in ("compare", "improve", "better", "baseline", "versus", " vs "))

def _issue(level: str, code: str, claim: str = "", **extra) -> dict:
    return {"level": level, "code": code, "claim": claim, **extra}
