import subprocess
import re
import json
from pathlib import Path

import pytest

from backend.app.config import Settings
from backend.app.models.experiment import ExperimentBundle, ExperimentFile, ExperimentManifest
from backend.app.providers.experiment import (
    LocalGpuExperimentProvider,
    MockExperimentProvider,
    RemoteGpuExperimentProvider,
)
from backend.app.workflow.experiment_harness import compile_bundle_runtime_contract


def _fake_popen_from(fake_run):
    class FakePopen:
        pid = 12345

        def __init__(self, command, **kwargs):
            completed = fake_run(command, **kwargs)
            self.returncode = completed.returncode
            if completed.stdout and kwargs.get("stdout"):
                kwargs["stdout"].write(completed.stdout)

        def poll(self):
            return self.returncode

    return FakePopen


def test_mock_experiment_marks_result_as_non_real():
    provider = MockExperimentProvider()

    result = provider.run({"name": "dev fixture"})

    assert result["is_real_experiment"] is False
    assert result["provider"] == "mock"


def test_local_provider_does_not_default_to_fashion_mnist(tmp_path):
    provider = LocalGpuExperimentProvider(Settings.from_env({
        "EXPERIMENT_PROVIDER": "local_gpu",
        "LOCAL_EXPERIMENT_WORKDIR": str(tmp_path / "experiments"),
    }))

    assert provider.plan({}, {})["dataset"] == ""
    assert provider.plan({"dataset": {"canonical_name": "fashion-mnist"}}, {})["dataset"] == "fashion-mnist"


def test_remote_gpu_provider_requires_bundle_before_ssh(monkeypatch):
    calls = []

    def fake_run(command, check, capture_output, text, timeout):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='{"accuracy": 0.91}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = Settings.from_env({
        "EXPERIMENT_PROVIDER": "remote_gpu",
        "REMOTE_GPU_HOST": "gpu.example.com",
        "REMOTE_GPU_USER": "runner",
        "REMOTE_GPU_PROJECT_DIR": "/srv/ai-scientist",
        "REMOTE_GPU_PYTHON": "python",
        "REMOTE_GPU_CUDA_VISIBLE_DEVICES": "0",
    })
    provider = RemoteGpuExperimentProvider(settings)

    with pytest.raises(RuntimeError, match="REMOTE_EXPERIMENT_BUNDLE_REQUIRED"):
        provider.run({
            "name": "cnn_baseline",
            "command": "python experiments/cnn.py --seed 7 --output results/baseline.json",
        })

    assert calls == []


def test_remote_gpu_provider_rejects_legacy_command_payload():
    settings = Settings.from_env({
        "EXPERIMENT_PROVIDER": "remote_gpu",
        "REMOTE_GPU_HOST": "gpu.example.com",
        "REMOTE_GPU_USER": "runner",
        "REMOTE_GPU_PROJECT_DIR": "/srv/ai-scientist",
        "REMOTE_GPU_PYTHON": "python",
    })

    with pytest.raises(RuntimeError, match="REMOTE_EXPERIMENT_BUNDLE_REQUIRED"):
        RemoteGpuExperimentProvider(settings).run(
            {"command": "python train.py"},
            {"command": "python train.py; echo injected"},
        )


def test_remote_gpu_provider_rejects_legacy_generated_code_before_ssh(monkeypatch):
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

    with pytest.raises(RuntimeError, match="REMOTE_EXPERIMENT_BUNDLE_REQUIRED"):
        provider.run(task, code)

    assert calls == []


