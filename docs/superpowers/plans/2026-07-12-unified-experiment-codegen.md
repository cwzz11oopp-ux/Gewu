# Unified Experiment Code Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a real `experiment_code` artifact from the existing `experiment-bridge` Skill, then deploy and run it through both `local_gpu` and SSH-backed `remote_gpu`.

**Architecture:** `ExperimentAgent` generates and normalizes code artifacts using `LLMProvider.generate_json()`. `WorkflowEngine` stores `experiment_task` and `experiment_code` separately, then passes both to providers during `experiment_run_analysis`. Providers only deploy and run generated code; they do not decide scientific content.

**Tech Stack:** FastAPI backend, Pydantic models, repository artifacts, subprocess SSH execution, React/Vite frontend, pytest backend tests, TypeScript build.

## Global Constraints

- Reuse `skills/experiment-bridge/SKILL.md`; do not add a new Skill for the first implementation.
- Generated file paths must be relative and must not contain `..`.
- First implementation entrypoint is `train.py`.
- Generated `train.py` must accept `--seed` and `--output`.
- Local deployment writes only under `LOCAL_EXPERIMENT_WORKDIR`.
- Remote deployment writes only under `REMOTE_GPU_PROJECT_DIR`.
- Remote config test must perform real SSH/Python/project-dir validation.
- Do not install dependencies automatically.
- Keep legacy `provider.run(task)` behavior working when no generated code artifact is supplied.

---

## File Structure

- Create `backend/app/workflow/experiment_code.py`
  - Owns schema normalization, default mock code, generated file path validation, and deployment command normalization.
- Modify `backend/app/agents/experiment.py`
  - Adds `generate_code(plan, task, instructions)` and passes Skill instructions to Qwen.
- Modify `backend/app/workflow/engine.py`
  - Saves `experiment_code` artifacts during `experiment_task`.
  - Passes latest `experiment_code` into `experiment_run_analysis`.
- Modify `backend/app/providers/experiment.py`
  - Extends provider protocol to `run(task, code=None)`.
  - Adds local generated-file deployment.
  - Adds remote generated-file deployment over SSH.
  - Adds remote connection validation helper.
- Modify `backend/app/api/providers.py`
  - Uses real remote SSH readiness validation in `/api/settings/experiment/test`.
- Modify `frontend/src/api/types.ts`
  - Adds remote diagnostic fields to provider/test result types.
- Modify `frontend/src/components/ProjectSettingsModal.tsx`
  - Displays remote diagnostics and catches failed save/test errors.
- Test files:
  - `tests/backend/test_experiment_code.py`
  - `tests/backend/test_workflow_engine.py`
  - `tests/backend/test_experiment_provider.py`
  - `tests/backend/test_api.py`
  - Existing frontend build command covers TypeScript regressions.

---

### Task 1: Experiment Code Contract

**Files:**
- Create: `backend/app/workflow/experiment_code.py`
- Test: `tests/backend/test_experiment_code.py`

**Interfaces:**
- Produces: `normalize_experiment_code(raw: dict, task: dict, python_command: str) -> dict`
- Produces: `validate_relative_file_path(path: str) -> str`
- Produces: `default_mock_experiment_code(task: dict, python_command: str) -> dict`
- Consumes: task dicts containing `seed`, `metrics_path`, `log_path`

- [ ] **Step 1: Write failing tests for schema defaults and path rejection**

Add `tests/backend/test_experiment_code.py`:

```python
import pytest

from backend.app.workflow.experiment_code import (
    default_mock_experiment_code,
    normalize_experiment_code,
    validate_relative_file_path,
)


def test_normalize_experiment_code_rejects_parent_directory_file_path():
    raw = {
        "entrypoint": "train.py",
        "files": [{"path": "../train.py", "content": "print('bad')"}],
    }
    task = {"seed": 7, "metrics_path": "results/run_seed_7.json", "log_path": "logs/run_seed_7.log"}

    with pytest.raises(ValueError, match="EXPERIMENT_CODE_PATH_INVALID"):
        normalize_experiment_code(raw, task, "python")


def test_normalize_experiment_code_builds_command_and_keeps_files():
    raw = {
        "entrypoint": "train.py",
        "files": [{"path": "train.py", "content": "print('{}')"}],
        "assumptions": ["uses local toy data"],
    }
    task = {"seed": 7, "metrics_path": "results/run_seed_7.json", "log_path": "logs/run_seed_7.log"}

    code = normalize_experiment_code(raw, task, "python")

    assert code["entrypoint"] == "train.py"
    assert code["command"] == "python train.py --seed 7 --output results/run_seed_7.json"
    assert code["metrics_path"] == "results/run_seed_7.json"
    assert code["log_path"] == "logs/run_seed_7.log"
    assert code["files"] == [{"path": "train.py", "content": "print('{}')"}]


def test_default_mock_experiment_code_is_executable_contract():
    task = {"seed": 7, "metrics_path": "results/local_seed_7.json", "log_path": "logs/local_seed_7.log"}

    code = default_mock_experiment_code(task, "python")

    assert code["entrypoint"] == "train.py"
    assert code["files"][0]["path"] == "train.py"
    assert "--seed 7" in code["command"]
    assert "--output results/local_seed_7.json" in code["command"]
    assert "json.dumps(metrics)" in code["files"][0]["content"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend/test_experiment_code.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.app.workflow.experiment_code'`.

