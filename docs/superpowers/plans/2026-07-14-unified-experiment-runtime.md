# Unified Local GPU and SSH Experiment Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute one auditable experiment bundle through either the backend machine's GPU or a generic SSH GPU server, with stable experiment/result IDs, dependency preflight, file-based metrics, and truthful real-experiment status.

**Architecture:** `ExperimentBundle` is a provider-neutral manifest and file set. `LocalGpuExperimentProvider` and `RemoteGpuExperimentProvider` deploy the same bundle into `<run_id>/<experiment_id>`, run a structured argument list, validate the result file, and return the same result contract. Workflow artifacts and UI use `experiment_1` and `experiment_1_result` consistently.

**Tech Stack:** Python 3.12, Pydantic 2, subprocess, Windows OpenSSH/generic POSIX SSH target, PyTorch CUDA preflight, FastAPI, React 19, TypeScript, pytest.

## Global Constraints

- This plan depends on `2026-07-14-supervisor-skill-runtime.md` being complete.
- GitHub local deployment is primary; generic SSH supports AutoDL and self-hosted servers.
- No ECS-specific configuration or cloud API is permitted.
- Local GPU means the machine running the backend process.
- Experiment IDs are Run-scoped: `experiment_1`, `experiment_2`, and so on.
- Result IDs are stable: `experiment_1_result` corresponds to `experiment_1`.
- Parameters, seeds, ablations, and attempts do not enter IDs.
- Providers do not install dependencies without explicit user action.
- File-based JSON is the result source; stdout is a log only.
- GPU-required experiments cannot silently run on CPU and remain marked as GPU experiments.
- Do not overwrite unrelated worktree changes or existing user experiment files.

---

## File Structure

Create:

- `backend/app/models/experiment.py`: manifest, bundle, environment, attempt, and result models.
- `backend/app/workflow/experiment_bundle.py`: IDs, normalization, safe paths, and hashes.
- `backend/app/providers/experiment_runtime.py`: shared preflight, result validation, and environment helpers.
- `tests/backend/test_experiment_bundle.py`: manifest and ID tests.
- `tests/backend/test_experiment_runtime.py`: shared validation and CUDA preflight tests.
- `tests/backend/test_gpu_smoke.py`: opt-in real CUDA integration test.
- `requirements/base.txt`: backend application dependencies.
- `requirements/literature.txt`: local literature dependencies.
- `requirements/experiment-common.txt`: experiment utilities excluding platform-specific PyTorch wheels.

Modify:

- `backend/app/agents/experiment.py`: generate the bundle contract and requirements.
- `backend/app/workflow/experiment_code.py`: migrate compatibility normalization to ExperimentBundle.
- `backend/app/providers/experiment.py`: local and SSH deployment/execution.
- `backend/app/workflow/engine.py`: assign IDs, save bundle, and save stable result.
- `backend/app/storage/repository.py`: Run-scoped experiment sequence helper.
- `backend/app/config.py`: runtime fields and readiness semantics.
- `backend/app/api/providers.py`: CUDA and SSH preflight diagnostics.
- `frontend/src/api/types.ts`: experiment manifest/result/preflight types.
- `frontend/src/components/ProjectSettingsModal.tsx`: device-index help and remote diagnostics.
- `frontend/src/components/ExperimentPanel.tsx`: experiment/result relation and environment display.
- `frontend/tests/ui-contract.test.mjs`: experiment UI contracts.
- `tests/backend/test_experiment_code.py`: compatibility tests.
- `tests/backend/test_experiment_provider.py`: provider tests.
- `tests/backend/test_workflow_engine.py`: Artifact relationship tests.
- `tests/backend/test_api.py`: configuration/preflight tests.
- `.env.example`: generic local/SSH settings.
- `README.md`: clone, install, local GPU, and AutoDL/self-hosted SSH instructions.
- `docs/runbook.md`: experiment troubleshooting.

### Task 1: Define Stable Experiment IDs and Manifest Models

**Files:**
- Create: `backend/app/models/experiment.py`
- Create: `backend/app/workflow/experiment_bundle.py`
- Create: `tests/backend/test_experiment_bundle.py`
- Modify: `backend/app/storage/repository.py`

