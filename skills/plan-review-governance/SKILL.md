---
name: plan-review-governance
description: Bound adversarial research-plan review so only validated scientific blockers can prevent experiment execution. Use during research_plan generation, review, revision, and recovery.
allowed-tools: read_run, read_artifact
---

# Plan Review Governance

Apply the bounded scientific review protocol in `../shared-references/bounded-scientific-review.md`.

## Review authority

Be adversarial in finding problems, but do not equate every concern with a veto. Return structured issues with stable IDs and classify each as `BLOCKER`, `WARNING`, or `SUGGESTION`.

A `BLOCKER` is permitted only when it maps to the current frozen `plan_review_policy.blocker_classes`, cites concrete evidence from the current Plan/Run, and proves one of three things: the data/task cannot answer the frozen research question; the experimental design has a deterministic scientific or mathematical error that invalidates its result; or the Plan violates a frozen user constraint/scientific contract. Otherwise downgrade it.

For `CLAIM_PLAN_MISMATCH`, audit the whole Plan in the initial finding. Put every field containing the same stale intervention, comparator, mechanism, endpoint, experiment framing, decision rule, risk, or claim boundary into that one stable blocker. Do not split residual manifestations of the same scientific contradiction into later warnings. Confirm that multi-endpoint claims have an exhaustive positive/negative/inconclusive matrix and that iterative optimization specifies validation-only acceptance, rollback, stopping, and final test isolation.

Implementation and runtime concerns are never Plan blockers: tensor axes/dtypes/shapes, loader semantics and MAT/HDF5/CSV mappings, feature or FFT/window implementation, training code, APIs/interfaces, paths, dependencies, output formats, runtime behavior, and Experiment Bundle issues belong to Loader/Experiment validation, Harness, or bounded repair. Keep them as warnings or audit findings as useful; do not require the Plan to pre-write training-code details.

## Convergence rules

- Round 1 may establish the initial blocker ledger.
- Later rounds primarily verify whether existing blockers are fixed.
- A closed blocker cannot reopen unless the revised plan regresses the same condition with concrete evidence.
- A new blocker after Round 1 requires `new_blocker_basis`: `regression` or `new_evidence`.
- New warnings/suggestions are always allowed but cannot prevent execution.
- If validated open blockers are zero, verdict must be `ACCEPT` even if warnings or suggestions remain.
- The Skill proposes findings only. It never directly accepts, rejects, closes, or reopens a plan issue; the governance ledger validates every transition.
- `closed_issue_ids` is informational and has no transition authority. Closure requires a complete finding tied to the current candidate, review, round, changed contract fields or candidate evidence, and a concrete resolution.

## Revision contract

For `REVISE`, return only the minimum required fixes for validated open blockers. Preserve resolved Plan fields and the frozen problem anchor. Do not widen the contribution, add optional complexity, or demand a stronger method merely because it is preferable.

Use only canonical Plan Contract field IDs from the registry frozen in `plan_review_policy`. Input aliases may be accepted and immediately canonicalized, but ledger issues and `fix_map` must persist canonical IDs. Reviewer and reviser prompts must use the frozen runtime contracts, schemas, fixed instructions, field registry, and semantic versions; live disk components cannot alter a resumed Run.

Return:

- `verdict`: `ACCEPT` or `REVISE`;
- `issues` using the bounded issue schema;
- `validated_open_blocker_ids`;
- `warning_ids`;
- `suggestion_ids`;
- `closed_issue_ids`;
- `reopened_issue_ids` with regression evidence;
- `fix_requirements` keyed by blocker ID;
- `scope_check`: primary claim, original-question link, and drift status.