- [ ] **Step 3: Implement contract module**

Create `backend/app/workflow/experiment_code.py`:

```python
from __future__ import annotations

import posixpath
from typing import Any


def validate_relative_file_path(path: str) -> str:
    normalized = posixpath.normpath(str(path).replace("\\", "/"))
    if normalized in {"", "."} or normalized.startswith("../") or normalized == ".." or posixpath.isabs(normalized):
        raise ValueError(f"EXPERIMENT_CODE_PATH_INVALID:{path}")
    return normalized


def normalize_experiment_code(raw: dict[str, Any], task: dict[str, Any], python_command: str) -> dict[str, Any]:
    entrypoint = validate_relative_file_path(str(raw.get("entrypoint") or "train.py"))
    if entrypoint != "train.py":
        raise ValueError(f"EXPERIMENT_CODE_ENTRYPOINT_INVALID:{entrypoint}")
    files = []
    for item in raw.get("files") or []:
        path = validate_relative_file_path(str(item.get("path") or ""))
        content = str(item.get("content") or "")
        if not content.strip():
            raise ValueError(f"EXPERIMENT_CODE_FILE_EMPTY:{path}")
        files.append({"path": path, "content": content})
    if not any(item["path"] == entrypoint for item in files):
        raise ValueError(f"EXPERIMENT_CODE_ENTRYPOINT_MISSING:{entrypoint}")
    seed = int(task.get("seed") or 7)
    metrics_path = str(raw.get("metrics_path") or task.get("metrics_path") or f"results/run_seed_{seed}.json")
    log_path = str(raw.get("log_path") or task.get("log_path") or f"logs/run_seed_{seed}.log")
    return {
        "entrypoint": entrypoint,
        "files": files,
        "command": str(raw.get("command") or f"{python_command} {entrypoint} --seed {seed} --output {metrics_path}"),
        "metrics_path": metrics_path,
        "log_path": log_path,
        "assumptions": list(raw.get("assumptions") or []),
        "validation": dict(raw.get("validation") or {}),
    }


def default_mock_experiment_code(task: dict[str, Any], python_command: str) -> dict[str, Any]:
    seed = int(task.get("seed") or 7)
    metrics_path = str(task.get("metrics_path") or f"results/run_seed_{seed}.json")
    log_path = str(task.get("log_path") or f"logs/run_seed_{seed}.log")
    content = '''from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    random.seed(args.seed)
    baseline = [0, 1, 1, 0, 1, 0]
    predictions = [(value if random.random() > 0.1 else 1 - value) for value in baseline]
    accuracy = sum(int(a == b) for a, b in zip(baseline, predictions)) / len(baseline)
    metrics = {"accuracy": accuracy, "seed": args.seed}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metrics), encoding="utf-8")
    print(json.dumps(metrics))


if __name__ == "__main__":
    main()
'''
    return normalize_experiment_code(
        {
            "entrypoint": "train.py",
            "files": [{"path": "train.py", "content": content}],
            "metrics_path": metrics_path,
            "log_path": log_path,
            "assumptions": ["Development fallback code computes metrics from a deterministic toy baseline."],
            "validation": {"requires_network": False, "expected_metrics": ["accuracy"]},
        },
        task,
        python_command,
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run the same command from Step 2.

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/workflow/experiment_code.py tests/backend/test_experiment_code.py
git commit -m "feat: define experiment code artifact contract"
```

---

### Task 2: Generate `experiment_code` in Workflow

**Files:**
- Modify: `backend/app/agents/experiment.py`
- Modify: `backend/app/workflow/engine.py`
- Modify: `backend/app/providers/llm.py`
- Test: `tests/backend/test_workflow_engine.py`