**Interfaces:**
- Produces: `ExperimentManifest`, `ExperimentBundle`, `ExperimentEnvironment`, `ExperimentAttempt`, `ExperimentResult`.
- Produces: `Repository.next_experiment_id(run_id: str) -> str`.
- Produces: `result_id_for(experiment_id: str) -> str`.

- [ ] **Step 1: Write failing ID and model tests**

```python
def test_experiment_and_result_ids_are_stable_within_run(repository, run):
    assert repository.next_experiment_id(run.id) == "experiment_1"
    repository.add_artifact(
        run.id,
        "experiment_task",
        "Experiment 1",
        {"experiment_id": "experiment_1"},
        "experiment_task",
        "Experiment Agent",
    )
    assert repository.next_experiment_id(run.id) == "experiment_2"
    assert result_id_for("experiment_1") == "experiment_1_result"
```

Add tests that parameters never change IDs and separate Runs both start at `experiment_1`.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_experiment_bundle.py -q`

Expected: FAIL because models and helpers do not exist.

- [ ] **Step 3: Implement strict models**

```python
class ExperimentManifest(BaseModel):
    schema_version: int = 1
    run_id: str
    experiment_id: str
    result_id: str
    entrypoint: str = "train.py"
    python_args: list[str]
    requirements_file: str = "requirements.txt"
    requires_gpu: bool = False
    expected_metrics: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    seeds: list[int] = Field(default_factory=list)


class ExperimentBundle(BaseModel):
    manifest: ExperimentManifest
    files: list[ExperimentFile]
    requirements: list[str] = Field(default_factory=list)
```

Validate `experiment_id` against `^experiment_[1-9][0-9]*$` and require `result_id == f"{experiment_id}_result"`.

- [ ] **Step 4: Implement repository sequence**

Count unique `experiment_id` values from `experiment_task` artifacts. Do not use random Artifact IDs as the experiment sequence.

- [ ] **Step 5: Run model tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_experiment_bundle.py tests/backend/test_repository.py -q`

Expected: PASS.

- [ ] **Step 6: Commit experiment models**

```powershell
git add backend/app/models/experiment.py backend/app/workflow/experiment_bundle.py backend/app/storage/repository.py tests/backend/test_experiment_bundle.py tests/backend/test_repository.py
git commit -m "feat: define stable experiment bundle IDs"
```

### Task 2: Generate a Complete Experiment Bundle

**Files:**
- Modify: `backend/app/agents/experiment.py`
- Modify: `backend/app/workflow/experiment_code.py`
- Modify: `tests/backend/test_experiment_code.py`
- Modify: `tests/backend/test_agents_use_llm.py`

**Interfaces:**
- Consumes: `run_id`, `experiment_id`, normalized plan, and configured Python command.
- Produces: `ExperimentAgent.generate_bundle(...) -> ExperimentBundle`.

- [ ] **Step 1: Write failing bundle-generation tests**

```python
def test_generate_bundle_contains_manifest_code_and_requirements(recording_llm):
    bundle = ExperimentAgent(provider, recording_llm).generate_bundle(
        run_id="run_1",
        experiment_id="experiment_1",
        plan=PLAN,
        task=TASK,
        instructions="Use experiment-implementation.",
        python_command="python",
    )
    assert bundle.manifest.result_id == "experiment_1_result"
    assert bundle.manifest.python_args[-1] == "results/experiment_1_result.json"
    assert any(file.path == "train.py" for file in bundle.files)
    assert "torch" in bundle.requirements
```

Add tests that every required argparse option appears in `python_args`, result writing is required, and parent paths are rejected.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_experiment_code.py tests/backend/test_agents_use_llm.py -q`

Expected: FAIL because only the legacy code artifact is generated.

- [ ] **Step 3: Change the Qwen output contract**

Request this schema:

```python
{
    "entrypoint": "train.py",
    "files": [{"path": "train.py", "content": "complete Python source"}],
    "python_args": ["--seed", "42", "--output", "results/experiment_1_result.json"],
    "requirements": ["numpy", "torch", "torchvision"],
    "requires_gpu": True,
    "expected_metrics": ["test_accuracy"],
    "parameters": {},
    "seeds": [42],
}
```

The generated source must write a JSON object to the output path with top-level `run_id`, `experiment_id`, `result_id`, and nested `metrics`. The first three values must come from manifest-provided command arguments rather than source-code constants. Printing JSON remains optional and stdout is never used as the metrics source.

- [ ] **Step 4: Keep a compatibility adapter**

`normalize_experiment_code()` remains temporarily for existing tests and saved artifacts, but delegates safe path validation to `experiment_bundle.py` and converts legacy `command` into an argument list only when it can be parsed safely.

- [ ] **Step 5: Run generation tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_experiment_code.py tests/backend/test_agents_use_llm.py -q`