def test_local_gpu_provider_uses_resolved_workdir_local_python_and_relative_entrypoint(tmp_path, monkeypatch):
    workdir = tmp_path / "experiments"
    workdir.mkdir()
    (workdir / "train.py").write_text("# test entrypoint\n", encoding="utf-8")
    calls = []

    def fake_run(command, check, capture_output, text, timeout, cwd):
        calls.append({"command": command, "cwd": cwd})
        return subprocess.CompletedProcess(command, 0, stdout='{"accuracy": 0.93}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("backend.app.providers.experiment.platform.node", lambda: "test-host")
    settings = Settings.from_env({
        "EXPERIMENT_PROVIDER": "local_gpu",
        "LOCAL_EXPERIMENT_WORKDIR": str(workdir),
        "LOCAL_GPU_PYTHON": "local-python",
        "REMOTE_GPU_PYTHON": "remote-python",
    })
    provider = LocalGpuExperimentProvider(settings)

    task = provider.plan({"claim": "test"}, {"seed": 7})
    result = provider.run(task)

    assert task["command"].startswith("local-python train.py ")
    assert "experiments/train.py" not in task["command"]
    assert calls == [{"command": task["command"].split(), "cwd": str(workdir.resolve())}]
    assert result["metrics"]["accuracy"] == 0.93


def test_local_gpu_provider_rejects_missing_workdir_with_stable_error_code(tmp_path):
    missing_workdir = tmp_path / "missing"
    settings = Settings.from_env({
        "EXPERIMENT_PROVIDER": "local_gpu",
        "LOCAL_EXPERIMENT_WORKDIR": str(missing_workdir),
    })
    provider = LocalGpuExperimentProvider(settings)

    with pytest.raises(RuntimeError, match="LOCAL_EXPERIMENT_WORKDIR_INVALID"):
        provider.run(provider.plan({"claim": "test"}, {}))


def test_local_gpu_provider_requires_train_entrypoint_in_resolved_workdir(tmp_path):
    workdir = tmp_path / "experiments"
    workdir.mkdir()
    settings = Settings.from_env({
        "EXPERIMENT_PROVIDER": "local_gpu",
        "LOCAL_EXPERIMENT_WORKDIR": str(workdir),
    })
    provider = LocalGpuExperimentProvider(settings)

    expected_error = f"LOCAL_EXPERIMENT_ENTRYPOINT_INVALID: {workdir.resolve() / 'train.py'}"
    with pytest.raises(RuntimeError, match=re.escape(expected_error)):
        provider.run(provider.plan({"claim": "test"}, {}))


def test_local_gpu_provider_deploys_generated_code_before_running(tmp_path, monkeypatch):
    workdir = tmp_path / "experiments"
    calls = []

    def fake_run(command, check, capture_output, text, timeout, cwd):
        calls.append({"command": command, "cwd": cwd})
        return subprocess.CompletedProcess(command, 0, stdout='{"accuracy": 0.97}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("backend.app.providers.experiment.platform.node", lambda: "test-host")
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

    assert workdir.is_dir()
    assert (workdir / "train.py").read_text(encoding="utf-8") == "print('{\"accuracy\": 0.97}')"
    assert calls == [{"command": code["command"].split(), "cwd": str(workdir.resolve())}]
    assert result["metrics"]["accuracy"] == 0.97
    assert result["deployed_files"] == [str(workdir.resolve() / "train.py")]


def test_local_gpu_provider_reports_missing_python_dependency(tmp_path, monkeypatch):
    workdir = tmp_path / "experiments"

    def fake_run(command, check, capture_output, text, timeout, cwd):
        raise subprocess.CalledProcessError(
            1,
            command,
            output="",
            stderr="ModuleNotFoundError: No module named 'numpy'",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = Settings.from_env({
        "EXPERIMENT_PROVIDER": "local_gpu",
        "LOCAL_EXPERIMENT_WORKDIR": str(workdir),
        "LOCAL_GPU_PYTHON": "local-python",
    })
    provider = LocalGpuExperimentProvider(settings)
    code = {
        "entrypoint": "train.py",
        "files": [{"path": "train.py", "content": "import numpy"}],
        "command": "local-python train.py --seed 7 --output results/local_seed_7.json",
    }

    with pytest.raises(RuntimeError, match="LOCAL_EXPERIMENT_DEPENDENCY_MISSING:numpy"):
        provider.run(provider.plan({"claim": "test"}, {"seed": 7}), code)


def _runtime_bundle(requires_gpu=True, requirements=None):
    return ExperimentBundle(
        manifest=ExperimentManifest(
            run_id="run_1",
            experiment_id="experiment_1",
            result_id="experiment_1_result",
            python_args=[
                "--run-id",
                "run_1",
                "--experiment-id",
                "experiment_1",
                "--result-id",
                "experiment_1_result",
                "--output",
                "results/experiment_1_result.json",
            ],
            requires_gpu=requires_gpu,
            expected_metrics=["accuracy"],
            parameters={"seed": 7},
            seeds=[7],
        ),
        files=[ExperimentFile(path="train.py", content="print('training')")],
        requirements=requirements or [],
    )


def test_local_runner_uses_isolated_directory_cuda_environment_and_result_file(
    tmp_path, monkeypatch
):
    root = tmp_path / "experiments"
    captured = []

    def fake_run(command, **kwargs):
        captured.append({"command": command, **kwargs})
        script = command[2] if len(command) > 2 and command[1] == "-c" else ""
        if "importlib.util.find_spec" in script:
            return subprocess.CompletedProcess(command, 0, stdout='{"missing": []}', stderr="")
        if "torch.cuda.is_available" in script:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "available": True,
                        "device_count": 1,
                        "device_names": ["Test GPU"],
                        "python_version": "3.11.9",
                        "torch_version": "2.13.0+cu132",
                        "torch_cuda": "13.2",
                    }
                ),
                stderr="",
            )
        cwd = Path(kwargs["cwd"])
        output = cwd / command[command.index("--output") + 1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "run_id": "run_1",
                    "experiment_id": "experiment_1",
                    "result_id": "experiment_1_result",
                    "metrics": {"accuracy": 0.93},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="training log", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_from(fake_run))
    settings = Settings.from_env(
        {
            "EXPERIMENT_PROVIDER": "local_gpu",
            "LOCAL_EXPERIMENT_WORKDIR": str(root),
            "LOCAL_GPU_PYTHON": r"C:\Program Files\Python\python.exe",
            "LOCAL_GPU_CUDA_VISIBLE_DEVICES": "0",
        }
    )

    result = LocalGpuExperimentProvider(settings).run(
        {"run_id": "run_1"}, _runtime_bundle(requirements=["numpy"])
    )

    experiment_dir = root / "run_1" / "experiment_1"
    assert result["experiment_id"] == "experiment_1"
    assert result["result_id"] == "experiment_1_result"
    assert result["workdir"] == str(experiment_dir.resolve())
    assert result["metrics"] == {"accuracy": 0.93}
    assert result["environment"]["python_version"] == "3.11.9"
    assert result["is_real_experiment"] is True
    attempt_dir = experiment_dir / "attempts" / result["attempt_id"]
    assert (attempt_dir / "manifest.json").is_file()
    assert (attempt_dir / "runtime_status.json").is_file()
    assert not (experiment_dir / ".experiment.lock").exists()
    assert (experiment_dir / "logs" / "experiment_1.log").read_text(encoding="utf-8") == (
        "training log"
    )
    assert all(call["env"]["CUDA_VISIBLE_DEVICES"] == "0" for call in captured)
    training_call = captured[-1]["command"]
    assert training_call[0] == r"C:\Program Files\Python\python.exe"


