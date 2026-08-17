from backend.app.config import Settings


def test_qwen_output_cap_is_omitted_by_default():
    assert Settings.from_env({}).qwen_max_tokens == 0


def test_qwen_uses_separate_task_models_with_legacy_general_alias():
    defaults = Settings.from_env({})
    configured = Settings.from_env({
        "QWEN_MODEL": "general-model",
        "QWEN_REASONING_MODEL": "reasoning-model",
        "QWEN_CODE_MODEL": "code-model",
        "QWEN_CODE_FALLBACK_MODEL": "code-fallback-model",
        "QWEN_FAST_MODEL": "fast-model",
    })

    assert defaults.qwen_model == "qwen3.7-plus"
    assert defaults.qwen_reasoning_model == "qwen3.7-max"
    assert defaults.qwen_code_model == "qwen3-coder-plus"
    assert defaults.qwen_code_fallback_model == "qwen3-coder-flash"
    assert defaults.qwen_fast_model == "qwen3.6-flash"
    assert defaults.qwen_reasoning_timeout_seconds == 600
    assert defaults.qwen_general_timeout_seconds == 300
    assert defaults.qwen_code_timeout_seconds == 600
    assert defaults.qwen_fast_timeout_seconds == 120
    assert defaults.feedback_max_iterations == 4
    assert configured.qwen_model == "general-model"
    assert configured.qwen_reasoning_model == "reasoning-model"
    assert configured.qwen_code_model == "code-model"
    assert configured.qwen_code_fallback_model == "code-fallback-model"
    assert configured.qwen_fast_model == "fast-model"


def test_qwen_general_model_setting_takes_precedence_over_legacy_alias():
    settings = Settings.from_env({
        "QWEN_MODEL": "legacy-general",
        "QWEN_GENERAL_MODEL": "new-general",
    })

    assert settings.qwen_model == "new-general"


def test_competition_mode_requires_real_evidence_and_real_experiment():
    settings = Settings.from_env({
        "COMPETITION_MODE": "true",
        "LLM_PROVIDER": "qwen",
        "QWEN_API_KEY": "key",
        "LITERATURE_PROVIDER": "arxiv_semantic_scholar",
        "EXPERIMENT_PROVIDER": "remote_gpu",
        "REMOTE_GPU_HOST": "gpu.example.com",
        "REMOTE_GPU_USER": "runner",
        "REMOTE_GPU_PROJECT_DIR": "/srv/ai-scientist",
    })

    status = settings.provider_status()

    assert status["literature"]["ready"] is True
    assert status["experiment"]["ready"] is True
    assert status["experiment"]["mode"] == "remote_gpu"


def test_competition_mode_rejects_mock_experiment_provider():
    settings = Settings.from_env({
        "COMPETITION_MODE": "true",
        "EXPERIMENT_PROVIDER": "mock",
        "LITERATURE_PROVIDER": "arxiv_semantic_scholar",
    })

    status = settings.provider_status()

    assert status["experiment"]["ready"] is False
    assert status["experiment"]["code"] == "REAL_EXPERIMENT_REQUIRED"


def test_development_mode_allows_mock_provider_with_warning():
    settings = Settings.from_env({
        "COMPETITION_MODE": "false",
        "EXPERIMENT_PROVIDER": "mock",
        "LITERATURE_PROVIDER": "mock",
    })

    status = settings.provider_status()

    assert status["experiment"]["ready"] is True
    assert status["experiment"]["warning"] == "Mock experiment results are development fixtures."


def test_local_gpu_settings_keep_local_runtime_values_separate_from_remote_values():
    settings = Settings.from_env({
        "EXPERIMENT_PROVIDER": "local_gpu",
        "REMOTE_GPU_PYTHON": "remote-python",
        "REMOTE_GPU_CUDA_VISIBLE_DEVICES": "7",
        "LOCAL_EXPERIMENT_WORKDIR": "~/local-experiments",
        "LOCAL_GPU_PYTHON": "local-python",
        "LOCAL_GPU_CUDA_VISIBLE_DEVICES": "0,1",
    })

    assert settings.local_gpu_workdir == "~/local-experiments"
    assert settings.local_gpu_python == "local-python"
    assert settings.local_gpu_cuda_visible_devices == "0,1"
    assert settings.remote_gpu_python == "remote-python"
    assert settings.remote_gpu_cuda_visible_devices == "7"


def test_local_gpu_provider_status_allows_generated_train_entrypoint(tmp_path):
    workdir = tmp_path / "experiments"
    workdir.mkdir()
    settings = Settings.from_env({
        "EXPERIMENT_PROVIDER": "local_gpu",
        "LOCAL_GPU_ENABLED": "true",
        "LOCAL_EXPERIMENT_WORKDIR": str(workdir),
    })

    status = settings.provider_status()

    assert status["experiment"]["ready"] is True
    assert status["experiment"]["code"] == ""
    assert status["experiment"]["resolved_workdir"] == str(workdir.resolve())
    assert status["experiment"]["entrypoint_exists"] is False


def test_local_gpu_provider_status_allows_runtime_to_create_missing_workdir(tmp_path):
    workdir = tmp_path / "experiments"
    settings = Settings.from_env(
        {
            "EXPERIMENT_PROVIDER": "local_gpu",
            "LOCAL_GPU_ENABLED": "true",
            "LOCAL_EXPERIMENT_WORKDIR": str(workdir),
            "LOCAL_GPU_PYTHON": "configured-python",
            "LOCAL_GPU_CUDA_VISIBLE_DEVICES": "0",
        }
    )

    status = settings.provider_status()["experiment"]

    assert status["ready"] is True
    assert status["code"] == ""
    assert status["workdir_exists"] is False
    assert status["python"] == "configured-python"
