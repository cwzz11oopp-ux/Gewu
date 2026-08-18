from __future__ import annotations

import json
import hashlib
import os
import platform
import posixpath
import re
import shlex
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from backend.app.config import Settings
from backend.app.models.experiment import ExperimentBundle
from backend.app.runtime_status import read_json_retry, write_json_atomic
from backend.app.workflow.dataset_catalog import (
    availability_status,
    dataset_batch_presence_script,
    dataset_card,
    dataset_download_script,
    dataset_present,
    dataset_presence_script,
    dataset_spec,
    supported_dataset_names,
)
from backend.app.workflow.dataset_inspection import verify_dataset_contract
from backend.app.workflow.experiment_code import smoke_data_reduction_issues
from backend.app.workflow.experiment_harness import harness_file_path, harness_source
from backend.app.providers.experiment_runtime import (
    build_python_command,
    cuda_probe_command,
    parse_cuda_probe,
    validate_result_file,
    validate_result_payload,
)


class ExperimentProvider(Protocol):
    def plan(self, hypothesis: dict, constraints: dict) -> dict: ...
    def run(self, task: dict, code: dict | ExperimentBundle | None = None) -> dict: ...
    def analyze(self, result: dict) -> dict: ...
    def python_command(self) -> str: ...
    def dataset_availability(self) -> list[dict]: ...
    def recover_completed_result(
        self, task: dict, bundle: ExperimentBundle
    ) -> dict | None: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _execution_timeout(settings: Settings) -> int | None:
    return settings.experiment_timeout_seconds if settings.experiment_timeout_seconds > 0 else None


def _probe_timeout(settings: Settings) -> int:
    return min(settings.experiment_timeout_seconds, 30) if settings.experiment_timeout_seconds > 0 else 30


def _tail(value: str, limit: int = 2000) -> str:
    return value[-limit:]


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_experiment_lock(lock_path: Path, attempt_id: str) -> None:
    """Atomically prevent concurrent attempts for one run/experiment."""
    payload = {"attempt_id": attempt_id, "pid": os.getpid(), "created_at": _now()}
    for _ in range(2):
        try:
            with lock_path.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            return
        except FileExistsError:
            try:
                existing = json.loads(lock_path.read_text(encoding="utf-8"))
                owner_pid = int(existing.get("pid") or 0)
            except (OSError, ValueError, TypeError):
                owner_pid = 0
            if _pid_alive(owner_pid):
                raise RuntimeError(
                    f"LOCAL_EXPERIMENT_ALREADY_RUNNING:attempt_id={existing.get('attempt_id', '')};pid={owner_pid}"
                )
            lock_path.unlink(missing_ok=True)
    raise RuntimeError("LOCAL_EXPERIMENT_LOCK_UNAVAILABLE")