def test_local_provider_recovers_matching_completed_attempt_without_retraining(
    tmp_path, monkeypatch
):
    root = tmp_path / "experiments"

    def fake_run(command, **kwargs):
        script = command[2] if len(command) > 2 and command[1] == "-c" else ""
        if "importlib.util.find_spec" in script:
            return subprocess.CompletedProcess(command, 0, stdout='{"missing": []}', stderr="")
        cwd = Path(kwargs["cwd"])
        output = cwd / command[command.index("--output") + 1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "run_id": "run_1",
                    "experiment_id": "experiment_1",
                    "result_id": "experiment_1_result",
                    "metrics": {"accuracy": 0.93},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="training log", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_from(fake_run))
    provider = LocalGpuExperimentProvider(
        Settings.from_env(
            {
                "EXPERIMENT_PROVIDER": "local_gpu",
                "LOCAL_EXPERIMENT_WORKDIR": str(root),
                "LOCAL_GPU_PYTHON": "local-python",
            }
        )
    )
    bundle = _runtime_bundle(requires_gpu=False)
    original = provider.run({"run_id": "run_1"}, bundle)

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not retrain")),
    )
    recovered = provider.recover_completed_result({"run_id": "run_1"}, bundle)

    assert recovered is not None
    assert recovered["attempt_id"] == original["attempt_id"]
    assert recovered["metrics"] == {"accuracy": 0.93}
    assert recovered["recovered_from_completed_attempt"] is True

    attempt_dir = root / "run_1" / "experiment_1" / "attempts" / original["attempt_id"]
    (attempt_dir / "train.py").write_text("print('tampered')", encoding="utf-8")
    assert provider.recover_completed_result({"run_id": "run_1"}, bundle) is None