**Interfaces:**
- Consumes: `normalize_experiment_code(raw, task, python_command)`
- Consumes: `default_mock_experiment_code(task, python_command)`
- Produces: `ExperimentAgent.generate_code(plan: dict, task: dict, instructions: str, python_command: str) -> dict`
- Produces artifact type: `experiment_code`

- [ ] **Step 1: Write failing workflow test**

Append to `tests/backend/test_workflow_engine.py`:

```python
def test_experiment_task_creates_experiment_code_artifact(tmp_path):
    class CodegenLLM(RecordingLLM):
        def generate_json(self, task: str, inputs: dict, schema_hint: dict, instructions: str = "") -> dict:
            if task == "experiment.generate_code":
                self.tasks.append(task)
                self.inputs.append((task, inputs))
                assert "experiment-bridge" in instructions
                return {
                    "entrypoint": "train.py",
                    "files": [{"path": "train.py", "content": "print('{\"accuracy\": 1.0}')"}],
                    "assumptions": ["test generated code"],
                }
            return super().generate_json(task, inputs, schema_hint, instructions)

    repository = Repository(data_dir=str(tmp_path / "data"))
    loader = SkillLoader(Path(__file__).resolve().parents[2])
    llm = CodegenLLM()
    engine = WorkflowEngine(
        repository,
        llm,
        MockLiteratureProvider(),
        MockExperimentProvider(),
        loader,
        SkillRegistry(),
        SkillCatalog(loader),
    )
    run = _selected_hypothesis_run(engine, repository)
    run = engine.run_step(run.id, "research_plan")

    run = engine.run_step(run.id, "experiment_task")

    latest = {artifact.type: artifact for artifact in run.artifacts}
    assert "experiment_task" in latest
    assert latest["experiment_code"].content["entrypoint"] == "train.py"
    assert latest["experiment_code"].content["command"].startswith("python train.py ")
    assert "experiment.generate_code" in llm.tasks
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend/test_workflow_engine.py::test_experiment_task_creates_experiment_code_artifact -q
```

Expected: FAIL because `experiment_code` artifact is not created.

- [ ] **Step 3: Add mock LLM fallback for codegen**

In `backend/app/providers/llm.py`, add before the default return in `MockLLMProvider.generate_json()`:

```python
        if task == "experiment.generate_code":
            seed = int(inputs.get("task", {}).get("seed") or 7)
            return {
                "provider_mode": self.mode,
                "fallback_used": True,
                "fallback_reason": "Development fallback; generated toy experiment code.",
                "entrypoint": "train.py",
                "files": [{
                    "path": "train.py",
                    "content": (
                        "from pathlib import Path\\n"
                        "import argparse, json, random\\n"
                        "parser = argparse.ArgumentParser()\\n"
                        "parser.add_argument('--seed', type=int, default=7)\\n"
                        "parser.add_argument('--output', required=True)\\n"
                        "args = parser.parse_args()\\n"
                        "random.seed(args.seed)\\n"
                        "metrics = {'accuracy': 0.5 + (args.seed % 10) / 100, 'seed': args.seed}\\n"
                        "Path(args.output).parent.mkdir(parents=True, exist_ok=True)\\n"
                        "Path(args.output).write_text(json.dumps(metrics), encoding='utf-8')\\n"
                        "print(json.dumps(metrics))\\n"
                    ),
                }],
                "metrics_path": f"results/run_seed_{seed}.json",
                "log_path": f"logs/run_seed_{seed}.log",
                "assumptions": ["Mock LLM generated deterministic toy code."],
                "validation": {"requires_network": False, "expected_metrics": ["accuracy"]},
            }
```

- [ ] **Step 4: Add `ExperimentAgent.generate_code()`**

Update `backend/app/agents/experiment.py`:

```python
from backend.app.providers.experiment import ExperimentProvider
from backend.app.providers.llm import LLMProvider
from backend.app.workflow.experiment_code import default_mock_experiment_code, normalize_experiment_code


class ExperimentAgent:
    name = "Experiment Skill"

    def __init__(self, experiment_provider: ExperimentProvider, llm_provider: LLMProvider | None = None) -> None:
        self.experiment_provider = experiment_provider
        self.llm_provider = llm_provider

    def build_task(self, plan: dict) -> dict:
        task = self.experiment_provider.plan(plan, plan.get("experiment_constraints", {"seed": 7}))
        task.setdefault("plan", plan)
        return task

    def generate_code(self, plan: dict, task: dict, instructions: str, python_command: str) -> dict:
        if self.llm_provider is None:
            return default_mock_experiment_code(task, python_command)
        raw = self.llm_provider.generate_json(
            "experiment.generate_code",
            {"plan": plan, "task": task},
            {
                "entrypoint": "train.py",
                "files": [{"path": "train.py", "content": "python source"}],
                "command": f"{python_command} train.py --seed 7 --output results/run_seed_7.json",
                "metrics_path": "results/run_seed_7.json",
                "log_path": "logs/run_seed_7.log",
            },
            instructions=instructions,
        )
        return normalize_experiment_code(raw, task, python_command)

    def run_and_analyze(self, task: dict, code: dict | None = None) -> dict:
        result = self.experiment_provider.run(task, code)
        analysis = self.experiment_provider.analyze(result)
        return {**result, "analysis": analysis}
```

