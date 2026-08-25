import json

import pytest

from backend.app.workflow.dataset_inspection import (
    DatasetInspectionError,
    contract_canonical_name,
    contract_dataset_name,
    dataset_option,
    inspect_dataset_directory,
    verify_dataset_contract,
)
from backend.app.workflow.phase2_evidence import dataset_profile
from backend.app.workflow.engine import WorkflowEngine


def test_inspection_creates_stable_contract_and_schema(tmp_path):
    root = tmp_path / "ipix"
    root.mkdir()
    (root / "train.csv").write_text(
        "signal,label\n0.1,0\n0.8,1\n", encoding="utf-8"
    )
    (root / "metadata.json").write_text(
        json.dumps([{"length": 512, "channel": "HH"}]), encoding="utf-8"
    )

    first = inspect_dataset_directory(str(root))
    second = inspect_dataset_directory(str(root))

    assert first["inspection_status"] == "verified"
    assert first["contract_id"] == second["contract_id"]
    assert first["content_fingerprint"] == second["content_fingerprint"]
    assert first["file_count"] == 2
    assert {item["format"] for item in first["schemas"]} == {"csv", "json"}


def test_contract_verification_rejects_changed_dataset(tmp_path):
    root = tmp_path / "ipix"
    root.mkdir()
    data = root / "train.csv"
    data.write_text("signal,label\n0.1,0\n", encoding="utf-8")
    profile = inspect_dataset_directory(str(root))
    data.write_text("signal,label\n0.1,0\n0.8,1\n", encoding="utf-8")

    with pytest.raises(
        DatasetInspectionError, match="DATASET_CONTRACT_FINGERPRINT_MISMATCH"
    ):
        verify_dataset_contract(profile, str(root))


def test_inspection_rejects_empty_directory(tmp_path):
    with pytest.raises(DatasetInspectionError, match="DATASET_DIRECTORY_EMPTY"):
        inspect_dataset_directory(str(tmp_path))


def test_inspection_reads_mat_variable_shapes_without_loading_full_arrays(tmp_path):
    scipy_io = pytest.importorskip("scipy.io")
    root = tmp_path / "ipix"
    root.mkdir()
    scipy_io.savemat(
        root / "clu.mat",
        {"clutter": [[0.1, 0.2, 0.3]], "labels": [[0, 1, 0]]},
    )

    profile = inspect_dataset_directory(str(root))

    schema = profile["schemas"][0]
    assert schema["format"] == "mat"
    assert schema["variables"]["clutter"]["shape"] == [1, 3]
    assert schema["variables"]["labels"]["shape"] == [1, 3]


def test_observed_structure_reads_real_mat_keys_shapes_and_dtypes_without_values(tmp_path):
    scipy_io = pytest.importorskip("scipy.io")
    numpy = pytest.importorskip("numpy")
    root = tmp_path / "ipix"
    root.mkdir()
    scipy_io.savemat(
        root / "clutter.mat",
        {"clutter": numpy.array([[123456.75, 2.0]], dtype=numpy.float32)},
    )

    profile = inspect_dataset_directory(str(root))

    observed = profile["observed_structure"]
    assert observed == [{
        "relative_path": "clutter.mat",
        "filename": "clutter.mat",
        "format": "mat",
        "suffix": ".mat",
        "arrays": [{"key": "clutter", "shape": [1, 2], "dtype": "float32"}],
    }]
    assert "__header__" not in json.dumps(observed)
    assert "123456.75" not in json.dumps(observed)


def test_observed_structure_reads_csv_columns_and_recognized_dtypes(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "signals.csv").write_text(
        "signal,label,name\n0.25,1,clutter\n", encoding="utf-8"
    )

    observed = inspect_dataset_directory(str(root))["observed_structure"]

    assert observed == [{
        "relative_path": "signals.csv",
        "filename": "signals.csv",
        "format": "csv",
        "suffix": ".csv",
        "columns": ["signal", "label", "name"],
        "column_dtypes": {"signal": "number", "label": "integer", "name": "string"},
    }]


def test_observed_structure_records_mat_parse_failure_without_fabricating_arrays(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "broken.mat").write_bytes(b"not a mat file")

    observed = inspect_dataset_directory(str(root))["observed_structure"][0]

    assert observed["relative_path"] == "broken.mat"
    assert observed["format"] == "mat"
    assert observed["inspection_error"]
    assert "arrays" not in observed


def test_dataset_profile_and_bound_option_preserve_observed_structure(tmp_path):
    root = tmp_path / "dataset"
    root.mkdir()
    (root / "signals.csv").write_text("x,label\n1.0,0\n", encoding="utf-8")
    inspected = inspect_dataset_directory(str(root))

    profile = dataset_profile(inspected, {"task_type": "classification"})

    assert profile["observed_structure"] == inspected["observed_structure"]
    assert dataset_option(profile)["card"]["observed_structure"] == inspected["observed_structure"]
    accepted_plan = WorkflowEngine._bind_plan_to_dataset({"dataset": {}}, profile)
    assert accepted_plan["dataset"]["observed_structure"] == inspected["observed_structure"]


def test_named_contract_separates_semantics_from_directory_name(tmp_path):
    root = tmp_path / "fashionmnist"
    root.mkdir()
    (root / "train.bin").write_bytes(b"fashion")

    profile = inspect_dataset_directory(
        str(root), canonical_name="FashionMNIST", display_name="FashionMNIST"
    )

    assert profile["canonical_name"] == "fashion-mnist"
    assert profile["display_name"] == "FashionMNIST"
    assert profile["directory_name"] == "fashionmnist"
    assert profile["root"] == str(root.resolve())


def test_legacy_generic_directory_name_is_not_a_canonical_dataset():
    assert contract_canonical_name({"name": "datasets"}) == ""
    assert contract_canonical_name({"name": "FashionMNIST"}) == "fashion-mnist"


def test_custom_dataset_has_a_runtime_name_without_a_catalog_alias():
    assert contract_dataset_name(
        {
            "canonical_name": "",
            "display_name": "IPIX17",
            "directory_name": "IPIX17",
        }
    ) == "IPIX17"
    assert contract_dataset_name({"name": "datasets"}) == ""
