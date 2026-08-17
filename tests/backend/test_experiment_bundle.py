import pytest

from backend.app.models.experiment import ExperimentBundle, ExperimentFile, ExperimentManifest
from backend.app.storage.repository import Repository
from backend.app.workflow.experiment_bundle import result_id_for


def _bundle(run_id="run_1", experiment_id="experiment_1", result_id="experiment_1_result"):
    return ExperimentBundle(
        manifest=ExperimentManifest(
            run_id=run_id,
            experiment_id=experiment_id,
            result_id=result_id,
            python_args=[
                "--run-id",
                run_id,
                "--experiment-id",
                experiment_id,
                "--result-id",
                result_id,
                "--output",
                f"results/{result_id}.json",
            ],
        ),
        files=[ExperimentFile(path="train.py", content="print('ok')")],
    )


def test_experiment_and_result_ids_are_stable_within_run(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    run = repository.create_run("problem", "Run")

    assert repository.next_experiment_id(run.id) == "experiment_1"
    repository.add_artifact(
        run.id,
        "experiment_task",
        "Experiment 1",
        {"experiment_id": "experiment_1"},
        "experiment_task",
        "Experiment Agent",
    )

    assert repository.next_experiment_id(run.id) == "experiment_2"
    assert result_id_for("experiment_1") == "experiment_1_result"


def test_separate_runs_start_at_experiment_one_and_parameters_do_not_change_id(tmp_path):
    repository = Repository(data_dir=str(tmp_path / "data"))
    first = repository.create_run("problem", "First")
    second = repository.create_run("problem", "Second")

    assert repository.next_experiment_id(first.id) == "experiment_1"
    assert repository.next_experiment_id(second.id) == "experiment_1"
    assert _bundle().manifest.experiment_id == _bundle().model_copy(
        update={"manifest": _bundle().manifest.model_copy(update={"parameters": {"lr": 0.1}})}
    ).manifest.experiment_id


@pytest.mark.parametrize("experiment_id", ["experiment_0", "experiment_x", "../experiment_1"])
def test_manifest_rejects_invalid_experiment_id(experiment_id):
    with pytest.raises(ValueError):
        _bundle(experiment_id=experiment_id, result_id=f"{experiment_id}_result")


def test_manifest_requires_matching_result_id():
    with pytest.raises(ValueError, match="EXPERIMENT_RESULT_ID_MISMATCH"):
        _bundle(result_id="experiment_2_result")


def test_bundle_rejects_parent_paths():
    with pytest.raises(ValueError, match="EXPERIMENT_CODE_PATH_INVALID"):
        ExperimentFile(path="../train.py", content="print('bad')")