- [ ] **Step 5: Wire agent and workflow**

In `backend/app/workflow/engine.py` constructor, change:

```python
self.experiment_agent = ExperimentAgent(experiment_provider)
```

to:

```python
self.experiment_agent = ExperimentAgent(experiment_provider, llm_provider)
```

In `run_step()` under `experiment_task`, after `task = ...`, add:

```python
python_command = getattr(self.experiment_provider, "python_command", lambda: "python")()
code = self.experiment_agent.generate_code(latest["plan"].content, task, instructions, python_command)
```

Then save both artifacts:

```python
self.repository.add_artifact(
    run_id, "experiment_task", "Experiment Task", task, step_id, self.experiment_agent.name
)
self.repository.add_artifact(
    run_id, "experiment_code", "Experiment Code", code, step_id, self.experiment_agent.name
)
```

Update the trace output to include:

```python
{"task": task, "code": {"entrypoint": code["entrypoint"], "files": [item["path"] for item in code["files"]]}}
```

In `experiment_run_analysis`, change:

```python
result = self.experiment_agent.run_and_analyze(latest["experiment_task"].content)
```

to:

```python
result = self.experiment_agent.run_and_analyze(
    latest["experiment_task"].content,
    latest.get("experiment_code").content if latest.get("experiment_code") else None,
)
```

- [ ] **Step 6: Add provider `python_command()` methods and protocol change**

In `backend/app/providers/experiment.py`, update protocol:

```python
class ExperimentProvider(Protocol):
    def plan(self, hypothesis: dict, constraints: dict) -> dict: ...
    def run(self, task: dict, code: dict | None = None) -> dict: ...
    def analyze(self, result: dict) -> dict: ...
    def python_command(self) -> str: ...
```

Add to providers:

```python
def python_command(self) -> str:
    return "python"
```

for `MockExperimentProvider`, and:

```python
def python_command(self) -> str:
    return self.settings.remote_gpu_python
```

for `RemoteGpuExperimentProvider`, and:

```python
def python_command(self) -> str:
    return self.settings.local_gpu_python
```

for `LocalGpuExperimentProvider`.

Change each `run` signature to `run(self, task: dict, code: dict | None = None) -> dict`.

- [ ] **Step 7: Run workflow test and related backend tests**

Run:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend/test_workflow_engine.py::test_experiment_task_creates_experiment_code_artifact tests/backend/test_workflow_engine.py::test_supervisor_delegates_reasoning_to_llm_agents -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add backend/app/agents/experiment.py backend/app/workflow/engine.py backend/app/providers/llm.py backend/app/providers/experiment.py tests/backend/test_workflow_engine.py
git commit -m "feat: generate experiment code artifacts"
```

---

### Task 3: Local Generated-Code Deployment

**Files:**
- Modify: `backend/app/providers/experiment.py`
- Test: `tests/backend/test_experiment_provider.py`

**Interfaces:**
- Consumes: `code["files"]`, `code["command"]`, `code["metrics_path"]`, `code["log_path"]`
- Produces: local deployment under resolved `LOCAL_EXPERIMENT_WORKDIR`

- [ ] **Step 1: Write failing local provider deployment test**

Append to `tests/backend/test_experiment_provider.py`:

```python
def test_local_gpu_provider_deploys_generated_code_before_running(tmp_path, monkeypatch):
    workdir = tmp_path / "experiments"
    workdir.mkdir()
    calls = []

    def fake_run(command, check, capture_output, text, timeout, cwd):
        calls.append({"command": command, "cwd": cwd})
        return subprocess.CompletedProcess(command, 0, stdout='{"accuracy": 0.97}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = Settings.from_env({
        "EXPERIMENT_PROVIDER": "local_gpu",
        "LOCAL_EXPERIMENT_WORKDIR": str(workdir),
        "LOCAL_GPU_PYTHON": "local-python",
    })
    provider = LocalGpuExperimentProvider(settings)
    task = provider.plan({"claim": "test"}, {"seed": 7})
    code = {
        "entrypoint": "train.py",
        "files": [{"path": "train.py", "content": "print('{\"accuracy\": 0.97}')"}],
        "command": "local-python train.py --seed 7 --output results/local_seed_7.json",
        "metrics_path": "results/local_seed_7.json",
        "log_path": "logs/local_seed_7.log",
    }

    result = provider.run(task, code)

    assert (workdir / "train.py").read_text(encoding="utf-8") == "print('{\"accuracy\": 0.97}')"
    assert calls == [{"command": code["command"].split(), "cwd": str(workdir.resolve())}]
    assert result["metrics"]["accuracy"] == 0.97
    assert result["deployed_files"] == [str(workdir.resolve() / "train.py")]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend/test_experiment_provider.py::test_local_gpu_provider_deploys_generated_code_before_running -q