def test_local_bundle_runs_smoke_test_before_formal_attempt(tmp_path, monkeypatch):
    root = tmp_path / "experiments"
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        script = command[2] if len(command) > 2 and command[1] == "-c" else ""
        if "importlib.util.find_spec" in script:
            return subprocess.CompletedProcess(
                command, 0, stdout='{"missing": []}', stderr=""
            )
        cwd = Path(kwargs["cwd"])
        output = cwd / command[command.index("--output") + 1]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "run_id": "run_1",
                    "experiment_id": "experiment_1",
                    "result_id": "experiment_1_result",
                    "metrics": {"accuracy": 0.93},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_from(fake_run))
    bundle = _runtime_bundle(requires_gpu=False).model_copy(deep=True)
    bundle.manifest.supports_smoke_test = True
    provider = LocalGpuExperimentProvider(
        Settings.from_env(
            {
                "EXPERIMENT_PROVIDER": "local_gpu",
                "LOCAL_EXPERIMENT_WORKDIR": str(root),
                "LOCAL_GPU_PYTHON": "local-python",
            }
        )
    )

    result = provider.run({"run_id": "run_1"}, bundle)

    assert result["metrics"]["accuracy"] == 0.93
    assert any("--smoke-test" in command for command in calls)
    assert (root / "run_1" / "experiment_1" / "attempts" / result["attempt_id"] / "logs" / "smoke-test.log").is_file()


def test_local_bundle_smoke_uses_compiled_harness_without_output_argument(
    tmp_path, monkeypatch
):
    """The runtime harness owns output paths, so its command has no --output."""
    root = tmp_path / "experiments"
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        script = command[2] if len(command) > 2 and command[1] == "-c" else ""
        if "importlib.util.find_spec" in script:
            return subprocess.CompletedProcess(command, 0, stdout='{"missing": []}', stderr="")
        cwd = Path(kwargs["cwd"])
        name = "experiment_1_result.json"
        output = cwd / "results" / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "run_id": "run_1",
                    "experiment_id": "experiment_1",
                    "result_id": "experiment_1_result",
                    "metrics": {"accuracy": 0.93},
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_from(fake_run))
    plan = {
        "seeds": [7],
        "parameters": {"seed": 7},
        "iteration_contract": {"required_metrics": ["accuracy"]},
    }
    bundle = compile_bundle_runtime_contract(
        plan,
        {"run_id": "run_1", "experiment_id": "experiment_1", "result_id": "experiment_1_result"},
        _runtime_bundle(requires_gpu=False),
    )
    bundle.manifest.supports_smoke_test = True
    provider = LocalGpuExperimentProvider(
        Settings.from_env(
            {
                "EXPERIMENT_PROVIDER": "local_gpu",
                "LOCAL_EXPERIMENT_WORKDIR": str(root),
                "LOCAL_GPU_PYTHON": "local-python",
            }
        )
    )

    result = provider.run({"run_id": "run_1"}, bundle)

    harness_calls = [call for call in calls if len(call) > 1 and call[1] == ".gewu_harness.py"]
    assert harness_calls == [
        ["local-python", ".gewu_harness.py", "--smoke-test"],
        ["local-python", ".gewu_harness.py"],
    ]
    assert result["metrics"] == {"accuracy": 0.93}


