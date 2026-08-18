from __future__ import annotations

from dataclasses import replace
from typing import Any

from backend.app.config import Settings
from backend.app.storage.json_store import JsonStore


EXPERIMENT_CONFIG_FILE = "provider_config.json"
LOCAL_SECRETS_FILE = "local_secrets.json"
MODEL_CONFIG_FILE = "model_config.json"

DEFAULT_MODEL_ROLES = {
    "GENERAL_REASONING": {"provider_id": "qwen", "model": "qwen3.7-plus"},
    "RESEARCH": {"provider_id": "qwen", "model": "qwen3.7-plus"},
    "HYPOTHESIS_GENERATION": {"provider_id": "qwen", "model": "qwen3.7-max"},
    "EVIDENCE_REASONING": {"provider_id": "qwen", "model": "qwen3.7-max"},
    "RESEARCH_PLAN_GENERATION": {"provider_id": "qwen", "model": "qwen3.7-plus"},
    "RESEARCH_PLAN_REVIEW": {"provider_id": "deepseek", "model": "deepseek-chat"},
    "EXPERIMENT_CODE_GENERATION": {"provider_id": "qwen", "model": "qwen3-coder-plus"},
    "CRITIC": {"provider_id": "qwen", "model": "qwen3.7-max"},
    "WRITER": {"provider_id": "qwen", "model": "qwen3.7-plus"},
}


