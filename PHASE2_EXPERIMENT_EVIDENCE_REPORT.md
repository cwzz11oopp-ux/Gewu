# Phase 2 Experiment Evidence Report

Date: 2026-08-16

## Scope

Phase 2 was implemented only on the production `/api/runs` workflow.  Phase 1 admission, immutable constraints, preflight, append-only history, and the experimental V2 API were not redesigned.  No Phase 3 scientific revision, Idea evolution, PaperProfile, Skill refactor, automatic ablation, or report redesign was added.

## Core implementation

- Added `backend/app/workflow/phase2_evidence.py`: deterministic DatasetProfile enrichment, BaselineProfile, 10% reproduction comparison, frozen fair-experiment contract, smoke/small/formal protocols, metric direction normalization, paired-result statistics, and execution routing.
- Dataset inspection remains the existing read-only inspector.  Its saved Artifact is enriched to `dataset_profile_version: 2` before problem structuring.
- Research planning appends `baseline_profile` and `fair_experiment_contract` Artifacts.  Experiment tasks carry the frozen smoke protocol without changing the existing Bundle/Harness runtime.
- Successful experiment output appends a `result_evidence` Artifact.  It only computes comparative evidence from explicit `baseline_seed_metrics` and `idea_seed_metrics`; lack of paired measurements remains `not_comparable` and is never inferred by an LLM.
- `ExperimentPanel` reads the real DatasetProfile, BaselineProfile, Fair Experiment Contract, and ResultEvidence Artifacts and displays the requested evidence fields.

## DatasetProfile

`DatasetProfile` now records sample count (when read-only schema inspection can establish it), columns/input shape, target/label candidate, missing-value status, split source, user description, and task compatibility.

- Classification: class distribution/count explicitly recorded as known or unknown; stratified split policy.
- Forecasting: a time column yields `chronological_by_time_column`; without one, an explicit user/data-order confirmation yields `chronological_by_row_order`; otherwise the profile remains compatible but `needs_confirmation`.  It is marked incompatible only on explicit evidence, always forbids random time leakage, and retains required input length/horizon fields.
- Anomaly detection: normal/anomaly label candidate, training and threshold/evaluation protocol fields.

Official train/test-looking sources are preferred; otherwise the profile marks the split as `generated_frozen`.  Semantic facts that cannot be proved by structural inspection remain explicitly unknown.

## Baseline and 10% reproduction

`BaselineProfile` applies the requested priority: user baseline, repository baseline, then literature/local planning fallback.  It persists source/reference, implementation type, training config, paper/local metrics, and reproduction status.

For declared paper metrics, `reproduction_check` uses an **absolute 10 percentage-point** tolerance for proportion metrics (Accuracy/F1/AUC/Pd; `0.80 → 0.71` is a 9-point difference) and **relative 10% deviation** for error metrics (MSE/MAE/RMSE).  Out-of-tolerance results route to `baseline_diagnosis`; after diagnosis, `approximate_reproduction` preserves paper/local values, deviations, and possible causes while retaining the local baseline as the fair comparison anchor.

## Progressive experiments and fairness

`fair_experiment_contract` freezes dataset contract, split, preprocessing, primary/secondary metrics, seeds, epochs, training config, evaluation protocol, and baseline identity.  `progressive_protocol` produces distinct immutable views:

- `smoke`: one seed and at most one epoch; code/data/interface verification only.
- `small_scale`: paired baseline-vs-Idea screening with reduced, equal budget.
- `formal_validation`: full frozen user epoch/seed budget and paired formal comparison.

The contract records same-seed pairing and prevents repair from being treated as permission to change a frozen scientific variable.

## Deterministic ResultAnalyzer and routes

`result_evidence` computes, without any model call: baseline/Idea mean and standard deviation, per-seed paired deltas, mean/median delta, delta standard deviation, positive count/ratio, 95% normal confidence interval, standardized effect size, and noise magnitude.  It supports maximize and minimize metrics and defines positive delta as movement in the desired direction.

It outputs only an execution route:

- `expand_validation` for stable positive paired evidence;
- `add_seeds` for inconclusive/noise-level evidence;
- `scientific_review` for negative completed evidence, or seed-cap exhaustion;
- `engineering_diagnosis` for anomalies or non-comparable/missing paired data.

No automatic statement of overall success or scientific value is produced.

## Frontend

The existing experiment page now renders Artifact-backed DatasetProfile ID, baseline/source state, reproduction state, stage, primary/secondary metrics, epoch/seeds, baseline-vs-Idea means, mean/std/paired deltas, and current route.  It does not draw synthetic performance data.

## Verification

- Focused backend: `python -m pytest tests/backend/test_phase2_evidence.py tests/backend/test_phase1_foundation.py -q` → **11 passed**.
- Full backend regression: `python -m pytest tests/backend -q -m "not gpu"` → **557 passed, 2 skipped** in 109.06s.
- Frontend contract tests: `pnpm --dir frontend exec node --test tests/phase1-foundation.test.mjs tests/phase2-evidence.test.mjs` → **5 passed**.
- Frontend production build: `pnpm --dir frontend run build` → **passed**.

## Safety confirmation

No real research Run, real model request, real literature request, real training E2E, historical run-data mutation, or Git commit was performed.  Phase 3 was not entered.
