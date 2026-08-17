import json

import pytest

from backend.app.models.experiment import ExperimentBundle, ExperimentFile, ExperimentManifest
from backend.app.providers.experiment_runtime import (
    build_python_command,
    cuda_probe_command,
    parse_cuda_probe,
    validate_result_file,
)


@pytest.fixture
def bundle():
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
            expected_metrics=["accuracy"],
        ),
        files=[ExperimentFile(path="train.py", content="print('ok')")],
    )


def test_python_path_with_spaces_stays_one_argument(bundle):
    command = build_python_command(r"C:\Program Files\Python\python.exe", bundle)

    assert command[0] == r"C:\Program Files\Python\python.exe"
    assert command[1] == "train.py"
    assert command[2:] == bundle.manifest.python_args


def test_result_file_must_match_manifest_ids(tmp_path, bundle):
    path = tmp_path / "result.json"
    path.write_text(
        json.dumps(
            {
                "run_id": "run_1",
                "experiment_id": "experiment_2",
                "result_id": "experiment_2_result",
                "metrics": {"accuracy": 0.9},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="EXPERIMENT_RESULT_ID_MISMATCH"):
        validate_result_file(path, bundle.manifest)


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"run_id": "run_1", "experiment_id": "experiment_1", "result_id": "experiment_1_result", "metrics": {}}, "EXPERIMENT_METRIC_MISSING"),
        ({"run_id": "run_1", "experiment_id": "experiment_1", "result_id": "experiment_1_result", "metrics": {"accuracy": float("nan")}}, "EXPERIMENT_METRIC_NON_FINITE"),
        ({"run_id": "run_1", "experiment_id": "experiment_1", "result_id": "experiment_1_result", "metrics": {"accuracy": float("inf")}}, "EXPERIMENT_METRIC_NON_FINITE"),
    ],
)
def test_result_file_rejects_missing_or_non_finite_metrics(tmp_path, bundle, payload, error):
    path = tmp_path / "result.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match=error):
        validate_result_file(path, bundle.manifest)


def test_result_file_missing_and_invalid_json_are_stable_errors(tmp_path, bundle):
    missing = tmp_path / "missing.json"
    with pytest.raises(RuntimeError, match="EXPERIMENT_RESULT_MISSING"):
        validate_result_file(missing, bundle.manifest)

    invalid = tmp_path / "invalid.json"
    invalid.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="EXPERIMENT_RESULT_INVALID_JSON"):
        validate_result_file(invalid, bundle.manifest)


def test_cuda_probe_is_structured_json_and_unavailable_state_is_preserved():
    command = cuda_probe_command("python")
    assert command[:2] == ["python", "-c"]

    probe = parse_cuda_probe(
        json.dumps(
            {
                "available": False,
                "device_count": 0,
                "device_names": [],
                "torch_version": "2.13.0+cpu",
                "torch_cuda": None,
            }
        )
    )

    assert probe.available is False
    assert probe.device_count == 0


def test_cuda_probe_rejects_non_json_output():
    with pytest.raises(RuntimeError, match="EXPERIMENT_CUDA_PROBE_FAILED"):
        parse_cuda_probe("torch unavailable")
