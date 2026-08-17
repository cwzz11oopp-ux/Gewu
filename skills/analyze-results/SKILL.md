---
name: analyze-results
description: Analyze validated experiment metrics against the exact parent plan without inventing values. Use after a successful result schema check and before claim review or report export.
allowed-tools: read_run, read_artifact, read_experiment_result
---

# Analyze Results

## Inputs

Read only the validated result, exact parent plan, manifest, parameters, seeds, environment, and accepted attempt record. Compare primary metrics with baselines and declared success/failure criteria. Report variability only when seed-level values exist and identify missing comparisons.

## Output Contract

Return `experiment_id`, `result_id`, `metrics`, `comparisons`, `observations`, `limitations`, and a preliminary `verdict`. For each interpretation identify the comparison method and measured basis. Distinguish measured fact, derived comparison, and interpretation.

## Boundaries

Copy metric values from the validated result exactly. Never recompute missing metrics from prose or stdout, never invent seed-level variability, and never turn a statistically or practically negligible difference into a supported claim.
