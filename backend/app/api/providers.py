from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import httpx

from backend.app.providers.experiment import validate_remote_gpu_settings, validate_local_gpu_preflight
from backend.app.providers.experiment_runtime import cuda_probe_command, parse_cuda_probe
from backend.app.workflow.dataset_inspection import (
    DatasetInspectionError,
    inspect_dataset_directory,
)


class RemoteExperimentConfig(BaseModel):
    host: str = ""
    user: str = ""
    port: int = 22
    ssh_key_path: str = ""
    project_dir: str = ""
    python: str = "python"
    cuda_visible_devices: str = ""
    timeout_seconds: int = Field(default=0, ge=0)


class LocalExperimentConfig(BaseModel):
    enabled: bool = False
    workdir: str = "experiments"
    python: str = "python"
    cuda_visible_devices: str = ""
    timeout_seconds: int = Field(default=0, ge=0)


class DatasetConfig(BaseModel):
    source: str = Field(default="auto_local", pattern="^(auto|auto_local|official|online|local)$")
    dir: str = "datasets"
    mirror_url: str = ""
    download_retries: int = Field(default=5, ge=1, le=10)
    allow_online_dataset_download: bool = False


class ExperimentSettingsRequest(BaseModel):
    provider: str = Field(default="remote_gpu", pattern="^(remote_gpu|local_gpu|mock)$")
    remote: RemoteExperimentConfig = Field(default_factory=RemoteExperimentConfig)
    local: LocalExperimentConfig = Field(default_factory=LocalExperimentConfig)
    dataset: DatasetConfig = Field(default_factory=DatasetConfig)


class QwenKeyRequest(BaseModel):
    api_key: str
    base_url: str | None = None
    model: str | None = None


class ModelProviderRequest(BaseModel):
    provider_id: str
    provider_type: str = "openai_compatible"
    display_name: str = ""
    base_url: str
    api_key: str = ""
    models: list[str] = Field(default_factory=list)
    enabled: bool = True
    connection_policy: dict[str, Any] = Field(default_factory=dict)


class ModelRoleRequest(BaseModel):
    provider_id: str
    model: str


def _masked_provider(item: dict[str, Any], configured: bool) -> dict[str, Any]:
    return {**item, "api_key": "********" if configured else "", "configured": configured}


def _default_model_providers(settings) -> list[dict[str, Any]]:
    return [
        {"provider_id": "qwen", "provider_type": "openai_compatible", "display_name": "Qwen",
         "base_url": settings.qwen_base_url, "models": list(dict.fromkeys([
             settings.qwen_model, settings.qwen_reasoning_model, settings.qwen_code_model,
             settings.qwen_code_fallback_model, settings.qwen_fast_model])), "enabled": True,
         "connection_policy": {"timeout_seconds": settings.qwen_timeout_seconds}},
        {"provider_id": "deepseek", "provider_type": "openai_compatible", "display_name": "DeepSeek",
         "base_url": settings.deepseek_base_url, "models": [settings.deepseek_model], "enabled": True,
         "connection_policy": {"timeout_seconds": settings.qwen_reasoning_timeout_seconds}},
    ]