def test_local_bundle_runner_reports_preflight_dependency_command(tmp_path, monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout='{"missing": ["numpy"]}', stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_from(fake_run))
    settings = Settings.from_env(
        {
            "EXPERIMENT_PROVIDER": "local_gpu",
            "LOCAL_EXPERIMENT_WORKDIR": str(tmp_path / "experiments"),
            "LOCAL_GPU_PYTHON": "local-python",
        }
    )

    with pytest.raises(RuntimeError, match="LOCAL_EXPERIMENT_DEPENDENCY_MISSING:numpy") as exc:
        LocalGpuExperimentProvider(settings).run(
            {"run_id": "run_1"}, _runtime_bundle(requires_gpu=False, requirements=["numpy"])
        )

    experiment_dir = (tmp_path / "experiments" / "run_1" / "experiment_1").resolve()
    assert str(experiment_dir) in str(exc.value)
    assert "attempts" in str(exc.value)
    assert "requirements.txt" in str(exc.value)


def test_local_bundle_runner_removes_stale_result_before_each_attempt(tmp_path, monkeypatch):
    root = tmp_path / "experiments"
    result_path = root / "run_1" / "experiment_1" / "results" / "experiment_1_result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "run_id": "run_1",
                "experiment_id": "experiment_1",
                "result_id": "experiment_1_result",
                "metrics": {"accuracy": 0.99},
            }
        ),
        encoding="utf-8",
    )

    def fake_run(command, **kwargs):
        if "-c" in command:
            return subprocess.CompletedProcess(command, 0, stdout='{"missing": []}', stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="finished without result", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_from(fake_run))
    settings = Settings.from_env(
        {
            "EXPERIMENT_PROVIDER": "local_gpu",
            "LOCAL_EXPERIMENT_WORKDIR": str(root),
            "LOCAL_GPU_PYTHON": "local-python",
        }
    )

    with pytest.raises(RuntimeError, match="EXPERIMENT_RESULT_MISSING"):
        LocalGpuExperimentProvider(settings).run(
            {"run_id": "run_1"},
            _runtime_bundle(requires_gpu=False, requirements=[]),
        )

    assert not result_path.exists()


def test_remote_bundle_runner_deploys_hashes_and_fetches_result_and_log(monkeypatch):
    calls = []
    deployment = {}

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        remote_command = command[-1]
        if "hashlib.sha256" in remote_command:
            deployment.update(json.loads(kwargs["input"]))
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "deployed_files": [
                            "/srv/ai-scientist/run_1/experiment_1/train.py"
                        ]
                    }
                ),
                stderr="",
            )
        if "importlib.util.find_spec" in remote_command:
            return subprocess.CompletedProcess(
                command, 0, stdout='{"missing": []}', stderr=""
            )
        if "torch.cuda.is_available" in remote_command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "available": True,
                        "device_count": 1,
                        "device_names": ["Remote Test GPU"],
                        "python_version": "3.10.14",
                        "torch_version": "2.13.0+cu132",
                        "torch_cuda": "13.2",
                    }
                ),
                stderr="",
            )
        if "EXPERIMENT_RESULT_MISSING" in remote_command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "run_id": "run_1",
                            "experiment_id": "experiment_1",
                            "result_id": "experiment_1_result",
                            "metrics": {"accuracy": 0.96},
                        },
                        "log": "remote training log",
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = Settings.from_env(
        {
            "EXPERIMENT_PROVIDER": "remote_gpu",
            "REMOTE_GPU_HOST": "gpu.example.com",
            "REMOTE_GPU_USER": "runner",
            "REMOTE_GPU_PROJECT_DIR": "/srv/ai-scientist",
            "REMOTE_GPU_PYTHON": "remote-python",
            "REMOTE_GPU_CUDA_VISIBLE_DEVICES": "2",
        }
    )

    result = RemoteGpuExperimentProvider(settings).run(
        {"run_id": "run_1"}, _runtime_bundle(requirements=["numpy"])
    )

    assert result["experiment_id"] == "experiment_1"
    assert result["result_id"] == "experiment_1_result"
    assert result["workdir"] == "/srv/ai-scientist/run_1/experiment_1"
    assert result["metrics"] == {"accuracy": 0.96}
    assert result["log"] == "remote training log"
    assert result["environment"]["device_names"] == ["Remote Test GPU"]
    assert result["environment"]["python_version"] == "3.10.14"
    assert deployment["root"] == "/srv/ai-scientist/run_1/experiment_1"
    assert deployment["files"][0]["sha256"] == _runtime_bundle().files[0].sha256
    assert all("BatchMode=yes" in call["command"] for call in calls)
    run_call = next(
        call for call in calls if "train.py" in call["command"][-1]
    )
    assert "CUDA_VISIBLE_DEVICES=2" in run_call["command"][-1]


