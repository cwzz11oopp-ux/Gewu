"""Model config is the single source of truth for the role -> {provider_id, model}
routing used by every scientific LLM request.

These tests lock down the minimal fix contract:
1. A model config saved through one backend instance is picked up by another
   instance on the next admission/preflight WITHOUT restarting that instance
   (fingerprint-based sync, not a per-request rebuild).
2. The role model configured in the frontend is the exact model used by the
   router; a Settings.from_env value is never a role fallback.
3. An unconfigured role fails with MODEL_ROLE_NOT_CONFIGURED and an
   unconfigured provider fails with MODEL_PROVIDER_NOT_CONFIGURED -- neither
   silently falls back to qwen/deepseek.
"""

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.providers.llm import QwenLLMProvider, get_llm_provider
from backend.app.storage.runtime_config import RuntimeConfigStore

# Models observed by every (patched) provider preflight across all app
# instances in this module. keyed as (provider_id, model).
PREFLIGHT_CAPTURES: list[tuple[str, str]] = []


def _capture_preflight(self, provider_id: str = "qwen"):
    """Offline stand-in for QwenLLMProvider.preflight that records the exact
    model this provider instance is pinned to instead of calling the network."""
    model = self.settings.qwen_model
    PREFLIGHT_CAPTURES.append((provider_id, model))
    return {"provider": provider_id, "model": model, "structured": True}


@pytest.fixture(autouse=True)
def _capture_preflight_fixture(monkeypatch):
    PREFLIGHT_CAPTURES.clear()
    monkeypatch.setattr(QwenLLMProvider, "preflight", _capture_preflight)


def _write_model_config(
    data_dir,
    model: str,
    role: str = "RESEARCH",
    provider_id: str = "p1",
    provider_base_url: str = "https://example.invalid/v1",
) -> None:
    (data_dir / "model_config.json").write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "provider_id": provider_id,
                        "provider_type": "openai_compatible",
                        "display_name": "P",
                        "base_url": provider_base_url,
                        "models": [model],
                        "enabled": True,
                        "connection_policy": {"timeout_seconds": 10},
                    }
                ],
                "roles": {role: {"provider_id": provider_id, "model": model}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data_dir / "local_secrets.json").write_text(
        json.dumps({"model_providers": {provider_id: {"api_key": "test-key"}}}),
        encoding="utf-8",
    )


def _app_env() -> dict[str, str]:
    return {
        "COMPETITION_MODE": "false",
        "LLM_PROVIDER": "qwen",
        "LITERATURE_PROVIDER": "mock",
        "EXPERIMENT_PROVIDER": "mock",
        "QWEN_API_KEY": "test-only",
        # from_env value deliberately differs from the saved config model; it
        # must never be used as a role fallback.
        "QWEN_MODEL": "ENV_MODEL",
    }