Expected: PASS.

- [ ] **Step 6: Commit bundle generation**

```powershell
git add backend/app/agents/experiment.py backend/app/workflow/experiment_code.py tests/backend/test_experiment_code.py tests/backend/test_agents_use_llm.py
git commit -m "feat: generate complete experiment bundles"
```

### Task 3: Implement Shared Preflight and Result Validation

**Files:**
- Create: `backend/app/providers/experiment_runtime.py`
- Create: `tests/backend/test_experiment_runtime.py`

**Interfaces:**
- Produces: `build_python_command(python: str, bundle: ExperimentBundle) -> list[str]`.
- Produces: `validate_result_file(path: Path, manifest: ExperimentManifest) -> dict[str, Any]`.
- Produces: `cuda_probe_command(python: str) -> list[str]` and `parse_cuda_probe(stdout: str) -> CudaProbe`.

- [ ] **Step 1: Write failing command, result, and CUDA tests**

```python
def test_python_path_with_spaces_stays_one_argument(bundle):
    command = build_python_command(r"C:\Program Files\Python\python.exe", bundle)
    assert command[0] == r"C:\Program Files\Python\python.exe"
    assert command[1] == "train.py"


def test_result_file_must_match_manifest_ids(tmp_path, manifest):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"experiment_id": "experiment_2", "metrics": {"accuracy": 0.9}}))
    with pytest.raises(RuntimeError, match="EXPERIMENT_RESULT_ID_MISMATCH"):
        validate_result_file(path, manifest)
```

Add missing file, invalid JSON, absent metric, non-finite number, and CUDA unavailable tests.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_experiment_runtime.py -q`

Expected: FAIL with module import error.

- [ ] **Step 3: Implement file-based validation**

Require result shape:

```json
{
  "run_id": "run_1",
  "experiment_id": "experiment_1",
  "result_id": "experiment_1_result",
  "metrics": {"test_accuracy": 0.91}
}
```

Reject NaN and Infinity using `math.isfinite` for numeric metrics.

- [ ] **Step 4: Implement a JSON CUDA probe**

The probe prints one JSON object containing `available`, `device_count`, `device_names`, `torch_version`, and `torch_cuda`. Parsing failures return `EXPERIMENT_CUDA_PROBE_FAILED`.

- [ ] **Step 5: Run shared runtime tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_experiment_runtime.py -q`

Expected: PASS.

- [ ] **Step 6: Commit shared runtime helpers**

```powershell
git add backend/app/providers/experiment_runtime.py tests/backend/test_experiment_runtime.py
git commit -m "feat: validate experiment environment and results"
```

### Task 4: Refactor LocalGpuExperimentProvider

**Files:**
- Modify: `backend/app/providers/experiment.py`
- Modify: `tests/backend/test_experiment_provider.py`

**Interfaces:**
- Consumes: `ExperimentBundle`.
- Produces: `run(task: dict, bundle: ExperimentBundle | None = None) -> dict` with stable IDs and environment.

- [ ] **Step 1: Write failing local-run tests**

```python
def test_local_runner_uses_isolated_directory_and_cuda_environment(tmp_path, monkeypatch, bundle):
    provider = LocalGpuExperimentProvider(local_settings(tmp_path, cuda_devices="0"))
    result = provider.run({"run_id": "run_1"}, bundle)
    assert result["experiment_id"] == "experiment_1"
    assert result["result_id"] == "experiment_1_result"
    assert result["workdir"].endswith(r"run_1\experiment_1")
    assert captured_env["CUDA_VISIBLE_DEVICES"] == "0"
```

