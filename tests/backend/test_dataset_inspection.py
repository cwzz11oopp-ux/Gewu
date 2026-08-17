import json

import pytest

from backend.app.workflow.dataset_inspection import (
    DatasetInspectionError,
    contract_canonical_name,
    inspect_dataset_directory,
    verify_dataset_contract,
)


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
