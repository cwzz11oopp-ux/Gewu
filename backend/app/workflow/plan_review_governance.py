from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Iterable, Mapping

from backend.app.workflow.plan_contract import (
    CANONICAL_PLAN_CONTRACT_FIELDS,
    FIELD_ALIAS_TO_CANONICAL,
    canonical_contract_field,
    canonical_contract_fields,
)


REVIEW_SEVERITIES = frozenset({"BLOCKER", "WARNING", "SUGGESTION"})
ISSUE_STATUSES = frozenset({"OPEN", "CLOSED", "REOPENED", "DEFERRED", "REJECTED"})
POLICY_SCHEMA_VERSION = 3
GOVERNANCE_IMPLEMENTATION_SEMANTIC_VERSION = "plan-review-governance-v5"
MIGRATION_SCHEMA_VERSION = 2
RECOVERY_SCHEMA_VERSION = 1

# These are implementation concerns owned by the loader, Experiment Validator,
# Harness, and bounded experiment repair.  The wording check deliberately uses
# the finding content, not an issue ID, so a reviewer cannot turn a code-level
# concern into a plan veto merely by labelling it BLOCKER.
_EXECUTION_RESOLVABLE_TERMS = frozenset({
    "tensor axis", "axis", "dtype", "data type", "tensor shape", "shape",
    "loader", "mat/", "matlab", "hdf5", "csv", "field mapping", "fft",
    "window parameter", "window size", "training code", "implementation",
    "api", "interface", "path", "dependency", "output format", "runtime",
    "experiment bundle", "code-level", "code level",
})


class PlanReviewPolicyIntegrityError(ValueError):
    """A frozen governance record is missing, ambiguous, or no longer verifiable."""


@dataclass(frozen=True)
class ReviewAdjudication:
    issues: list[dict[str, Any]]
    validated_open_blocker_ids: tuple[str, ...]
    warning_ids: tuple[str, ...]
    suggestion_ids: tuple[str, ...]
    closed_issue_ids: tuple[str, ...]

    @property
    def verdict(self) -> str:
        return "REVISE" if self.validated_open_blocker_ids else "ACCEPT"


