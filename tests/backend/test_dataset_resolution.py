from types import SimpleNamespace

import pytest

from backend.app.workflow.dataset_catalog import canonical_dataset_name_from_text
from backend.app.workflow.dataset_inspection import (
    DatasetInspectionError,
    dataset_option,
    inspect_dataset_directory,
    resolve_local_dataset_directory,
)
from backend.app.workflow.engine import WorkflowEngine


@pytest.fixture
def datasets(tmp_path):
    root = tmp_path / "datasets"
    for relative in (
        "cifar-10-batches-py/data_batch_1",
        "cifar-100-python/train",
        "KMNIST/raw/train-images-idx3-ubyte",
        "UCIHAR/UCI HAR Dataset/train/X_train.txt",
        "BloodMNIST/samples.csv",
        "fashionmnist/train-images-idx3-ubyte.gz",
        "MNIST/raw/train-images-idx3-ubyte",
        "IPIX17/samples.csv",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x,label\n1,0\n")
    (root / "README.md").write_text("shared cache", encoding="utf-8")
    (root / "UCIHAR/UCI HAR Dataset.names").write_text("metadata", encoding="utf-8")
    (root / "UCIHAR/UCI HAR Dataset.zip").write_bytes(b"archive placeholder")
    return root


@pytest.mark.parametrize("problem,expected", [
    ("使用KMNIST训练", ""),
    ("Use BloodMNIST", ""),
    ("Use MedMNIST", ""),
    ("使用MNIST训练", "mnist"),
    ("Fashion MNIST", "fashion-mnist"),
    ("fashion_mnist", "fashion-mnist"),
    ("CIFAR-100", "cifar-100"),
    ("CIFAR10", "cifar-10"),
    ("NotMNISTExtra", ""),
])
def test_catalog_names_have_boundaries(problem, expected):
    assert canonical_dataset_name_from_text(problem) == expected


@pytest.mark.parametrize("name,relative,canonical", [
    ("CIFAR-10", "cifar-10-batches-py", "cifar-10"),
    ("CIFAR-100", "cifar-100-python", "cifar-100"),
    ("KMNIST", "KMNIST", ""),
    ("UCI HAR", "UCIHAR/UCI HAR Dataset", ""),
    ("BloodMNIST", "BloodMNIST", ""),
    ("FashionMNIST", "fashionmnist", "fashion-mnist"),
    ("MNIST", "MNIST", "mnist"),
    ("IPIX17", "IPIX17", ""),
])
def test_parent_and_concrete_directory_bind_same_dataset(datasets, name, relative, canonical):
    expected = (datasets / relative).resolve()
    question = f"使用{name}开展研究"
    assert resolve_local_dataset_directory(str(datasets), question) == (expected, canonical)
    assert resolve_local_dataset_directory(str(expected), question) == (expected, canonical)


def test_har_download_wrapper_resolves_inner_data_directory(datasets):
    selected, canonical = resolve_local_dataset_directory(str(datasets / "UCIHAR"), "使用UCI HAR")
    assert selected == (datasets / "UCIHAR/UCI HAR Dataset").resolve()
    assert canonical == ""


def test_parent_path_in_question_does_not_bind_entire_cache(datasets):
    selected, _ = resolve_local_dataset_directory(str(datasets), f"Use KMNIST in {datasets}")
    assert selected == (datasets / "KMNIST").resolve()


def test_explicit_directory_takes_precedence_over_comparison_dataset(datasets):
    selected, canonical = resolve_local_dataset_directory(
        str(datasets / "KMNIST"), "Compare KMNIST and MNIST"
    )
    assert selected == (datasets / "KMNIST").resolve()
    assert canonical == ""


def test_name_can_be_selected_from_constraints(datasets):
    selected, _ = resolve_local_dataset_directory(str(datasets), "训练轻量网络", "数据固定为 BloodMNIST")
    assert selected == (datasets / "BloodMNIST").resolve()


def test_explicit_catalog_directory_keeps_its_name_when_comparison_is_mentioned(datasets):
    selected, canonical = resolve_local_dataset_directory(
        str(datasets / "cifar-10-batches-py"), "Compare CIFAR-10 and FashionMNIST"
    )
    assert selected == (datasets / "cifar-10-batches-py").resolve()
    assert canonical == "cifar-10"


def test_multiple_parent_matches_require_explicit_selection(datasets):
    with pytest.raises(DatasetInspectionError, match="DATASET_DIRECTORY_AMBIGUOUS"):
        resolve_local_dataset_directory(str(datasets), "Compare KMNIST and BloodMNIST")


def test_multiple_alias_directories_do_not_select_first(datasets):
    (datasets / "cifar10").mkdir()
    with pytest.raises(DatasetInspectionError, match="DATASET_DIRECTORY_AMBIGUOUS"):
        resolve_local_dataset_directory(str(datasets), "CIFAR-10")


def test_missing_catalog_dataset_does_not_fall_back_to_other_dataset(datasets):
    with pytest.raises(DatasetInspectionError, match="DATASET_SELECTED_DIRECTORY_NOT_FOUND"):
        resolve_local_dataset_directory(str(datasets / "BloodMNIST"), "CIFAR-10")


def test_shared_parent_without_selection_is_rejected(datasets):
    with pytest.raises(DatasetInspectionError, match="DATASET_DIRECTORY_SELECTION_REQUIRED"):
        resolve_local_dataset_directory(str(datasets), "训练轻量网络")


def test_parent_metadata_file_does_not_make_entire_cache_a_dataset(datasets):
    (datasets / "metadata.json").write_text("{}", encoding="utf-8")
    with pytest.raises(DatasetInspectionError, match="DATASET_DIRECTORY_SELECTION_REQUIRED"):
        resolve_local_dataset_directory(str(datasets), "训练轻量网络")


def test_explicit_custom_directory_without_catalog_name_is_preserved(tmp_path):
    root = tmp_path / "custom-signals"
    root.mkdir()
    (root / "samples.csv").write_text("x,label\n1,0\n", encoding="utf-8")
    assert resolve_local_dataset_directory(str(root), "Train a classifier") == (root.resolve(), "")


def test_custom_named_directory_is_not_confused_by_substring(datasets):
    with pytest.raises(DatasetInspectionError, match="DATASET_DIRECTORY_SELECTION_REQUIRED"):
        resolve_local_dataset_directory(str(datasets), "Use myKMNISTextra")


def test_engine_binds_selected_directory_and_keeps_concrete_loader_hint(datasets):
    settings = SimpleNamespace(dataset_source="local", dataset_dir=str(datasets))
    engine = SimpleNamespace(experiment_provider=SimpleNamespace(settings=settings))
    run = SimpleNamespace(problem_input="使用CIFAR-10", constraints="")
    profile = WorkflowEngine._inspect_configured_local_dataset(engine, run)
    assert profile["root"] == str((datasets / "cifar-10-batches-py").resolve())
    assert profile["canonical_name"] == "cifar-10"
    card = dataset_option(profile)["card"]
    assert "selected dataset directory" in card["loader"]
    assert "not a shared cache" in card["loader"]
    bound = WorkflowEngine._bind_plan_to_dataset({}, profile)
    settings.dataset_dir = str(datasets / "KMNIST")
    assert bound["dataset"]["root"] == profile["root"]
    assert bound["dataset"]["contract_id"] == profile["contract_id"]


def test_inspection_fingerprint_excludes_other_datasets(datasets):
    root, canonical = resolve_local_dataset_directory(str(datasets), "KMNIST")
    before = inspect_dataset_directory(str(root), canonical_name=canonical)
    (datasets / "IPIX17/samples.csv").write_text("changed", encoding="utf-8")
    after = inspect_dataset_directory(str(root), canonical_name=canonical)
    assert before["contract_id"] == after["contract_id"]
    assert all("IPIX" not in item["relative_path"] for item in before["files"])
