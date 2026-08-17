# Round 5 — Step 3 Deterministic Harness / Contract Compiler Report

**Status: PASS — Step 3 complete. Step 4 was not started.**

## Scope boundary

- Official project root: `D:\Gewu`
- No Hypothesis or Plan scientific content was changed.
- No Fashion-MNIST E2E or real training was run.
- No Git commit was created.

## 1. Previous runtime call chain and ownership

Before Step 3, the production path was:

```text
Accepted Plan → Experiment Task → ExperimentAgent → LLM Bundle/train.py
→ static/plan validation → provider deployment → train.py smoke/full execution
→ train.py result JSON → strict result validation → Artifact
```

`normalize_experiment_bundle()` already injected run/experiment/result IDs and a nominal output argument. However, generated `train.py` was still responsible for most runtime protocol behavior: reading `DATA_ROOT`, honoring seeds/parameters, interpreting smoke mode, accepting identity/output arguments, choosing its emitted JSON, and serializing the final result envelope. The provider supplied environment variables and then trusted the generated program to write the final result location.

## 2. Deterministic runtime contract compiler

New module: `backend/app/workflow/experiment_harness.py`.

`compile_runtime_contract(plan, task, bundle)` consumes only accepted/system state plus the already normalized/frozen Bundle and creates canonical JSON with a SHA-256 identity. The compiler contains no time, UUID, or model-generated text; identical inputs produce byte-equivalent contract data and Harness source.

Contract inputs and outputs include:

- run, experiment, and result IDs;
- dataset name, contract ID, fingerprint, and expected verified data root;
- GPU requirement;
- frozen seeds and parameters;
- expected metrics and iteration-required metrics;
- smoke capability;
- implementation entrypoint;
- deterministic temporary implementation output path and final result output path; and
- canonical contract hash / Harness filename.

The compiled contract is attached to `ExperimentBundle.runtime_contract` and is persisted in the normal Bundle and candidate-attempt Artifacts. `ExperimentAgent.validate_bundle()` recompiles it and rejects a mismatch with `EXPERIMENT_RUNTIME_CONTRACT_MISMATCH`.

## 3. Deterministic Harness ownership

The provider deterministically materializes `.gewu_harness.py` from the embedded contract. This file is not supplied by the LLM and is regenerated identically for the same contract.

The Harness owns:

- checked `DATA_ROOT` / local dataset-contract ID / fingerprint binding;
- system-provided `GEWU_SEEDS_JSON` and `GEWU_PARAMETERS_JSON` injection;
- run/experiment/result identity passed to scientific implementation;
- smoke/full context;
- temporary implementation output and final output location;
- final result envelope and contract runtime metadata; and
- hand-off of its final envelope to the existing strict runtime result validator.

The Harness launches LLM-generated `train.py` with a deterministic temporary output destination. It reads only that implementation metrics object, ignores any generated ID values, and writes the final envelope using the frozen contract IDs. Thus generated code cannot select the system final result path or forge its accepted identity.

## 4. Scientific implementation boundary

LLM-generated `train.py` remains responsible for scientific implementation only:

- model, loss, optimizer, preprocessing;
- training/evaluation logic;
- scientific metric values; and
- producing a finite `metrics` object at the temporary runtime-provided path.

It does not own data-root identity, frozen seeds/parameters, task IDs, final result path, final envelope, or Harness source.

## 5. Repair invariance and lineage

The Step 1 sequence remains unchanged:

```text
candidate 1: generate_bundle()
candidate 2+: repair_bundle(previous candidate, issues, history, frozen contract)
```

Both generation and repair compile the same deterministic runtime contract from the frozen Bundle/accepted Plan. Candidate attempt Artifacts remain intact and now include the compiled contract in `normalized_bundle`. Tests verify rejected and repaired candidates have identical runtime contracts and parent-attempt lineage.

Runtime code-repair candidates continue to use the same `experiment_candidate_attempt` Artifact type and parent chain from Step 2.

## 6. Dataset, seed, parameter, and result protection

- Local provider still verifies the inspected dataset directory before setting `DATA_ROOT`, `DATASET_CONTRACT_ID`, and `DATASET_FINGERPRINT`; it now also sets `GEWU_VERIFIED_DATA_ROOT` for the Harness binding check.
- A local candidate assignment such as `DATA_ROOT = 'D:/other'` or `os.environ['DATA_ROOT'] = ...` is statically rejected with `EXPERIMENT_DATASET_ROOT_OVERRIDE_FORBIDDEN`.
- Candidate manifest contract drift remains rejected by existing Plan validation. Runtime-contract drift is independently rejected by contract recompilation.
- Seeds and parameters are frozen in the contract and injected to the child process as JSON environment values; candidate/repair changes cannot alter the final system contract.
- The Harness takes only `metrics` from implementation output and creates the final identity/result envelope itself.

## 7. Ownership table

| Layer | Responsibility |
| --- | --- |
| Skill | Guide scientific implementation; prohibit hard-coded dataset roots, downloads, frozen-contract edits, and protocol bypass attempts. |
| Static validator | Source safety, dependency declarations, no runtime download, DATA_ROOT source rules/override rejection, CUDA/progress/smoke protocol, plan and frozen-contract consistency. |
| Contract compiler / Harness | Canonical runtime contract, verified dataset binding, seed/parameter injection, IDs, execution mode, output paths, final envelope. |
| Provider/runtime | Verify/stage dataset, deploy deterministic Harness, provide GPU/runtime environment, execute smoke/full commands, persist runtime metadata. |
| Runtime result validator | Exact IDs, `expected_metrics ⊆ actual_metrics`, numeric/finite metrics, result file validity, smoke exit result. |

No Validator rule was removed. Ownership was moved only where a deterministic system component can enforce the protocol more reliably.

## 8. Files changed

- `backend/app/models/experiment.py`
- `backend/app/workflow/experiment_harness.py` (new)
- `backend/app/agents/experiment.py`
- `backend/app/providers/experiment_runtime.py`
- `backend/app/providers/experiment.py`
- `skills/experiment-implementation/SKILL.md`
- `tests/backend/test_experiment_harness.py` (new)
- `tests/backend/test_workflow_engine.py`

## 9. New test coverage

1. Compiler and generated Harness source are deterministic and contain no UUID drift.
2. Compiled dataset ID/fingerprint/root, seeds, and parameters are frozen; contract tampering is rejected.
3. Harness rejects an unverified local dataset binding.
4. Candidate DATA_ROOT override and runtime-contract dataset substitution are rejected.
5. Harness ignores forged implementation IDs and an attacker-selected output argument, writes only the deterministic final envelope, and permits dynamic metric keys.
6. Existing strict runtime validation still rejects missing and non-finite metrics.
7. Step 1 repair tests verify generated/repaired candidate runtime contracts are identical and Step 2 lineage remains recoverable.

## 10. Test results

Focused Step 3 + Experiment/Workflow regression:

```text
tests/backend/test_experiment_harness.py
tests/backend/test_experiment_code.py
tests/backend/test_workflow_engine.py
tests/backend/test_dataset_planning.py
148 passed in 22.25s
```

Provider/runtime focus:

```text
26 passed in 0.27s
```

Complete backend regression:

```text
503 passed, 2 skipped in 72.67s
```

## Explicit stop condition

- Step 4 not started.
- Fashion-MNIST E2E not run.
- Git commit not created.