class RuntimeConfigStore:
    def __init__(self, data_dir: str) -> None:
        self.store = JsonStore(data_dir)

    def experiment_config(self) -> dict[str, Any]:
        return self.store.read(EXPERIMENT_CONFIG_FILE)

    def save_experiment_config(self, value: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_experiment_config(value)
        self.store.write(EXPERIMENT_CONFIG_FILE, normalized)
        return normalized

    def local_secrets(self) -> dict[str, Any]:
        return self.store.read(LOCAL_SECRETS_FILE)

    def model_config(self) -> dict[str, Any]:
        saved = self.store.read(MODEL_CONFIG_FILE)
        return {
            "providers": list(saved.get("providers") or []),
            "roles": {**DEFAULT_MODEL_ROLES, **(saved.get("roles") or {})},
        }

    def save_model_provider(self, value: dict[str, Any]) -> dict[str, Any]:
        config = self.model_config()
        provider_id = str(value.get("provider_id") or "").strip().lower()
        public = {
            "provider_id": provider_id,
            "provider_type": str(value.get("provider_type") or "openai_compatible"),
            "display_name": str(value.get("display_name") or provider_id.title()),
            "base_url": str(value.get("base_url") or "").rstrip("/"),
            "models": [str(item) for item in (value.get("models") or []) if str(item).strip()],
            "enabled": bool(value.get("enabled", True)),
            "connection_policy": dict(value.get("connection_policy") or {}),
        }
        providers = [item for item in config["providers"] if item.get("provider_id") != provider_id]
        providers.append(public)
        self.store.write(MODEL_CONFIG_FILE, {"providers": providers, "roles": config["roles"]})
        if "api_key" in value and value.get("api_key"):
            secrets = self.local_secrets()
            secrets.setdefault("model_providers", {})[provider_id] = {"api_key": str(value["api_key"])}
            self.store.write(LOCAL_SECRETS_FILE, secrets)
        return public

    def save_model_role(self, role: str, value: dict[str, Any]) -> dict[str, str]:
        config = self.model_config()
        assignment = {
            "provider_id": str(value.get("provider_id") or ""),
            "model": str(value.get("model") or ""),
        }
        config["roles"][role] = assignment
        self.store.write(MODEL_CONFIG_FILE, config)
        return assignment

    def save_qwen_key(self, api_key: str, base_url: str | None = None, model: str | None = None) -> dict[str, Any]:
        current = self.local_secrets()
        qwen = dict(current.get("qwen") or {})
        qwen["api_key"] = api_key
        if base_url:
            qwen["base_url"] = base_url
        if model:
            qwen["model"] = model
        current["qwen"] = qwen
        self.store.write(LOCAL_SECRETS_FILE, current)
        return {"configured": bool(api_key), "model": qwen.get("model", "")}

    def apply(self, settings: Settings) -> Settings:
        settings = self._apply_experiment(settings, self.experiment_config())
        settings = self._apply_qwen(settings, self.local_secrets())
        settings = self._apply_model_providers(settings)
        return settings

    def _apply_model_providers(self, settings: Settings) -> Settings:
        config = self.model_config()
        saved_config = self.store.read(MODEL_CONFIG_FILE)
        providers = {item.get("provider_id"): item for item in config["providers"]}
        secrets = self.local_secrets().get("model_providers") or {}
        qwen = providers.get("qwen") or {}
        deepseek = providers.get("deepseek") or {}
        roles = config["roles"]
        experiment_code_role = roles.get("EXPERIMENT_CODE_GENERATION") or {}
        return replace(
            settings,
            model_role_assignments=config["roles"],
            qwen_api_key=str((secrets.get("qwen") or {}).get("api_key") or settings.qwen_api_key),
            qwen_base_url=str(qwen.get("base_url") or settings.qwen_base_url),
            qwen_model=str(
                (((saved_config.get("roles") or {}).get("RESEARCH_PLAN_GENERATION") or {}).get("model"))
                or settings.qwen_model
            ),
            qwen_code_model=(
                str(experiment_code_role.get("model") or settings.qwen_code_model)
                if experiment_code_role.get("provider_id") == "qwen"
                else settings.qwen_code_model
            ),
            deepseek_api_key=str((secrets.get("deepseek") or {}).get("api_key") or settings.deepseek_api_key),
            deepseek_base_url=str(deepseek.get("base_url") or settings.deepseek_base_url),
            deepseek_model=str(
                (((saved_config.get("roles") or {}).get("RESEARCH_PLAN_REVIEW") or {}).get("model"))
                or settings.deepseek_model
            ),
        )

    def _apply_experiment(self, settings: Settings, config: dict[str, Any]) -> Settings:
        if not config:
            return settings
        provider = str(config.get("provider") or settings.experiment_provider)
        remote = config.get("remote") or {}
        local = config.get("local") or {}
        dataset = config.get("dataset") or {}
        configured_dataset_source = str(dataset.get("source") or settings.dataset_source)
        allow_online = bool(dataset.get("allow_online_dataset_download", False))
        effective_dataset_source = (
            "local"
            if configured_dataset_source == "auto_local"
            or (configured_dataset_source == "online" and not allow_online)
            else "official"
            if configured_dataset_source == "online"
            else configured_dataset_source
        )
        selected = local if provider == "local_gpu" else remote
        timeout = int(selected.get("timeout_seconds", settings.experiment_timeout_seconds))
        return replace(
            settings,
            experiment_provider=provider,
            experiment_timeout_seconds=int(timeout),
            dataset_source=effective_dataset_source,
            dataset_dir=str(dataset.get("dir") or settings.dataset_dir),
            dataset_mirror_url=str(dataset.get("mirror_url") or settings.dataset_mirror_url),
            dataset_download_retries=int(dataset.get("download_retries") or settings.dataset_download_retries),
            remote_gpu_host=str(remote.get("host") or settings.remote_gpu_host),
            remote_gpu_user=str(remote.get("user") or settings.remote_gpu_user),
            remote_gpu_port=int(remote.get("port") or settings.remote_gpu_port),
            remote_gpu_ssh_key_path=str(remote.get("ssh_key_path") or settings.remote_gpu_ssh_key_path),
            remote_gpu_project_dir=str(remote.get("project_dir") or settings.remote_gpu_project_dir),
            remote_gpu_python=str(remote.get("python") or settings.remote_gpu_python),
            remote_gpu_cuda_visible_devices=str(
                remote.get("cuda_visible_devices") or settings.remote_gpu_cuda_visible_devices
            ),
            local_gpu_enabled=bool(local.get("enabled", settings.local_gpu_enabled)),
            local_gpu_workdir=str(local.get("workdir", settings.local_gpu_workdir)),
            local_gpu_python=str(local.get("python") or settings.local_gpu_python),
            local_gpu_cuda_visible_devices=str(
                local.get("cuda_visible_devices") or settings.local_gpu_cuda_visible_devices
            ),
        )

    def _apply_qwen(self, settings: Settings, secrets: dict[str, Any]) -> Settings:
        qwen = secrets.get("qwen") or {}
        if not qwen:
            return settings
        return replace(
            settings,
            qwen_api_key=str(qwen.get("api_key") or settings.qwen_api_key),
            qwen_base_url=str(qwen.get("base_url") or settings.qwen_base_url),
            qwen_model=str(qwen.get("model") or settings.qwen_model),
        )

    def _normalize_experiment_config(self, value: dict[str, Any]) -> dict[str, Any]:
        provider = str(value.get("provider") or "remote_gpu")
        remote = value.get("remote") or {}
        local = value.get("local") or {}
        dataset = value.get("dataset") or {}
        dataset_source = str(dataset.get("source") or "auto_local")
        if dataset_source not in {"auto", "auto_local", "official", "online", "local"}:
            dataset_source = "auto_local"
        return {
            "provider": provider,
            "dataset": {
                "source": dataset_source,
                "dir": str(dataset.get("dir") or "datasets"),
                "mirror_url": str(dataset.get("mirror_url") or ""),
                "download_retries": max(1, min(int(dataset.get("download_retries") or 5), 10)),
                "allow_online_dataset_download": bool(dataset.get("allow_online_dataset_download", False)),
            },
            "remote": {
                "host": str(remote.get("host") or ""),
                "user": str(remote.get("user") or ""),
                "port": int(remote.get("port") or 22),
                "ssh_key_path": str(remote.get("ssh_key_path") or ""),
                "project_dir": str(remote.get("project_dir") or ""),
                "python": str(remote.get("python") or "python"),
                "cuda_visible_devices": str(remote.get("cuda_visible_devices") or ""),
                "timeout_seconds": int(remote.get("timeout_seconds", 0)),
            },
            "local": {
                "enabled": bool(local.get("enabled", False)),
                "workdir": str(local.get("workdir") or "experiments"),
                "python": str(local.get("python") or "python"),
                "cuda_visible_devices": str(local.get("cuda_visible_devices") or ""),
                "timeout_seconds": int(local.get("timeout_seconds", 0)),
            },
        }
