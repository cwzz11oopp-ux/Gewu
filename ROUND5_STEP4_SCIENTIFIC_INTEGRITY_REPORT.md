# Round 5 — Step 4 Scientific Experiment Integrity Report

**Status: PASS — Step 4 complete. Step 5 was not started.**

## Previous scientific call chain and gaps

Previous chain:

```text
Research Question → research/evidence → hypothesis selection → plan → experiment task
→ Bundle/runtime → result → Critic revision → report
```

The chain preserved provenance and runtime correctness, but it had no first-class contract proving that every claim had planned evidence, a task metric, comparison/control, and interpretation. Dataset split text was not normalized as a scientific contract; leakage risks and escalation boundaries were therefore not represented as recoverable scientific state.

## Scientific coverage contract and validator

New `backend/app/workflow/scientific_integrity.py` compiles a canonical scientific contract containing:

- research question and selected hypothesis claims;
- plan traceability/evidence, evaluations, comparisons, and experiment task context;
- split contract; and
- progressive stages.

The coverage validator aggregates structured issues, rather than failing fast or using exact-string equality as its primary mechanism. It detects missing claim evidence, unmeasured claim metrics, missing baseline/comparison when the claim requires one, and absent decision rules. Scientific contract and issues are attached to the Plan; task compilation carries the contract downstream.

## Split and leakage integrity

`split_contract` is now a normalized Plan field. It can describe train/validation/test partition IDs, reproducibility identity/seed, selection sources, final metric source, group membership, strategy, and domain structure.

- **ERROR:** missing held-out partition, train/test overlap, validation/test overlap, cross-split declared group, test used for tuning/threshold/selection, final metric from train.
- **WARNING / review:** missing reproducibility evidence or a structure-aware policy for group/time-series/spatial/duplicate-sensitive data. These are explicitly warnings, not invented deterministic facts.

## Progressive experiment and escalation boundary

The contract expresses stages (`sanity`, `pilot`, `full`, or an explicit equivalent), evidence targets, escalation criteria, and stop criteria. `progressive_decision()` distinguishes:

- `code_failure` → existing Step 1 code-repair path;
- `unsupported` / `inconclusive` scientific evidence → scientific feedback, no automatic code repair; and
- successful evidence → stop/evaluate escalation under the declared contract.

This prevents result quality from being treated as an invitation to repeatedly repair code.

## Scientific feedback persistence and lineage

After Critic feedback, the workflow persists a `scientific_feedback` Artifact parented to the revision. It includes tested claims, observed metrics/result ID, supported/unsupported/inconclusive verdict, limitations, recommended scientific action, contract hash, and `code_repair_allowed: false`.

The existing lineage remains additive:

```text
Question → selected hypothesis → Plan/scientific_contract → Experiment Task
→ split/progressive contract → Result → revision → scientific_feedback
```

`experiment_candidate_attempt`, frozen Bundle, deterministic runtime contract, Harness, and all Step 1–3 checks remain unchanged.

## Files changed

- `backend/app/workflow/scientific_integrity.py` (new)
- `backend/app/workflow/plan_contract.py`
- `backend/app/workflow/engine.py`
- `tests/backend/test_scientific_integrity.py` (new)

## Test coverage

- missing question/hypothesis coverage;
- hypothesis claim without evidence;
- metric mismatch (accuracy claim vs training loss only);
- missing baseline/control;
- valid complete coverage;
- train/test and validation/test overlap;
- test used for selection and final metric from train;
- reproducible split identity and structure-risk warning;
- progressive stop/no blind escalation;
- code failure vs scientific failure;
- persistent scientific feedback;
- Step 1 repair, Step 2 lineage, and Step 3 Harness regression in focused workflow suites.

## Test results

Focused Step 4 plus workflow/experiment/dataset regression:

```text
155 passed in 24.20s
```

Post-finalization Plan/Workflow focus:

```text
16 passed in 2.10s
```

Complete backend regression after final plan-contract reattachment:

```text
510 passed, 2 skipped in 76.81s
```

## Explicit stop condition

- Step 5 not started.
- Fashion-MNIST E2E not run.
- Git commit not created.