def _update_experiment_lock(lock_path: Path, attempt_id: str, pid: int) -> None:
    lock_path.write_text(
        json.dumps({"attempt_id": attempt_id, "pid": pid, "updated_at": _now()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _kill_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), 9)
        except OSError:
            process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


_MISSING_MODULE_PATTERN = re.compile(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]")
_ACTIVE_LOCAL_PROCESSES: dict[int, subprocess.Popen] = {}
_ACTIVE_LOCAL_PROCESSES_LOCK = threading.Lock()


def terminate_active_local_experiments() -> None:
    """Terminate every process tree owned by this backend instance."""
    with _ACTIVE_LOCAL_PROCESSES_LOCK:
        processes = list(_ACTIVE_LOCAL_PROCESSES.values())
    for process in processes:
        _kill_process_tree(process)


def _configured_device_indexes(value: str) -> list[int] | None:
    if not value:
        return []
    if not re.fullmatch(r"\d+(?:,\d+)*", value):
        return None
    return [int(item) for item in value.split(",")]


def validate_local_gpu_preflight(settings: Settings) -> dict:
    """The one real local-runtime probe used by settings and run admission."""
    workdir_value = str(settings.local_gpu_workdir or "")
    if not workdir_value:
        return {
            "ok": False, "provider": "local_gpu", "code": "LOCAL_EXPERIMENT_WORKDIR_INVALID",
            "message": "Local experiment workdir is required.", "missing": ["LOCAL_EXPERIMENT_WORKDIR"],
            "workdir": "", "resolved_workdir": "",
        }
    workdir = Path(workdir_value).expanduser().resolve()
    base = {
        "provider": "local_gpu", "workdir": workdir_value,
        "resolved_workdir": str(workdir), "entrypoint": str(workdir / "train.py"),
        "entrypoint_exists": (workdir / "train.py").is_file(), "missing": [],
    }
    if not settings.local_gpu_enabled:
        return {**base, "ok": False, "code": "LOCAL_GPU_DISABLED", "message": "Local GPU is disabled."}
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        probe_file = workdir / ".gewu-write-probe"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
    except OSError as exc:
        return {**base, "ok": False, "code": "LOCAL_EXPERIMENT_WORKDIR_INVALID", "message": f"Local experiment workdir is not writable: {exc}"}
    indexes = _configured_device_indexes(settings.local_gpu_cuda_visible_devices)
    if indexes is None:
        return {**base, "ok": False, "code": "LOCAL_GPU_DEVICE_INDEX_INVALID", "message": "CUDA device indexes must use values such as 0 or 0,1."}
    env = os.environ.copy()
    env.pop("CUDA_VISIBLE_DEVICES", None)
    try:
        completed = subprocess.run(
            cuda_probe_command(settings.local_gpu_python), check=True,
            capture_output=True, text=True, timeout=_probe_timeout(settings),
            cwd=str(workdir), env=env,
        )
        probe = parse_cuda_probe(completed.stdout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
        return {**base, "ok": False, "code": "LOCAL_GPU_PYTHON_PROBE_FAILED", "message": "The configured Local Python could not complete the CUDA probe.", "python": settings.local_gpu_python, "error_type": type(exc).__name__}
    diagnostics = {
        "python": settings.local_gpu_python, "python_version": probe.python_version,
        "torch_version": probe.torch_version, "torch_cuda": probe.torch_cuda or "",
        "cuda_available": probe.available, "device_count": probe.device_count,
        "device_names": probe.device_names,
        "available_device_indexes": list(range(probe.device_count)),
        "dependency_status": "ready",
    }
    if not probe.available or probe.device_count < 1:
        return {**base, **diagnostics, "ok": False, "code": "LOCAL_EXPERIMENT_CUDA_UNAVAILABLE", "message": "The configured Local Python cannot access a CUDA GPU."}
    if any(index >= probe.device_count for index in indexes):
        return {**base, **diagnostics, "ok": False, "code": "LOCAL_GPU_DEVICE_INDEX_INVALID", "message": "A configured CUDA device index is outside the available range."}
    return {**base, **diagnostics, "ok": True, "code": "", "message": "Local Python, torch, CUDA, GPU selection, and workdir are ready."}


def validate_remote_gpu_settings(settings: Settings) -> dict:
    missing = [
        name
        for name, value in {
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
        return {
            **base,
            "code": "REMOTE_GPU_CONFIG_MISSING",
            "message": "Remote GPU config is missing required fields.",
        }
    indexes = _configured_device_indexes(settings.remote_gpu_cuda_visible_devices)
    if indexes is None:
        return {
            **base,
            "code": "REMOTE_GPU_DEVICE_INDEX_INVALID",
            "message": "CUDA device indexes must use values such as 0 or 0,1.",
        }
    ssh = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(settings.remote_gpu_port),
    ]
    if settings.remote_gpu_ssh_key_path:
        ssh.extend(["-i", settings.remote_gpu_ssh_key_path])
    remote = f"{settings.remote_gpu_user}@{settings.remote_gpu_host}"
    project_dir = shlex.quote(settings.remote_gpu_project_dir)
    python_command = shlex.quote(settings.remote_gpu_python)
    probe_script = cuda_probe_command(settings.remote_gpu_python)[2]
    check = (
        f"mkdir -p {project_dir} && "
        f"test -w {project_dir} && "
        f"cd {project_dir} && "
        f"{python_command} -c {shlex.quote(probe_script)}"
    )
    try:
        completed = subprocess.run(
            [*ssh, remote, check],
            check=True,
            capture_output=True,
            text=True,
            timeout=_probe_timeout(settings),
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        return {
            **base,
            "code": "REMOTE_GPU_SSH_FAILED",
            "stdout_tail": _tail(getattr(exc, "stdout", "") or ""),
            "stderr_tail": _tail(getattr(exc, "stderr", "") or ""),
            "message": "Remote GPU SSH readiness check failed.",
        }
    try:
        probe = parse_cuda_probe(completed.stdout)
    except RuntimeError:
        return {
            **base,
            "code": "REMOTE_GPU_PROBE_INVALID",
            "stdout_tail": _tail(completed.stdout or ""),
            "stderr_tail": _tail(completed.stderr or ""),
            "message": "Remote Python did not return valid CUDA diagnostics.",
        }
    diagnostics = {
        "python_version": probe.python_version,
        "torch_version": probe.torch_version,
        "torch_cuda": probe.torch_cuda or "",
        "cuda_available": probe.available,
        "device_count": probe.device_count,
        "device_names": probe.device_names,
        "available_device_indexes": list(range(probe.device_count)),
        "dependency_status": "ready",
    }
    if not probe.available or probe.device_count < 1:
        return {
            **base,
            **diagnostics,
            "code": "REMOTE_EXPERIMENT_CUDA_UNAVAILABLE",
            "message": "The configured Remote Python cannot access a CUDA GPU.",
        }
    if any(index >= probe.device_count for index in indexes):
        return {
            **base,
            **diagnostics,
            "code": "REMOTE_GPU_DEVICE_INDEX_INVALID",
            "message": "A configured CUDA device index is outside the available range.",
        }
    return {
        **base,
        **diagnostics,
        "ok": True,
        "code": "",
        "stdout_tail": _tail(completed.stdout or ""),
        "stderr_tail": _tail(completed.stderr or ""),
        "message": "Remote GPU SSH readiness check passed.",
    }


class MockExperimentProvider:
    def python_command(self) -> str:
        return "python"

    def dataset_availability(self) -> list[dict]:
        return [
            {"name": name, "status": "downloadable", "marker": "", "card": dataset_card(name)}
            for name in supported_dataset_names()
        ]

    def plan(self, hypothesis: dict, constraints: dict) -> dict:
        return {
            "name": "development_fixture",
            "hypothesis": hypothesis,
            "constraints": constraints,
            "command": "mock",
        }

    def run(self, task: dict, code: dict | ExperimentBundle | None = None) -> dict:
        if isinstance(code, ExperimentBundle):
            manifest = code.manifest
            metric_names = manifest.expected_metrics or ["accuracy"]
            return {
                "run_id": manifest.run_id,
                "experiment_id": manifest.experiment_id,
                "result_id": manifest.result_id,
                "provider": "mock",
                "is_real_experiment": False,
                "task": task,
                "metrics": {name: 0.5 for name in metric_names},
                "parameters": manifest.parameters,
                "seeds": manifest.seeds,
                "environment": {"provider": "mock", "cuda_available": False},
                "start_time": _now(),
                "end_time": _now(),
            }
        return {
            "provider": "mock",
            "is_real_experiment": False,
            "task": task,
            "metrics": {"accuracy": 0.5},
            "start_time": _now(),
            "end_time": _now(),
        }

    def analyze(self, result: dict) -> dict:
        return {"summary": "Development fixture result; excluded from competition export.", "result": result}


class RemoteGpuExperimentProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def python_command(self) -> str:
        return self.settings.remote_gpu_python

    def dataset_availability(self) -> list[dict]:
        root = self._remote_dataset_root()
        markers = {name: dataset_spec(name).marker for name in supported_dataset_names()}
        command = (
            f"mkdir -p {shlex.quote(root)} && "
            f"{shlex.quote(self.python_command())} -c {shlex.quote(dataset_batch_presence_script())} "
            f"{shlex.quote(root)} {shlex.quote(json.dumps(markers))}"
        )
        try:
            completed = subprocess.run(
                [*self._ssh_base(), command],
                check=True,
                capture_output=True,
                text=True,
                timeout=_probe_timeout(self.settings),
            )
            present = json.loads(completed.stdout.strip())
        except Exception:
            present = None
        entries = []
        for name in supported_dataset_names():
            if present is None:
                status = "unknown"
            elif present.get(name):
                status = "cached"
            elif self.settings.dataset_source != "local":
                status = "downloadable"
            else:
                status = "missing"
            entries.append(
                {
                    "name": name,
                    "status": status,
                    "marker": markers[name],
                    "card": dataset_card(name),
                }
            )
        return entries

    def _ssh_base(self) -> list[str]:
        command = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(self.settings.remote_gpu_port),
        ]
        if self.settings.remote_gpu_ssh_key_path:
            command.extend(["-i", self.settings.remote_gpu_ssh_key_path])
        command.append(f"{self.settings.remote_gpu_user}@{self.settings.remote_gpu_host}")
        return command

    def plan(self, hypothesis: dict, constraints: dict) -> dict:
        seed = constraints.get("seed", 7)
        plan_dataset = hypothesis.get("dataset") if isinstance(hypothesis, dict) else None
        dataset = (
            str(plan_dataset.get("canonical_name") or "")
            if isinstance(plan_dataset, dict)
            else ""
        )
        return {
            "name": "remote_neural_network_experiment",
            "hypothesis": hypothesis,
            "dataset": dataset,
            "seed": seed,
            "command": (
                f"{self.settings.remote_gpu_python} experiments/train.py "
                f"--seed {seed} --output results/run_seed_{seed}.json"
            ),
            "metrics_path": f"results/run_seed_{seed}.json",
            "log_path": f"logs/run_seed_{seed}.log",
        }

    def _deploy_generated_code(self, code: dict) -> list[str]:
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
        payload = json.dumps({
            "root": self.settings.remote_gpu_project_dir,
            "files": code.get("files") or [],
        })
        completed = subprocess.run(
            [*self._ssh_base(), f"{self.settings.remote_gpu_python} -c {json.dumps(writer)}"],
            input=payload,
            check=True,
            capture_output=True,
            text=True,
            timeout=_execution_timeout(self.settings),
        )
        return [line for line in completed.stdout.splitlines() if line.strip()]

    def _bundle_workdir(self, bundle: ExperimentBundle) -> str:
        run_id = bundle.manifest.run_id
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", run_id) or run_id in {".", ".."}:
            raise RuntimeError(f"EXPERIMENT_RUN_ID_INVALID:{run_id}")
        return posixpath.join(
            self.settings.remote_gpu_project_dir.rstrip("/"),
            run_id,
            bundle.manifest.experiment_id,
        )

    def _deploy_bundle(self, bundle: ExperimentBundle, remote_dir: str) -> list[str]:
        writer = (
            "import hashlib, json, pathlib, sys\n"
            "payload=json.load(sys.stdin)\n"
            "root=pathlib.Path(payload['root']).resolve()\n"
            "root.mkdir(parents=True, exist_ok=True)\n"
            "deployed=[]\n"
            "for item in payload['files']:\n"
            "    rel=pathlib.PurePosixPath(item['path'])\n"
            "    if rel.is_absolute() or '..' in rel.parts:\n"
            "        raise SystemExit('EXPERIMENT_CODE_PATH_INVALID:'+item['path'])\n"
            "    target=(root / pathlib.Path(*rel.parts)).resolve()\n"
            "    if root not in target.parents:\n"
            "        raise SystemExit('EXPERIMENT_CODE_PATH_INVALID:'+item['path'])\n"
            "    digest=hashlib.sha256(item['content'].encode('utf-8')).hexdigest()\n"
            "    if digest != item['sha256']:\n"
            "        raise SystemExit('EXPERIMENT_CODE_HASH_INVALID:'+item['path'])\n"
            "    target.parent.mkdir(parents=True, exist_ok=True)\n"
            "    target.write_text(item['content'], encoding='utf-8')\n"
            "    deployed.append(str(target))\n"
            "(root / 'manifest.json').write_text(json.dumps(payload['manifest'], indent=2), encoding='utf-8')\n"
            "requirements=root / payload['manifest']['requirements_file']\n"
            "requirements.write_text(''.join(value+'\\n' for value in payload['requirements']), encoding='utf-8')\n"
            "(root / 'results').mkdir(exist_ok=True)\n"
            "(root / 'logs').mkdir(exist_ok=True)\n"
            "print(json.dumps({'deployed_files': deployed}))\n"
        )
        files = [item.model_dump() for item in bundle.files]
        if bundle.runtime_contract is not None:
            source = harness_source(bundle.runtime_contract)
            files.append({
                "path": harness_file_path(bundle.runtime_contract),
                "content": source,
                "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
            })
        payload = json.dumps(
            {
                "root": remote_dir,
                "files": files,
                "manifest": bundle.manifest.model_dump(),
                "requirements": bundle.requirements,
            },
            ensure_ascii=False,
        )
        command = (
            f"{shlex.quote(self.python_command())} -c {shlex.quote(writer)}"
        )
        try:
            completed = subprocess.run(
                [*self._ssh_base(), command],
                input=payload,
                check=True,
                capture_output=True,
                text=True,
                timeout=_execution_timeout(self.settings),
            )
            response = json.loads(completed.stdout.strip())
            return [str(item) for item in response.get("deployed_files") or []]
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            raise RuntimeError("REMOTE_EXPERIMENT_DEPLOY_FAILED") from exc

    def _remote_missing_dependencies(
        self, bundle: ExperimentBundle, remote_dir: str
    ) -> list[str]:
        modules = [_requirement_module(value) for value in bundle.requirements]
        modules = [value for value in modules if value]
        if not modules:
            return []
        script = (
            "import importlib.util, json; "
            f"modules={json.dumps(modules)}; "
            "print(json.dumps({'missing': [m for m in modules if importlib.util.find_spec(m) is None]}))"
        )
        remote_command = (
            f"cd {shlex.quote(remote_dir)} && "
            f"{shlex.quote(self.python_command())} -c {shlex.quote(script)}"
        )
        try:
            completed = subprocess.run(
                [*self._ssh_base(), remote_command],
                check=True,
                capture_output=True,
                text=True,
                timeout=_probe_timeout(self.settings),
            )
            response = json.loads(completed.stdout.strip())
            return [str(item) for item in response.get("missing") or []]
        except Exception as exc:
            raise RuntimeError("REMOTE_EXPERIMENT_DEPENDENCY_PROBE_FAILED") from exc

    def _remote_cuda_probe(self, remote_dir: str):
        probe = cuda_probe_command(self.python_command())
        cuda_prefix = ""
        if self.settings.remote_gpu_cuda_visible_devices:
            cuda_prefix = (
                "CUDA_VISIBLE_DEVICES="
                f"{shlex.quote(self.settings.remote_gpu_cuda_visible_devices)} "
            )
        remote_command = (
            f"cd {shlex.quote(remote_dir)} && {cuda_prefix}"
            f"{shlex.quote(probe[0])} -c {shlex.quote(probe[2])}"
        )
        try:
            completed = subprocess.run(
                [*self._ssh_base(), remote_command],
                check=True,
                capture_output=True,
                text=True,
                timeout=_probe_timeout(self.settings),
            )
            return parse_cuda_probe(completed.stdout)
        except Exception as exc:
            raise RuntimeError("REMOTE_EXPERIMENT_CUDA_PROBE_FAILED") from exc

    def _remote_dataset_root(self) -> str:
        return posixpath.join(self.settings.remote_gpu_project_dir.rstrip("/"), "_datasets")

    def _provision_remote_dataset(self, name: str) -> str:
        spec = dataset_spec(name)
        root = self._remote_dataset_root()
        presence_command = (
            f"mkdir -p {shlex.quote(root)} && "
            f"{shlex.quote(self.python_command())} -c {shlex.quote(dataset_presence_script())} "
            f"{shlex.quote(root)} {shlex.quote(spec.marker)}"
        )
        try:
            completed = subprocess.run(
                [*self._ssh_base(), presence_command],
                check=True,
                capture_output=True,
                text=True,
                timeout=_probe_timeout(self.settings),
            )
            present = bool(json.loads(completed.stdout.strip()).get("present"))
        except Exception as exc:
            raise RuntimeError("REMOTE_EXPERIMENT_DATASET_PROBE_FAILED") from exc
        if present:
            return root
        if self.settings.dataset_source == "local":
            raise RuntimeError(
                f"EXPERIMENT_DATASET_LOCAL_MISSING:{name}. Place the dataset under {root} on the "
                f"remote host (expected directory '{spec.marker}'), or switch the dataset source "
                "to online so the backend downloads it once into the shared cache."
            )
        download_command = (
            f"{shlex.quote(self.python_command())} -c {shlex.quote(dataset_download_script())} "
            f"{shlex.quote(root)} {shlex.quote(name)} "
            f"{shlex.quote(self.settings.dataset_mirror_url if self.settings.dataset_source == 'auto' else '')} "
            f"{self.settings.dataset_download_retries}"
        )
        try:
            subprocess.run(
                [*self._ssh_base(), download_command],
                check=True,
                capture_output=True,
                text=True,
                timeout=_execution_timeout(self.settings),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"EXPERIMENT_DATASET_DOWNLOAD_TIMEOUT:{name}") from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            stderr = _tail(getattr(exc, "stderr", "") or "")
            raise RuntimeError(
                f"EXPERIMENT_DATASET_DOWNLOAD_FAILED:{name}. {stderr}"
            ) from exc
        return root

    def _write_remote_environment(self, remote_dir: str, environment: dict) -> None:
        writer = (
            "import json, pathlib, sys; "
            "payload=json.load(sys.stdin); "
            "pathlib.Path(payload['root'], 'environment.json').write_text("
            "json.dumps(payload['environment'], indent=2), encoding='utf-8')"
        )
        command = f"{shlex.quote(self.python_command())} -c {shlex.quote(writer)}"
        try:
            subprocess.run(
                [*self._ssh_base(), command],
                input=json.dumps({"root": remote_dir, "environment": environment}),
                check=True,
                capture_output=True,
                text=True,
                timeout=_probe_timeout(self.settings),
            )
        except Exception as exc:
            raise RuntimeError("REMOTE_EXPERIMENT_ENVIRONMENT_WRITE_FAILED") from exc

    def _fetch_bundle_outputs(self, remote_dir: str, bundle: ExperimentBundle) -> dict:
        result_path = f"results/{bundle.manifest.result_id}.json"
        log_path = f"logs/{bundle.manifest.experiment_id}.log"
        reader = (
            "import json, pathlib, sys\n"
            "payload=json.load(sys.stdin)\n"
            "root=pathlib.Path(payload['root']).resolve()\n"
            "result=root / payload['result_path']\n"
            "log=root / payload['log_path']\n"
            "if not result.is_file():\n"
            "    raise SystemExit('EXPERIMENT_RESULT_MISSING:'+str(result))\n"
            "print(json.dumps({'result': json.loads(result.read_text(encoding='utf-8')), "
            "'log': log.read_text(encoding='utf-8') if log.is_file() else ''}))\n"
        )
        command = f"{shlex.quote(self.python_command())} -c {shlex.quote(reader)}"
        try:
            completed = subprocess.run(
                [*self._ssh_base(), command],
                input=json.dumps(
                    {
                        "root": remote_dir,
                        "result_path": result_path,
                        "log_path": log_path,
                    }
                ),
                check=True,
                capture_output=True,
                text=True,
                timeout=_probe_timeout(self.settings),
            )
            return json.loads(completed.stdout.strip())
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            raise RuntimeError("REMOTE_EXPERIMENT_RESULT_FETCH_FAILED") from exc

    def _run_bundle(self, task: dict, bundle: ExperimentBundle) -> dict:
        manifest = bundle.manifest
        if manifest.dataset_contract_id:
            raise RuntimeError(
                "REMOTE_LOCAL_DATASET_NOT_STAGED:"
                "The selected dataset is bound to a local directory. "
                "Use the local GPU provider or explicitly stage and re-inspect it on the remote host."
            )
        start = _now()
        remote_dir = self._bundle_workdir(bundle)
        deployed_files = self._deploy_bundle(bundle, remote_dir)
        missing = self._remote_missing_dependencies(bundle, remote_dir)
        if missing:
            install_command = shlex.join(
                [
                    self.python_command(),
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    posixpath.join(remote_dir, manifest.requirements_file),
                ]
            )
            raise RuntimeError(
                "REMOTE_EXPERIMENT_DEPENDENCY_MISSING:"
                f"{','.join(missing)}. Install it in the configured Remote Python environment with: "
                f"{install_command}"
            )

        data_root = ""
        if manifest.dataset:
            data_root = self._provision_remote_dataset(manifest.dataset)

        probe = self._remote_cuda_probe(remote_dir) if manifest.requires_gpu else None
        if probe and (not probe.available or probe.device_count < 1):
            raise RuntimeError("REMOTE_EXPERIMENT_CUDA_UNAVAILABLE")
        environment = {
            "provider": "remote_gpu",
            "dataset": manifest.dataset,
            "dataset_source": self.settings.dataset_source if manifest.dataset else "",
            "data_root": data_root,
            "python_version": probe.python_version if probe else "",
            "torch_version": probe.torch_version if probe else "",
            "torch_cuda": probe.torch_cuda if probe else "",
            "cuda_available": probe.available if probe else False,
            "device_count": probe.device_count if probe else 0,
            "device_names": probe.device_names if probe else [],
            "cuda_visible_devices": self.settings.remote_gpu_cuda_visible_devices,
            "remote_host": self.settings.remote_gpu_host,
            "runtime_contract_sha256": (
                bundle.runtime_contract.contract_sha256 if bundle.runtime_contract else ""
            ),
        }
        self._write_remote_environment(remote_dir, environment)

        log_path = f"logs/{manifest.experiment_id}.log"
        command_parts = build_python_command(self.python_command(), bundle)
        cuda_prefix = ""
        if self.settings.remote_gpu_cuda_visible_devices:
            cuda_prefix = (
                "CUDA_VISIBLE_DEVICES="
                f"{shlex.quote(self.settings.remote_gpu_cuda_visible_devices)} "
            )
        data_root_prefix = f"DATA_ROOT={shlex.quote(data_root)} " if data_root else ""
        remote_command = (
            f"cd {shlex.quote(remote_dir)} && mkdir -p results logs && "
            f"rm -f {shlex.quote(f'results/{manifest.result_id}.json')} && "
            f"{data_root_prefix}{cuda_prefix}{shlex.join(command_parts)} > {shlex.quote(log_path)} 2>&1"
        )
        try:
            subprocess.run(
                [*self._ssh_base(), remote_command],
                check=True,
                capture_output=True,
                text=True,
                timeout=_execution_timeout(self.settings),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("REMOTE_EXPERIMENT_TIMEOUT") from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError("REMOTE_EXPERIMENT_RUN_FAILED") from exc

        outputs = self._fetch_bundle_outputs(remote_dir, bundle)
        payload = validate_result_payload(outputs.get("result"), manifest)
        result_path = posixpath.join(remote_dir, "results", f"{manifest.result_id}.json")
        remote_log_path = posixpath.join(remote_dir, log_path)
        return {
            **payload,
            "provider": "remote_gpu",
            "is_real_experiment": True,
            "workdir": remote_dir,
            "command": command_parts,
            "parameters": manifest.parameters,
            "seeds": payload.get("seeds"),
            "environment": environment,
            "deployed_files": deployed_files,
            "metrics_path": result_path,
            "log_path": remote_log_path,
            "log": str(outputs.get("log") or ""),
            "start_time": start,
            "end_time": _now(),
            "task": task,
        }

    def run(self, task: dict, code: dict | ExperimentBundle | None = None) -> dict:
        if isinstance(code, ExperimentBundle):
            return self._run_bundle(task, code)
        raise RuntimeError("REMOTE_EXPERIMENT_BUNDLE_REQUIRED")

    def analyze(self, result: dict) -> dict:
        metrics = result.get("metrics", {})
        return {
            "summary": f"Real remote GPU run completed with metrics {metrics}.",
            "result": result,
        }


class LocalGpuExperimentProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def python_command(self) -> str:
        return self.settings.local_gpu_python

    def dataset_availability(self) -> list[dict]:
        root = Path(self.settings.dataset_dir).expanduser().resolve()
        return availability_status(root, self.settings.dataset_source)

    def plan(self, hypothesis: dict, constraints: dict) -> dict:
        seed = constraints.get("seed", 7)
        plan_dataset = hypothesis.get("dataset") if isinstance(hypothesis, dict) else None
        dataset = (
            str(plan_dataset.get("canonical_name") or "")
            if isinstance(plan_dataset, dict)
            else ""
        )
        return {
            "name": "local_neural_network_experiment",
            "hypothesis": hypothesis,
            "dataset": dataset,
            "seed": seed,
            "command": (
                f"{self.settings.local_gpu_python} train.py "
                f"--seed {seed} --output results/local_seed_{seed}.json"
            ),
            "metrics_path": f"results/local_seed_{seed}.json",
            "log_path": f"logs/local_seed_{seed}.log",
        }

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

    def recover_completed_result(
        self, task: dict, bundle: ExperimentBundle
    ) -> dict | None:
        """Recover a validated local attempt after its HTTP request was interrupted."""
        if any(smoke_data_reduction_issues(item.content) for item in bundle.files):
            return None
        manifest = bundle.manifest
        root = Path(self.settings.local_gpu_workdir).expanduser().resolve()
        experiment_dir = (root / manifest.run_id / manifest.experiment_id).resolve()
        if root not in experiment_dir.parents:
            return None
        status_path = experiment_dir / "runtime_status.json"
        if not status_path.is_file():
            return None
        try:
            status = read_json_retry(status_path)
            attempt_id = str(status.get("attempt_id") or "")
            if (
                status.get("state") != "completed"
                or status.get("exit_code") != 0
                or status.get("result_ready") is not True
                or status.get("run_id") != manifest.run_id
                or status.get("experiment_id") != manifest.experiment_id
                or not re.fullmatch(r"attempt_\d{8}T\d{6}_[0-9a-f]{8}", attempt_id)
            ):
                return None
            attempt_dir = (experiment_dir / "attempts" / attempt_id).resolve()
            if experiment_dir not in attempt_dir.parents:
                return None
            stored_manifest = json.loads(
                (attempt_dir / "manifest.json").read_text(encoding="utf-8")
            )
            if stored_manifest != manifest.model_dump():
                return None
            deployed_files = []
            for item in bundle.files:
                deployed = (attempt_dir / Path(*item.path.split("/"))).resolve()
                if attempt_dir not in deployed.parents or not deployed.is_file():
                    return None
                # Text deployment on Windows may translate LF to CRLF. Compare
                # normalized source hashes so byte-only line-ending changes do
                # not make an otherwise identical completed attempt unusable.
                deployed_source = deployed.read_text(encoding="utf-8")
                normalized_deployed = deployed_source.replace("\r\n", "\n").replace("\r", "\n")
                normalized_expected = item.content.replace("\r\n", "\n").replace("\r", "\n")
                digest = hashlib.sha256(normalized_deployed.encode("utf-8")).hexdigest()
                expected_digest = hashlib.sha256(normalized_expected.encode("utf-8")).hexdigest()
                if digest != expected_digest:
                    return None
                deployed_files.append(str(deployed))
            if bundle.runtime_contract is not None:
                harness = (attempt_dir / harness_file_path(bundle.runtime_contract)).resolve()
                if attempt_dir not in harness.parents or not harness.is_file():
                    return None
                expected_harness = harness_source(bundle.runtime_contract)
                actual_harness = harness.read_text(encoding="utf-8")
                if hashlib.sha256(actual_harness.encode("utf-8")).hexdigest() != hashlib.sha256(expected_harness.encode("utf-8")).hexdigest():
                    return None
                deployed_files.append(str(harness))

            result_path = attempt_dir / "results" / f"{manifest.result_id}.json"
            log_path = attempt_dir / "logs" / f"{manifest.experiment_id}.log"
            environment_path = attempt_dir / "environment.json"
            payload = validate_result_file(result_path, manifest)
            environment = (
                json.loads(environment_path.read_text(encoding="utf-8"))
                if environment_path.is_file()
                else {}
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError):
            return None

        command = build_python_command(self.settings.local_gpu_python, bundle)
        if bundle.runtime_contract is None:
            result_argument = command.index("--output") + 1
            command[result_argument] = str(Path("results") / f"{manifest.result_id}.json")
        return {
            **payload,
            "provider": "local_gpu",
            "is_real_experiment": True,
            "attempt_id": attempt_id,
            "workdir": str(experiment_dir),
            "command": command,
            "parameters": manifest.parameters,
            "seeds": payload.get("seeds"),
            "environment": environment,
            "deployed_files": deployed_files,
            "metrics_path": str(result_path),
            "log_path": str(log_path),
            "start_time": status.get("started_at"),
            "end_time": status.get("updated_at"),
            "task": task,
            "recovered_from_completed_attempt": True,
        }

    def run(self, task: dict, code: dict | ExperimentBundle | None = None) -> dict:
        if isinstance(code, ExperimentBundle):
            return self._run_bundle(task, code)
        start = _now()
        workdir = Path(self.settings.local_gpu_workdir).expanduser().resolve()
        if code:
            try:
                workdir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(f"LOCAL_EXPERIMENT_WORKDIR_INVALID: {workdir}") from exc
        elif not workdir.is_dir():
            raise RuntimeError(f"LOCAL_EXPERIMENT_WORKDIR_INVALID: {workdir}")
        deployed_files = self._deploy_generated_code(workdir, code) if code else []
        entrypoint = workdir / "train.py"
        if not entrypoint.is_file():
            raise RuntimeError(f"LOCAL_EXPERIMENT_ENTRYPOINT_INVALID: {entrypoint}")
        command = str((code or {}).get("command") or task["command"])
        try:
            completed = subprocess.run(
                command.split(),
                check=True,
                capture_output=True,
                text=True,
                timeout=_execution_timeout(self.settings),
                cwd=str(workdir),
            )
        except subprocess.CalledProcessError as exc:
            match = _MISSING_MODULE_PATTERN.search(exc.stderr or "")
            if match:
                module = match.group(1).split(".")[0]
                raise RuntimeError(
                    "LOCAL_EXPERIMENT_DEPENDENCY_MISSING:"
                    f"{module}. Install it in the configured Local Python environment with: "
                    f"{self.settings.local_gpu_python} -m pip install {module}"
                ) from exc
            raise RuntimeError(
                f"LOCAL_EXPERIMENT_RUN_FAILED: stdout={_tail(exc.stdout or '')} stderr={_tail(exc.stderr or '')}"
            ) from exc
        metrics = json.loads(completed.stdout.strip().splitlines()[-1])
        return {
            "provider": "local_gpu",
            "is_real_experiment": True,
            "task": task,
            "command": command,
            "metrics": metrics,
            "dataset": task.get("dataset", ""),
            "seed": task.get("seed"),
            "metrics_path": (code or {}).get("metrics_path") or task.get("metrics_path"),
            "log_path": (code or {}).get("log_path") or task.get("log_path"),
            "deployed_files": deployed_files,
            "start_time": start,
            "end_time": _now(),
            "environment": {"machine": socket.gethostname(), "python": platform.python_version()},
        }

    def _run_bundle(self, task: dict, bundle: ExperimentBundle) -> dict:
        manifest = bundle.manifest
        start = _now()
        root = Path(self.settings.local_gpu_workdir).expanduser().resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"LOCAL_EXPERIMENT_WORKDIR_INVALID: {root}") from exc
        experiment_dir = (root / manifest.run_id / manifest.experiment_id).resolve()
        if root not in experiment_dir.parents:
            raise RuntimeError("LOCAL_EXPERIMENT_WORKDIR_INVALID")
        experiment_dir.mkdir(parents=True, exist_ok=True)
        attempt_id = f"attempt_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
        attempt_dir = experiment_dir / "attempts" / attempt_id
        attempt_dir.mkdir(parents=True)
        (attempt_dir / "results").mkdir()
        (attempt_dir / "logs").mkdir()

        deployed_files = []
        for item in bundle.files:
            target = (attempt_dir / Path(*item.path.split("/"))).resolve()
            if attempt_dir not in target.parents:
                raise RuntimeError(f"EXPERIMENT_CODE_PATH_INVALID:{item.path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.content, encoding="utf-8")
            deployed_files.append(str(target))
        if bundle.runtime_contract is not None:
            harness_target = (attempt_dir / harness_file_path(bundle.runtime_contract)).resolve()
            if attempt_dir not in harness_target.parents:
                raise RuntimeError("HARNESS_PATH_INVALID")
            source = harness_source(bundle.runtime_contract)
            harness_target.write_text(source, encoding="utf-8")
            deployed_files.append(str(harness_target))
        (attempt_dir / "manifest.json").write_text(
            json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (attempt_dir / manifest.requirements_file).write_text(
            "".join(f"{requirement}\n" for requirement in bundle.requirements),
            encoding="utf-8",
        )

        env = os.environ.copy()
        if self.settings.local_gpu_cuda_visible_devices:
            env["CUDA_VISIBLE_DEVICES"] = self.settings.local_gpu_cuda_visible_devices
        python = self.settings.local_gpu_python
        missing = self._missing_dependencies(
            python, bundle.requirements, attempt_dir, env
        )
        if missing:
            requirements_path = attempt_dir / manifest.requirements_file
            command = subprocess.list2cmdline(
                [python, "-m", "pip", "install", "-r", str(requirements_path)]
            )
            raise RuntimeError(
                "LOCAL_EXPERIMENT_DEPENDENCY_MISSING:"
                f"{','.join(missing)}. Install it in the configured Local Python environment with: "
                f"{command}"
            )

        data_root = ""
        plan_dataset = (task.get("plan") or {}).get("dataset") or {}
        if manifest.dataset_contract_id:
            if self.settings.dataset_source != "local":
                raise RuntimeError(
                    "EXPERIMENT_DATASET_CONTRACT_SOURCE_INVALID:"
                    "Bound local datasets require dataset_source=local."
                )
            if manifest.dataset_contract_id != plan_dataset.get("contract_id"):
                raise RuntimeError("EXPERIMENT_DATASET_CONTRACT_MISMATCH")
            if (
                manifest.dataset_fingerprint
                and manifest.dataset_fingerprint != plan_dataset.get("content_fingerprint")
            ):
                raise RuntimeError("EXPERIMENT_DATASET_FINGERPRINT_MISMATCH")
            configured_root = Path(self.settings.dataset_dir).expanduser().resolve()
            contract_root = Path(
                str(plan_dataset.get("root") or self.settings.dataset_dir)
            ).expanduser().resolve()
            if configured_root != contract_root and configured_root not in contract_root.parents:
                raise RuntimeError("EXPERIMENT_DATASET_ROOT_OUTSIDE_CONFIGURED_ROOT")
            verified_profile = verify_dataset_contract(plan_dataset, str(contract_root))
            data_root = verified_profile["root"]
            env["DATA_ROOT"] = data_root
            env["GEWU_VERIFIED_DATA_ROOT"] = data_root
            env["DATASET_CONTRACT_ID"] = manifest.dataset_contract_id
            env["DATASET_FINGERPRINT"] = manifest.dataset_fingerprint
        elif manifest.dataset:
            data_root = self._provision_dataset(manifest.dataset, python, env)
            env["DATA_ROOT"] = data_root

        probe = None
        if manifest.requires_gpu:
            try:
                completed_probe = subprocess.run(
                    cuda_probe_command(python),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=_probe_timeout(self.settings),
                    cwd=str(attempt_dir),
                    env=env,
                )
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError("LOCAL_EXPERIMENT_CUDA_PROBE_FAILED") from exc
            probe = parse_cuda_probe(completed_probe.stdout)
            if not probe.available or probe.device_count < 1:
                raise RuntimeError("LOCAL_EXPERIMENT_CUDA_UNAVAILABLE")

        environment = {
            "provider": "local_gpu",
            "python_version": probe.python_version if probe else "",
            "torch_version": probe.torch_version if probe else "",
            "torch_cuda": probe.torch_cuda if probe else "",
            "cuda_available": probe.available if probe else False,
            "device_count": probe.device_count if probe else 0,
            "device_names": probe.device_names if probe else [],
            "cuda_visible_devices": self.settings.local_gpu_cuda_visible_devices,
            "machine": socket.gethostname(),
            "dataset": manifest.dataset,
            "dataset_contract_id": manifest.dataset_contract_id,
            "dataset_fingerprint": manifest.dataset_fingerprint,
            "dataset_source": (
                self.settings.dataset_source
                if manifest.dataset or manifest.dataset_contract_id
                else ""
            ),
            "data_root": data_root,
            "runtime_contract_sha256": (
                bundle.runtime_contract.contract_sha256 if bundle.runtime_contract else ""
            ),
        }
        (attempt_dir / "environment.json").write_text(
            json.dumps(environment, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        command = build_python_command(python, bundle)
        if bundle.runtime_contract is None:
            result_argument = command.index("--output") + 1
            command[result_argument] = str(Path("results") / f"{manifest.result_id}.json")
        log_path = attempt_dir / "logs" / f"{manifest.experiment_id}.log"
        result_path = attempt_dir / "results" / f"{manifest.result_id}.json"
        status_path = attempt_dir / "runtime_status.json"
        latest_status_path = experiment_dir / "runtime_status.json"
        lock_path = experiment_dir / ".experiment.lock"
        env["PYTHONUNBUFFERED"] = "1"
        started_at = _now()
        started_monotonic = time.monotonic()

        if manifest.supports_smoke_test:
            smoke_command = list(command)
            # The compiled Harness owns one deterministic final-result path for
            # both smoke and full modes.  Legacy Bundles receive a distinct
            # smoke output argument directly.
            smoke_result_path = (
                Path("results") / f"{manifest.result_id}.smoke.json"
                if bundle.runtime_contract is None
                else Path(bundle.runtime_contract.result_output_path)
            )
            # A compiled runtime contract invokes the system-owned harness, whose
            # only CLI flag is ``--smoke-test``.  The harness itself owns both
            # implementation and final-result paths.  Legacy Bundles still pass
            # the result path directly to train.py and therefore need replacement.
            if bundle.runtime_contract is None:
                smoke_command[smoke_command.index("--output") + 1] = str(
                    smoke_result_path
                )
            smoke_command.append("--smoke-test")
            smoke_log_path = attempt_dir / "logs" / "smoke-test.log"
            try:
                smoke = subprocess.run(
                    smoke_command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=str(attempt_dir),
                    env=env,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(
                    f"EXPERIMENT_BUNDLE_SMOKE_TEST_FAILED:{type(exc).__name__}"
                ) from exc
            smoke_log = (smoke.stdout or "") + (smoke.stderr or "")
            smoke_log_path.write_text(smoke_log, encoding="utf-8")
            if smoke.returncode != 0:
                raise RuntimeError(
                    "EXPERIMENT_BUNDLE_SMOKE_TEST_FAILED:"
                    + _tail(smoke_log)
                )
            try:
                validate_result_file(attempt_dir / smoke_result_path, manifest)
            except RuntimeError as exc:
                raise RuntimeError(
                    f"EXPERIMENT_BUNDLE_SMOKE_TEST_FAILED:{exc}"
                ) from exc

        def write_status(state: str, process_id: int, *, exit_code: int | None = None) -> None:
            payload = {
                "run_id": manifest.run_id,
                "experiment_id": manifest.experiment_id,
                "attempt_id": attempt_id,
                "state": state,
                "phase": "training" if state == "running" else state,
                "pid": process_id,
                "started_at": started_at,
                "updated_at": _now(),
                "elapsed_seconds": round(time.monotonic() - started_monotonic, 1),
                "timeout_seconds": self.settings.experiment_timeout_seconds,
                "log_path": str(log_path),
                "log_bytes": log_path.stat().st_size if log_path.exists() else 0,
                "result_path": str(result_path),
                "result_ready": result_path.is_file(),
                "exit_code": exit_code,
            }
            write_json_atomic(status_path, payload)
            write_json_atomic(latest_status_path, payload)

        timeout = _execution_timeout(self.settings)
        _acquire_experiment_lock(lock_path, attempt_id)
        try:
            # Only the lock owner may invalidate the compatibility result from a previous attempt.
            (experiment_dir / "results" / f"{manifest.result_id}.json").unlink(missing_ok=True)
            with log_path.open("w", encoding="utf-8") as log_stream:
                popen_options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
                process = subprocess.Popen(
                    command,
                    stdout=log_stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    cwd=str(attempt_dir),
                    env=env,
                    **popen_options,
                )
                with _ACTIVE_LOCAL_PROCESSES_LOCK:
                    _ACTIVE_LOCAL_PROCESSES[process.pid] = process
                _update_experiment_lock(lock_path, attempt_id, process.pid)
                write_status("running", process.pid)
                while process.poll() is None:
                    elapsed = time.monotonic() - started_monotonic
                    if timeout is not None and elapsed >= timeout:
                        _kill_process_tree(process)
                        write_status("timed_out", process.pid, exit_code=process.returncode)
                        raise RuntimeError("LOCAL_EXPERIMENT_TIMEOUT")
                    write_status("running", process.pid)
                    time.sleep(2)
                exit_code = process.returncode
        except BaseException:
            # A status-write failure or cancelled request must never leave the training tree behind.
            if "process" in locals():
                _kill_process_tree(process)
            raise
        finally:
            if "process" in locals():
                with _ACTIVE_LOCAL_PROCESSES_LOCK:
                    _ACTIVE_LOCAL_PROCESSES.pop(process.pid, None)
            lock_path.unlink(missing_ok=True)

        if exit_code != 0:
            if (attempt_dir / ".terminate_requested").is_file():
                write_status("terminated", process.pid, exit_code=exit_code)
                raise RuntimeError("LOCAL_EXPERIMENT_TERMINATED")
            write_status("failed", process.pid, exit_code=exit_code)
            combined_log = log_path.read_text(encoding="utf-8", errors="replace")
            match = _MISSING_MODULE_PATTERN.search(combined_log)
            if match:
                module = match.group(1).split(".")[0]
                requirements_path = attempt_dir / manifest.requirements_file
                raise RuntimeError(
                    "LOCAL_EXPERIMENT_DEPENDENCY_MISSING:"
                    f"{module}. Install it in the configured Local Python environment with: "
                    f"{subprocess.list2cmdline([python, '-m', 'pip', 'install', '-r', str(requirements_path)])}"
                )
            raise RuntimeError(
                f"LOCAL_EXPERIMENT_RUN_FAILED: log={_tail(combined_log)}"
            )
        payload = validate_result_file(result_path, manifest)
        write_status("completed", process.pid, exit_code=exit_code)
        legacy_log = experiment_dir / "logs" / f"{manifest.experiment_id}.log"
        legacy_result = experiment_dir / "results" / f"{manifest.result_id}.json"
        legacy_log.parent.mkdir(exist_ok=True)
        legacy_result.parent.mkdir(exist_ok=True)
        legacy_log.write_text(log_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        legacy_result.write_text(result_path.read_text(encoding="utf-8"), encoding="utf-8")
        return {
            **payload,
            "provider": "local_gpu",
            "is_real_experiment": True,
            "attempt_id": attempt_id,
            "workdir": str(experiment_dir),
            "command": command,
            "parameters": manifest.parameters,
            "seeds": payload.get("seeds", manifest.seeds),
            "environment": environment,
            "deployed_files": deployed_files,
            "metrics_path": str(result_path),
            "log_path": str(log_path),
            "start_time": start,
            "end_time": _now(),
            "task": task,
        }

    def _provision_dataset(self, name: str, python: str, env: dict[str, str]) -> str:
        spec = dataset_spec(name)
        root = Path(self.settings.dataset_dir).expanduser().resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"EXPERIMENT_DATASET_DIR_INVALID:{root}") from exc
        if dataset_present(root, name):
            return str(root)
        if self.settings.dataset_source == "local":
            raise RuntimeError(
                f"EXPERIMENT_DATASET_LOCAL_MISSING:{name}. Place the dataset under {root} "
                f"(expected directory '{spec.marker}'), or switch the dataset source to online "
                "so the backend downloads it once into the shared cache."
            )
        try:
            subprocess.run(
                [
                    python,
                    "-c",
                    dataset_download_script(),
                    str(root),
                    name,
                    self.settings.dataset_mirror_url if self.settings.dataset_source == "auto" else "",
                    str(self.settings.dataset_download_retries),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=_execution_timeout(self.settings),
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"EXPERIMENT_DATASET_DOWNLOAD_TIMEOUT:{name}") from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            stderr = _tail(getattr(exc, "stderr", "") or "")
            raise RuntimeError(
                f"EXPERIMENT_DATASET_DOWNLOAD_FAILED:{name}. {stderr}"
            ) from exc
        if not dataset_present(root, name):
            raise RuntimeError(
                f"EXPERIMENT_DATASET_DOWNLOAD_FAILED:{name}. Download finished but the dataset "
                f"marker '{spec.marker}' is missing under {root}."
            )
        return str(root)

    def quarantine_failed_dataset_download(self, name: str) -> dict:
        """Move known incomplete download artifacts to a recoverable quarantine.

        The target list comes from the whitelisted dataset catalog. No caller-
        supplied path is accepted, and every resolved target is verified to
        remain inside the configured dataset root.
        """
        spec = dataset_spec(name)
        root = Path(self.settings.dataset_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        if dataset_present(root, spec.name):
            return {
                "status": "not_needed",
                "dataset": spec.name,
                "moved": [],
                "reason": "dataset marker is already present",
            }

        quarantine = (
            root
            / ".failed-downloads"
            / spec.name
            / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        ).resolve()
        moved = []
        for relative_name in spec.download_artifacts:
            source = (root / Path(*relative_name.split("/"))).resolve()
            if root not in source.parents or not source.is_file():
                continue
            target = (quarantine / source.relative_to(root)).resolve()
            if quarantine not in target.parents:
                raise RuntimeError("EXPERIMENT_DATASET_REPAIR_PATH_INVALID")
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            moved.append({"from": str(source), "to": str(target)})

        return {
            "status": "completed",
            "dataset": spec.name,
            "moved": moved,
            "quarantine": str(quarantine) if moved else "",
            "reason": (
                "known incomplete download artifacts quarantined"
                if moved
                else "no known partial download artifact was present"
            ),
        }

    def _missing_dependencies(
        self,
        python: str,
        requirements: list[str],
        workdir: Path,
        env: dict[str, str],
    ) -> list[str]:
        modules = [_requirement_module(value) for value in requirements]
        modules = [value for value in modules if value]
        if not modules:
            return []
        script = (
            "import importlib.util, json; "
            f"modules={json.dumps(modules)}; "
            "print(json.dumps({'missing': [m for m in modules if importlib.util.find_spec(m) is None]}))"
        )
        try:
            completed = subprocess.run(
                [python, "-c", script],
                check=True,
                capture_output=True,
                text=True,
                timeout=_probe_timeout(self.settings),
                cwd=str(workdir),
                env=env,
            )
            payload = json.loads(completed.stdout.strip())
            return [str(item) for item in payload.get("missing") or []]
        except Exception as exc:
            raise RuntimeError("LOCAL_EXPERIMENT_DEPENDENCY_PROBE_FAILED") from exc

    def analyze(self, result: dict) -> dict:
        return {"summary": f"Real local run completed with metrics {result.get('metrics', {})}.", "result": result}


def get_experiment_provider(settings: Settings) -> ExperimentProvider:
    if settings.experiment_provider == "mock":
        return MockExperimentProvider()
    if settings.experiment_provider == "local_gpu":
        return LocalGpuExperimentProvider(settings)
    return RemoteGpuExperimentProvider(settings)


def _requirement_module(requirement: str) -> str:
    name = re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].strip().replace("-", "_")
    return {"pillow": "PIL", "scikit_learn": "sklearn"}.get(name.lower(), name)


def _combined_log(stdout: str, stderr: str) -> str:
    if stdout and stderr:
        return f"{stdout}\n{stderr}"
    return stdout or stderr
