---
name: hypothesis-experiment-gate
description: Refine a research hypothesis into a plausible, falsifiable, and executable experiment contract, and decide whether it is ready for expensive formal runs. Use when a proposed mechanism may not improve the target metric, when hypothesis, dataset, task, baseline, metric, or generated implementation may drift, or when a failed or flat training run must be separated from genuine hypothesis falsification.
---

# Hypothesis Experiment Gate

Improve the probability of obtaining an informative experiment, not the probability of reporting a positive result. Never weaken controls, hide negative evidence, or tune the hypothesis after seeing test results.

## 1. Diagnose the claim before planning

Separate these outcomes:

- `hypothesis_falsified`: a valid, adequately powered experiment contradicts the prediction.
- `experiment_invalid`: code, data, optimization, numerical, metric, or audit failure prevents inference.
- `hypothesis_underspecified`: the intervention, comparator, mechanism, population, metric, direction, or boundary conditions are ambiguous.

Do not label an invalid experiment as evidence against the hypothesis.

## 2. Operationalize one primary hypothesis

Rewrite the hypothesis as:

> On `[population/dataset and task]`, changing `[one intervention]` relative to `[strong executable baseline]`, while holding `[controls]` fixed, will change `[primary metric]` in `[direction and minimum meaningful magnitude]` because `[mechanism]`, provided `[boundary conditions]`.

Require an evidence-based mechanism and state why it should operate in this representation and regime. Split accuracy, efficiency, robustness, and interpretability into separate hypotheses unless one preregistered composite endpoint is justified.

Prefer the lowest-risk intervention that still tests the mechanism. For high-dimensional or poorly conditioned methods, first test a diagonal, low-rank, normalized, or low-dimensional variant before a full-parameter version.

## 3. Run an alignment audit

Build a contract table covering:

- claim and mechanism;
- dataset, split, preprocessing, and task type;
- baseline and intervention;
- fixed controls and parameter-budget tolerance;
- primary metric, direction, threshold, and aggregation;
- seeds, uncertainty summary, and stopping rule;
- expected manifest fields and source-level metric provenance.

Reject any plan or implementation with classification/regression drift, dataset drift, metric-name or semantic drift, unmatched parameter budgets, multiple simultaneous causal changes, unsupported data, or a metric that cannot be traced to generated source.

## 4. Challenge feasibility

Check representation dimensionality, distance concentration, activation scale, gradient flow, parameter count, initialization, optimizer compatibility, numerical conditioning, data sufficiency, compute budget, and whether the baseline is known to learn.

Require a stronger conventional baseline when the proposed baseline is untrained, obsolete, or structurally unable to solve the task. A novel variant is not credible merely because it beats a broken baseline.

## 5. Define a staged execution gate

Keep execution gating minimal:

1. Static contract validation: identifiers, dataset, task, parameters, seeds, metric provenance, and finite-value handling.
2. Smoke run: one backend-selected seed, the complete verified dataset and frozen split, one real train/evaluation batch, finite metrics, and successful serialization.
3. Formal run: the frozen backend seed set.

An overfit check or pilot may be recorded as a diagnostic recommendation, but is not an additional blocker for formal execution. Stop only when static validation, the smoke run, or runtime integrity actually fails.

## 6. Pre-register interpretation

Define positive, negative, and inconclusive outcomes before execution. Use a minimum meaningful effect rather than an arbitrary large promised gain. Require uncertainty reporting and seed-level consistency; do not select favorable seeds or repeatedly alter thresholds.

## 7. Bound reviewer veto power

Apply `../shared-references/bounded-scientific-review.md`. Scientific review may be adversarial, but only adjudicated blockers from the frozen review policy may prevent formal execution. Classify other concerns as warnings or suggestions. Do not reopen resolved blockers without concrete regression evidence. After the first review round, a newly proposed blocker must arise from regression or new evidence. When validated open blockers reach zero, the plan is review-ready even if non-blocking concerns remain.

## Output contract

Return:

- `diagnosis`: `ready`, `revise`, or `blocked`, with `hypothesis_falsified`, `experiment_invalid`, and `hypothesis_underspecified` explicitly distinguished when applicable;
- `revised_hypothesis`;
- `mechanism_and_evidence`;
- `boundary_conditions`;
- `alignment_contract`;
- `baseline_and_controls`;
- `feasibility_risks` with mitigations;
- `staged_gates` with measurable pass/fail criteria;
- `formal_experiment_entry_conditions`;
- `positive_negative_inconclusive_rules`;
- `remaining_unknowns`.

When diagnosis is `revise` or `blocked`, emit a finding under the frozen policy with concrete Plan evidence. The diagnosis does not directly authorize or reject the plan; only validated `OPEN` blocker state in the governance ledger controls acceptance.
