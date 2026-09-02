---
name: research-refine
description: Refine a selected hypothesis or a feedback-rejected plan into the smallest precise, executable research method. Use during initial research planning and when experiment feedback requires a scientific plan revision.
allowed-tools: read_run, read_artifact
---

# Research Refine

Freeze the problem anchor and supported parts of the selected hypothesis. For feedback revision, begin from the previous accepted plan and change only fields required by a named unsupported claim, audit issue, or decision failure.

For a frozen optimization goal, a measured improvement opportunity is also a
valid reason to revise a supported result. If feedback supplies an
`implementation_reference`, current_plan is the retained best plan, while
experiment_result may describe a later, worse trial. Preserve that distinction.
Read research_context and scientific_synthesis before planning; preserve all
backend-owned iteration lineage fields during review. Do not repeat an already
executed configuration by only changing its description. A protocol change
requires a fresh controlled comparison, not a claim of cross-protocol gain.

State the proposed method, mechanism, why it should address the observed weakness, and the evidence or measured observation motivating it. Distinguish verified evidence, measured result, and assumption.

Return the complete shared Plan Contract: `objective`, `hypotheses`, `method`, `dataset`, `comparisons`, `evaluations`, `procedure`, `parameters`, `statistical_summary`, `success_criteria`, `failure_criteria`, `expected_artifacts`, `stop_conditions`, `traceability`, `diagnosis`, `revised_hypothesis`, `mechanism_and_evidence`, `boundary_conditions`, `alignment_contract`, `baseline_and_controls`, `feasibility_risks`, `staged_gates`, `formal_experiment_entry_conditions`, `positive_negative_inconclusive_rules`, `remaining_unknowns`, `capacity_confounder`, and `local_dataset_loader_verification`. The backend owns concrete seeds: give only `procedure.repetitions`. Map every method component to a mechanism, every change to feedback, and every decision criterion to a measurable metric. Do not widen the contribution or add optional complexity unless it resolves a concrete failure.

Before returning, perform an atomic cross-field consistency sweep. Every claim-bearing or interpretive field must describe the same intervention, comparator, mechanism, endpoints, and claim boundary; remove stale terminology from superseded methods everywhere, including experiment names, risks, evidence mechanisms, and capacity boundaries. If several primary endpoints are required, define a justified minimum meaningful improvement for each: positive requires all required endpoints, negative follows a preregistered adverse/null rule, and mixed directions or insufficient precision are inconclusive. Do not treat non-significance with a small seed count as proof of no effect.

For multi-round single-variable optimization, enumerate the one changed variable and fixed controls per round. Use validation data only for selection, reject and roll back non-improving changes, stop after a preregistered consecutive-no-improvement limit or maximum round budget, freeze the winner, and touch the test set only once for final evaluation.

For a `PIVOT`, write one unitary new claim identically in `objective`,
`hypotheses`, `primary_claim`, and `revised_hypothesis.claim`; do not retain the
contradicted parent wording in any of those fields. State the minimal code-level
intent (file/area and semantic change), and mark inherited loader, split,
baseline, controls, metrics, and runtime protocol as unchanged. Do not make a
historical failed variant a new required arm unless the new claim directly
compares against it.

## Bounded review revision

Apply `../shared-references/bounded-scientific-review.md`. On review-driven revision, treat the previous Plan as the base document. Read the frozen problem anchor and `plan_review_issue_ledger`; change only fields required by validated `OPEN` blockers. Preserve `CLOSED` items unless the new patch would regress them. Return a `fix_map` from each blocker ID to the exact Plan Contract fields changed. Warnings and suggestions may be recorded but must not expand the required patch. Never broaden the selected claim merely to satisfy a reviewer; explicitly separate the primary claim, original-question link, and any minimal secondary endpoint needed for interpretation.

For `CLAIM_PLAN_MISMATCH`, the required patch includes the transitive semantic closure of that same blocker: inspect every schema-available field and update every residual occurrence of the contradicted method, mechanism, comparator, endpoint, decision rule, experiment framing, risk, or claim boundary. This is one atomic repair, not warning-driven scope expansion. Do not stop after changing only the headline fields.

This Skill may emit or address frozen-policy findings, but it never accepts or rejects the plan. `fix_map` keys must exactly equal validated `OPEN` blocker IDs, and only the governance ledger may close a blocker or authorize `ACCEPT`.
