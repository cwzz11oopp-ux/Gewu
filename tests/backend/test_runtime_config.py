from backend.app.config import Settings
from backend.app.storage.runtime_config import RuntimeConfigStore


def test_runtime_config_applies_local_python_cuda_and_workdir_without_overwriting_remote_settings(tmp_path):
    store = RuntimeConfigStore(str(tmp_path))
    settings = Settings.from_env({
        "REMOTE_GPU_PYTHON": "remote-python",
        "REMOTE_GPU_CUDA_VISIBLE_DEVICES": "7",
    })
    config = {
        "provider": "local_gpu",
        "local": {
            "enabled": True,
            "workdir": "~/local-experiments",
            "python": "local-python",
            "cuda_visible_devices": "0,1",
            "timeout_seconds": 300,
        },
    }

    applied = store._apply_experiment(settings, config)

    assert applied.experiment_provider == "local_gpu"
    assert applied.local_gpu_workdir == "~/local-experiments"
    assert applied.local_gpu_python == "local-python"
    assert applied.local_gpu_cuda_visible_devices == "0,1"
    assert applied.remote_gpu_python == "remote-python"
    assert applied.remote_gpu_cuda_visible_devices == "7"
