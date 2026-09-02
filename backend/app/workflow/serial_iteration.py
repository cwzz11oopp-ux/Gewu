"""Bounded serial search over immutable, audited experiment artifacts.

This is a selection policy, not a claim that adaptive search proves a discovery.
Only comparable paired measurements can replace an incumbent; all other trials
remain evidence. Legacy runs without a frozen policy keep their original route.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from math import isclose, isfinite

from backend.app.models.experiment import ExperimentBundle
from backend.app.workflow.phase2_evidence import paired_seed_metrics, result_evidence


def digest(value) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def freeze_iteration_policy(problem: dict, user_input: str, max_rounds: int) -> dict:
    intent = problem.get("research_intent") or {}
    quote = str(intent.get("goal_quote") or "").strip() if isinstance(intent, dict) else ""
    optimize = (
        isinstance(intent, dict) and intent.get("kind") == "optimization"
        and bool(quote) and quote in user_input
    )
    policy = {
        "schema_version": 1,
        "kind": "optimization" if optimize else "verification",
        "goal_quote": quote if quote in user_input else "",
        "reason": str(intent.get("reason") or "")[:500] if isinstance(intent, dict) else "",
        "max_rounds": max(1, max_rounds),
        "stagnation_patience": 2,
        "promotion_rule": "paired_95ci_positive",
        "confirmation_status": "independent_confirmation_required",
    }
    policy["policy_sha256"] = digest(policy)
    return policy


def iteration_policy(artifacts) -> dict:
    policies = [a.content for a in artifacts if a.type == "iteration_policy"]
    if len(policies) > 1:
        raise ValueError("ITERATION_POLICY_DUPLICATED")
    policy = deepcopy(policies[0]) if policies else {}
    if policy.get("schema_version") != 1:
        return {}
    expected = policy.pop("policy_sha256", "")
    if not expected or digest(policy) != expected:
        raise ValueError("ITERATION_POLICY_INTEGRITY_INVALID")
    return {**policy, "policy_sha256": expected}


def trial_signature(plan: dict) -> str:
    # Exclude narrative/lineage/IDs. Rephrasing the motivation is not a new trial.
    fields = ("method", "dataset", "comparisons", "evaluations", "parameters",
              "procedure", "seeds", "baseline_and_controls", "split_contract",
              "preprocessing", "statistical_summary")
    return digest({key: plan.get(key) for key in fields})


def _lineage(index: dict, result):
    bundle = index.get(result.parent_artifact_id)
    if bundle is None or bundle.type != "experiment_bundle":
        return None
    parent = bundle
    visited = set()
    while parent is not None and parent.id not in visited:
        visited.add(parent.id)
        if parent.type == "experiment_task":
            plan = index.get(parent.parent_artifact_id)
            if plan is not None and plan.type == "plan":
                return plan, parent, bundle
            return None
        if parent.type not in {"experiment_bundle", "experiment_diagnosis"}:
            return None
        parent = index.get(parent.parent_artifact_id)
    return None


def _observation(artifacts, index: dict, result) -> dict:
    row = {"result_id": result.id, "eligible": False, "reason": "AUDITED_LINEAGE_REQUIRED"}
    lineage = _lineage(index, result)
    if lineage is None:
        return row
    plan, task, bundle = lineage
    executed_plan = task.content.get("plan") or plan.content
    row.update(plan_id=plan.id, task_id=task.id, bundle_id=bundle.id,
               trial_signature=trial_signature(executed_plan))
    row["intervention"] = {
        "method": str(executed_plan.get("method") or "")[:600],
        "parameters": deepcopy(executed_plan.get("parameters") or {}),
        "changed_fields": list((executed_plan.get("iteration_contract") or {}).get("changed_fields") or []),
    }
    revision = next((a.content for a in reversed(artifacts)
                     if a.type == "revision" and a.parent_artifact_id == result.id), {})
    row.update(verdict=revision.get("verdict", ""),
               decision=revision.get("decision", ""),
               selected_direction=deepcopy(revision.get("selected_direction") or {}),
               learning=str(revision.get("feedback") or revision.get("selection_reason") or "")[:600])
    value = result.content
    manifest = bundle.content.get("manifest") or {}
    if (value.get("is_real_experiment") is not True
            or (value.get("audit") or {}).get("integrity_status") != "passed"
            or value.get("status") in {"failed", "error"} or value.get("anomalies")
            or value.get("experiment_id") != manifest.get("experiment_id")
            or value.get("result_id") != manifest.get("result_id")):
        return row
    protocol = task.content.get("phase2_protocol") or {}
    runtime = bundle.content.get("runtime_contract") or {}
    metric = str(protocol.get("primary_metric") or "")
    seed_values = runtime.get("seeds") or protocol.get("seeds") or []
    fingerprint = runtime.get("dataset_fingerprint") or (executed_plan.get("dataset") or {}).get("content_fingerprint")
    if (not metric or not seed_values or not fingerprint or not protocol.get("split")
            or (runtime.get("stage") or protocol.get("stage")) == "smoke"):
        row["reason"] = "COMPARISON_PROTOCOL_INCOMPLETE"
        return row
    baseline, candidate = paired_seed_metrics(value, metric, [metric], executed_plan.get("comparisons"))
    if (set(baseline) != set(seed_values) or set(candidate) != set(seed_values)
            or len(value.get("seed_results") or []) != len(seed_values)
            or not all(isfinite(v) for v in [*baseline.values(), *candidate.values()])):
        row["reason"] = "COMPLETE_PAIRED_MEASUREMENTS_REQUIRED"
        return row
    metric_evidence = result_evidence(baseline, candidate, metric, protocol.get("primary_metric_direction"))
    # Exclude the intervention but include every declared evaluation/control
    # dimension. Changed protocols start separate series, never a global ranking.
    comparison = (executed_plan.get("comparisons") or [{}])[-1]
    protocol_key = digest({
        "dataset": fingerprint, "metric": metric, "direction": metric_evidence["direction"],
        "split": protocol.get("split"), "split_contract": executed_plan.get("split_contract"),
        "preprocessing": protocol.get("preprocessing"), "seeds": seed_values,
        "stage": runtime.get("stage") or protocol.get("stage"),
        "budget": protocol.get("training_budget"), "epochs": runtime.get("epochs"),
        "baseline": comparison.get("baseline"), "controls": executed_plan.get("baseline_and_controls"),
        "evaluations": executed_plan.get("evaluations"),
        "constraints_id": task.content.get("research_constraints_artifact_id"),
    })
    row.update(eligible=True, reason="", protocol_key=protocol_key, metric=metric,
               direction=metric_evidence["direction"], score=metric_evidence["idea"]["mean"],
               baseline_score=metric_evidence["baseline"]["mean"],
               candidate_seeds=candidate, baseline_seeds=baseline,
               baseline_comparison_status=metric_evidence["status"],
               bundle_sha256=digest(bundle.content))
    return row


def build_iteration_memory(artifacts) -> dict:
    artifacts = list(artifacts)
    policy = iteration_policy(artifacts)
    if policy.get("kind") != "optimization":
        return {"enabled": False}
    index = {a.id: a for a in artifacts}
    history, best_by_protocol = [], {}
    stagnant = 0
    results = [a for a in artifacts if a.type == "experiment_result"
               and str(a.content.get("status") or "").lower() != "failed"]
    # Re-analysis/retry of one task is not an additional scientific experiment.
    task_results = {}
    for result in results:
        lineage = _lineage(index, result)
        task_results[lineage[1].id if lineage else result.id] = result.id
    for result in (a for a in results if a.id in task_results.values()):
        row = _observation(artifacts, index, result)
        key = row.get("protocol_key")
        incumbent = best_by_protocol.get(key)
        row["selection"] = "ineligible"
        if row["eligible"]:
            if incumbent is None:
                best_by_protocol[key] = row
                row["selection"] = "initial_candidate"
            elif any(not isclose(row["baseline_seeds"][seed], value, rel_tol=1e-6, abs_tol=1e-8)
                     for seed, value in incumbent["baseline_seeds"].items()):
                row.update(eligible=False, reason="BASELINE_DRIFT_REQUIRES_REVALIDATION")
            else:
                evidence = result_evidence(incumbent["candidate_seeds"], row["candidate_seeds"],
                                           row["metric"], row["direction"])
                row["incumbent_comparison"] = {
                    k: evidence[k] for k in ("status", "mean_delta", "confidence_interval_95")
                }
                row["selection"] = "keep_incumbent"
                if evidence["status"] == "positive_stable":
                    best_by_protocol[key] = row
                    row["selection"] = "promote"
        # A change of protocol is not progress; it cannot reset the search budget.
        if row["selection"] == "promote" or not history:
            stagnant = 0
        else:
            stagnant += 1
        history.append(row)
    current = history[-1] if history else {}
    best = best_by_protocol.get(current.get("protocol_key")) if current.get("eligible") else None
    return {
        "enabled": True, "policy": policy, "rounds_observed": len(history),
        "stagnant_rounds": stagnant, "current": deepcopy(current),
        "best": deepcopy(best), "best_by_protocol": deepcopy(list(best_by_protocol.values())),
        "history": deepcopy(history),
        "confirmation_status": "independent_confirmation_required",
        "interpretation": "Best observed candidate within a comparable protocol, not independent confirmation or proof of gain over baseline.",
    }


def prompt_memory(memory: dict) -> dict:
    """No source or full artifacts in decision context; keep prompt growth bounded."""
    def bounded(value, depth=0):
        if isinstance(value, str):
            return value[:600]
        if isinstance(value, dict):
            if depth >= 3:
                return "[nested details omitted]"
            entries = list(value.items())[:20]
            return {k: bounded(v, depth + 1) for k, v in entries}
        if isinstance(value, list):
            return [bounded(v, depth + 1) for v in value[:12]] if depth < 3 else "[nested details omitted]"
        return value

    def compact(row):
        return {k: bounded(v) for k, v in (row or {}).items()
                if k not in {"candidate_seeds", "baseline_seeds", "selected_direction"}}
    return {
        **{k: deepcopy(memory.get(k)) for k in
           ("enabled", "policy", "rounds_observed", "stagnant_rounds", "confirmation_status", "interpretation")},
        "best": compact(memory.get("best")), "current": compact(memory.get("current")),
        "history": [compact(r) for r in memory.get("history", [])[-8:]],
        "context_policy": "Last 8 trials; long text/nested parameters truncated. Full immutable artifacts remain authoritative.",
    }


def continuation_stop(memory: dict, iteration: int, max_rounds: int) -> str:
    budget = min(max_rounds, int(memory.get("policy", {}).get("max_rounds", max_rounds)))
    if iteration >= budget:
        return "ITERATION_LIMIT_REACHED"
    if memory.get("stagnant_rounds", 0) >= memory.get("policy", {}).get("stagnation_patience", 2):
        return "OPTIMIZATION_STAGNATION_LIMIT"
    return ""


def direction_issues(direction: dict, memory: dict) -> list[str]:
    if direction.get("decision") == "REPORT":
        return []
    selected = direction.get("selected_direction") or {}
    issues = []
    for key in ("name", "problem_addressed", "result_basis", "changed_variable", "fixed_controls",
                "target_metrics", "success_rule", "failure_rule", "stop_rule", "source_result_ids"):
        value = selected.get(key)
        if not value or (isinstance(value, str) and not value.strip()):
            issues.append(f"ITERATION_DIRECTION_REQUIRED:{key}")
    for key in ("result_basis", "fixed_controls", "target_metrics"):
        values = selected.get(key)
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
            issues.append(f"ITERATION_DIRECTION_LIST_REQUIRED:{key}")
    known = {r["result_id"] for r in memory.get("history", [])}
    sources = selected.get("source_result_ids")
    if not isinstance(sources, list) or not sources or any(s not in known for s in sources if isinstance(s, str)) or any(not isinstance(s, str) for s in sources):
        issues.append("ITERATION_DIRECTION_RESULT_REFERENCE_INVALID")
    if not any(c.get("name") == selected.get("name") and c.get("changed_variable") == selected.get("changed_variable")
               for c in direction.get("optimization_candidates", [])):
        issues.append("ITERATION_DIRECTION_NOT_IN_CANDIDATES")
    return issues


def implementation_base(artifacts, reference: dict) -> dict:
    """Fail closed rather than silently regenerating from the latest/wrong code."""
    index = {a.id: a for a in artifacts}
    result = index.get(reference.get("result_id"))
    lineage = _lineage(index, result) if result is not None else None
    if lineage is None:
        raise ValueError("ITERATION_BASE_LINEAGE_INVALID")
    plan, task, bundle = lineage
    if (plan.id != reference.get("plan_id") or task.id != reference.get("task_id")
            or bundle.id != reference.get("bundle_id") or digest(bundle.content) != reference.get("bundle_sha256")):
        raise ValueError("ITERATION_BASE_SNAPSHOT_MISMATCH")
    validated = ExperimentBundle.model_validate(bundle.content)
    files = [f.model_dump() for f in validated.files if f.path == "train.py"]
    if len(files) != 1:
        raise ValueError("ITERATION_BASE_SOURCE_REQUIRED")
    return {"kind": "serial_optimization", "bundle_artifact_id": bundle.id,
            "files": files, "requirements": deepcopy(validated.requirements),
            "parameters": deepcopy((task.content.get("plan") or plan.content).get("parameters") or {}),
            "reference": deepcopy(reference)}


def apply_source_edits(base: dict, raw: dict, *, allow_unchanged: bool = False) -> dict:
    """Apply exact edits to an immutable train.py, not a regenerated replacement."""
    source = base["files"][0]["content"]
    edits = raw.get("edits")
    if not isinstance(edits, list) or not (0 if allow_unchanged else 1) <= len(edits) <= 12:
        raise ValueError("EXPERIMENT_CODE_ITERATION_PATCH_INVALID:edits")
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("EXPERIMENT_CODE_ITERATION_PATCH_INVALID:shape")
        old, new = edit.get("old"), edit.get("new")
        if (not isinstance(old, str) or not isinstance(new, str) or not old or old == new
                or source.count(old) != 1 or len(old) > 0.75 * len(source)):
            raise ValueError("EXPERIMENT_CODE_ITERATION_PATCH_INVALID:unique_match_required")
        source = source.replace(old, new, 1)
    if source == base["files"][0]["content"] and not allow_unchanged:
        raise ValueError("EXPERIMENT_CODE_ITERATION_PATCH_INVALID:no_change")
    return {"files": [{"path": "train.py", "content": source}],
            "requirements": raw.get("requirements", base["requirements"])}
