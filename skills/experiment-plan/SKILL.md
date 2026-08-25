---
name: experiment-plan
description: Specify the smallest reproducible experiment matrix that can confirm or falsify a research claim. Use for initial research planning and for a follow-up plan after partial or failed audited results.
allowed-tools: read_run, read_artifact
---

# Experiment Plan

## Inputs

Read the accepted hypothesis, problem constraints, available dataset cards, and compute budget. Do not repair missing scientific assumptions by silently choosing a convenient dataset, baseline, metric, or threshold.

## Protocol

Design the smallest credible experiment set within the stated compute budget. Build a claim-to-evidence map first, then define dataset and split, preprocessing, model and strongest relevant baselines, controlled variables, parameters, repetition count, metrics, statistical summaries, expected artifacts, and stop conditions. The backend owns concrete random seeds: specify `procedure.repetitions`, never choose or return a seed list.

For each experiment state the method used, why that method is diagnostic, its evidence basis, the one changed factor, fixed controls, decisive metric, success/failure threshold, and positive/negative interpretation. Distinguish the primary experiment from optional ablations and provide enough detail for ExperimentAgent to generate a bundle without guessing.

## Output Contract

Return the research objective and hypotheses, method and mechanism, dataset, comparisons, evaluations, procedure, parameters, statistical summary, success and failure criteria, expected artifacts, stop conditions, primary experiment, optional ablations, resources, risks, and claim-to-metric traceability. Also return the shared Plan Contract fields: `diagnosis`, `revised_hypothesis`, `mechanism_and_evidence`, `boundary_conditions`, `alignment_contract`, `baseline_and_controls`, `feasibility_risks`, `staged_gates`, `formal_experiment_entry_conditions`, `positive_negative_inconclusive_rules`, `remaining_unknowns`, `capacity_confounder`, and `local_dataset_loader_verification`.

For a local dataset, state the immutable dataset fingerprint/contract reference, a reproducible disjoint split identity, validation-only checkpoint/early-stopping policy, one-final-evaluation test-isolation policy, aggregation and uncertainty method, capacity-confounder control and claim boundary, effect-size justification, and local-loader verification procedure. Do not invent concrete dataset values; use the supplied dataset card and run constraints.

## Acceptance

Emit a frozen-policy finding when the intervention is unnamed, an executable comparator is absent, multiple causal factors change, decision rules are omitted, an unchanged failed run is repeated, compute constraints are violated, or the selected dataset card conflicts with the plan. This Skill does not accept or reject a plan; only validated governance ledger state may do so.

## Review-bounded planning

Apply `../shared-references/bounded-scientific-review.md`. Keep `primary_claim`, `original_question_link`, and `secondary_endpoints` explicit so a selected hypothesis cannot silently drift from the user's original question. When revising, consume the stable blocker ledger and emit a `fix_map`; do not rewrite resolved sections unless necessary for consistency. Non-blocking reviewer preferences must not be promoted into mandatory plan complexity.
