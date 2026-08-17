# Round 5 — Step 2 Validator and Candidate Lineage Report

**Status: PASS — Step 2 complete. Step 3 was not started.**

## Scope and safety boundary

- Official project root: `D:\Gewu`
- No Hypothesis or Plan was changed.
- No Fashion-MNIST E2E, training, or Harness refactor was run.
- No Git commit was created.

## Former fail-fast validators

The following gates previously stopped at their first detectable error:

1. `validate_experiment_bundle_source()` stopped on the first source, dependency, dataset, CUDA, metric-literal, or smoke-protocol error.
2. `_validate_python_source()` stopped on the first encoding, syntax, API, tensor/NumPy, array-stride, model-clone, or progress error.
3. `_validate_bundle_against_plan()` returned early for a local dataset contract, so its seed, parameter, and iteration-contract checks did not run.
4. `ExperimentAgent.validate_bundle()` passed through the first exception from the source gate before checking the plan gate.

## Aggregated static validation

`ExperimentBundleValidationError` now preserves a deduplicated ordered `issues` list. `experiment_validation_issues()` returns that full list to the existing Supervisor revision loop.

- Source files are statically inspected first, and all independent detectable source issues are collected.
- Dependency, no-runtime-download, external-dataset root, CUDA, result-mechanism, smoke-protocol, plan/dataset, seed, parameter, and iteration-metric issues are then combined in one validation response.
- An unparseable Python file still reports its syntax failure for that file; semantic AST checks cannot safely continue on malformed syntax.

## Metric validation ownership

The obsolete `expected metric name must appear literally in train.py` rule was removed.

- Static validation now requires a metrics/result generation mechanism for smoke-capable bundles: a `metrics` payload, JSON serialization, and an output/result path mechanism.
- Dynamic metric keys are therefore accepted.
- `expected_metrics ⊆ actual runtime metrics` remains strict at smoke/runtime in `validate_result_payload()` / `validate_result_file()`. Missing, non-numeric, and non-finite runtime metrics still fail the Bundle smoke/runtime gate.

## Tensor → NumPy safety

The validator now uses Python AST semantics rather than the former single substring check.

- Accepted: `tensor.detach().cpu().numpy()` and equivalent detached temporaries such as `detached = tensor.detach(); on_cpu = detached.cpu(); on_cpu.numpy()`.
- Rejected: any `.numpy()` call whose receiver is not known to originate from a detached tensor chain, including live `tensor.cpu().numpy()`.

## Unified dataset and implementation contract

The rules now have one ownership model:

| Owner | Enforced rule |
| --- | --- |
| Dataset inspector/runtime | A bound local contract must resolve to the recorded root, contract ID, and fingerprint; runtime provides that verified location as `DATA_ROOT`. |
| Bundle source validator | Runtime downloads are forbidden. External torchvision datasets require the declared dataset plus `DATA_ROOT` and `download=False`. |
| Plan validator | A local binding cannot be substituted; it must retain contract ID/fingerprint and use `DATA_ROOT`. Local `torchvision` use is allowed only under the same no-download/`DATA_ROOT` rules, rather than being categorically forbidden. Seeds, parameters, and iteration metrics always run. |
| `experiment-implementation` Skill | Instructs the model to use `DATA_ROOT` only, never download, serialize metrics/results, and use semantic detach-before-NumPy safety. |
| Smoke/runtime | Verifies the actual result identifiers, expected metrics, numeric/finite values, and smoke exit status. |

There are no remaining contradictory Validator/Skill rules in this scope. The dataset inspector deliberately validates filesystem identity rather than code generation behavior.

## Local dataset early-return repair

The local-contract branch no longer returns before common plan checks. With a local dataset contract, the validator now still evaluates:

- planned seeds;
- planned parameters; and
- iteration-contract required metrics.

## Rejected candidate persistence and lineage

Every Experiment Task candidate and every runtime code-repair candidate now creates an existing repository Artifact of type `experiment_candidate_attempt`, whether accepted or rejected. Rejected candidates are permanent run artifacts; they are no longer only temporary staging files or event summaries.

Each Artifact stores:

- raw model output;
- normalized Bundle when normalization succeeded;
- manifest, `files` (including `train.py`), and requirements snapshots;
- complete validation issues;
- `attempt_id`, `parent_attempt_id`, and `attempt_number`;
- repair history;
- SkillRuntime instruction hash and skill invocation audit;
- plan artifact ID and dataset contract reference (ID, fingerprint, root); and
- acceptance state and Artifact parent link.

Lineage is recovered by reading `experiment_candidate_attempt` Artifacts in attempt order or following `parent_artifact_id` / `parent_attempt_id`:

```text
Candidate 1 Artifact → validation_issues → Candidate 2 Artifact → validation_issues → Candidate 3 Artifact
```

The first candidate is parented to the accepted plan Artifact. Each repair candidate is parented to the immediately preceding candidate Artifact and records that same Artifact ID in `parent_attempt_id`.

## Tests added or updated

- A candidate with independent source/API/tensor/dependency faults returns all static issues in one response.
- Dynamically constructed expected metric keys are not rejected by a static literal check.
- A rejected candidate is recoverable from Artifact content, including raw and normalized data, validation issues, Skill hash, plan and dataset references.
- A repair candidate records the previous candidate's `parent_attempt_id` and Artifact parent link.
- Local dataset contracts still validate seeds and parameters.
- Equivalent detached Tensor → NumPy temporary forms pass.
- Unsafe live Tensor → NumPy conversion remains rejected.

## Test results

Focused Step 2 suite:

```text
tests/backend/test_experiment_code.py + tests/backend/test_workflow_engine.py
131 passed in 22.79s
```

Runtime repair lineage focus:

```text
3 passed in 1.70s
```

Complete backend regression:

```text
497 passed, 2 skipped in 93.78s
```
