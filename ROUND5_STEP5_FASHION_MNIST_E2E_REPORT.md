# Round 5 Step 5 — Fashion-MNIST Production E2E Acceptance

## Final status

**BLOCKED — CONTRACT_FAILURE**

The production workflow correctly prevented a formal experiment from starting because
the generated research plan exhausted its bounded strict-review loop. No safety,
scientific-integrity, dataset, Bundle, Harness, or runtime validator was weakened.
Consequently there are **no Fashion-MNIST training metrics** to report: no smoke or
full training was authorized or executed.

## Controlled production entry and identity

- Official project root: `D:\Gewu`
- Revalidated production API: `http://127.0.0.1:8003`
  - launched from `D:\Gewu` with `D:\Gewu\.venv\Scripts\python.exe`
  - Qwen ready, `local_gpu` ready, local dataset source confirmed
- Clean acceptance Run: `run_2b20b001c675`
- Actual production APIs used:
  - `POST /api/runs`
  - `POST /api/runs/{run_id}/steps/problem_understanding/run`
  - `POST /api/runs/{run_id}/steps/knowledge_integration/run`
  - `POST /api/runs/{run_id}/steps/hypothesis_generation/run`
  - `POST /api/runs/{run_id}/hypotheses/user`
  - `POST /api/runs/{run_id}/hypotheses/select`
  - the latter resumed the normal workflow orchestrator.

No `ExperimentAgent`, generated `train.py`, accepted result payload, or provider was
called directly.

Research question: under the same frozen Fashion-MNIST split, training budget,
optimizer settings, and seeds, does a small CNN achieve higher held-out test accuracy
than a simple MLP?

User-controlled hypothesis: CNN is treatment, MLP is baseline/control, primary metric
is held-out test accuracy; validation may select checkpoints but test must never tune
the model. Dataset replacement, cleaning, and downloads are forbidden.

## Dataset and hypothesis provenance

- Dataset Artifact: `art_448f1dd3f7e6`
- Dataset contract: `dataset_32db1f45ec300996`
- Root: `D:\Gewu\datasets\fashionmnist`
- Fingerprint: `sha256:32db1f45ec3009968baff6d4e05e5d02ef1e7a42373e802b9a9fcc5a2c3494b8`
- Online download: disabled; verified local binding was used.
- User hypothesis Artifact: `art_05283f4a3c0c`
- Idea-review checkpoint: `art_217b14222a77`
- Candidate-reasoning checkpoint: `art_11b2d78151ca`
- Idea-review Artifact: `art_e0bcaa3a9bbb`
- Reasoning Artifact: `art_742b7b145f2d`
- Selected-hypothesis Artifact: `art_6297b0c42e7d`

The final selected candidate was the user-owned hypothesis (`source: user`), with the
original CNN-vs-MLP claim preserved. Its actual evidence review was `GO` / `revised`
and selection status was `awaiting_selection` before the normal user-selection API was
called.

The model's initial ordinary hypothesis-generation attempt returned no usable
candidates (`HYPOTHESIS_CANDIDATES_EMPTY`). The supported user-hypothesis recovery API
then persisted and evaluated the supplied controlled hypothesis; it was not a manual
Artifact insertion.

## Scientific coverage and split integrity

There is no accepted Plan Artifact, therefore there is no accepted scientific coverage
contract, split contract, Experiment Task, runtime-contract hash, Harness identity,
candidate attempt, result ID, or feedback/revision Artifact for this acceptance run.

This is intentional gate behavior. The three persisted plan reviews
(`art_2859e300cd99`, `art_18abc51ef05f`, `art_b2388fa5341b`) identified real,
unresolved defects, including:

- absent reproducible train/validation/test split identity and validation policy;
- incomplete scoped evidence basis;
- underspecified statistical aggregation and MDE justification;
- parameter-capacity confounding / contradictory parameter-matching revisions;
- incomplete early-stopping and test-isolation policy; and
- missing required plan-contract sections and local-loader verification.