def _experiment_test_result(config: dict[str, Any], base_settings) -> dict[str, Any]:
    dataset = config.get("dataset") or {}
    dataset_profile = None
    if str(dataset.get("source") or "auto_local") in {"local", "auto_local"}:
        try:
            dataset_profile = inspect_dataset_directory(
                str(dataset.get("dir") or "")
            )
        except DatasetInspectionError as exc:
            return {
                "ok": False,
                "provider": config.get("provider") or "",
                "code": str(exc).split(":", 1)[0],
                "missing": [],
                "message": str(exc),
            }
    provider = config.get("provider")
    if provider == "remote_gpu":
        remote = config.get("remote") or {}
        required = {
            "REMOTE_GPU_HOST": remote.get("host"),
            "REMOTE_GPU_USER": remote.get("user"),
            "REMOTE_GPU_PROJECT_DIR": remote.get("project_dir"),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            return {
                "ok": False,
                "provider": provider,
                "code": "REMOTE_GPU_CONFIG_MISSING",
                "missing": missing,
                "message": "Remote GPU config is missing required fields.",
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
        result = validate_remote_gpu_settings(settings)
        if dataset_profile:
            result["dataset_profile"] = dataset_profile
        return result
    if provider == "local_gpu":
        result = _local_experiment_test_result(config.get("local") or {}, base_settings=base_settings)
        if dataset_profile:
            result["dataset_profile"] = dataset_profile
        return result
    result = {"ok": provider == "mock", "provider": provider, "missing": [], "message": "Mock provider is for development."}
    if dataset_profile:
        result["dataset_profile"] = dataset_profile
    return result


def _local_experiment_test_result(
    local: dict[str, Any], *, probe_cuda: bool = True, base_settings=None
) -> dict[str, Any]:
    if probe_cuda and base_settings is not None:
        settings = replace(
            base_settings,
            local_gpu_enabled=bool(local.get("enabled", False)),
            local_gpu_workdir=str(local.get("workdir") or ""),
            local_gpu_python=str(local.get("python") or "python"),
            local_gpu_cuda_visible_devices=str(local.get("cuda_visible_devices") or ""),
            experiment_timeout_seconds=int(local.get("timeout_seconds") or 1200),
        )
        return validate_local_gpu_preflight(settings)
    configured_workdir = str(local.get("workdir") or "")
    resolved_workdir = Path(configured_workdir).expanduser().resolve() if configured_workdir else None
    if not configured_workdir or resolved_workdir is None:
        return {
            "ok": False,
            "provider": "local_gpu",
            "code": "LOCAL_EXPERIMENT_WORKDIR_INVALID",
            "missing": ["LOCAL_EXPERIMENT_WORKDIR"],
            "workdir": configured_workdir,
            "resolved_workdir": str(resolved_workdir) if resolved_workdir else "",
            "message": "Local experiment workdir is invalid. Choose an existing directory.",
        }
    if not local.get("enabled"):
        return {
            "ok": False,
            "provider": "local_gpu",
            "code": "LOCAL_GPU_DISABLED",
            "missing": ["LOCAL_GPU_ENABLED"],
            "workdir": configured_workdir,
            "resolved_workdir": str(resolved_workdir),
            "message": "Local GPU is disabled.",
        }
    try:
        resolved_workdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {
            "ok": False,
            "provider": "local_gpu",
            "code": "LOCAL_EXPERIMENT_WORKDIR_INVALID",
            "missing": ["LOCAL_EXPERIMENT_WORKDIR"],
            "workdir": configured_workdir,
            "resolved_workdir": str(resolved_workdir),
            "message": f"Local experiment workdir cannot be created: {exc}",
        }
    entrypoint = resolved_workdir / "train.py"
    base = {
        "ok": True,
        "provider": "local_gpu",
        "code": "",
        "missing": [],
        "workdir": configured_workdir,
        "resolved_workdir": str(resolved_workdir),
        "entrypoint": str(entrypoint),
        "entrypoint_exists": entrypoint.is_file(),
    }
    configured_devices = str(local.get("cuda_visible_devices") or "")
    if configured_devices and not re.fullmatch(r"\d+(?:,\d+)*", configured_devices):
        return {
            **base,
            "ok": False,
            "code": "LOCAL_GPU_DEVICE_INDEX_INVALID",
            "message": "CUDA device indexes must use values such as 0 or 0,1.",
        }
    if not probe_cuda:
        return {
            **base,
            "message": "Local experiment workdir is ready. train.py will be generated during the experiment run.",
        }

    python = str(local.get("python") or "python")
    env = os.environ.copy()
    env.pop("CUDA_VISIBLE_DEVICES", None)
    try:
        completed = subprocess.run(
            cuda_probe_command(python),
            check=True,
            capture_output=True,
            text=True,
            timeout=min(int(local.get("timeout_seconds") or 1200), 30),
            cwd=str(resolved_workdir),
            env=env,
        )
        probe = parse_cuda_probe(completed.stdout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        dependency_status = "missing_torch" if "No module named" in stderr else "probe_failed"
        return {
            **base,
            "ok": False,
            "code": "LOCAL_GPU_PYTHON_PROBE_FAILED",
            "python": python,
            "dependency_status": dependency_status,
            "stdout_tail": (getattr(exc, "stdout", "") or "")[-2000:],
            "stderr_tail": stderr[-2000:],
            "message": "The configured Local Python could not complete the CUDA probe.",
        }

    diagnostics = {
        "python": python,
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
            "ok": False,
            "code": "LOCAL_EXPERIMENT_CUDA_UNAVAILABLE",
            "message": "The configured Local Python cannot access a CUDA GPU.",
        }
    indexes = [int(item) for item in configured_devices.split(",") if item]
    if any(index >= probe.device_count for index in indexes):
        return {
            **base,
            **diagnostics,
            "ok": False,
            "code": "LOCAL_GPU_DEVICE_INDEX_INVALID",
            "message": "A configured CUDA device index is outside the available range.",
        }
    return {
        **base,
        **diagnostics,
        "message": "Local Python, CUDA, and experiment workdir are ready.",
    }


def _reject_invalid_local_config(config: dict[str, Any]) -> None:
    if config.get("provider") != "local_gpu":
        return
    result = _local_experiment_test_result(
        config.get("local") or {}, probe_cuda=False
    )
    if result["ok"] or result.get("code") == "LOCAL_GPU_DISABLED":
        return
    raise HTTPException(status_code=400, detail=result)


def _reject_invalid_dataset_config(config: dict[str, Any]) -> None:
    dataset = config.get("dataset") or {}
    if str(dataset.get("source") or "auto_local") != "local":
        return
    try:
        inspect_dataset_directory(str(dataset.get("dir") or ""))
    except DatasetInspectionError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": str(exc).split(":", 1)[0],
                "message": str(exc),
            },
        ) from exc


def build_router(deps) -> APIRouter:
    router = APIRouter(prefix="/api/settings")

    @router.get("/providers")
    def provider_status():
        status = deps.settings.provider_status()
        saved = {item.get("provider_id"): item for item in deps.runtime_config.model_config()["providers"]}
        secrets = deps.runtime_config.local_secrets().get("model_providers") or {}
        providers = []
        defaults = _default_model_providers(deps.settings)
        default_ids = {item["provider_id"] for item in defaults}
        for default in defaults:
            item = {**default, **saved.get(default["provider_id"], {})}
            configured = bool((secrets.get(item["provider_id"]) or {}).get("api_key")) or bool(
                deps.settings.qwen_api_key if item["provider_id"] == "qwen" else deps.settings.deepseek_api_key
            )
            providers.append(_masked_provider(item, configured))
        for provider_id, item in saved.items():
            if not provider_id or provider_id in default_ids:
                continue
            configured = bool((secrets.get(provider_id) or {}).get("api_key"))
            providers.append(_masked_provider(item, configured))
        return {**status, "model_providers": providers}

    @router.post("/providers")
    def add_model_provider(body: ModelProviderRequest):
        saved = deps.runtime_config.save_model_provider(body.model_dump())
        deps.reload()
        return _masked_provider(saved, bool(body.api_key))

    @router.put("/providers/{provider_id}")
    def update_model_provider(provider_id: str, body: ModelProviderRequest):
        value = body.model_dump()
        value["provider_id"] = provider_id
        saved = deps.runtime_config.save_model_provider(value)
        deps.reload()
        configured = bool(body.api_key) or bool((deps.runtime_config.local_secrets().get("model_providers") or {}).get(provider_id))
        return _masked_provider(saved, configured)

    @router.post("/providers/{provider_id}/test")
    def test_model_provider(provider_id: str):
        providers = provider_status()["model_providers"]
        provider = next((item for item in providers if item["provider_id"] == provider_id), None)
        if provider is None:
            raise HTTPException(status_code=404, detail="MODEL_PROVIDER_NOT_FOUND")
        stored_key = ((deps.runtime_config.local_secrets().get("model_providers") or {}).get(provider_id) or {}).get("api_key")
        if provider_id == "qwen":
            key = stored_key or deps.settings.qwen_api_key
        elif provider_id == "deepseek":
            key = stored_key or deps.settings.deepseek_api_key
        else:
            key = stored_key
        if not key:
            return {"ok": False, "provider_id": provider_id, "code": "MODEL_PROVIDER_NOT_CONFIGURED"}
        try:
            response = httpx.get(
                f"{str(provider['base_url']).rstrip('/')}/models",
                headers={"Authorization": f"Bearer {key}"}, timeout=10.0,
            )
            response.raise_for_status()
        except httpx.TimeoutException:
            return {"ok": False, "provider_id": provider_id, "code": "MODEL_CALL_TIMEOUT"}
        except httpx.HTTPError as exc:
            return {"ok": False, "provider_id": provider_id, "code": "MODEL_PROVIDER_FAILURE", "message": str(exc)}
        return {"ok": True, "provider_id": provider_id, "connection": "Connected",
                "last_test": datetime.now(timezone.utc).isoformat()}

    @router.get("/model-roles")
    def get_model_roles():
        return deps.runtime_config.model_config()["roles"]

    @router.put("/model-roles/{role}")
    def update_model_role(role: str, body: ModelRoleRequest):
        result = deps.runtime_config.save_model_role(role.upper(), body.model_dump())
        deps.reload()
        return result

    @router.get("/experiment")
    def get_experiment_settings():
        config = deps.runtime_config.experiment_config()
        if config:
            return {
                **config,
                "dataset": {
                    "source": deps.settings.dataset_source,
                    "dir": deps.settings.dataset_dir,
                    "mirror_url": deps.settings.dataset_mirror_url,
                    "download_retries": deps.settings.dataset_download_retries,
                    "allow_online_dataset_download": False,
                    **(config.get("dataset") or {}),
                },
            }
        return {
            "provider": deps.settings.experiment_provider,
            "remote": {
                "host": deps.settings.remote_gpu_host,
                "user": deps.settings.remote_gpu_user,
                "port": deps.settings.remote_gpu_port,
                "ssh_key_path": deps.settings.remote_gpu_ssh_key_path,
                "project_dir": deps.settings.remote_gpu_project_dir,
                "python": deps.settings.remote_gpu_python,
                "cuda_visible_devices": deps.settings.remote_gpu_cuda_visible_devices,
                "timeout_seconds": deps.settings.experiment_timeout_seconds,
            },
            "local": {
                "enabled": deps.settings.local_gpu_enabled,
                "workdir": deps.settings.local_gpu_workdir,
                "python": deps.settings.local_gpu_python,
                "cuda_visible_devices": deps.settings.local_gpu_cuda_visible_devices,
                "timeout_seconds": deps.settings.experiment_timeout_seconds,
            },
            "dataset": {
                "source": deps.settings.dataset_source,
                "dir": deps.settings.dataset_dir,
                "mirror_url": deps.settings.dataset_mirror_url,
                "download_retries": deps.settings.dataset_download_retries,
                "allow_online_dataset_download": False,
            },
        }

    @router.post("/experiment")
    def save_experiment_settings(body: ExperimentSettingsRequest):
        value = body.model_dump()
        _reject_invalid_dataset_config(value)
        _reject_invalid_local_config(value)
        config = deps.runtime_config.save_experiment_config(value)
        deps.reload()
        return config

    @router.post("/experiment/test")
    def test_experiment_settings(body: ExperimentSettingsRequest):
        return _experiment_test_result(body.model_dump(), deps.base_settings)

    @router.post("/qwen-key")
    def save_qwen_key(body: QwenKeyRequest):
        result = deps.runtime_config.save_qwen_key(body.api_key, body.base_url, body.model)
        deps.reload()
        return {
            "configured": result["configured"],
            "model": result.get("model") or deps.settings.qwen_model,
        }

    return router
