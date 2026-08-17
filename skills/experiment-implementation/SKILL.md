---
name: experiment-implementation
description: Build a complete provider-neutral experiment bundle from an accepted plan.
allowed-tools: read_run, read_artifact, build_experiment_bundle
---

# Experiment Implementation

Use the configured Qwen Provider to generate the scientific implementation for one provider-neutral `ExperimentBundle`. The system compiles the accepted Plan, Task, frozen contract, dataset binding, seeds, parameters, identifiers, output locations, and smoke/full mode into a deterministic runtime contract and Harness.

## Code contract

- **Mandatory pre-return check:** detach every PyTorch tensor before converting it to NumPy. Safe equivalent forms are allowed, including `tensor.detach().cpu().numpy()` and a detached temporary moved to CPU before `.numpy()`; never convert a live/autograd tensor directly.
- Return exactly one `{ "path": "train.py", "content_lines": [...] }` item. Put exactly one physical Python line in each string, using an empty string for a blank line. Never return a `content` field, embedded newline characters, literal `\n`, Markdown fences, fragments, ellipses, or explanations.
- Do not return `entrypoint`, identifiers, output paths, or `python_args`; the backend/Harness adds those protocol fields deterministically.
- Make every Python file syntactically valid under `compile(source, path, "exec")`.
- Make the entrypoint accept `--run-id`, `--experiment-id`, `--result-id`, `--output`, `--seed`, and `--smoke-test`. Write a finite JSON object containing a nested `metrics` object at the runtime-provided temporary output path. Every value directly under `metrics` must be one finite number (flatten comparison outputs into distinct metric keys); nested metric objects and boolean values are invalid. Metric keys may be constructed dynamically. The system Harness owns the final result envelope, identifiers, and final output location.
- Declare every third-party import in `requirements`. Do not assume dependencies are installed, install packages, download datasets at runtime, or execute the bundle in this step.

## Experiment contract

- Implement the accepted plan exactly. Use the runtime-provided `DATA_ROOT` and dataset card without silently substituting another dataset or hard-coding a dataset root. Never download data at runtime.
- If the verified local Fashion-MNIST contract lists `train-images-idx3-ubyte.gz`, `train-labels-idx1-ubyte.gz`, `t10k-images-idx3-ubyte.gz`, and `t10k-labels-idx1-ubyte.gz` directly under `DATA_ROOT`, implement a local gzip IDX reader over exactly those files. Do not use `torchvision.datasets.FashionMNIST`, which expects a different `FashionMNIST/raw` cache layout; do not create, copy, move, or download data to imitate that layout.
- Implement a real experimental intervention. Baseline and variant must differ only in the factor under test; never construct and train identical baseline and "improved" models under different variable names.
- Match model input shape, class count, preprocessing, metrics, repetition count, seeds, and every Plan parameter to the accepted Plan. The Bundle manifest must echo the Plan's exact `seeds`, `parameters`, and expected metrics; schema examples such as `[42]` or `{}` are never defaults. Read system-bound seeds and parameters from the Harness environment when needed; do not redefine frozen values.
- When GPU is required, check CUDA availability, move the model and tensors to the CUDA device, and synchronize before recording results.

Before returning, inspect the complete bundle against every item above. Return only the JSON bundle.

## Harness gates

- Use documented APIs from the declared dependency versions. Prefer `torch.nn.functional` for functional activations such as `softplus`; do not invent top-level framework functions.
- Keep executable work inside `main()` so importing or compiling the module does not start training.
- Make model construction and one forward/loss/backward/update batch valid before the full experiment is eligible to run.
- Before converting any PyTorch tensor to NumPy, detach it first. `tensor.detach().cpu().numpy()` and equivalent detached temporary forms are valid; direct `.cpu().numpy()` on a live/autograd tensor is forbidden.
- For every epoch-based training loop, emit exactly one JSON line at epoch end with `event: "epoch_end"`, `variant`, `seed`, one-based `epoch`, `total_epochs`, and mean `loss`; call `print(..., flush=True)` so the harness can observe real progress. Emit `variant_start`, `seed_start`, and `variant_end` JSON events when those dimensions exist. Do not rely on tqdm-only terminal rendering.
- When repairing an existing Bundle, make the smallest safe source change required by the supplied traceback. Preserve the accepted manifest dataset, parameters, seeds, expected metrics, identifiers, and GPU requirement exactly.
- Treat every validator rejection as actionable repair feedback. Return another complete corrected source file rather than explanations or a weakened contract.

## Ownership boundary

You own only scientific implementation: model, loss, optimizer, data processing, training, evaluation, and the metrics values. The system owns deterministic runtime protocol: dataset root/identity, frozen seeds and parameters, run/task/result identity, CUDA/runtime context, smoke/full invocation, output destination, and final result envelope. Do not attempt to rewrite, replace, or bypass any system-owned protocol field during generation or repair.