def test_remote_bundle_runner_reports_dependency_command_without_installing(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command[-1])
        if "hashlib.sha256" in command[-1]:
            return subprocess.CompletedProcess(
                command, 0, stdout='{"deployed_files": []}', stderr=""
            )
        return subprocess.CompletedProcess(
            command, 0, stdout='{"missing": ["numpy"]}', stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = Settings.from_env(
        {
            "EXPERIMENT_PROVIDER": "remote_gpu",
            "REMOTE_GPU_HOST": "gpu.example.com",
            "REMOTE_GPU_USER": "runner",
            "REMOTE_GPU_PROJECT_DIR": "/srv/ai-scientist",
            "REMOTE_GPU_PYTHON": "remote-python",
        }
    )

    with pytest.raises(RuntimeError, match="REMOTE_EXPERIMENT_DEPENDENCY_MISSING:numpy") as exc:
        RemoteGpuExperimentProvider(settings).run(
            {"run_id": "run_1"},
            _runtime_bundle(requires_gpu=False, requirements=["numpy"]),
        )

    assert (
        "remote-python -m pip install -r "
        "/srv/ai-scientist/run_1/experiment_1/requirements.txt"
    ) in str(exc.value)
    assert not any("pip install" in command for command in commands)


def test_remote_bundle_runner_removes_stale_result_before_training(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        remote_command = command[-1]
        commands.append(remote_command)
        if "hashlib.sha256" in remote_command:
            return subprocess.CompletedProcess(
                command, 0, stdout='{"deployed_files": []}', stderr=""
            )
        if "importlib.util.find_spec" in remote_command:
            return subprocess.CompletedProcess(command, 0, stdout='{"missing": []}', stderr="")
        if "EXPERIMENT_RESULT_MISSING" in remote_command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "result": {
                            "run_id": "run_1",
                            "experiment_id": "experiment_1",
                            "result_id": "experiment_1_result",
                            "metrics": {"accuracy": 0.8},
                        },
                        "log": "ok",
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    settings = Settings.from_env(
        {
            "EXPERIMENT_PROVIDER": "remote_gpu",
            "REMOTE_GPU_HOST": "gpu.example.com",
            "REMOTE_GPU_USER": "runner",
            "REMOTE_GPU_PROJECT_DIR": "/srv/ai-scientist",
            "REMOTE_GPU_PYTHON": "remote-python",
        }
    )

    RemoteGpuExperimentProvider(settings).run(
        {"run_id": "run_1"},
        _runtime_bundle(requires_gpu=False, requirements=[]),
    )

    training_command = next(command for command in commands if "train.py" in command)
    assert "rm -f results/experiment_1_result.json" in training_command
    assert training_command.index("rm -f") < training_command.index("train.py")
