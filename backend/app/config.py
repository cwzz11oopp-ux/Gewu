from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Any


def _bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    competition_mode: bool
    llm_provider: str
    qwen_api_key: str
    qwen_base_url: str
    qwen_model: str
    qwen_reasoning_model: str
    qwen_code_model: str
    qwen_code_fallback_model: str
    qwen_fast_model: str
    qwen_max_tokens: int
    qwen_timeout_seconds: int
    qwen_reasoning_timeout_seconds: int
    qwen_general_timeout_seconds: int
    qwen_code_timeout_seconds: int
    qwen_fast_timeout_seconds: int
    qwen_retries_per_model: int
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    max_deepseek_plan_revision: int
    model_role_assignments: dict[str, dict[str, str]]
    feedback_max_iterations: int
    literature_provider: str
    semantic_scholar_api_key: str
    arxiv_max_results: int
    experiment_provider: str
    experiment_timeout_seconds: int
    dataset_source: str
    dataset_dir: str
    dataset_mirror_url: str
    dataset_download_retries: int
    remote_gpu_host: str
    remote_gpu_user: str
    remote_gpu_port: int
    remote_gpu_ssh_key_path: str
    remote_gpu_project_dir: str
    remote_gpu_python: str
    remote_gpu_cuda_visible_devices: str
    local_gpu_enabled: bool
    local_gpu_workdir: str
    local_gpu_python: str
    local_gpu_cuda_visible_devices: str
    data_dir: str
    allowed_origins: tuple[str, ...]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        return cls(
            competition_mode=_bool(source.get("COMPETITION_MODE"), True),
            llm_provider=source.get("LLM_PROVIDER", "qwen"),
            qwen_api_key=source.get("QWEN_API_KEY", ""),
            qwen_base_url=source.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            qwen_model=source.get(
                "QWEN_GENERAL_MODEL",
                source.get("QWEN_MODEL", "qwen3.7-plus"),
            ),
            qwen_reasoning_model=source.get("QWEN_REASONING_MODEL", "qwen3.7-max"),
            qwen_code_model=source.get("QWEN_CODE_MODEL", "qwen3-coder-plus"),
            qwen_code_fallback_model=source.get(
                "QWEN_CODE_FALLBACK_MODEL", "qwen3-coder-flash"
            ),
            qwen_fast_model=source.get("QWEN_FAST_MODEL", "qwen3.6-flash"),
            qwen_max_tokens=int(source.get("QWEN_MAX_TOKENS", "0")),
            qwen_timeout_seconds=int(source.get("QWEN_TIMEOUT_SECONDS", "300")),
            qwen_reasoning_timeout_seconds=int(source.get(
                "QWEN_REASONING_TIMEOUT_SECONDS",
                source.get("QWEN_TIMEOUT_SECONDS", "600"),
            )),
            qwen_general_timeout_seconds=int(source.get(
                "QWEN_GENERAL_TIMEOUT_SECONDS",
                source.get("QWEN_TIMEOUT_SECONDS", "300"),
            )),
            qwen_code_timeout_seconds=int(source.get(
                "QWEN_CODE_TIMEOUT_SECONDS",
                source.get("QWEN_TIMEOUT_SECONDS", "600"),
            )),
            qwen_fast_timeout_seconds=int(source.get(
                "QWEN_FAST_TIMEOUT_SECONDS",
                source.get("QWEN_TIMEOUT_SECONDS", "120"),
            )),
            qwen_retries_per_model=max(
                0, int(source.get("QWEN_RETRIES_PER_MODEL", "1"))
            ),
            deepseek_api_key=source.get("DEEPSEEK_API_KEY", ""),
            deepseek_base_url=source.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            deepseek_model=source.get("DEEPSEEK_MODEL", "deepseek-chat"),
            max_deepseek_plan_revision=max(
                0, int(source.get("MAX_DEEPSEEK_PLAN_REVISION", "2"))
            ),
            model_role_assignments={},
            feedback_max_iterations=max(
                1, int(source.get("FEEDBACK_MAX_ITERATIONS", "4"))
            ),
            literature_provider=source.get("LITERATURE_PROVIDER", "arxiv_semantic_scholar"),
            semantic_scholar_api_key=source.get("SEMANTIC_SCHOLAR_API_KEY", ""),
            arxiv_max_results=int(source.get("ARXIV_MAX_RESULTS", "10")),
            experiment_provider=source.get("EXPERIMENT_PROVIDER", "remote_gpu"),
            experiment_timeout_seconds=int(source.get("EXPERIMENT_TIMEOUT_SECONDS", "0")),
            dataset_source=source.get("EXPERIMENT_DATASET_SOURCE", "auto"),
            dataset_dir=source.get("EXPERIMENT_DATASET_DIR", "datasets"),
            dataset_mirror_url=source.get("EXPERIMENT_DATASET_MIRROR_URL", ""),
            dataset_download_retries=int(source.get("EXPERIMENT_DATASET_DOWNLOAD_RETRIES", "5")),
            remote_gpu_host=source.get("REMOTE_GPU_HOST", ""),
            remote_gpu_user=source.get("REMOTE_GPU_USER", ""),
            remote_gpu_port=int(source.get("REMOTE_GPU_PORT", "22")),
            remote_gpu_ssh_key_path=source.get("REMOTE_GPU_SSH_KEY_PATH", ""),
            remote_gpu_project_dir=source.get("REMOTE_GPU_PROJECT_DIR", ""),
            remote_gpu_python=source.get("REMOTE_GPU_PYTHON", "python"),
            remote_gpu_cuda_visible_devices=source.get("REMOTE_GPU_CUDA_VISIBLE_DEVICES", ""),
            local_gpu_enabled=_bool(source.get("LOCAL_GPU_ENABLED"), False),
            local_gpu_workdir=source.get("LOCAL_EXPERIMENT_WORKDIR", ""),
            local_gpu_python=source.get("LOCAL_GPU_PYTHON", "python"),
            local_gpu_cuda_visible_devices=source.get("LOCAL_GPU_CUDA_VISIBLE_DEVICES", ""),
            data_dir=source.get("DATA_DIR", "backend/data"),
            allowed_origins=tuple(
                origin.strip()
                for origin in source.get(
                    "ALLOWED_ORIGINS",
                    "http://127.0.0.1:5173,http://localhost:5173",
                ).split(",")
                if origin.strip()
            ),
        )

    def provider_status(self) -> dict[str, dict[str, object]]:
        literature_ready = self.literature_provider != "mock" or not self.competition_mode
        experiment_ready = self._experiment_ready()
        return {
            "llm": {
                "mode": self.llm_provider,
                "ready": self.llm_provider != "qwen" or bool(self.qwen_api_key),
                "code": "" if self.llm_provider != "qwen" or self.qwen_api_key else "QWEN_API_KEY_MISSING",
            },
            "literature": {
                "mode": self.literature_provider,
                "ready": literature_ready,
                "code": "" if literature_ready else "VERIFIED_LITERATURE_REQUIRED",
            },
            "experiment": experiment_ready,
        }

    def _experiment_ready(self) -> dict[str, object]:
        if self.experiment_provider == "mock":
            if self.competition_mode:
                return {
                    "mode": "mock",
                    "ready": False,
                    "code": "REAL_EXPERIMENT_REQUIRED",
                }
            return {
                "mode": "mock",
                "ready": True,
                "warning": "Mock experiment results are development fixtures.",
            }
        if self.experiment_provider == "remote_gpu":
            missing = [
                name
                for name, value in {
                    "REMOTE_GPU_HOST": self.remote_gpu_host,
                    "REMOTE_GPU_USER": self.remote_gpu_user,
                    "REMOTE_GPU_PROJECT_DIR": self.remote_gpu_project_dir,
                }.items()
                if not value
            ]
            return {
                "mode": "remote_gpu",
                "ready": not missing,
                "missing": missing,
                "code": "" if not missing else "REMOTE_GPU_CONFIG_MISSING",
            }
        if self.experiment_provider == "local_gpu":
            configured_workdir = self.local_gpu_workdir
            resolved_workdir = Path(configured_workdir).expanduser().resolve() if configured_workdir else None
            if not self.local_gpu_enabled:
                return {
                    "mode": "local_gpu",
                    "ready": False,
                    "code": "LOCAL_GPU_DISABLED",
                    "workdir": configured_workdir,
                    "resolved_workdir": str(resolved_workdir) if resolved_workdir else "",
                }
            if resolved_workdir is None:
                return {
                    "mode": "local_gpu",
                    "ready": False,
                    "code": "LOCAL_EXPERIMENT_WORKDIR_INVALID",
                    "workdir": configured_workdir,
                    "resolved_workdir": str(resolved_workdir) if resolved_workdir else "",
                }
            if self.local_gpu_cuda_visible_devices and not re.fullmatch(
                r"\d+(?:,\d+)*", self.local_gpu_cuda_visible_devices
            ):
                return {
                    "mode": "local_gpu",
                    "ready": False,
                    "code": "LOCAL_GPU_DEVICE_INDEX_INVALID",
                    "workdir": configured_workdir,
                    "resolved_workdir": str(resolved_workdir),
                }
            entrypoint = resolved_workdir / "train.py"
            return {
                "mode": "local_gpu",
                "ready": True,
                "code": "",
                "workdir": configured_workdir,
                "resolved_workdir": str(resolved_workdir),
                "workdir_exists": resolved_workdir.is_dir(),
                "python": self.local_gpu_python,
                "cuda_visible_devices": self.local_gpu_cuda_visible_devices,
                "entrypoint": str(entrypoint),
                "entrypoint_exists": entrypoint.is_file(),
                "dataset_source": self.dataset_source,
                "dataset_dir": self.dataset_dir,
                "dataset_mirror_url": self.dataset_mirror_url,
            }
        return {
            "mode": self.experiment_provider,
            "ready": False,
            "code": "UNKNOWN_EXPERIMENT_PROVIDER",
        }
