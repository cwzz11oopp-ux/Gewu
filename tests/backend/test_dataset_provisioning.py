import subprocess
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.providers.experiment import LocalGpuExperimentProvider
from backend.app.workflow.dataset_catalog import (
    dataset_present,
    dataset_spec,
    normalize_dataset_name,
    supported_dataset_names,
)


def _settings(tmp_path, source):
    return Settings.from_env({
        "EXPERIMENT_PROVIDER": "local_gpu",
        "LOCAL_GPU_ENABLED": "true",
        "LOCAL_EXPERIMENT_WORKDIR": str(tmp_path / "experiments"),
        "LOCAL_GPU_PYTHON": "python",
        "EXPERIMENT_DATASET_SOURCE": source,
        "EXPERIMENT_DATASET_DIR": str(tmp_path / "datasets"),
    })


def test_normalize_dataset_name_accepts_common_aliases():
    assert normalize_dataset_name("CIFAR-10") == "cifar-10"
    assert normalize_dataset_name("cifar10") == "cifar-10"
    assert normalize_dataset_name("Fashion MNIST") == "fashion-mnist"
    assert normalize_dataset_name({"name": "MNIST"}) == "mnist"
    assert normalize_dataset_name("ImageNet") == ""
    assert normalize_dataset_name(None) == ""


def test_supported_datasets_have_specs_and_markers():
    for name in supported_dataset_names():
        spec = dataset_spec(name)
        assert spec.torchvision_class
        assert spec.marker


def test_local_source_missing_dataset_raises_with_placement_hint(tmp_path):
    provider = LocalGpuExperimentProvider(_settings(tmp_path, "local"))

    with pytest.raises(RuntimeError, match="EXPERIMENT_DATASET_LOCAL_MISSING:cifar-10") as excinfo:
        provider._provision_dataset("cifar-10", "python", {})

    assert "cifar-10-batches-py" in str(excinfo.value)


def test_local_source_present_dataset_skips_download(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "local")
    marker = tmp_path / "datasets" / "cifar-10-batches-py"
    marker.mkdir(parents=True)
    (marker / "data_batch_1").write_bytes(b"x")

    def fail_run(*args, **kwargs):
        raise AssertionError("no subprocess expected when dataset is present")

    monkeypatch.setattr(subprocess, "run", fail_run)
    provider = LocalGpuExperimentProvider(settings)

    root = provider._provision_dataset("cifar-10", "python", {})

    assert root == str((tmp_path / "datasets").resolve())


def test_online_source_downloads_via_backend_subprocess(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "online")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        marker = tmp_path / "datasets" / "cifar-10-batches-py"
        marker.mkdir(parents=True)
        (marker / "data_batch_1").write_bytes(b"x")
        return subprocess.CompletedProcess(command, 0, stdout="DATASET_READY:cifar-10", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = LocalGpuExperimentProvider(settings)

    root = provider._provision_dataset("cifar-10", "python", {})

    assert root == str((tmp_path / "datasets").resolve())
    assert len(calls) == 1
    assert calls[0][0] == "python"
    assert "download=True" in calls[0][2]
    assert calls[0][-1] == "5"
    assert dataset_present(tmp_path / "datasets", "cifar-10")


def test_auto_source_passes_mirror_and_retry_settings(tmp_path, monkeypatch):
    settings = Settings.from_env({
        "EXPERIMENT_PROVIDER": "local_gpu",
        "LOCAL_GPU_ENABLED": "true",
        "LOCAL_EXPERIMENT_WORKDIR": str(tmp_path / "experiments"),
        "EXPERIMENT_DATASET_SOURCE": "auto",
        "EXPERIMENT_DATASET_DIR": str(tmp_path / "datasets"),
        "EXPERIMENT_DATASET_MIRROR_URL": "https://mirror.example/{filename}",
        "EXPERIMENT_DATASET_DOWNLOAD_RETRIES": "7",
    })
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        marker = tmp_path / "datasets" / "cifar-10-batches-py"
        marker.mkdir(parents=True)
        (marker / "data_batch_1").write_bytes(b"x")
        return subprocess.CompletedProcess(command, 0, stdout="DATASET_READY:cifar-10", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    LocalGpuExperimentProvider(settings)._provision_dataset("cifar-10", "python", {})

    assert calls[0][-2:] == ["https://mirror.example/{filename}", "7"]


def test_online_source_failed_download_raises(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "online")

    def fake_run(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, output="", stderr="connection refused")

    monkeypatch.setattr(subprocess, "run", fake_run)
    provider = LocalGpuExperimentProvider(settings)

    with pytest.raises(RuntimeError, match="EXPERIMENT_DATASET_DOWNLOAD_FAILED:cifar-10"):
        provider._provision_dataset("cifar-10", "python", {})


def test_corrupt_download_is_moved_to_recoverable_quarantine(tmp_path):
    settings = _settings(tmp_path, "online")
    partial = tmp_path / "datasets" / "cifar-10-python.tar.gz"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"incomplete archive")
    unrelated = tmp_path / "datasets" / "user-note.txt"
    unrelated.write_text("keep me", encoding="utf-8")
    provider = LocalGpuExperimentProvider(settings)

    repair = provider.quarantine_failed_dataset_download("cifar-10")

    assert repair["status"] == "completed"
    assert repair["dataset"] == "cifar-10"
    assert len(repair["moved"]) == 1
    assert not partial.exists()
    quarantined = repair["moved"][0]["to"]
    assert ".failed-downloads" in quarantined
    assert Path(quarantined).read_bytes() == b"incomplete archive"
    assert unrelated.read_text(encoding="utf-8") == "keep me"