Add tests for dependency failure, GPU-required CUDA failure, file-based result parsing with empty stdout, log persistence, and attempt append.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_experiment_provider.py -q`

Expected: FAIL because current provider writes to the shared root, ignores local CUDA config, and parses stdout.

- [ ] **Step 3: Deploy to an isolated directory**

Resolve the configured root once, then append safe `run_id` and `experiment_id`. Write `manifest.json`, `requirements.txt`, source files, `environment.json`, logs, and results only inside that directory.

- [ ] **Step 4: Run preflight and process with explicit environment**

```python
env = os.environ.copy()
if self.settings.local_gpu_cuda_visible_devices:
    env["CUDA_VISIBLE_DEVICES"] = self.settings.local_gpu_cuda_visible_devices
completed = subprocess.run(
    command,
    cwd=str(experiment_dir),
    env=env,
    capture_output=True,
    text=True,
    timeout=self.settings.experiment_timeout_seconds,
    check=False,
)
```

Use the same environment for the CUDA probe. Check imports without installing them.

- [ ] **Step 5: Read and validate the result file**

Write stdout/stderr to `logs/experiment_1.log`; parse only `results/experiment_1_result.json`. Set `is_real_experiment=true` only after all validations pass.

- [ ] **Step 6: Run local provider tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_experiment_provider.py tests/backend/test_experiment_runtime.py -q`

Expected: PASS.

- [ ] **Step 7: Commit local runner**

```powershell
git add backend/app/providers/experiment.py tests/backend/test_experiment_provider.py
git commit -m "feat: run isolated local GPU experiment bundles"
```

### Task 5: Refactor Generic SSH Experiment Provider

**Files:**
- Modify: `backend/app/providers/experiment.py`
- Modify: `tests/backend/test_experiment_provider.py`
- Modify: `tests/backend/test_api.py`

**Interfaces:**
- Consumes: the same `ExperimentBundle` as Task 4.
- Produces: remote deployment hashes, result, log, and environment in the common result contract.

- [ ] **Step 1: Write failing generic SSH tests**

```python
def test_remote_runner_deploys_run_scoped_bundle_and_fetches_result(monkeypatch, bundle):
    provider = RemoteGpuExperimentProvider(remote_settings(project_dir="/root/autodl-tmp/project"))
    result = provider.run({"run_id": "run_1"}, bundle)
    assert "/root/autodl-tmp/project/run_1/experiment_1" in captured_remote_commands
    assert result["result_id"] == "experiment_1_result"
    assert result["provider"] == "remote_gpu"
```

Add tests for SSH timeout, Python failure, CUDA failure, hash mismatch, missing dependency, result fetch failure, and absence of ECS-specific keys.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_experiment_provider.py tests/backend/test_api.py -q`

Expected: FAIL because the current SSH runner uses a shared directory and stdout-last-line metrics.

- [ ] **Step 3: Harden SSH base arguments**

Build arguments without shell concatenation for local parameters:

```python
["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-p", str(port), *key_args, remote]
```

Remote shell paths and arguments must use `shlex.quote`. Host-key policy remains the user's normal OpenSSH configuration; do not disable verification.

- [ ] **Step 4: Deploy bundle and verify hashes**

Continue using stdin to a small remote Python writer, but include SHA-256 for every file and return a JSON deployment receipt. Reject any normalized path outside the experiment directory.

- [ ] **Step 5: Preflight and execute remotely**

Probe remote Python imports, PyTorch CUDA, and `nvidia-smi`. If dependencies are missing, return:

```text
<remote-python> -m pip install -r <remote-experiment-dir>/requirements.txt
```

Do not execute the command automatically.

- [ ] **Step 6: Fetch explicit files**

Fetch `results/experiment_1_result.json`, `logs/experiment_1.log`, and `environment.json` with explicit remote `cat` calls and parse each response independently. Do not infer metrics from the training command's stdout.

- [ ] **Step 7: Run SSH tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_experiment_provider.py tests/backend/test_api.py -q`

Expected: PASS with mocked subprocess calls; no real server is required.

- [ ] **Step 8: Commit generic SSH runner**

