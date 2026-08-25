from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.app.errors import not_found
from backend.app.runtime_status import read_json_retry, write_json_atomic


class CreateRunRequest(BaseModel):
    title: str
    problem_input: str
    domain: str = ""
    constraints: str = ""
    github_repository_url: str | None = None
    research_constraints: dict = Field(default_factory=dict)
    knowledge_base_id: str = Field(default="default", min_length=1, max_length=100)


class FeedbackRequest(BaseModel):
    message: str


class UserHypothesisRequest(BaseModel):
    claim: str
    replacement_index: int | None = None


class HypothesisSelectionRequest(BaseModel):
    candidate_index: int


class TerminateExperimentRequest(BaseModel):
    clear_attempt: bool = False


def _owned_process_command(pid: int) -> str:
    if os.name == "nt":
        probe = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return probe.stdout.strip()
    command_path = Path(f"/proc/{pid}/cmdline")
    if not command_path.is_file():
        return ""
    return command_path.read_bytes().replace(b"\0", b" ").decode(errors="replace")


def _terminate_owned_experiment_process(pid: int, run_id: str, experiment_id: str) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    command = _owned_process_command(pid)
    if f"--run-id {run_id}" not in command or f"--experiment-id {experiment_id}" not in command:
        raise RuntimeError("EXPERIMENT_PROCESS_OWNERSHIP_MISMATCH")
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(pid), 15)
        except OSError:
            os.kill(pid, 15)
    return True


def conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": str(exc), "message": str(exc)})