```

Expected: FAIL because generated files are not deployed.

- [ ] **Step 3: Implement local deployment helper**

In `backend/app/providers/experiment.py`, add:

```python
def _tail(value: str, limit: int = 2000) -> str:
    return value[-limit:]
```

Add to `LocalGpuExperimentProvider`:

```python
    def _deploy_generated_code(self, workdir: Path, code: dict) -> list[str]:
        deployed = []
        for item in code.get("files") or []:
            relative = Path(str(item["path"]))
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"EXPERIMENT_CODE_PATH_INVALID: {item['path']}")
            target = (workdir / relative).resolve()
            if workdir not in target.parents and target != workdir:
                raise RuntimeError(f"EXPERIMENT_CODE_PATH_INVALID: {item['path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(item["content"]), encoding="utf-8")
            deployed.append(str(target))
        return deployed
```

In `LocalGpuExperimentProvider.run()`, after workdir validation:

```python
deployed_files = self._deploy_generated_code(workdir, code) if code else []
```

Use:

```python
command = str((code or {}).get("command") or task["command"])
completed = subprocess.run(command.split(), ...)
```

Add to result:

```python
"deployed_files": deployed_files,
```

On `subprocess.CalledProcessError`, raise:

```python
raise RuntimeError(
    f"LOCAL_EXPERIMENT_RUN_FAILED: stdout={_tail(exc.stdout or '')} stderr={_tail(exc.stderr or '')}"
) from exc
```

- [ ] **Step 4: Run local provider tests**

Run:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend/test_experiment_provider.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/providers/experiment.py tests/backend/test_experiment_provider.py
git commit -m "feat: deploy generated code for local experiments"
```

---

### Task 4: Remote Generated-Code Deployment

**Files:**
- Modify: `backend/app/providers/experiment.py`
- Test: `tests/backend/test_experiment_provider.py`

**Interfaces:**
- Consumes: `code["files"]`, `code["command"]`, `code["metrics_path"]`, `code["log_path"]`
- Produces: SSH commands that deploy generated files under `REMOTE_GPU_PROJECT_DIR`

- [ ] **Step 1: Write failing remote deployment test**

Append to `tests/backend/test_experiment_provider.py`:

```python
def test_remote_gpu_provider_deploys_generated_code_over_ssh(monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text, timeout, input=None):
        calls.append({"command": command, "input": input})
        return subprocess.CompletedProcess(command, 0, stdout='{"accuracy": 0.94}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = Settings.from_env({
        "EXPERIMENT_PROVIDER": "remote_gpu",
        "REMOTE_GPU_HOST": "gpu.example.com",
        "REMOTE_GPU_USER": "runner",
        "REMOTE_GPU_PROJECT_DIR": "/srv/ai-scientist",
        "REMOTE_GPU_PYTHON": "remote-python",
        "REMOTE_GPU_CUDA_VISIBLE_DEVICES": "0",
    })
    provider = RemoteGpuExperimentProvider(settings)
    task = provider.plan({"claim": "test"}, {"seed": 7})
    code = {
        "entrypoint": "train.py",
        "files": [{"path": "train.py", "content": "print('{\"accuracy\": 0.94}')"}],
        "command": "remote-python train.py --seed 7 --output results/run_seed_7.json",
        "metrics_path": "results/run_seed_7.json",
        "log_path": "logs/run_seed_7.log",
    }

    result = provider.run(task, code)

    assert result["metrics"]["accuracy"] == 0.94
    assert any("python" in call["command"] for call in calls)
    assert any(call["input"] and "train.py" in call["input"] for call in calls)
    assert "CUDA_VISIBLE_DEVICES=0" in calls[-1]["command"][-1]
    assert "cd /srv/ai-scientist" in calls[-1]["command"][-1]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend/test_experiment_provider.py::test_remote_gpu_provider_deploys_generated_code_over_ssh -q
```

Expected: FAIL because remote provider does not deploy generated files.

- [ ] **Step 3: Add SSH command helpers**

In `RemoteGpuExperimentProvider`, add:

```python
    def _ssh_base(self) -> list[str]:
        command = ["ssh", "-p", str(self.settings.remote_gpu_port)]
        if self.settings.remote_gpu_ssh_key_path:
            command.extend(["-i", self.settings.remote_gpu_ssh_key_path])
        command.append(f"{self.settings.remote_gpu_user}@{self.settings.remote_gpu_host}")
        return command
```

Add:

```python
    def _deploy_generated_code(self, code: dict) -> list[str]:
        deployed = []
        writer = (
            "import json, pathlib, sys\n"
            "payload=json.load(sys.stdin)\n"
            "root=pathlib.Path(payload['root']).resolve()\n"
            "root.mkdir(parents=True, exist_ok=True)\n"
            "for item in payload['files']:\n"
            "    rel=pathlib.PurePosixPath(item['path'])\n"
            "    if rel.is_absolute() or '..' in rel.parts:\n"
            "        raise SystemExit(f'EXPERIMENT_CODE_PATH_INVALID:{item[\"path\"]}')\n"
            "    target=(root / pathlib.Path(*rel.parts)).resolve()\n"
            "    if root not in target.parents and target != root:\n"
            "        raise SystemExit(f'EXPERIMENT_CODE_PATH_INVALID:{item[\"path\"]}')\n"
            "    target.parent.mkdir(parents=True, exist_ok=True)\n"
            "    target.write_text(item['content'], encoding='utf-8')\n"
            "    print(str(target))\n"
        )
        payload = json.dumps({"root": self.settings.remote_gpu_project_dir, "files": code.get("files") or []})
        completed = subprocess.run(
            [*self._ssh_base(), f"{self.settings.remote_gpu_python} -c {json.dumps(writer)}"],
            input=payload,
            check=True,
            capture_output=True,
            text=True,
            timeout=self.settings.experiment_timeout_seconds,
        )
        deployed.extend(line for line in completed.stdout.splitlines() if line.strip())
        return deployed
```

- [ ] **Step 4: Wire remote deployment into run**

In `RemoteGpuExperimentProvider.run()`, before building execution command:

```python
deployed_files = self._deploy_generated_code(code) if code else []
run_command = str((code or {}).get("command") or task["command"])
metrics_path = str((code or {}).get("metrics_path") or task["metrics_path"])
log_path = str((code or {}).get("log_path") or task["log_path"])
```

Use `run_command`, `metrics_path`, and `log_path` in the remote shell command.

Replace manual SSH command assembly with:

```python
ssh_command = [*self._ssh_base(), command]
```

Add to result:

```python
"deployed_files": deployed_files,
```

- [ ] **Step 5: Run remote provider tests**

Run:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend/test_experiment_provider.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/providers/experiment.py tests/backend/test_experiment_provider.py
git commit -m "feat: deploy generated code over ssh"
```

---

### Task 5: Remote Settings Validation and Frontend Diagnostics

**Files:**
- Modify: `backend/app/providers/experiment.py`
- Modify: `backend/app/api/providers.py`
- Modify: `frontend/src/api/types.ts`
- Modify: `frontend/src/components/ProjectSettingsModal.tsx`
- Test: `tests/backend/test_api.py`

**Interfaces:**
- Produces: `validate_remote_gpu_settings(settings: Settings) -> dict`
- Consumes: frontend remote config fields already stored by `RuntimeConfigStore`
- Produces remote test result fields: `code`, `host`, `user`, `port`, `project_dir`, `stdout_tail`, `stderr_tail`

- [ ] **Step 1: Write failing remote API validation tests**

Add to `tests/backend/test_api.py`:

```python
def test_remote_experiment_connection_test_runs_ssh_readiness_check(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text, timeout):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="Python 3.12.0\n", stderr="")

    import subprocess
    monkeypatch.setattr(subprocess, "run", fake_run)
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "false"})
    client = TestClient(app)

    response = client.post(
        "/api/settings/experiment/test",
        json={
            "provider": "remote_gpu",
            "remote": {
                "host": "gpu.example.com",
                "user": "runner",
                "port": 2222,
                "ssh_key_path": "C:/Users/runner/.ssh/id_rsa",
                "project_dir": "/srv/ai-scientist",
                "python": "python",
            },
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["code"] == ""
    assert body["host"] == "gpu.example.com"
    assert calls[0][:5] == ["ssh", "-p", "2222", "-i", "C:/Users/runner/.ssh/id_rsa"]
    assert "python --version" in calls[0][-1]


def test_remote_experiment_connection_test_reports_ssh_failure(tmp_path, monkeypatch):
    import subprocess

    def fake_run(command, check, capture_output, text, timeout):
        raise subprocess.CalledProcessError(255, command, stdout="", stderr="permission denied")

    monkeypatch.setattr(subprocess, "run", fake_run)
    app = create_app(data_dir=str(tmp_path), env={"COMPETITION_MODE": "false"})
    client = TestClient(app)

    response = client.post(
        "/api/settings/experiment/test",
        json={
            "provider": "remote_gpu",
            "remote": {"host": "gpu.example.com", "user": "runner", "project_dir": "/srv/ai-scientist"},
        },
    )

    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "REMOTE_GPU_SSH_FAILED"
    assert "permission denied" in body["stderr_tail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend/test_api.py::test_remote_experiment_connection_test_runs_ssh_readiness_check tests/backend/test_api.py::test_remote_experiment_connection_test_reports_ssh_failure -q
```

Expected: FAIL because remote test only checks missing fields.

- [ ] **Step 3: Implement remote validation helper**

In `backend/app/providers/experiment.py`, add module function:

```python
def validate_remote_gpu_settings(settings: Settings) -> dict:
    missing = [
        name for name, value in {
            "REMOTE_GPU_HOST": settings.remote_gpu_host,
            "REMOTE_GPU_USER": settings.remote_gpu_user,
            "REMOTE_GPU_PROJECT_DIR": settings.remote_gpu_project_dir,
        }.items()
        if not value
    ]
    base = {
        "ok": False,
        "provider": "remote_gpu",
        "host": settings.remote_gpu_host,
        "user": settings.remote_gpu_user,
        "port": settings.remote_gpu_port,
        "project_dir": settings.remote_gpu_project_dir,
        "missing": missing,
    }
    if missing:
        return {**base, "code": "REMOTE_GPU_CONFIG_MISSING", "message": "Remote GPU config is missing required fields."}
    ssh = ["ssh", "-p", str(settings.remote_gpu_port)]
    if settings.remote_gpu_ssh_key_path:
        ssh.extend(["-i", settings.remote_gpu_ssh_key_path])
    remote = f"{settings.remote_gpu_user}@{settings.remote_gpu_host}"
    check = (
        f"mkdir -p {settings.remote_gpu_project_dir} && "
        f"test -w {settings.remote_gpu_project_dir} && "
        f"cd {settings.remote_gpu_project_dir} && "
        f"{settings.remote_gpu_python} --version"
    )
    try:
        completed = subprocess.run(
            [*ssh, remote, check],
            check=True,
            capture_output=True,
            text=True,
            timeout=min(settings.experiment_timeout_seconds, 30),
        )
    except subprocess.CalledProcessError as exc:
        return {
            **base,
            "code": "REMOTE_GPU_SSH_FAILED",
            "stdout_tail": _tail(exc.stdout or ""),
            "stderr_tail": _tail(exc.stderr or ""),
            "message": "Remote GPU SSH readiness check failed.",
        }
    return {
        **base,
        "ok": True,
        "code": "",
        "stdout_tail": _tail(completed.stdout or ""),
        "stderr_tail": _tail(completed.stderr or ""),
        "message": "Remote GPU SSH readiness check passed.",
    }
```

- [ ] **Step 4: Use helper in settings API**

In `backend/app/api/providers.py`, import:

```python
from dataclasses import replace
from backend.app.providers.experiment import validate_remote_gpu_settings
```

In `_experiment_test_result`, replace the remote success branch with:

```python
        if missing:
            return {
                "ok": False,
                "provider": provider,
                "code": "REMOTE_GPU_CONFIG_MISSING",
                "missing": missing,
                "message": "远程 GPU 配置缺少必填字段。",
            }
        settings = replace(
            base_settings,
            remote_gpu_host=str(remote.get("host") or ""),
            remote_gpu_user=str(remote.get("user") or ""),
            remote_gpu_port=int(remote.get("port") or 22),
            remote_gpu_ssh_key_path=str(remote.get("ssh_key_path") or ""),
            remote_gpu_project_dir=str(remote.get("project_dir") or ""),
            remote_gpu_python=str(remote.get("python") or "python"),
            remote_gpu_cuda_visible_devices=str(remote.get("cuda_visible_devices") or ""),
            experiment_timeout_seconds=int(remote.get("timeout_seconds") or 1200),
        )
        return validate_remote_gpu_settings(settings)
```

Change `_experiment_test_result` signature to:

```python
def _experiment_test_result(config: dict[str, Any], base_settings) -> dict[str, Any]:
```

Update caller:

```python
return _experiment_test_result(body.model_dump(), deps.base_settings)
```

- [ ] **Step 5: Update frontend types**

In `frontend/src/api/types.ts`, ensure `ExperimentTestResult` includes:

```typescript
  code?: string;
  host?: string;
  user?: string;
  port?: number;
  project_dir?: string;
  stdout_tail?: string;
  stderr_tail?: string;
```

Ensure `ProviderStatus` includes the same optional remote diagnostic fields if rendered elsewhere.

- [ ] **Step 6: Update settings modal diagnostics and error handling**

In `frontend/src/components/ProjectSettingsModal.tsx`, change `saveSettings()` to catch errors:

```typescript
  async function saveSettings() {
    try {
      const saved = await api.saveExperimentSettings(settings);
      setSettings(saved);
      setMessage("服务器配置已保存，并已应用到后端运行时。");
      await onStatusRefresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  }
```

Change test result block to:

```tsx
            {testResult ? (
              <div className={testResult.ok ? "settings-result ok" : "settings-result warn"}>
                <strong>{testResult.ok ? "配置检查通过" : `配置检查失败: ${testResult.code || testResult.missing.join(", ")}`}</strong>
                {testResult.project_dir ? <small>Project: {testResult.project_dir}</small> : null}
                {testResult.resolved_workdir ? <small>Workdir: {testResult.resolved_workdir}</small> : null}
                {testResult.entrypoint ? <small>Entrypoint: {testResult.entrypoint}</small> : null}
                {testResult.stdout_tail ? <small>stdout: {testResult.stdout_tail}</small> : null}
                {testResult.stderr_tail ? <small>stderr: {testResult.stderr_tail}</small> : null}
              </div>
            ) : null}
```

- [ ] **Step 7: Run backend API tests and frontend build**

Run:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend/test_api.py -q
```

Expected: PASS.

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd' run build
```

from `D:\竞赛\frontend`.

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add backend/app/providers/experiment.py backend/app/api/providers.py frontend/src/api/types.ts frontend/src/components/ProjectSettingsModal.tsx tests/backend/test_api.py
git commit -m "feat: validate remote gpu settings over ssh"
```

---

### Task 6: Full Regression and Manual Smoke

**Files:**
- Modify only if tests reveal integration defects.
- Test: full backend suite and frontend build.

**Interfaces:**
- Consumes all previous tasks.
- Produces verified local/remote-capable experiment code path.

- [ ] **Step 1: Run full backend suite**

Run:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run from `D:\竞赛\frontend`:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd' run build
```

Expected: PASS.

- [ ] **Step 3: Run local generated-code smoke through backend objects**

Run a focused test command after creating a local provider test that uses the real generated mock code:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend/test_experiment_provider.py::test_local_gpu_provider_deploys_generated_code_before_running -q
```

Expected: PASS.

- [ ] **Step 4: Verify current remote config status without requiring a real server**

Run:

```powershell
$env:PYTHONPATH='D:\竞赛\.venv\Lib\site-packages'; & 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/backend/test_api.py::test_remote_experiment_connection_test_reports_ssh_failure -q
```

Expected: PASS with mocked SSH failure diagnostics.

- [ ] **Step 5: Commit final integration fixes if any**

If no files changed after verification, skip this commit. If integration fixes were needed:

```powershell
git add <changed-files>
git commit -m "fix: stabilize generated experiment execution"
```

---

## Self-Review Checklist

- Spec coverage:
  - Unified `experiment_code` artifact: Task 1 and Task 2.
  - Local deployment/run: Task 3.
  - Remote SSH deployment/run: Task 4.
  - Frontend SSH settings to backend validation: Task 5.
  - Full regression: Task 6.
- Placeholder scan:
  - This plan contains no placeholder markers and no empty implementation steps.
- Type consistency:
  - `normalize_experiment_code(raw, task, python_command)` returns a dict consumed by both providers.
  - `ExperimentAgent.generate_code(plan, task, instructions, python_command)` returns that same dict.
  - Provider `run(task, code=None)` remains backward compatible.