```powershell
git add backend/app/providers/experiment.py tests/backend/test_experiment_provider.py tests/backend/test_api.py
git commit -m "feat: run experiment bundles over generic ssh"
```

### Task 6: Integrate Stable Experiment Artifacts into WorkflowEngine

**Files:**
- Modify: `backend/app/workflow/engine.py`
- Modify: `backend/app/agents/experiment.py`
- Modify: `tests/backend/test_workflow_engine.py`

**Interfaces:**
- Consumes: `Repository.next_experiment_id()` and `ExperimentAgent.generate_bundle()`.
- Produces: `experiment_task`, `experiment_bundle`, and `experiment_result` Artifacts sharing stable IDs.

- [ ] **Step 1: Write failing workflow relationship test**

```python
def test_workflow_links_experiment_bundle_and_result_by_stable_ids(engine, selected_run):
    run = engine.run_step(selected_run.id, "research_plan")
    run = engine.run_step(run.id, "experiment_task")
    task = latest(run, "experiment_task").content
    bundle = latest(run, "experiment_bundle").content
    assert task["experiment_id"] == "experiment_1"
    assert bundle["manifest"]["result_id"] == "experiment_1_result"
```

Continue through run analysis and assert the result carries the same IDs and `parent_artifact_id` points to the bundle Artifact.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_workflow_engine.py -q`

Expected: FAIL because the engine saves legacy `experiment_code` without stable IDs.

- [ ] **Step 3: Save the new Artifact sequence**

At `experiment_task`, allocate one ID, add it to task, generate the bundle, save `experiment_task` then `experiment_bundle`. At run analysis, pass the latest bundle to the selected Provider and save `experiment_result` with the bundle Artifact as parent.

- [ ] **Step 4: Preserve rerun semantics**

Rerunning `experiment_run_analysis` reuses the existing experiment ID and appends an attempt. Rerunning from `experiment_task` after downstream artifacts are removed allocates the next ID only when the previous task Artifact remains locked; otherwise it rebuilds `experiment_1` for that Run.

- [ ] **Step 5: Run workflow and report guard tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_workflow_engine.py tests/backend/test_report_export.py -q`

Expected: PASS.

- [ ] **Step 6: Commit workflow integration**

```powershell
git add backend/app/workflow/engine.py backend/app/agents/experiment.py tests/backend/test_workflow_engine.py tests/backend/test_report_export.py
git commit -m "feat: link experiment bundles and results"
```

### Task 7: Improve Local and Remote Configuration Diagnostics

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/api/providers.py`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/components/ProjectSettingsModal.tsx`
- Modify: `frontend/src/components/ExperimentPanel.tsx`
- Modify: `frontend/tests/ui-contract.test.mjs`
- Modify: `tests/backend/test_config.py`
- Modify: `tests/backend/test_api.py`

**Interfaces:**
- Produces: preflight response fields `python_version`, `cuda_available`, `device_count`, `device_names`, and `dependency_status`.

- [ ] **Step 1: Write failing backend diagnostic tests**

Assert local test rejects `cuda_visible_devices="5070"` with `LOCAL_GPU_DEVICE_INDEX_INVALID`, accepts `0` and `0,1`, and returns the CUDA probe fields. Assert remote diagnostics use only generic SSH fields.

- [ ] **Step 2: Write failing frontend contract tests**

Require the settings UI to say “CUDA 设备索引” and render device names returned by preflight. Require ExperimentPanel to render `experiment_1 → experiment_1_result`.

- [ ] **Step 3: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_config.py tests/backend/test_api.py -q`

Expected: FAIL on new diagnostics.

Run from `frontend/`: `node --test tests/ui-contract.test.mjs`

Expected: FAIL on new UI contracts.

- [ ] **Step 4: Implement stable diagnostics**

Validate CUDA device strings with `^\d+(,\d+)*$`. Run the configured Python probe for local settings and the SSH probe for remote settings. Return stdout/stderr tails only on failure.

- [ ] **Step 5: Update the UI**

Use the existing form layout. Show provider, Python, CUDA availability, device names, project directory, and verification result. Do not add ECS copy or fields.

- [ ] **Step 6: Run backend and frontend tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_config.py tests/backend/test_api.py -q`

