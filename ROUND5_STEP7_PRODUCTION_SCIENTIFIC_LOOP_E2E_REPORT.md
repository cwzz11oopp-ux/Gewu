# Round 5 Step 7 — Production Scientific Loop E2E Report

## Final status: COMPLETE

- Formal Run: `run_a5c60cfe56ff` — no new Run created.
- Step 7 completed through the formal API; final report Artifact: `art_d583cc7faf0e`.
- No Git commit, manual production Artifact insertion, dataset replacement, or Round 6 work was performed.

## Original blocker: exact cause and repair

Pre-fix recovery raised:

```text
Traceback (most recent call last):
  File "backend/app/api/runs.py", line 325, in rerun_from
    return deps.engine.rerun_from(run_id, step_id)
  File "backend/app/workflow/engine.py", line 2094, in rerun_from
    self._rerun_from(run_id, step_id)
  File "backend/app/workflow/engine.py", line 2194, in _rerun_from
    return self.run_step(run_id, step_id)
  File "backend/app/workflow/engine.py", line 202, in run_step
    self._run_step(run_id, step_id)
  File "backend/app/workflow/engine.py", line 1285, in _run_step
    artifact.content.get("normalized_bundle", {}).get("manifest", {}).get("experiment_id")
AttributeError: 'NoneType' object has no attribute 'get'
```

Root cause: workflow recovery assumed `normalized_bundle` was a mapping. Persisted cancelled runtime-repair candidates (`art_b6eeec0993c6`, `art_e541a17a3204`) correctly contained `normalized_bundle: null`; the explicit null propagated through `dict.get` and the next `.get("manifest")` crashed.

Classification: **workflow recovery wiring defect with an unmodelled nullable persisted candidate field** — not a missing checkpoint field, Artifact lookup, candidate/result lookup, or provider response.

The repair adds explicit candidate ownership (`experiment_id`, `task_artifact_id`) and normalization status, and reconstructs lineage from those fields with bounded legacy plan linkage. It does not suppress the error with an early return.

## Contract-preserving recovery and verification

The persisted-checkpoint regression covers checkpoint reload → experiment retry → the former nullable lookup → normal repaired completion. It uses an isolated test repository, not a production Artifact.

Formal recovery additionally found that the Harness executed only `seeds[0]` while declaring five seeds. The Harness now executes all formal seeds, preserves per-seed metrics, aggregates mean/std, and the formal audit requires exact seed-result lineage. Audit rejection now flows into bounded diagnose → repair → smoke → retry rather than being passed downstream.

- Focused regression: `110 passed`.
- Full backend regression: `519 passed, 2 skipped`.
- Dataset contract unchanged: `dataset_32db1f45ec300996`, `D:\Gewu\datasets\fashionmnist`.
- Bound seeds: `[42, 123, 456, 789, 1024]`.

## Validated scientific result

Accepted Bundle: `art_3c0d6b2ce620`. Validated real Result: `art_40c3b329e885`.

- CNN mean held-out accuracy: **91.362%**
- Capacity-matched MLP mean accuracy: **88.888%**
- Difference: **+2.474 percentage points** for CNN
- Parameters: CNN 421,642; MLP 402,570 (within declared <5% tolerance)
- All five bound seeds completed on the local CUDA GPU and CNN exceeded MLP on every seed.
- Independent audit: `passed`, `is_real_experiment: true`.

## Scientific loop completion

The formal pipeline completed feedback revision, Qwen primary scientific analysis, DeepSeek independent review, disagreement detection, synthesis, scientific conclusion, and hypothesis-evolution decision. All corresponding artifacts, the final report, and post-export research state are persisted under the same Run.

The final bounded conclusion supports the small CNN over the MLP on this verified Fashion-MNIST setup. Limitations remain explicit: parameter count is only a capacity proxy; external validity is limited to this dataset/model scale; and no deduplication ablation was run.

## Stop condition

Step 7 is complete. STOP. No Round 6 action was started.
