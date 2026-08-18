# Bounded Scientific Review Governance

## Purpose

Keep scientific review adversarial but bounded. The reviewer may discover unlimited concerns, but only validated blockers may prevent execution. Do not optimize for positive results; optimize for a valid, informative experiment.

## Authority split

- **Skill policy owns scientific meaning.** The active Skill package defines the problem anchor, blocker classes, severity rules, and review semantics.
- **Engine owns protocol invariants only.** It enforces schema, state transitions, revision bounds, ledger consistency, and the rule that only validated blockers can block. It must not hard-code task-, dataset-, model-, metric-, or domain-specific scientific judgments.
- **Deterministic validators own machine-checkable facts.** Dataset identity, split overlap, missing required fields, parameter budgets when computable, artifact provenance, and similar facts should be verified by code when possible.
- **Reviewer proposes; adjudication decides authority.** A model finding is not automatically a blocker.
- **No parallel plan gate.** Supervisor and Skills perform routing, structure checks, or emit findings; they never independently accept or reject `research_plan`.

## Frozen review policy

At the first plan review, freeze a `plan_review_policy` for the current Run from the active Skills and Plan Contract. Persist its version/hash. It contains:

- `problem_anchor`: original question, selected hypothesis, non-goals, frozen constraints, primary claim scope;
- `blocker_classes`: scientific-invalidity classes authorized by the active Skill policy;
- `severity_semantics`;
- `max_content_revisions`;
- `reopen_rules`;
- `acceptance_rule`.
- the normalized authoritative Plan Contract and canonical field registry, including aliases accepted only at input boundaries;
- the complete reviewer and reviser runtime instructions, fixed planner instructions, output schemas, and prompt schema version;
- the governance implementation semantic version and hashes for every frozen component.

Resume and rerun must build reviewer and reviser requests exclusively from this frozen semantic package. Current disk Skills, live Plan Contract helpers, current fixed instructions, or a newer field registry may define a new Run, but must not change an existing Run's adjudication.

Do not freeze a domain answer. Freeze only the review policy and current research anchor.

## Severity

Every review issue must be exactly one of:

- `BLOCKER`: if unresolved, the planned experiment cannot support a valid inference for the frozen claim.
- `WARNING`: important weakness, uncertainty, or reproducibility risk that should be recorded but does not invalidate the experiment.
- `SUGGESTION`: optional improvement; never blocks execution.

A reviewer may not use rhetorical severity. A `BLOCKER` must map to an authorized blocker class, identify the affected Plan Contract field(s), and provide concrete evidence from the current plan/run.

## Default blocker classes

These are policy categories, not domain-specific answers:

- `CLAIM_PLAN_MISMATCH`: the plan cannot answer the frozen selected claim or silently widens/narrows it without explicit secondary scope.
- `MISSING_EXECUTABLE_COMPARATOR`: no executable baseline/control needed to identify the claimed effect.
- `MULTIPLE_CAUSAL_CHANGES`: the primary comparison changes multiple causal factors without a design that identifies the target intervention.
- `DATASET_TASK_DRIFT`: dataset, task, target, preprocessing, or split semantics conflict with the frozen contract.
- `DATA_LEAKAGE_OR_TEST_CONTAMINATION`: train/validation/test or pilot/formal usage invalidates confirmatory inference.
- `PRIMARY_ENDPOINT_UNDEFINED`: primary metric, direction, aggregation, or interpretation is too ambiguous to decide the hypothesis.
- `INTERVENTION_UNDEFINED`: the intervention is not implementable or distinguishable from the comparator.
- `FROZEN_CONSTRAINT_VIOLATION`: the plan violates a user/frozen compute, parameter, latency, data, or other binding constraint.
- `FORMAL_INFERENCE_INVALID`: the preregistered formal analysis is structurally incapable of supporting the intended inference, including dependent-data misuse or confirmatory seed contamination.
- `FORMAL_EXPERIMENT_NOT_EXECUTABLE`: required data, implementation, runtime, or outputs are unspecified to the point that ExperimentAgent must guess a scientifically material choice.

Skills may add, remove, or narrow classes for a workflow. Engine must read them from the frozen policy; do not duplicate this list in engine code.

## Issue schema

Each issue must contain:

- `issue_id`: stable across rounds;
- `blocker_class` or `null`;
- `severity`;
- `title`;
- `contract_fields`;
- `evidence`;
- `reason`;
- `required_fix` for blockers;
- `status`: `OPEN`, `CLOSED`, `REOPENED`, `DEFERRED`, or `REJECTED`;
- `introduced_round`;
- `last_checked_round`;
- `reopen_basis` when applicable.

`contract_fields` uses the one canonical Plan Contract field registry persisted in the frozen policy. Compatibility aliases are canonicalized at the input boundary and never persisted as a second internal vocabulary. The same registry drives findings, ledger fields, `fix_map`, revision diff, closure, and reopen validation.

## Adjudication

A proposed `BLOCKER` is valid only when all are true:

1. It maps to an authorized blocker class from `plan_review_policy`.
2. It points to concrete current-plan/run evidence.
3. It affects valid inference, executability, or a frozen constraint rather than stylistic completeness.
4. It is not merely a stronger-preference request when a valid method already exists.

If any condition fails, downgrade to `WARNING` or `SUGGESTION`, or mark `REJECTED`.

Prefer deterministic adjudication where facts are machine-checkable. Use a separate semantic adjudication step only where judgment is unavoidable; the original critic does not unilaterally grant itself blocking authority.

## Ledger and convergence

Maintain an append-only `plan_review_issue_ledger`.

- Revision N receives the previous candidate plus all `OPEN` validated blockers and the ledger of `CLOSED` items.
- The reviser must patch the smallest necessary fields and return a `fix_map` from each open blocker to changed Plan Contract fields.
- A `CLOSED` issue stays closed unless policy-authorized regression or new evidence proves that the condition is active again.
- Regression reopening requires changed affected fields, current-candidate evidence, and `reopen_basis=regression`; reviewer preference changes are not a valid basis.
- Policy-authorized `new_evidence` reopening requires a source artifact newer than the prior review and recorded in chronology. A bare `closed_issue_ids` or repeated finding never changes ledger state.
- After round 1, a newly discovered blocker requires `new_blocker_basis` equal to `regression` or `new_evidence`. Otherwise it cannot block the current plan.
- New warnings and suggestions remain allowed in every round.

## Acceptance and stopping

- `validated_open_blockers == 0` => `ACCEPT`, regardless of remaining warnings/suggestions.
- `validated_open_blockers > 0` and revision budget remains => `REVISE`.
- Revision budget exhausted with blockers remaining => `NEEDS_PLAN_REVISION`, preserving the ledger and candidate artifacts.
- Never raise the revision limit merely because the reviewer can continue suggesting improvements.
- A warning may be carried into experiment/report limitations; it cannot secretly function as a blocker.

## Scope discipline

The selected hypothesis may refine the original question, but the plan must explicitly distinguish:

- `primary_claim`: what the selected hypothesis directly tests;
- `original_question_link`: how the primary claim answers, narrows, or only partially addresses the original question;
- `secondary_endpoints`: any comparisons needed to preserve the original-question interpretation.

If the selected hypothesis cannot answer the original question by itself, do not force the plan to pretend it can. Either add a minimal control/secondary endpoint, or narrow the reported claim explicitly.