def canonical_sha256(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def normalize_skill_content(value: str) -> str:
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def freeze_review_policy(
    policy: Mapping[str, Any],
    *,
    policy_sha256: str,
    active_skill_ids: Iterable[str],
    instruction_hashes: Mapping[str, str],
    max_content_revisions: int,
    problem_anchor: Mapping[str, Any],
    research_constraints_reference: Mapping[str, Any],
    skill_snapshots: Iterable[Mapping[str, Any]] = (),
    runtime_instructions: str = "",
    review_runtime_contract_snapshot: str = "",
    revision_runtime_contract_snapshot: str = "",
    authoritative_plan_contract_snapshot: Mapping[str, str] | None = None,
    canonical_contract_field_registry: Mapping[str, str] | None = None,
    contract_field_aliases: Mapping[str, str] | None = None,
    planner_fixed_review_instructions: str = "",
    planner_fixed_revision_instructions: str = "",
    review_prompt_schema_snapshot: Mapping[str, Any] | None = None,
    revision_prompt_schema_snapshot: Mapping[str, Any] | None = None,
    prompt_schema_version: int = 1,
    governance_semantic_version: str = GOVERNANCE_IMPLEMENTATION_SEMANTIC_VERSION,
    source_artifact_lineage: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Create a self-verifying frozen policy payload owned by the active Skills."""
    source_schema_version = policy.get("schema_version")
    policy_id = str(policy.get("policy_id") or "").strip()
    blocker_classes = _unique_strings(policy.get("blocker_classes"))
    severity_levels = _unique_strings(policy.get("severity_levels"))
    non_blocking = _unique_strings(policy.get("non_blocking_severities"))
    reopen_bases = _unique_strings(policy.get("closed_issue_reopen_bases"))
    new_blocker_bases = _unique_strings(policy.get("new_blocker_after_initial_round_bases"))
    if not isinstance(source_schema_version, int) or source_schema_version < 1 or not policy_id:
        raise ValueError("PLAN_REVIEW_POLICY_INVALID:identity")
    if not blocker_classes:
        raise ValueError("PLAN_REVIEW_POLICY_INVALID:blocker_classes")
    if set(severity_levels) != REVIEW_SEVERITIES:
        raise ValueError("PLAN_REVIEW_POLICY_INVALID:severity_levels")
    if not set(non_blocking).issubset(REVIEW_SEVERITIES):
        raise ValueError("PLAN_REVIEW_POLICY_INVALID:non_blocking_severities")
    if not reopen_bases or not new_blocker_bases:
        raise ValueError("PLAN_REVIEW_POLICY_INVALID:transition_rules")
    if policy.get("accept_when_validated_open_blockers_zero") is not True:
        raise ValueError("PLAN_REVIEW_POLICY_INVALID:acceptance_rule")

    normalized_snapshots: list[dict[str, str]] = []
    for raw in skill_snapshots:
        skill_name = str(raw.get("skill_name") or raw.get("skill_id") or "").strip()
        content = normalize_skill_content(str(raw.get("normalized_content") or ""))
        if not skill_name or not content:
            raise ValueError("PLAN_REVIEW_POLICY_INVALID:skill_snapshot")
        normalized_snapshots.append({
            "skill_name": skill_name,
            "normalized_content": content,
            "sha256": canonical_sha256(content),
        })
    active = list(dict.fromkeys(str(item) for item in active_skill_ids))
    if normalized_snapshots and [item["skill_name"] for item in normalized_snapshots] != active:
        raise ValueError("PLAN_REVIEW_POLICY_INVALID:skill_snapshot_order")
    normalized_instructions = normalize_skill_content(runtime_instructions)
    review_contract = normalize_skill_content(review_runtime_contract_snapshot)
    revision_contract = normalize_skill_content(revision_runtime_contract_snapshot)
    contract_snapshot = deepcopy(
        dict(authoritative_plan_contract_snapshot or CANONICAL_PLAN_CONTRACT_FIELDS)
    )
    field_registry = deepcopy(
        dict(canonical_contract_field_registry or CANONICAL_PLAN_CONTRACT_FIELDS)
    )
    field_aliases = deepcopy(dict(contract_field_aliases or FIELD_ALIAS_TO_CANONICAL))
    review_schema = deepcopy(dict(review_prompt_schema_snapshot or {}))
    revision_schema = deepcopy(dict(revision_prompt_schema_snapshot or {}))
    fixed_review = normalize_skill_content(planner_fixed_review_instructions)
    fixed_revision = normalize_skill_content(planner_fixed_revision_instructions)
    if (
        not review_contract
        or not revision_contract
        or not contract_snapshot
        or contract_snapshot != field_registry
        or not review_schema
        or not revision_schema
        or not fixed_review
        or not fixed_revision
        or not isinstance(prompt_schema_version, int)
        or prompt_schema_version < 1
        or not str(governance_semantic_version).strip()
    ):
        raise ValueError("PLAN_REVIEW_POLICY_INVALID:semantic_package")
    if any(target not in field_registry for target in field_aliases.values()):
        raise ValueError("PLAN_REVIEW_POLICY_INVALID:contract_field_aliases")
    normalized_policy = {
        "schema_version": int(source_schema_version),
        "policy_id": policy_id,
        "severity_levels": severity_levels,
        "blocker_classes": blocker_classes,
        "non_blocking_severities": non_blocking,
        "accept_when_validated_open_blockers_zero": True,
        "closed_issue_reopen_bases": reopen_bases,
        "new_blocker_after_initial_round_bases": new_blocker_bases,
    }
    payload = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_version": policy_id,
        "policy_id": policy_id,
        "normalized_policy_payload": normalized_policy,
        "source_policy_sha256": str(policy_sha256),
        "policy_sha256": str(policy_sha256),
        "active_skill_ids": active,
        "instruction_hashes": {str(key): str(value) for key, value in instruction_hashes.items()},
        "skill_snapshots": normalized_snapshots,
        "runtime_instructions": normalized_instructions,
        "runtime_instructions_sha256": canonical_sha256(normalized_instructions),
        "review_runtime_contract_snapshot": review_contract,
        "review_runtime_contract_sha256": canonical_sha256(review_contract),
        "revision_runtime_contract_snapshot": revision_contract,
        "revision_runtime_contract_sha256": canonical_sha256(revision_contract),
        "authoritative_plan_contract_snapshot": contract_snapshot,
        "authoritative_plan_contract_sha256": canonical_sha256(contract_snapshot),
        "canonical_contract_field_registry": field_registry,
        "canonical_contract_field_registry_sha256": canonical_sha256(field_registry),
        "contract_field_aliases": field_aliases,
        "contract_field_aliases_sha256": canonical_sha256(field_aliases),
        "planner_fixed_review_instructions": fixed_review,
        "planner_fixed_revision_instructions": fixed_revision,
        "review_prompt_schema_snapshot": review_schema,
        "review_prompt_schema_sha256": canonical_sha256(review_schema),
        "revision_prompt_schema_snapshot": revision_schema,
        "revision_prompt_schema_sha256": canonical_sha256(revision_schema),
        "prompt_schema_version": prompt_schema_version,
        "governance_implementation_semantic_version": str(governance_semantic_version),
        "blocker_classes": blocker_classes,
        "severity_semantics": {"allowed": severity_levels, "non_blocking": non_blocking},
        "reopen_rules": {"allowed_bases": reopen_bases},
        "new_blocker_rules": {"after_initial_round_allowed_bases": new_blocker_bases},
        "acceptance_rule": "validated_open_blockers == 0 => ACCEPT",
        "max_content_revisions": max(0, int(max_content_revisions)),
        "problem_anchor": deepcopy(dict(problem_anchor)),
        "research_constraints_reference": deepcopy(dict(research_constraints_reference)),
        "source_artifact_lineage": deepcopy(dict(source_artifact_lineage or {})),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }
    return {**payload, "policy_payload_sha256": canonical_sha256(payload)}


def validate_frozen_review_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    content = deepcopy(dict(value or {}))
    expected_hash = str(content.pop("policy_payload_sha256", ""))
    if content.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:schema")
    if not expected_hash or canonical_sha256(content) != expected_hash:
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:payload_hash")
    normalized = content.get("normalized_policy_payload")
    if not isinstance(normalized, Mapping):
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:payload")
    if list(content.get("blocker_classes") or []) != list(normalized.get("blocker_classes") or []):
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:blocker_projection")
    if content.get("acceptance_rule") != "validated_open_blockers == 0 => ACCEPT":
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:acceptance")
    snapshots = content.get("skill_snapshots")
    active = list(content.get("active_skill_ids") or [])
    if not isinstance(snapshots, list) or not snapshots:
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:skill_snapshots")
    if [str(item.get("skill_name") or "") for item in snapshots] != active:
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:skill_snapshot_order")
    instruction_hashes = content.get("instruction_hashes")
    if (
        not isinstance(instruction_hashes, Mapping)
        or set(instruction_hashes) != set(active)
        or any(len(str(value)) != 64 for value in instruction_hashes.values())
    ):
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:instruction_hashes")
    for snapshot in snapshots:
        normalized_content = normalize_skill_content(snapshot.get("normalized_content") or "")
        if not normalized_content or snapshot.get("sha256") != canonical_sha256(normalized_content):
            raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:skill_snapshot_hash")
    instructions = normalize_skill_content(content.get("runtime_instructions") or "")
    if not instructions or content.get("runtime_instructions_sha256") != canonical_sha256(instructions):
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:runtime_instructions")
    semantic_hash_fields = (
        ("review_runtime_contract_snapshot", "review_runtime_contract_sha256"),
        ("revision_runtime_contract_snapshot", "revision_runtime_contract_sha256"),
        ("authoritative_plan_contract_snapshot", "authoritative_plan_contract_sha256"),
        ("canonical_contract_field_registry", "canonical_contract_field_registry_sha256"),
        ("contract_field_aliases", "contract_field_aliases_sha256"),
        ("review_prompt_schema_snapshot", "review_prompt_schema_sha256"),
        ("revision_prompt_schema_snapshot", "revision_prompt_schema_sha256"),
    )
    for field, hash_field in semantic_hash_fields:
        snapshot = content.get(field)
        if not snapshot or content.get(hash_field) != canonical_sha256(snapshot):
            raise PlanReviewPolicyIntegrityError(
                f"PLAN_REVIEW_POLICY_INTEGRITY:{field}"
            )
    registry = content.get("canonical_contract_field_registry")
    contract = content.get("authoritative_plan_contract_snapshot")
    aliases = content.get("contract_field_aliases")
    if (
        not isinstance(registry, Mapping)
        or dict(registry) != dict(contract or {})
        or not isinstance(aliases, Mapping)
        or any(value not in registry for value in aliases.values())
        or not normalize_skill_content(content.get("planner_fixed_review_instructions") or "")
        or not normalize_skill_content(content.get("planner_fixed_revision_instructions") or "")
        or not isinstance(content.get("prompt_schema_version"), int)
        or not str(content.get("governance_implementation_semantic_version") or "").strip()
    ):
        raise PlanReviewPolicyIntegrityError(
            "PLAN_REVIEW_POLICY_INTEGRITY:semantic_package"
        )
    if not isinstance(content.get("max_content_revisions"), int):
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:revision_budget")
    if not isinstance(content.get("problem_anchor"), Mapping):
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:anchor")
    if not isinstance(content.get("research_constraints_reference"), Mapping):
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:constraints")
    return {**content, "policy_payload_sha256": expected_hash}


def freeze_plan_governance_migration(
    *,
    legacy_plan_id: str,
    legacy_plan_content: Mapping[str, Any],
    legacy_lineage: Iterable[Mapping[str, Any]],
    frozen_policy_artifact_id: str,
    frozen_policy_payload: Mapping[str, Any],
    migration_source_state: Mapping[str, Any],
    created_at: str | None = None,
) -> dict[str, Any]:
    lineage = [deepcopy(dict(item)) for item in legacy_lineage]
    payload = {
        "schema_version": MIGRATION_SCHEMA_VERSION,
        "migration_version": "plan-governance-migration-v2",
        "legacy": True,
        "legacy_plan_id": str(legacy_plan_id),
        "legacy_plan_hash": canonical_sha256(dict(legacy_plan_content or {})),
        "legacy_lineage": lineage,
        "previous_plan_lineage": [
            str(item.get("artifact_id") or "") for item in lineage
        ],
        "frozen_policy_artifact_id": str(frozen_policy_artifact_id),
        "frozen_policy_payload_sha256": str(
            frozen_policy_payload.get("policy_payload_sha256") or ""
        ),
        "frozen_policy_snapshot": deepcopy(dict(frozen_policy_payload or {})),
        "migration_source_state": deepcopy(dict(migration_source_state or {})),
        "migration_timestamp": created_at or datetime.now(timezone.utc).isoformat(),
    }
    if (
        not payload["legacy_plan_id"]
        or not payload["frozen_policy_artifact_id"]
        or not payload["frozen_policy_payload_sha256"]
        or not lineage
    ):
        raise ValueError("PLAN_REVIEW_MIGRATION_INVALID:required_fields")
    return {**payload, "migration_payload_sha256": canonical_sha256(payload)}


def validate_plan_governance_migration(
    value: Mapping[str, Any],
    *,
    frozen_policy_artifact_id: str,
    frozen_policy_payload: Mapping[str, Any],
) -> dict[str, Any]:
    content = deepcopy(dict(value or {}))
    expected = str(content.pop("migration_payload_sha256", ""))
    if (
        content.get("schema_version") != MIGRATION_SCHEMA_VERSION
        or content.get("migration_version") != "plan-governance-migration-v2"
        or content.get("legacy") is not True
        or not expected
        or canonical_sha256(content) != expected
        or content.get("frozen_policy_artifact_id") != frozen_policy_artifact_id
        or content.get("frozen_policy_payload_sha256")
        != frozen_policy_payload.get("policy_payload_sha256")
        or content.get("frozen_policy_snapshot") != dict(frozen_policy_payload)
        or not isinstance(content.get("legacy_lineage"), list)
        or not content.get("legacy_plan_id")
        or not content.get("legacy_plan_hash")
        or not content.get("migration_timestamp")
    ):
        raise PlanReviewPolicyIntegrityError(
            "PLAN_REVIEW_POLICY_INTEGRITY:migration_payload"
        )
    return {**content, "migration_payload_sha256": expected}


def is_plan_governance_accepted(artifacts: Iterable[Any]) -> bool:
    """Prove that the current final plan is the output of a zero-blocker round."""
    rows = list(artifacts)
    policies = [item for item in rows if getattr(item, "type", "") == "plan_review_policy"]
    plans = [item for item in rows if getattr(item, "type", "") == "plan"]
    if len(policies) != 1 or not plans:
        return False
    policy = policies[0]
    try:
        frozen = validate_frozen_review_policy(getattr(policy, "content", {}) or {})
    except PlanReviewPolicyIntegrityError:
        return False
    plan = plans[-1]
    candidate_id = str(getattr(plan, "parent_artifact_id", "") or "")
    if not candidate_id or (getattr(plan, "content", {}) or {}).get("plan_candidate_id") != candidate_id:
        return False
    candidate = next(
        (
            item
            for item in rows
            if getattr(item, "id", "") == candidate_id
            and getattr(item, "type", "") == "research_plan_candidate"
        ),
        None,
    )
    if candidate is None:
        return False
    plan_position = rows.index(plan)
    governed_candidates = [
        item
        for item in rows
        if getattr(item, "type", "") == "research_plan_candidate"
        and (getattr(item, "content", {}) or {}).get("policy_artifact_id")
        == getattr(policy, "id", "")
    ]
    if (
        not governed_candidates
        or governed_candidates[-1].id != candidate_id
        or any(
            getattr(item, "type", "") == "plan_refinement_proposal"
            for item in rows[plan_position + 1 :]
        )
    ):
        return False
    candidate_content = getattr(candidate, "content", {}) or {}
    if (
        candidate_content.get("policy_artifact_id") != getattr(policy, "id", "")
        or candidate_content.get("policy_payload_sha256")
        != frozen.get("policy_payload_sha256")
        or candidate_content.get("plan_id") != candidate_id
    ):
        return False
    plan_content = getattr(plan, "content", {}) or {}
    if (
        plan_content.get("policy_artifact_id") != getattr(policy, "id", "")
        or plan_content.get("policy_payload_sha256")
        != frozen.get("policy_payload_sha256")
        or plan_content.get("accepted_candidate_payload_sha256")
        != canonical_sha256(candidate_content.get("normalized_plan") or {})
        or changed_contract_fields(
            candidate_content.get("normalized_plan") or {},
            plan_content,
            field_registry=frozen["canonical_contract_field_registry"],
            field_aliases=frozen["contract_field_aliases"],
        )
    ):
        return False
    ledgers = [
        item
        for item in rows
        if getattr(item, "type", "") == "plan_review_issue_ledger"
        and (getattr(item, "content", {}) or {}).get("policy_artifact_id")
        == getattr(policy, "id", "")
        and (getattr(item, "content", {}) or {}).get("plan_id") == candidate_id
    ]
    if len(ledgers) != 1:
        return False
    ledger = ledgers[0]
    ledger_content = deepcopy(getattr(ledger, "content", {}) or {})
    ledger_hash = str(ledger_content.pop("ledger_payload_sha256", ""))
    accepted_proof = ledger
    if (
        not ledger_hash
        or canonical_sha256(ledger_content) != ledger_hash
        or ledger_content.get("policy_payload_sha256")
        != frozen.get("policy_payload_sha256")
    ):
        return False
    if (
        ledger_content.get("validated_open_blocker_ids") != []
        or ledger_content.get("verdict") != "ACCEPT"
    ):
        recoveries = [
            item
            for item in rows
            if getattr(item, "type", "") == "plan_review_recovery_adjudication"
            and getattr(item, "parent_artifact_id", "") == getattr(ledger, "id", "")
        ]
        if len(recoveries) != 1:
            return False
        try:
            recovery = validate_plan_review_recovery(
                getattr(recoveries[0], "content", {}) or {},
                policy_artifact_id=str(getattr(policy, "id", "")),
                policy_payload_sha256=str(frozen.get("policy_payload_sha256") or ""),
                candidate_plan_id=candidate_id,
                review_id=str(ledger_content.get("review_id") or ""),
                ledger_id=str(getattr(ledger, "id", "")),
            )
        except PlanReviewPolicyIntegrityError:
            return False
        if recovery.get("validated_open_blocker_ids") or recovery.get("verdict") != "ACCEPT":
            return False
        accepted_proof = recoveries[0]
    if getattr(accepted_proof, "type", "") == "plan_review_recovery_adjudication":
        return True
    round_index = int(candidate_content.get("round_index") or 0)
    identity = canonical_sha256(
        {
            "policy_artifact_id": getattr(policy, "id", ""),
            "round_index": round_index,
            "plan_id": candidate_id,
        }
    )
    acceptance = [
        item
        for item in rows
        if getattr(item, "type", "") == "plan_review_round_state"
        and (getattr(item, "content", {}) or {}).get("round_identity") == identity
        and (getattr(item, "content", {}) or {}).get("phase") == "ROUND_COMPLETE"
        and (getattr(item, "content", {}) or {}).get("outcome") == "ACCEPT"
    ]
    return bool(
        len(acceptance) == 1
        and getattr(acceptance[0], "parent_artifact_id", None) == getattr(accepted_proof, "id", "")
        and (getattr(acceptance[0], "content", {}) or {}).get("phase_parent_id")
        == getattr(accepted_proof, "id", "")
    )


def freeze_plan_review_recovery(
    *,
    policy_artifact_id: str,
    policy_payload_sha256: str,
    candidate_plan_id: str,
    review_id: str,
    ledger_id: str,
    adjudication: ReviewAdjudication,
) -> dict[str, Any]:
    """Record a new-rule adjudication without altering any historical artifact."""
    payload = {
        "schema_version": RECOVERY_SCHEMA_VERSION,
        "policy_artifact_id": policy_artifact_id,
        "policy_payload_sha256": policy_payload_sha256,
        "plan_id": candidate_plan_id,
        "review_id": review_id,
        "ledger_id": ledger_id,
        "issues": deepcopy(adjudication.issues),
        "validated_open_blocker_ids": list(adjudication.validated_open_blocker_ids),
        "warning_ids": list(adjudication.warning_ids),
        "suggestion_ids": list(adjudication.suggestion_ids),
        "closed_issue_ids": list(adjudication.closed_issue_ids),
        "verdict": adjudication.verdict,
        "reason": "re-adjudicated under reduced plan-blocker scope",
    }
    return {**payload, "recovery_payload_sha256": canonical_sha256(payload)}


def validate_plan_review_recovery(
    value: Mapping[str, Any],
    *,
    policy_artifact_id: str,
    policy_payload_sha256: str,
    candidate_plan_id: str,
    review_id: str,
    ledger_id: str,
) -> dict[str, Any]:
    content = deepcopy(dict(value or {}))
    expected = str(content.pop("recovery_payload_sha256", ""))
    if (
        content.get("schema_version") != RECOVERY_SCHEMA_VERSION
        or not expected
        or canonical_sha256(content) != expected
        or content.get("policy_artifact_id") != policy_artifact_id
        or content.get("policy_payload_sha256") != policy_payload_sha256
        or content.get("plan_id") != candidate_plan_id
        or content.get("review_id") != review_id
        or content.get("ledger_id") != ledger_id
        or not isinstance(content.get("issues"), list)
        or not isinstance(content.get("validated_open_blocker_ids"), list)
    ):
        raise PlanReviewPolicyIntegrityError("PLAN_REVIEW_POLICY_INTEGRITY:recovery")
    return {**content, "recovery_payload_sha256": expected}


def changed_contract_fields(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
    *,
    field_registry: Mapping[str, Any] | None = None,
    field_aliases: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    before = dict(previous or {})
    after = dict(current or {})
    registry = dict(field_registry or CANONICAL_PLAN_CONTRACT_FIELDS)
    aliases = dict(field_aliases or FIELD_ALIAS_TO_CANONICAL)
    return tuple(
        sorted(
            field
            for field in registry
            if _canonical(_contract_value(before, field, aliases))
            != _canonical(_contract_value(after, field, aliases))
        )
    )


def fix_map_issues(
    fix_map: Any,
    *,
    open_blocker_ids: Iterable[str] = (),
    open_blockers: Iterable[Mapping[str, Any]] = (),
    changed_fields: Iterable[str],
    field_registry: Mapping[str, Any] | None = None,
    field_aliases: Mapping[str, str] | None = None,
) -> list[str]:
    """Validate exact per-blocker attribution for a bounded revision."""
    if not isinstance(fix_map, Mapping):
        return ["PLAN_REVIEW_FIX_MAP_REQUIRED"]
    blocker_rows = [dict(item) for item in open_blockers if isinstance(item, Mapping)]
    required_ids = tuple(str(item.get("issue_id")) for item in blocker_rows if str(item.get("issue_id") or "")) or tuple(str(item) for item in open_blocker_ids)
    actual_keys = {str(item) for item in fix_map}
    required_keys = set(required_ids)
    issues: list[str] = []
    registry = dict(field_registry or CANONICAL_PLAN_CONTRACT_FIELDS)
    aliases = dict(field_aliases or FIELD_ALIAS_TO_CANONICAL)
    for issue_id in sorted(required_keys - actual_keys):
        issues.append(f"PLAN_REVIEW_FIX_MAP_MISSING:{issue_id}")
    for issue_id in sorted(actual_keys - required_keys):
        issues.append(f"PLAN_REVIEW_FIX_MAP_EXTRA:{issue_id}")
    material_changes = {str(item) for item in changed_fields if str(item) not in {"fix_map", "provider_mode", "fallback_used", "normalization"}}
    blocker_by_id = {str(item.get("issue_id")): item for item in blocker_rows}
    for issue_id in required_ids:
        raw_fields = _unique_strings(fix_map.get(issue_id))
        fields = set(_canonical_fields(raw_fields, registry, aliases))
        invalid_fields = sorted(
            field
            for field in raw_fields
            if _canonical_field(field, registry, aliases) not in registry
        )
        if invalid_fields:
            issues.append(
                f"PLAN_REVIEW_FIX_MAP_UNKNOWN_FIELD:{issue_id}:"
                + ",".join(invalid_fields)
            )
        if not fields:
            issues.append(f"PLAN_REVIEW_FIX_MAP_EMPTY:{issue_id}")
            continue
        allowed = set(
            _canonical_fields(
                (blocker_by_id.get(issue_id) or {}).get("contract_fields")
                , registry, aliases
            )
        )
        if allowed and not fields.issubset(allowed):
            issues.append(f"PLAN_REVIEW_FIX_MAP_UNRELATED:{issue_id}:" + ",".join(sorted(fields - allowed)))
        unchanged = fields - material_changes
        if unchanged:
            issues.append(f"PLAN_REVIEW_FIX_MAP_UNCHANGED:{issue_id}:" + ",".join(sorted(unchanged)))
    if required_ids and not material_changes:
        issues.append("PLAN_REVIEW_REVISION_NO_CONTRACT_CHANGE")
    return list(dict.fromkeys(issues))


def deterministic_fix_map(
    open_blockers: Iterable[Mapping[str, Any]],
    *,
    changed_fields: Iterable[str],
    field_registry: Mapping[str, Any] | None = None,
    field_aliases: Mapping[str, str] | None = None,
) -> dict[str, list[str]]:
    """Attribute a revision from the verified contract diff, not model prose.

    ``fix_map`` is audit metadata. It should never require a second model call
    merely to restate which top-level fields changed. For each still-open
    blocker, retain only its authorized canonical fields that the engine
    observed to change. A blocker with no such field is deliberately emitted
    as an empty list so :func:`fix_map_issues` can reject the incomplete repair.
    """
    registry = dict(field_registry or CANONICAL_PLAN_CONTRACT_FIELDS)
    aliases = dict(field_aliases or FIELD_ALIAS_TO_CANONICAL)
    changed = set(_canonical_fields(changed_fields, registry, aliases))
    result: dict[str, list[str]] = {}
    for blocker in open_blockers:
        if not isinstance(blocker, Mapping):
            continue
        issue_id = str(blocker.get("issue_id") or "").strip()
        if not issue_id:
            continue
        allowed = set(
            _canonical_fields(
                blocker.get("contract_fields") or (), registry, aliases
            )
        )
        result[issue_id] = sorted(allowed & changed)
    return result


def canonicalize_fix_map(
    value: Any,
    *,
    field_registry: Mapping[str, Any],
    field_aliases: Mapping[str, str],
) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    registry = dict(field_registry)
    aliases = dict(field_aliases)
    return {
        str(issue_id): list(
            dict.fromkeys(
                _canonical_field(field, registry, aliases)
                for field in fields
                if str(field).strip()
            )
        )
        if isinstance(fields, list)
        else []
        for issue_id, fields in value.items()
    }


def adjudicate_review(
    previous: Iterable[Mapping[str, Any]] | None,
    review: Mapping[str, Any],
    *,
    frozen_policy: Mapping[str, Any],
    round_index: int,
    changed_fields: Iterable[str] = (),
    new_evidence_artifact_ids: Iterable[str] = (),
    candidate_plan_id: str = "",
    review_id: str = "",
) -> ReviewAdjudication:
    """Apply generic schema, chronology, transition, and acceptance invariants."""
    if round_index < 1:
        raise ValueError("PLAN_REVIEW_ROUND_INVALID")
    authorized_classes = set(_unique_strings(frozen_policy.get("blocker_classes")))
    reopen_bases = set(_unique_strings((frozen_policy.get("reopen_rules") or {}).get("allowed_bases")))
    new_blocker_bases = set(_unique_strings((frozen_policy.get("new_blocker_rules") or {}).get("after_initial_round_allowed_bases")))
    changed = set(str(item) for item in changed_fields)
    chronological_evidence = set(str(item) for item in new_evidence_artifact_ids)
    field_registry = dict(
        frozen_policy.get("canonical_contract_field_registry")
        or CANONICAL_PLAN_CONTRACT_FIELDS
    )
    field_aliases = dict(
        frozen_policy.get("contract_field_aliases") or FIELD_ALIAS_TO_CANONICAL
    )
    prior_order: list[str] = []
    prior: dict[str, dict[str, Any]] = {}
    for item in previous or ():
        if not isinstance(item, Mapping):
            continue
        normalized = deepcopy(dict(item))
        issue_id = str(normalized.get("issue_id") or "").strip()
        if not issue_id or issue_id in prior:
            continue
        prior_order.append(issue_id)
        prior[issue_id] = normalized
    incoming = review.get("issues")
    incoming = incoming if isinstance(incoming, list) else []
    seen: set[str] = set()
    ledger = deepcopy(prior)
    for position, raw_issue in enumerate(incoming):
        proposal = _normalize_issue(
            raw_issue,
            round_index,
            position,
            field_registry=field_registry,
            field_aliases=field_aliases,
        )
        proposal.update(candidate_plan_id=candidate_plan_id, review_id=review_id, round_index=round_index)
        issue_id = proposal["issue_id"]
        if issue_id in seen:
            continue
        seen.add(issue_id)
        old = prior.get(issue_id)
        if old is not None:
            ledger[issue_id] = _transition_existing(
                old, proposal, round_index=round_index, authorized_classes=authorized_classes,
                reopen_bases=reopen_bases, changed_fields=changed,
                chronological_evidence=chronological_evidence, candidate_plan_id=candidate_plan_id,
            )
            continue
        if proposal["severity"] == "BLOCKER" and proposal["status"] == "CLOSED":
            valid, reason = _valid_closed_issue_schema(proposal)
            proposal.update(
                status="CLOSED" if valid else "REJECTED",
                adjudication_reason=reason,
                validated_blocker=False,
            )
        elif proposal["severity"] == "BLOCKER":
            valid, reason = _valid_blocker_schema(proposal, authorized_classes)
            if valid and round_index > 1:
                valid, reason = _valid_late_blocker_basis(
                    proposal, allowed_bases=new_blocker_bases, changed_fields=changed,
                    chronological_evidence=chronological_evidence, candidate_plan_id=candidate_plan_id,
                )
            if not valid:
                proposal.update(severity="WARNING", status="REJECTED", adjudication_reason=reason, validated_blocker=False)
            else:
                proposal.update(status="OPEN", validated_blocker=True)
        else:
            proposal["status"] = proposal["status"] if proposal["status"] in {"CLOSED", "DEFERRED", "REJECTED"} else "DEFERRED"
            proposal["validated_blocker"] = False
        ledger[issue_id] = proposal
        prior_order.append(issue_id)
    # `closed_issue_ids` is informational only and deliberately has no transition authority.
    issues = [ledger[issue_id] for issue_id in prior_order if issue_id in ledger]
    open_ids = tuple(item["issue_id"] for item in issues if item.get("severity") == "BLOCKER" and item.get("validated_blocker") is True and item.get("status") in {"OPEN", "REOPENED"})
    return ReviewAdjudication(
        issues=issues,
        validated_open_blocker_ids=open_ids,
        warning_ids=tuple(item["issue_id"] for item in issues if item.get("severity") == "WARNING"),
        suggestion_ids=tuple(item["issue_id"] for item in issues if item.get("severity") == "SUGGESTION"),
        closed_issue_ids=tuple(item["issue_id"] for item in issues if item.get("status") == "CLOSED"),
    )


def _transition_existing(
    old: Mapping[str, Any], proposal: dict[str, Any], *, round_index: int,
    authorized_classes: set[str], reopen_bases: set[str], changed_fields: set[str],
    chronological_evidence: set[str], candidate_plan_id: str,
) -> dict[str, Any]:
    current = deepcopy(dict(old))
    current["last_checked_round"] = round_index
    old_status = str(old.get("status") or "").upper()
    requested = proposal["status"]
    if old_status == "CLOSED":
        basis = str(proposal.get("reopen_basis") or "").strip()
        affected = set(proposal.get("contract_fields") or old.get("contract_fields") or [])
        valid_basis = False
        if requested in {"OPEN", "REOPENED"} and basis in reopen_bases:
            if basis == "regression":
                valid_basis = bool(affected & changed_fields) and _evidence_points_to(proposal, candidate_plan_id)
            elif basis == "new_evidence":
                declared = set(proposal.get("evidence_artifact_ids") or [])
                valid_basis = bool(declared) and declared.issubset(chronological_evidence)
        if valid_basis and proposal.get("evidence") and proposal.get("reason"):
            valid, reason = _valid_blocker_schema(proposal, authorized_classes)
            if valid:
                proposal.update(status="REOPENED", validated_blocker=True, introduced_round=old.get("introduced_round", round_index))
                return proposal
            current["adjudication_reason"] = reason
        current.update(status="CLOSED", validated_blocker=False)
        return current
    if requested == "CLOSED" and old_status in {"OPEN", "REOPENED"}:
        valid, reason = _valid_closure(old, proposal, round_index=round_index, changed_fields=changed_fields, candidate_plan_id=candidate_plan_id)
        if valid:
            # The fields named when a blocker was opened define its immutable
            # scope.  A reviewer may cite different, genuinely changed fields
            # as the evidence of a repair; do not turn that evidence into a new
            # blocker scope.
            proposal["contract_fields"] = list(old.get("contract_fields") or [])
            proposal.update(status="CLOSED", validated_blocker=False, introduced_round=old.get("introduced_round", round_index))
            return proposal
        current.update(adjudication_reason=reason, status=old_status, validated_blocker=True)
        return current
    if old_status in {"OPEN", "REOPENED"}:
        valid, reason = _valid_blocker_schema(proposal, authorized_classes)
        if valid:
            proposal.update(status=old_status, validated_blocker=True, introduced_round=old.get("introduced_round", round_index))
            return proposal
        current.update(
            severity="WARNING",
            status="DEFERRED",
            adjudication_reason=reason,
            validated_blocker=False,
        )
        return current
    current.update(proposal)
    current.update(introduced_round=old.get("introduced_round", round_index), validated_blocker=False)
    return current


def _valid_closure(old: Mapping[str, Any], proposal: Mapping[str, Any], *, round_index: int, changed_fields: set[str], candidate_plan_id: str) -> tuple[bool, str]:
    if round_index <= int(old.get("introduced_round") or 0):
        return False, "closure_candidate_not_newer_than_issue"
    if not candidate_plan_id or proposal.get("candidate_plan_id") != candidate_plan_id:
        return False, "closure_candidate_identity_invalid"
    if not proposal.get("review_id") or int(proposal.get("round_index") or 0) != round_index:
        return False, "closure_review_identity_invalid"
    if not proposal.get("evidence") or not _has_content(proposal.get("resolution")):
        return False, "closure_evidence_and_resolution_required"
    proposed_fields = set(_unique_strings(proposal.get("contract_fields")))
    if not proposed_fields:
        return False, "closure_contract_fields_invalid"
    if not (proposed_fields & changed_fields) and not _evidence_points_to(proposal, candidate_plan_id):
        return False, "closure_not_supported_by_diff_or_candidate_evidence"
    return True, "validated_closure"


def _valid_closed_issue_schema(issue: Mapping[str, Any]) -> tuple[bool, str]:
    if not issue.get("evidence") or not _has_content(issue.get("resolution")):
        return False, "closure_evidence_and_resolution_required"
    return True, "validated_closure"


def _normalize_issue(
    raw_issue: Any,
    round_index: int,
    position: int,
    *,
    field_registry: Mapping[str, Any],
    field_aliases: Mapping[str, str],
) -> dict[str, Any]:
    raw = dict(raw_issue) if isinstance(raw_issue, Mapping) else {"reason": str(raw_issue)}
    severity = str(raw.get("severity") or "").upper()
    invalid_severity = severity not in REVIEW_SEVERITIES
    if invalid_severity:
        severity = "WARNING"
    status_value = str(raw.get("status") or "").upper()
    status_value = {"FIXED": "CLOSED", "NOT_FIXED": "OPEN"}.get(status_value, status_value)
    if status_value not in ISSUE_STATUSES:
        status_value = "OPEN" if severity == "BLOCKER" else "DEFERRED"
    raw_contract_fields = _unique_strings(raw.get("contract_fields") or ([raw.get("affected_plan_section")] if raw.get("affected_plan_section") else []))
    contract_fields = _canonical_fields(
        raw_contract_fields, field_registry, field_aliases
    )
    invalid_contract_fields = [
        field
        for field in raw_contract_fields
        if _canonical_field(field, field_registry, field_aliases)
        not in field_registry
    ]
    evidence = _nonempty_values(raw.get("evidence"))
    title = str(raw.get("title") or raw.get("description") or "Review issue").strip()
    blocker_class = str(raw.get("blocker_class") or "").strip() or None
    reason = str(raw.get("reason") or raw.get("description") or "").strip()
    issue_id = str(raw.get("issue_id") or "").strip()
    if not issue_id:
        identity = {"blocker_class": blocker_class, "title": title.casefold(), "contract_fields": contract_fields, "reason": reason.casefold()}
        issue_id = "PRI-" + sha256(_canonical(identity).encode("utf-8")).hexdigest()[:12]
    issue = {
        "issue_id": issue_id, "blocker_class": blocker_class, "severity": severity,
        "title": title, "contract_fields": contract_fields, "evidence": evidence,
        "reason": reason, "required_fix": deepcopy(raw.get("required_fix")),
        "resolution": deepcopy(raw.get("resolution")), "status": status_value,
        "introduced_round": int(raw.get("introduced_round") or round_index),
        "last_checked_round": round_index,
        "reopen_basis": str(raw.get("reopen_basis") or "").strip() or None,
        "new_blocker_basis": str(raw.get("new_blocker_basis") or "").strip() or None,
        "evidence_artifact_ids": _unique_strings(raw.get("evidence_artifact_ids")),
        "validated_blocker": False,
    }
    if invalid_contract_fields:
        issue["contract_field_validation_error"] = sorted(invalid_contract_fields)
    if invalid_severity:
        issue.update(status="REJECTED", adjudication_reason="invalid_severity")
    return issue


def _valid_blocker_schema(issue: Mapping[str, Any], authorized_classes: set[str]) -> tuple[bool, str]:
    if issue.get("severity") != "BLOCKER":
        return False, "severity_is_not_blocker"
    if issue.get("blocker_class") not in authorized_classes:
        return False, "blocker_class_not_authorized_by_frozen_policy"
    if _is_execution_resolvable(issue):
        return False, "execution_resolvable_issue_delegated_to_experiment"
    if not issue.get("contract_fields"):
        return False, "contract_fields_required"
    if issue.get("contract_field_validation_error"):
        return False, "contract_fields_not_canonical"
    if not issue.get("evidence"):
        return False, "evidence_required"
    if not _has_content(issue.get("required_fix")):
        return False, "required_fix_required"
    return True, "validated"


def _is_execution_resolvable(issue: Mapping[str, Any]) -> bool:
    text = " ".join(
        str(issue.get(field) or "")
        for field in ("title", "reason", "required_fix", "resolution")
    ).casefold()
    return any(term in text for term in _EXECUTION_RESOLVABLE_TERMS)


def _valid_late_blocker_basis(issue: Mapping[str, Any], *, allowed_bases: set[str], changed_fields: set[str], chronological_evidence: set[str], candidate_plan_id: str) -> tuple[bool, str]:
    basis = str(issue.get("new_blocker_basis") or "")
    if basis not in allowed_bases:
        return False, "late_blocker_basis_not_authorized"
    if basis == "regression":
        if not (set(issue.get("contract_fields") or []) & changed_fields):
            return False, "regression_not_supported_by_contract_diff"
        if not _evidence_points_to(issue, candidate_plan_id):
            return False, "regression_not_supported_by_candidate_chronology"
        return True, "validated_regression"
    if basis == "new_evidence":
        declared = set(issue.get("evidence_artifact_ids") or [])
        if not declared or not declared.issubset(chronological_evidence):
            return False, "new_evidence_not_supported_by_artifact_chronology"
        return True, "validated_new_evidence"
    return False, "late_blocker_basis_has_no_generic_validator"


def _evidence_points_to(issue: Mapping[str, Any], candidate_plan_id: str) -> bool:
    return bool(candidate_plan_id) and candidate_plan_id in set(_unique_strings(issue.get("evidence_artifact_ids")))


def _unique_strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _nonempty_values(value: Any) -> list[Any]:
    values = value if isinstance(value, list) else [value]
    return [deepcopy(item) for item in values if _has_content(item)]


def _has_content(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set, frozenset)):
        return bool(value)
    return value is not None


def _contract_value(
    value: Mapping[str, Any], canonical_field: str, aliases: Mapping[str, str]
) -> Any:
    if canonical_field in value:
        return value.get(canonical_field)
    for alias, target in aliases.items():
        if target == canonical_field and alias in value:
            return value.get(alias)
    return None


def _canonical_field(
    value: object, registry: Mapping[str, Any], aliases: Mapping[str, str]
) -> str:
    field = str(value or "").strip()
    canonical = aliases.get(field, field)
    return canonical if canonical in registry else field


def _canonical_fields(
    values: Any, registry: Mapping[str, Any], aliases: Mapping[str, str]
) -> list[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        return []
    return list(
        dict.fromkeys(
            canonical
            for item in values
            if (canonical := _canonical_field(item, registry, aliases)) in registry
        )
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