The resulting failure record is `art_95bc5a12e778`:
`MODEL_OUTPUT_VALIDATION_FAILURE`, attempt 3, with
`RESEARCH_PLAN_REVIEW_EXHAUSTED`. This is a **CONTRACT_FAILURE**, not an environment,
CUDA, dataset, Bundle, or scientific-outcome failure.

## Execution-stage evidence

| Stage | Actual status | Reason |
| --- | --- | --- |
| Experiment Task / Bundle generation | Not entered | Plan contract rejected |
| Candidate validation / repair lineage | Not entered | No Experiment Task |
| Deterministic runtime contract / Harness | Not created | No accepted Bundle |
| Local dataset runtime binding | Preflight PASS | Formal run blocked before launch |
| Sanity / pilot / full | Not entered | No approved plan |
| Smoke / strict result validation | Not entered | No provider invocation |
| Critic result review / scientific feedback / Writer | Not entered | No real result Artifact |

No code repair occurred for a scientific result, and no unsupported/inconclusive
scientific-outcome branch arose naturally. Step 4's automated scientific-integrity
tests remain the regression evidence for that unexercised branch.

## Step 5 production defects found and minimal fixes

1. **Stopped user-hypothesis recovery immediately cancelled itself.**
   `stop_requested=True` survived a paused run and caused the nested evidence-reasoning
   step to raise `PIPELINE_STOPPED`. `WorkflowEngine.add_user_hypothesis()` now clears
   that marker only when starting this recovery action. Regression:
   `test_user_hypothesis_recovers_a_stopped_run_before_re_evaluation`.

2. **Skill/API decision-literal conflict.**
   `evidence-recovery/SKILL.md` instructed `TARGETED_RETRIEVAL` / `REJECT`, while the
   authoritative Idea Selection schema accepts `GO`, `REVISE`, `PIVOT`, `STOP`, and
   `EVIDENCE_INSUFFICIENT`. The Skill now uses the schema literals and records a
   targeted-retrieval plan under the evidence-insufficient path. The validator remains
   strict. Regression:
   `test_contract_rejects_legacy_targeted_retrieval_decision_literal`.

3. **Model analysis could rewrite a user-supplied research claim.**
   The original claim is now an immutable user anchor through analysis and selection;
   critic evidence may assess it but cannot substitute another claim. Regression:
   `test_user_hypothesis_claim_is_immutable_through_analysis_and_selection`.

These are narrowly scoped production wiring/contract fixes. They do not modify the
controlled hypothesis, Plan review threshold, scientific-integrity gate, or runtime
security requirements.

## Tests and environment checks

- Before initial E2E: `510 passed, 2 skipped in 72.53s`.
- After recovery fix: focused user-hypothesis tests: `2 passed`;
  complete backend: `511 passed, 2 skipped in 96.84s`.
- After Skill contract fix: focused tests: `4 passed`;
  complete backend: `512 passed, 2 skipped in 78.80s`.
- After immutable user-claim fix: focused user-hypothesis tests: `3 passed`;
  final complete backend: **`513 passed, 2 skipped in 82.91s`**.
- Production preflight confirmed Qwen, RTX 5070 / CUDA 13.2, and the verified local
  Fashion-MNIST dataset. No runtime download occurred.

## Explicit declarations

- Real Fashion-MNIST training ran: **No — correctly blocked before execution.**
- Code repair from an experimental/scientific outcome: **No.**
- Scientific failure observed: **No; no experiment result exists.**
- Source code modified during Step 5: **Yes — three minimal production fixes above.**
- Git commit created: **No.**
- Round 6 entered: **No.**

The final Run state is:

`problem_understanding: completed; knowledge_integration: completed; hypothesis_generation: failed; evidence_reasoning: completed; research_plan: failed; experiment_task: pending; experiment_run_analysis: pending; feedback_revision: pending; report_export: pending`.

