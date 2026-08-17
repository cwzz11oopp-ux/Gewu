# Round 5 Fashion-MNIST Emergency Fixes

Canonical Run ID: `round5_fashionmnist_stability_001`  
Official Project Root: `D:\Gewu`

## EF-001 — P0 Experiment Skill contract omission

### File and location

`D:\Gewu\skills\experiment-implementation\SKILL.md`, Code contract and Harness gates.

### Original problem

The first two formal `experiment_task` recovery cycles exhausted their bounded Supervisor retry budgets solely on `EXPERIMENT_CODE_TENSOR_NUMPY_UNSAFE`. The normalizer rejects a source line containing `.cpu().numpy()` unless it is written as `.detach().cpu().numpy()`. The Experiment Skill required valid training code but did not state that repository-specific rule, so Qwen repeatedly produced candidates rejected before execution.

### Why this was P0 and blocking

No accepted `ExperimentBundle` means no command can be launched, no real Fashion-MNIST can be loaded by the experiment, and no Critic/Writer result stage can occur. The initial workflow had already exhausted its native recovery budget twice.

### Minimal change

Only the existing, general-purpose `experiment-implementation` Skill was clarified. No Fashion-MNIST-specific route, template, architecture, learning rate, seed, metric, state, data file, experiment code, test threshold, or result was introduced.

```diff
 ## Code contract
+- **Mandatory pre-return check:** inspect every physical source line. A line containing `.cpu().numpy()` is valid only when that same conversion is written exactly as `.detach().cpu().numpy()`. Rewrite every other occurrence before returning the bundle.
 ...
 ## Harness gates
 - Make model construction and one forward/loss/backward/update batch valid before the full experiment is eligible to run.
+- Before converting any PyTorch tensor to NumPy, use `tensor.detach().cpu().numpy()`; never emit `.cpu().numpy()` directly, including for evaluation outputs or diagnostics.
```

### Validation

- Targeted validation before the final recovery: `103 passed` for `tests/backend/test_workflow_skills.py` and `tests/backend/test_experiment_code.py`.
- Final backend regression: `492 passed, 2 skipped`.
- The third formal recovery showed the first two candidate failures changed from Tensor conversion to missing requirement and smoke-test-contract failures, demonstrating the amended Skill entered the fresh runtime package. It still did not complete the Bundle contract.

### Behavioral impact

The Skill now instructs every general experiment Bundle author to perform the same safety check the existing validator requires. Validators were not weakened, and no result was fabricated. The patch did not resolve the broader generated-Bundle convergence failure; Round 5 remains not passed.

### Git

No commit, push, or pull request was created.