def build_router(deps) -> APIRouter:
    router = APIRouter(prefix="/api/runs")

    @router.post("")
    def create_run(body: CreateRunRequest):
        return deps.repository.create_run(
            problem_input=body.problem_input,
            title=body.title,
            domain=body.domain,
            constraints=body.constraints,
            github_repository_url=body.github_repository_url,
            research_constraints=body.research_constraints,
            knowledge_base_id=body.knowledge_base_id,
        )

    @router.get("")
    def list_runs():
        return deps.repository.list_runs()

    @router.get("/{run_id}")
    def get_run(run_id: str):
        try:
            return deps.repository.get_run(run_id)
        except KeyError:
            raise not_found("run", run_id)

    @router.get("/{run_id}/experiment-progress")
    def experiment_progress(run_id: str):
        if not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id):
            raise not_found("run", run_id)
        run_root = (Path(deps.settings.local_gpu_workdir).expanduser().resolve() / run_id).resolve()
        if not run_root.is_dir():
            return {"state": "idle", "run_id": run_id}
        candidates = sorted(run_root.glob("*/runtime_status.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not candidates:
            return {"state": "idle", "run_id": run_id}
        status_path = candidates[0]
        try:
            status = read_json_retry(status_path)
        except (OSError, ValueError):
            return {"state": "unknown", "run_id": run_id}
        pid = int(status.get("pid") or 0)
        alive = False
        if pid > 0:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        heartbeat_age_seconds = None
        try:
            updated_at = datetime.fromisoformat(str(status.get("updated_at") or ""))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            heartbeat_age_seconds = max(0.0, (datetime.now(timezone.utc) - updated_at).total_seconds())
        except ValueError:
            pass
        reported_state = str(status.get("state") or "unknown")
        if reported_state in {"completed", "failed", "timed_out", "terminated"}:
            alive = False
        state = reported_state
        if reported_state == "running" and heartbeat_age_seconds is not None and heartbeat_age_seconds > 15:
            state = "orphaned"
        elif reported_state == "running" and not alive:
            state = "failed"
        log_path = Path(str(status.get("log_path") or ""))
        log_tail = ""
        if log_path.is_file():
            try:
                log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                pass
        gpu = {}
        if alive:
            try:
                probe = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=True,
                )
                values = [part.strip() for part in probe.stdout.splitlines()[0].split(",")]
                gpu = {
                    "utilization_percent": int(values[0]),
                    "memory_used_mb": int(values[1]),
                    "memory_total_mb": int(values[2]),
                    "temperature_c": int(values[3]),
                }
            except (OSError, ValueError, IndexError, subprocess.SubprocessError):
                pass
        return {
            **status,
            "state": state,
            "process_alive": alive,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "healthy": state == "running" and alive,
            "log_tail": log_tail,
            "gpu": gpu,
        }

    @router.get("/{run_id}/experiment-files/{file_kind}")
    def download_experiment_file(run_id: str, file_kind: str):
        if not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id):
            raise not_found("run", run_id)
        if file_kind not in {"result", "log", "code", "manifest", "environment"}:
            raise not_found("experiment file", file_kind)
        root = Path(deps.settings.local_gpu_workdir).expanduser().resolve()
        run_root = (root / run_id).resolve()
        if root not in run_root.parents or not run_root.is_dir():
            raise not_found("run", run_id)
        statuses = sorted(run_root.glob("*/runtime_status.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not statuses:
            raise not_found("experiment file", file_kind)
        experiment_dir = statuses[0].parent.resolve()
        patterns = {
            "result": "results/*.json",
            "log": "logs/*.log",
            "code": "attempts/*/train.py",
            "manifest": "attempts/*/manifest.json",
            "environment": "attempts/*/environment.json",
        }
        candidates = sorted(experiment_dir.glob(patterns[file_kind]), key=lambda path: path.stat().st_mtime, reverse=True)
        if not candidates:
            raise not_found("experiment file", file_kind)
        target = candidates[0].resolve()
        if experiment_dir not in target.parents or not target.is_file():
            raise not_found("experiment file", file_kind)
        media_types = {
            "result": "application/json",
            "log": "text/plain; charset=utf-8",
            "code": "text/x-python; charset=utf-8",
            "manifest": "application/json",
            "environment": "application/json",
        }
        return FileResponse(target, media_type=media_types[file_kind], filename=target.name)

    @router.post("/{run_id}/experiments/{experiment_id}/terminate")
    def terminate_experiment(run_id: str, experiment_id: str, body: TerminateExperimentRequest):
        if not re.fullmatch(r"run_[A-Za-z0-9_-]+", run_id):
            raise not_found("run", run_id)
        if not re.fullmatch(r"experiment_[A-Za-z0-9_-]+", experiment_id):
            raise not_found("experiment", experiment_id)
        root = Path(deps.settings.local_gpu_workdir).expanduser().resolve()
        experiment_dir = (root / run_id / experiment_id).resolve()
        if root not in experiment_dir.parents or not experiment_dir.is_dir():
            raise not_found("experiment", experiment_id)
        status_path = experiment_dir / "runtime_status.json"
        try:
            status = read_json_retry(status_path)
        except (OSError, ValueError) as exc:
            raise conflict(RuntimeError("EXPERIMENT_RUNTIME_STATUS_UNAVAILABLE")) from exc
        attempt_id = str(status.get("attempt_id") or "")
        if not re.fullmatch(r"attempt_[A-Za-z0-9_-]+", attempt_id):
            raise conflict(RuntimeError("EXPERIMENT_ATTEMPT_ID_INVALID"))
        attempt_dir = (experiment_dir / "attempts" / attempt_id).resolve()
        if experiment_dir not in attempt_dir.parents:
            raise conflict(RuntimeError("EXPERIMENT_ATTEMPT_PATH_INVALID"))
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / ".terminate_requested").write_text(
            json.dumps({"requested_at": datetime.now(timezone.utc).isoformat()}),
            encoding="utf-8",
        )
        pid = int(status.get("pid") or 0)
        try:
            terminated = _terminate_owned_experiment_process(pid, run_id, experiment_id)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise conflict(exc) from exc
        terminal_status = {
            **status,
            "state": "terminated",
            "phase": "terminated",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "terminated_by_user": True,
            "process_alive": False,
        }
        write_json_atomic(status_path, terminal_status)
        attempt_status = attempt_dir / "runtime_status.json"
        write_json_atomic(attempt_status, terminal_status)
        cleared = False
        if body.clear_attempt:
            # Give the owning provider loop one polling interval to observe the termination marker.
            time.sleep(2.2)
            latest = read_json_retry(status_path) if status_path.is_file() else {}
            if latest.get("attempt_id") != attempt_id:
                raise conflict(RuntimeError("EXPERIMENT_ATTEMPT_CHANGED"))
            shutil.rmtree(attempt_dir)
            status_path.unlink(missing_ok=True)
            cleared = True
        return {
            "terminated": terminated,
            "cleared": cleared,
            "run_id": run_id,
            "experiment_id": experiment_id,
            "attempt_id": attempt_id,
        }

    @router.delete("/{run_id}")
    def delete_run(run_id: str):
        try:
            deps.repository.delete_run(run_id)
        except KeyError:
            raise not_found("run", run_id)
        return {"deleted": True, "run_id": run_id}

    @router.post("/{run_id}/steps/{step_id}/run")
    def run_step(run_id: str, step_id: str):
        try:
            deps.sync_model_config()
            return deps.engine.run_step(run_id, step_id)
        except ValueError as exc:
            raise conflict(exc)
        except RuntimeError as exc:
            raise conflict(exc)

    @router.post("/{run_id}/pipeline/start")
    def start_pipeline(run_id: str):
        try:
            return deps.orchestrator.start(run_id)
        except KeyError:
            raise not_found("run", run_id)
        except (ValueError, RuntimeError) as exc:
            raise conflict(exc)

    @router.post("/{run_id}/preflight")
    def preflight(run_id: str):
        try:
            deps.sync_model_config()
            return deps.engine.preflight_run(run_id)
        except KeyError:
            raise not_found("run", run_id)
        except (ValueError, RuntimeError) as exc:
            raise conflict(exc)

    @router.post("/{run_id}/pipeline/stop")
    def stop_pipeline(run_id: str):
        try:
            return deps.orchestrator.stop(run_id)
        except KeyError:
            raise not_found("run", run_id)

    @router.post("/{run_id}/steps/{step_id}/rerun-from")
    def rerun_from(run_id: str, step_id: str):
        try:
            deps.sync_model_config()
            return deps.engine.rerun_from(run_id, step_id)
        except ValueError as exc:
            raise conflict(exc)
        except RuntimeError as exc:
            raise conflict(exc)

    @router.post("/{run_id}/hypotheses/user")
    def add_user_hypothesis(run_id: str, body: UserHypothesisRequest):
        try:
            return deps.engine.add_user_hypothesis(run_id, body.claim, body.replacement_index)
        except KeyError:
            raise not_found("run", run_id)
        except ValueError as exc:
            raise conflict(exc)

    @router.post("/{run_id}/hypotheses/select")
    def select_hypothesis(run_id: str, body: HypothesisSelectionRequest):
        try:
            deps.engine.select_hypothesis(run_id, body.candidate_index)
            return deps.orchestrator.start(run_id)
        except KeyError:
            raise not_found("run", run_id)
        except ValueError as exc:
            raise conflict(exc)

    @router.post("/{run_id}/hypotheses/regenerate")
    def regenerate_hypotheses(run_id: str):
        try:
            deps.engine.regenerate_hypotheses(run_id)
            return deps.orchestrator.start(run_id)
        except KeyError:
            raise not_found("run", run_id)
        except ValueError as exc:
            raise conflict(exc)

    @router.post("/{run_id}/feedback")
    def add_feedback(run_id: str, body: FeedbackRequest):
        deps.repository.append_event(run_id, "human_feedback", "human", body.message)
        return deps.repository.get_run(run_id)

    @router.get("/{run_id}/events")
    def list_events(run_id: str):
        return deps.repository.get_run(run_id).events

    return router
