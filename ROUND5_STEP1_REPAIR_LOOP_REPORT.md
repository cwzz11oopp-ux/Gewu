# Round 5 Step 1 — Experiment Repair Loop Report

Official Project Root: `D:\Gewu`  
Scope: **Step 1 only — Candidate → Repair loop**  
Date: 2026-08-14

## Outcome

**PASS.** The Experiment Task now generates one initial candidate and, after a correctable validation failure, repairs the prior candidate rather than regenerating a new one. No Validator rule was removed or weakened, and no Fashion-MNIST-specific behavior was added.

## 1. Why the old behavior was regeneration, not repair

The prior `WorkflowEngine` Experiment Task passed a revision text object to `build_experiment`, but `build_experiment` called `ExperimentAgent.generate_bundle()` on every `_produce_validated()` iteration. `ExperimentAgent.repair_bundle()` existed but had no Experiment Task caller. Thus each retry created a fresh `experiment.generate_bundle` model request; the previous manifest, source files, requirements, and repair history were absent from the LLM input.

Old chain:

```text
Plan → generate_bundle → normalize/validate → issue text → generate_bundle again
```

## 2. Files changed

- `backend/app/workflow/engine.py`
  - Experiment Task now creates one stable base task and keeps the prior structured Bundle/raw candidate, frozen contract, and repair history in the bounded Supervisor loop.
  - First pass calls `generate_bundle(validate=False)`; later passes call `repair_bundle(validate=False)`.
  - Existing preflight gates run through `validate_bundle()` after each generated/repaired candidate.
  - Final rejection event now includes the accumulated repair history when the candidate supplies it.
- `backend/app/agents/experiment.py`
  - Added `ExperimentBundleCandidateError` to preserve raw model output through a correctable structural-normalization failure.
  - Split candidate construction from unchanged validation via `validate_bundle()`.
  - `repair_bundle()` now accepts the previous raw candidate/frozen contract when no normalizable Bundle exists, and always supplies the previous manifest, files, requirements, validation feedback, structured repair history, plan, task, and frozen contract to the repair model request.
  - Repair output still has manifest scientific fields forced from the frozen contract.
- `backend/app/workflow/experiment_code.py`
  - Added a `validate_source` switch to `normalize_experiment_bundle()` and exposed `validate_experiment_bundle_source()`; default behavior remains source validation enabled. This is sequencing only, not a validator relaxation.
- `tests/backend/test_workflow_engine.py`
  - Converted prior retry fixtures to assert generate-once/repair-next behavior.
  - Added a two-repair test.
  - Added final repair-history assertions.
- `tests/backend/test_experiment_code.py`
  - Extended frozen-contract coverage to include the plan's experiment variants passed into repair.

## 3. Current call chain

```text
Plan
  → ExperimentAgent.build_task (once)
  → generate_bundle(validate=False)                       [candidate 1 only]
  → validate_bundle (existing source/smoke/plan gates)
       → accepted: create Experiment Task + Bundle artifacts
       → rejected: Supervisor revision event + repair history
  → repair_bundle(previous Bundle or raw candidate,
                  validation feedback,
                  repair history,
                  frozen contract,
                  plan/task)                               [candidate 2+]
  → validate_bundle
```

## 4. When `generate_bundle()` is called

Exactly once at the start of an Experiment Task. It constructs a candidate without immediately executing the preflight gates so that the same candidate can be supplied to repair if those gates reject it.

## 5. When `repair_bundle()` is called

For every later bounded retry after a correctable validation failure. It receives the previous normalizable `ExperimentBundle`; if the first candidate failed structural normalization, it receives the preserved raw candidate plus an immutable contract derived at the first attempt. It does not call `generate_bundle()` again.

## 6. Previous candidate and repair history

For a normalizable candidate, the repair LLM input contains:

- prior `manifest`, `files` including `train.py`, and `requirements`;
- `validation_feedback` for the immediately preceding candidate;
- structured `repair_history` entries `{attempt, issues}` for all prior failures;
- `plan`, `task`, diagnostic context, and frozen contract.

For an unnormalizable first output (for example, missing `files`), the raw model object is supplied as `previous_candidate`; its initial frozen contract is retained for later repair attempts. All rejected attempts remain in the Supervisor event stream, and the final limit event includes the entire `repair_history` array.

## 7. Frozen scientific contract

For a normalizable candidate, repair freezes the prior manifest's IDs, GPU requirement, dataset identity, expected metrics, parameters, and seeds. It cannot trust replacement versions of those fields from the repair model output. The unchanged Plan is also supplied to each repair, retaining experiment variants and the dataset contract. When the first candidate is structurally invalid, a frozen contract is captured from that first candidate plus upstream dataset/task identifiers and is not overwritten by later malformed repair output.

This step does not yet correct pre-existing Plan-contract validation gaps (including the known local-dataset early return); that is explicitly deferred to Step 2.

## 8. Test coverage

| Requirement | Test evidence |
| --- | --- |
| A. First attempt generates; failure invokes repair | `test_experiment_task_retries_invalid_generated_bundle` asserts one generate and one repair, with prior source and issue passed to repair. |
| B. Second repair gets previous candidate, newest issue, and full history | New `test_experiment_task_second_repair_receives_previous_candidate_and_full_history`. |
| C. Repair cannot drift scientific fields | `test_repair_bundle_freezes_scientific_contract` verifies dataset, parameters, seeds, expected metrics; it also verifies Plan variants remain present in repair input. |
| D. Retry limit retains all failures | `test_experiment_task_records_final_issue_before_revision_limit_error` verifies six repair-history records in the final rejection; `test_experiment_task_recovers_when_bundle_files_are_missing` covers raw-candidate repair. |

Commands run:

```text
D:\Gewu\.venv\Scripts\python.exe -m pytest tests/backend/test_experiment_code.py tests/backend/test_workflow_engine.py -q
127 passed in 21.98s

D:\Gewu\.venv\Scripts\python.exe -m pytest tests/backend -q
493 passed, 2 skipped in 71.88s
```

## Deliberately not performed

- No Validator aggregation, metric-rule changes, Tensor/NumPy rule changes, or rejected-candidate Artifact persistence (Step 2).
- No deterministic Harness/contract compiler (Step 3).
- No Question-Coverage, split-leakage, or progressive-experiment changes (Step 4).
- No new Fashion-MNIST E2E run (Step 5).
- No Git commit, push, or PR.

## Step 1 stop condition

Step 1 is complete and validated. Stop here pending approval to start Step 2.