Expected: PASS.

Run from `frontend/`: `node --test tests/ui-contract.test.mjs`

Expected: PASS.

Run from `frontend/`: `pnpm run build`

Expected: PASS.

- [ ] **Step 7: Commit diagnostics UI**

```powershell
git add backend/app/config.py backend/app/api/providers.py frontend/src/api/types.ts frontend/src/components/ProjectSettingsModal.tsx frontend/src/components/ExperimentPanel.tsx frontend/tests/ui-contract.test.mjs tests/backend/test_config.py tests/backend/test_api.py
git commit -m "feat: verify local and ssh experiment environments"
```

### Task 8: Add Opt-In Real GPU Smoke Test

**Files:**
- Create: `tests/backend/test_gpu_smoke.py`
- Create: `pytest.ini`

**Interfaces:**
- Produces: pytest marker `gpu`.
- Consumes: current configured Python environment and CUDA device 0.

- [ ] **Step 1: Add the smoke test**

```python
@pytest.mark.gpu
def test_real_cuda_tensor_computation():
    import torch

    assert torch.cuda.is_available()
    tensor = torch.randn((256, 256), device="cuda")
    result = tensor @ tensor
    torch.cuda.synchronize()
    assert result.device.type == "cuda"
    assert torch.isfinite(result).all().item()
```

- [ ] **Step 2: Register the marker**

```ini
[pytest]
markers =
    gpu: requires a real CUDA-capable PyTorch environment
```

- [ ] **Step 3: Verify default tests exclude the marker in CI instructions**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend -m "not gpu" -q`

Expected: all non-GPU backend tests PASS.

- [ ] **Step 4: Verify the current machine GPU**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_gpu_smoke.py -m gpu -q`

Expected on the current RTX 5070 machine: PASS.

- [ ] **Step 5: Commit GPU integration test**

```powershell
git add tests/backend/test_gpu_smoke.py pytest.ini
git commit -m "test: add opt-in CUDA smoke coverage"
```

### Task 9: Package GitHub Dependencies and Documentation

**Files:**
- Create: `requirements/base.txt`
- Create: `requirements/literature.txt`
- Create: `requirements/experiment-common.txt`
- Modify: `backend/requirements.txt`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/runbook.md`

**Interfaces:**
- Produces: clone-to-run instructions for Windows and Linux.
- Produces: generic AutoDL/self-hosted SSH setup instructions.

- [ ] **Step 1: Add dependency-file contract tests**

Add a repository test that asserts the three files exist, base includes FastAPI/uvicorn/Pydantic/httpx/python-multipart, literature includes pypdf, and experiment-common includes numpy/torchinfo but does not hard-code an incompatible CUDA wheel URL.

- [ ] **Step 2: Verify red state**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_config.py -q`

Expected: FAIL because split dependency files do not exist.

- [ ] **Step 3: Create dependency profiles**

Keep `backend/requirements.txt` as a compatibility aggregate using `-r ../requirements/...` entries or update README to install the three files explicitly. Do not include user keys or runtime data.

- [ ] **Step 4: Document exact local flow**

Include clone, Python 3.12 venv creation, backend requirements, frontend `pnpm install`, PyTorch platform selection, local config test, and startup commands.

- [ ] **Step 5: Document exact SSH flow**

Include SSH key setup, remote venv, remote project directory, remote Python path, requirements installation, config test, and experiment execution. Use AutoDL as an example only; keep configuration generic.

- [ ] **Step 6: Run full verification**

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend -m "not gpu" -q`

Expected: PASS.

Run: `.\.venv\Scripts\python.exe -m pytest tests/backend/test_gpu_smoke.py -m gpu -q`

Expected on configured GPU machine: PASS.

Run from `frontend/`: `node --test tests/ui-contract.test.mjs`

Expected: PASS.

Run from `frontend/`: `node --test tests/presentation.test.ts`

Expected: PASS.

Run from `frontend/`: `pnpm run build`

Expected: PASS.

- [ ] **Step 7: Commit packaging and docs**

```powershell
git add requirements backend/requirements.txt .env.example README.md docs/runbook.md tests/backend/test_config.py
git commit -m "docs: package local and generic ssh setup"
```
