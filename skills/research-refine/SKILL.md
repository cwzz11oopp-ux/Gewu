---
name: research-refine
description: Refine a selected hypothesis or a feedback-rejected plan into the smallest precise, executable research method. Use during initial research planning and when experiment feedback requires a scientific plan revision.
allowed-tools: read_run, read_artifact
---

# Research Refine

Freeze the problem anchor and supported parts of the selected hypothesis. For feedback revision, begin from the previous accepted plan and change only fields required by a named unsupported claim, audit issue, or decision failure.

State the proposed method, mechanism, why it should address the observed weakness, and the evidence or measured observation motivating it. Distinguish verified evidence, measured result, and assumption.

Return the complete shared Plan Contract: `objective`, `hypotheses`, `method`, `dataset`, `comparisons`, `evaluations`, `procedure`, `parameters`, `statistical_summary`, `success_criteria`, `failure_criteria`, `expected_artifacts`, `stop_conditions`, `traceability`, `diagnosis`, `revised_hypothesis`, `mechanism_and_evidence`, `boundary_conditions`, `alignment_contract`, `baseline_and_controls`, `feasibility_risks`, `staged_gates`, `formal_experiment_entry_conditions`, `positive_negative_inconclusive_rules`, `remaining_unknowns`, `capacity_confounder`, and `local_dataset_loader_verification`. The backend owns concrete seeds: give only `procedure.repetitions`. Map every method component to a mechanism, every change to feedback, and every decision criterion to a measurable metric. Do not widen the contribution or add optional complexity unless it resolves a concrete failure.

For a `PIVOT`, write one unitary new claim identically in `objective`,
`hypotheses`, `primary_claim`, and `revised_hypothesis.claim`; do not retain the
contradicted parent wording in any of those fields. State the minimal code-level
intent (file/area and semantic change), and mark inherited loader, split,
baseline, controls, metrics, and runtime protocol as unchanged. Do not make a
historical failed variant a new required arm unless the new claim directly
compares against it.

## Bounded review revision

Apply `../shared-references/bounded-scientific-review.md`. On review-driven revision, treat the previous Plan as the base document. Read the frozen problem anchor and `plan_review_issue_ledger`; change only fields required by validated `OPEN` blockers. Preserve `CLOSED` items unless the new patch would regress them. Return a `fix_map` from each blocker ID to the exact Plan Contract fields changed. Warnings and suggestions may be recorded but must not expand the required patch. Never broaden the selected claim merely to satisfy a reviewer; explicitly separate the primary claim, original-question link, and any minimal secondary endpoint needed for interpretation.

This Skill may emit or address frozen-policy findings, but it never accepts or rejects the plan. `fix_map` keys must exactly equal validated `OPEN` blocker IDs, and only the governance ledger may close a blocker or authorize `ACCEPT`.