def test_config_saved_on_app_a_is_used_by_app_b_without_restart(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_model_config(data_dir, model="MODEL_A")

    # Two independent backend instances (in-memory settings + fingerprint each)
    # sharing one on-disk config -- the multi-process deployment shape.
    app_a = create_app(data_dir=str(data_dir), env=_app_env())
    app_b = create_app(data_dir=str(data_dir), env=_app_env())

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        run = client_b.post(
            "/api/runs",
            json={"title": "T", "problem_input": "Fashion-MNIST multi-scale fusion?"},
        )
        assert run.status_code == 200
        run_id = run.json()["id"]

        # B starts on the saved MODEL_A, not the from_env ENV_MODEL.
        preflight_b = client_b.post(f"/api/runs/{run_id}/preflight")
        assert preflight_b.status_code == 200
        assert PREFLIGHT_CAPTURES == [("p1", "MODEL_A")]
        model_check = next(
            item
            for item in preflight_b.json()["checks"]
            if item["name"] == "p1:MODEL_A"
        )
        assert model_check["ok"] is True
        assert model_check["code"] == "AVAILABLE"

        # Save MODEL_A -> MODEL_B through app A only. app B must auto-see it.
        saved = client_a.put(
            "/api/settings/model-roles/RESEARCH",
            json={"provider_id": "p1", "model": "MODEL_B"},
        )
        assert saved.status_code == 200
        assert saved.json() == {"provider_id": "p1", "model": "MODEL_B"}

        # Next admission/preflight on B detects the fingerprint change, reloads
        # its own settings and uses MODEL_B -- no restart required.
        preflight_b2 = client_b.post(f"/api/runs/{run_id}/preflight")
        assert preflight_b2.status_code == 200
        assert PREFLIGHT_CAPTURES == [("p1", "MODEL_A"), ("p1", "MODEL_B")]
        model_check_b2 = next(
            item
            for item in preflight_b2.json()["checks"]
            if item["name"] == "p1:MODEL_B"
        )
        assert model_check_b2["ok"] is True


def test_role_configured_model_is_the_only_model_routed(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_model_config(data_dir, model="MODEL_A")
    store = RuntimeConfigStore(str(data_dir))
    settings = store.apply(Settings.from_env({"LLM_PROVIDER": "qwen", "QWEN_API_KEY": "test-key"}))
    router = get_llm_provider(settings)

    # The router exposes exactly the configured (provider_id, model) pair, and
    # the provider instance pins every task to that single model.
    assert router.configured_provider_models() == [("p1", "MODEL_A")]
    provider = router._route("RESEARCH")
    assert provider.settings.qwen_model == "MODEL_A"
    for task in ("research.structure_problem", "hypothesis.generate", "planning.build_plan"):
        assert provider._model_for_task(task) == "MODEL_A"


def test_unconfigured_role_fails_with_model_role_not_configured(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # Only RESEARCH is assigned; every other role must fail explicitly.
    _write_model_config(data_dir, model="MODEL_A")
    store = RuntimeConfigStore(str(data_dir))
    settings = store.apply(Settings.from_env({"LLM_PROVIDER": "qwen", "QWEN_API_KEY": "test-key"}))
    router = get_llm_provider(settings)

    with pytest.raises(RuntimeError, match="MODEL_ROLE_NOT_CONFIGURED:HYPOTHESIS_GENERATION"):
        router.generate_json("hypothesis.generate", {}, {})
    with pytest.raises(RuntimeError, match="MODEL_ROLE_NOT_CONFIGURED:EXPERIMENT_CODE_GENERATION"):
        router.generate_json("experiment.generate_code", {}, {})


def test_unconfigured_provider_fails_with_model_provider_not_configured(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # The role points at "ghost", which has no base_url, so no provider instance
    # exists. Routing must raise, not fall back to a default provider.
    _write_model_config(
        data_dir,
        model="M",
        provider_id="ghost",
        provider_base_url="",
    )
    store = RuntimeConfigStore(str(data_dir))
    settings = store.apply(Settings.from_env({"LLM_PROVIDER": "qwen", "QWEN_API_KEY": "test-key"}))
    router = get_llm_provider(settings)

    assert router.configured_provider_models() == []
    with pytest.raises(RuntimeError, match="MODEL_PROVIDER_NOT_CONFIGURED:ghost:M:role=RESEARCH"):
        router.generate_json("research.structure_problem", {}, {})


def test_sync_reloads_only_when_fingerprint_changes(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_model_config(data_dir, model="MODEL_A")
    store = RuntimeConfigStore(str(data_dir))

    class _Deps:
        def __init__(self):
            self.reload_calls = 0
            self.fingerprint = store.model_config_fingerprint()

        def reload(self):
            self.reload_calls += 1
            self.fingerprint = store.model_config_fingerprint()

        def sync(self):
            fingerprint = store.model_config_fingerprint()
            if fingerprint == self.fingerprint:
                return False
            self.reload()
            return True

    deps = _Deps()
    assert deps.sync() is False  # unchanged disk -> no reload
    assert deps.reload_calls == 0

    _write_model_config(data_dir, model="MODEL_B")
    assert deps.sync() is True  # changed disk -> reload exactly once
    assert deps.reload_calls == 1
    assert deps.sync() is False  # reloaded -> fingerprints aligned again
    assert deps.reload_calls == 1
