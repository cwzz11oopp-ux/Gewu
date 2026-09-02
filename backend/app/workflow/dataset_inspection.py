from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.app.workflow.dataset_catalog import (
    canonical_dataset_name_from_text, dataset_card, dataset_display_name,
    dataset_name_in_text, dataset_spec, normalize_dataset_name, supported_dataset_names,
)


SUPPORTED_TABULAR_SUFFIXES = {".csv", ".tsv"}
SUPPORTED_JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}
SUPPORTED_ARRAY_SUFFIXES = {".npy", ".npz"}
SUPPORTED_MAT_SUFFIXES = {".mat"}
INSPECTABLE_SUFFIXES = (
    SUPPORTED_TABULAR_SUFFIXES
    | SUPPORTED_JSON_SUFFIXES
    | SUPPORTED_ARRAY_SUFFIXES
    | SUPPORTED_MAT_SUFFIXES
)
MAX_FILES = 10_000
MAX_PROFILE_FILES = 50
HASH_SAMPLE_BYTES = 64 * 1024


class DatasetInspectionError(ValueError):
    pass


def contract_canonical_name(profile: dict[str, Any] | None) -> str:
    """Read a contract's semantic name without treating a generic root as one."""
    dataset = profile or {}
    canonical = normalize_dataset_name(dataset.get("canonical_name"))
    if canonical:
        return canonical
    legacy = str(dataset.get("name") or "").strip()
    if legacy.casefold() in {"data", "dataset", "datasets"}:
        return ""
    return normalize_dataset_name(legacy)


def contract_dataset_name(profile: dict[str, Any] | None) -> str:
    """Return a stable runtime label for catalog and custom datasets."""
    dataset = profile or {}
    canonical = contract_canonical_name(dataset)
    if canonical:
        return canonical
    for key in ("display_name", "directory_name", "name"):
        value = str(dataset.get(key) or "").strip()
        if value and value.casefold() not in {"data", "dataset", "datasets"}:
            return value
    return ""


def resolve_dataset_directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise DatasetInspectionError(f"DATASET_DIRECTORY_NOT_FOUND:{path}")
    return path


def _directory_dataset_names(path: Path) -> set[str]:
    names = {path.name, re.sub(r"[\s_-]*dataset$", "", path.name, flags=re.I)} - {""}
    for canonical in supported_dataset_names():
        marker = Path(dataset_spec(canonical).marker).parts[0]
        if path.name.casefold() == marker.casefold() or normalize_dataset_name(path.name) == canonical:
            names.add(canonical)
    return names


def resolve_local_dataset_directory(
    value: str, problem: str = "", constraints: str = ""
) -> tuple[Path, str]:
    """Bind one concrete directory; never silently bind a shared dataset cache."""
    root = resolve_dataset_directory(value)

    def matches(path: Path, text: str) -> bool:
        return any(dataset_name_in_text(text, name) for name in _directory_dataset_names(path))

    def has_data_files(path: Path) -> bool:
        return any(
            item.is_file()
            and not item.name.startswith(".")
            and item.suffix.casefold() not in {".md", ".zip", ".names"}
            and not item.name.casefold().endswith(".tar.gz")
            for item in path.iterdir()
        )

    canonical = canonical_dataset_name_from_text(problem) or canonical_dataset_name_from_text(constraints)
    children = [path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")]
    selected = None
    for text in (problem, constraints):
        if not text:
            continue
        if root.name.casefold() not in {"data", "dataset", "datasets", "cache"} and matches(root, text):
            selected = root
            break
        candidates = [path for path in children if matches(path, text)]
        if len(candidates) > 1:
            raise DatasetInspectionError(
                "DATASET_DIRECTORY_AMBIGUOUS:" + ",".join(sorted(str(path) for path in candidates))
            )
        if candidates:
            selected = candidates[0]
            break
    if selected is None:
        if canonical:
            raise DatasetInspectionError(f"DATASET_SELECTED_DIRECTORY_NOT_FOUND:{canonical}:under={root}")
        if root.name.casefold() in {"data", "dataset", "datasets", "cache"} and (
            not has_data_files(root)
            or any(path.name.casefold() not in {"train", "test", "val", "validation", "raw", "processed"} for path in children)
        ):
            raise DatasetInspectionError(f"DATASET_DIRECTORY_SELECTION_REQUIRED:{root}")
        selected = root  # An explicitly configured custom dataset remains supported.

    # Some downloads wrap the real dataset in one same-named directory (UCI HAR).
    # Only unwrap that exact name, never an arbitrary train/test/raw subdirectory.
    if not has_data_files(selected):
        nested = [
            path for path in selected.iterdir()
            if path.is_dir() and any(matches(path, name) for name in _directory_dataset_names(selected))
        ]
        if len(nested) > 1:
            raise DatasetInspectionError(f"DATASET_DIRECTORY_AMBIGUOUS:{selected}")
        if nested:
            selected = nested[0]
    selected = selected.resolve()
    if selected != root and not selected.is_relative_to(root):
        raise DatasetInspectionError(f"DATASET_DIRECTORY_OUTSIDE_CONFIGURED_ROOT:{selected}")
    # A comparison mentioned in the question must not relabel a custom dataset.
    canonical = next((
        name for name in supported_dataset_names()
        if matches(selected, name)
        and (dataset_name_in_text(problem, name) or dataset_name_in_text(constraints, name))
    ), "")
    return selected, canonical


def inspect_dataset_directory(
    value: str, *, canonical_name: str = "", display_name: str = ""
) -> dict[str, Any]:
    root = resolve_dataset_directory(value)
    canonical = normalize_dataset_name(canonical_name)
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise DatasetInspectionError(f"DATASET_DIRECTORY_EMPTY:{root}")
    if len(files) > MAX_FILES:
        raise DatasetInspectionError(
            f"DATASET_DIRECTORY_TOO_MANY_FILES:{len(files)}:limit={MAX_FILES}"
        )

    file_records = [_file_record(root, path) for path in files]
    profile_records = [
        record
        for path, record in zip(files, file_records)
        if path.suffix.lower() in INSPECTABLE_SUFFIXES
    ][:MAX_PROFILE_FILES]
    schemas = []
    observed_structure = []
    for record in profile_records:
        path = root / record["relative_path"]
        schema = _inspect_file(path)
        if schema:
            schemas.append({"relative_path": record["relative_path"], **schema})
        observed_structure.append(_observe_file(root, path))

    fingerprint_payload = {
        "files": [
            {
                "relative_path": record["relative_path"],
                "size_bytes": record["size_bytes"],
                "sample_sha256": record["sample_sha256"],
            }
            for record in file_records
        ]
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    contract_id = f"dataset_{fingerprint[:16]}"
    suffix_counts: dict[str, int] = {}
    total_bytes = 0
    for record in file_records:
        suffix = record["suffix"] or "(no extension)"
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
        total_bytes += int(record["size_bytes"])

    return {
        "contract_id": contract_id,
        "source_type": "local",
        "inspection_status": "verified",
        "root": str(root),
        # Legacy `name` remains a display fallback only; do not use it for
        # semantic validation of a dataset contract.
        "name": display_name or dataset_display_name(canonical) or root.name,
        "canonical_name": canonical,
        "display_name": display_name or dataset_display_name(canonical) or root.name,
        "directory_name": root.name,
        "content_fingerprint": f"sha256:{fingerprint}",
        "file_count": len(file_records),
        "total_bytes": total_bytes,
        "file_types": suffix_counts,
        "files": file_records[:MAX_PROFILE_FILES],
        "schemas": schemas,
        # This is deliberately structural only: it exposes real local keys,
        # shapes, dtypes, and tabular headers without retaining any values.
        "observed_structure": observed_structure,
        "limitations": (
            []
            if schemas
            else [
                "No CSV, TSV, JSON, JSONL, NPY, or NPZ file could be structurally sampled. "
                "The file inventory is verified, but a custom loader may be required."
            ]
        ),
        # Structural inspection cannot establish meaning.  Keep every missing
        # semantic fact explicit so later stages can request verification.
        "semantic_facts": {
            name: {"status": "unknown", "source_type": "none", "source_ids": [], "confidence": None}
            for name in ("sample_unit", "label_semantics", "axis_semantics", "group_identity", "temporal_identity", "split_protocol", "leakage_relationships")
        },
    }


def verify_dataset_contract(profile: dict[str, Any], configured_root: str) -> dict[str, Any]:
    current = inspect_dataset_directory(
        configured_root,
        canonical_name=contract_canonical_name(profile),
        display_name=str(profile.get("display_name") or ""),
    )
    expected_root = str(Path(str(profile.get("root") or "")).resolve())
    if current["root"] != expected_root:
        raise DatasetInspectionError(
            f"DATASET_CONTRACT_ROOT_MISMATCH:expected={expected_root}:actual={current['root']}"
        )
    if current["contract_id"] != profile.get("contract_id"):
        raise DatasetInspectionError(
            "DATASET_CONTRACT_FINGERPRINT_MISMATCH:"
            f"expected={profile.get('content_fingerprint')}:"
            f"actual={current['content_fingerprint']}"
        )
    return current


def dataset_option(profile: dict[str, Any]) -> dict[str, Any]:
    canonical = contract_canonical_name(profile)
    local_card = {
        "name": profile.get("display_name") or profile["name"],
        "source_type": "local",
        "file_count": profile["file_count"],
        "total_bytes": profile["total_bytes"],
        "file_types": profile["file_types"],
        "files": profile["files"],
        "schemas": profile["schemas"],
        "observed_structure": profile.get("observed_structure") or [],
        "limitations": profile["limitations"],
        "loader": (
            "Read the verified relative file paths beneath os.environ['DATA_ROOT']. "
            "DATA_ROOT is the selected dataset directory, not a shared cache: do not "
            "append the dataset name again, walk to a parent, or download fallback data."
        ),
    }
    return {
        "name": canonical or profile["name"],
        "canonical_name": canonical,
        "display_name": profile.get("display_name") or profile["name"],
        "status": "bound",
        "source": "local",
        "contract_id": profile["contract_id"],
        "content_fingerprint": profile["content_fingerprint"],
        "root": profile["root"],
        "card": {**(dataset_card(canonical) if canonical else {}), **local_card},
    }


def _file_record(root: Path, path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "suffix": path.suffix.lower(),
        "size_bytes": stat.st_size,
        "sample_sha256": _sample_hash(path),
    }


def _sample_hash(path: Path) -> str:
    digest = hashlib.sha256()
    size = path.stat().st_size
    with path.open("rb") as stream:
        digest.update(stream.read(HASH_SAMPLE_BYTES))
        if size > HASH_SAMPLE_BYTES:
            stream.seek(max(0, size - HASH_SAMPLE_BYTES))
            digest.update(stream.read(HASH_SAMPLE_BYTES))
    digest.update(str(size).encode("ascii"))
    return digest.hexdigest()


def _inspect_file(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    try:
        if suffix in SUPPORTED_TABULAR_SUFFIXES:
            return _inspect_delimited(path, "\t" if suffix == ".tsv" else ",")
        if suffix in {".jsonl", ".ndjson"}:
            return _inspect_json_lines(path)
        if suffix == ".json":
            return _inspect_json(path)
        if suffix in SUPPORTED_ARRAY_SUFFIXES:
            return _inspect_numpy(path)
        if suffix in SUPPORTED_MAT_SUFFIXES:
            return _inspect_mat(path)
    except Exception as exc:
        return {"format": suffix.lstrip("."), "inspection_error": str(exc)}
    return {}


def _observe_file(root: Path, path: Path) -> dict[str, Any]:
    """Return bounded, value-free facts observed from one supported local file."""
    suffix = path.suffix.lower()
    observed: dict[str, Any] = {
        "relative_path": path.relative_to(root).as_posix(),
        "filename": path.name,
        "format": suffix.lstrip("."),
        "suffix": suffix,
    }
    try:
        if suffix in SUPPORTED_TABULAR_SUFFIXES:
            return {**observed, **_observe_delimited(path, "\t" if suffix == ".tsv" else ",")}
        if suffix in SUPPORTED_MAT_SUFFIXES:
            return {**observed, **_observe_mat(path)}
        schema = _inspect_file(path)
        return {**observed, **schema}
    except Exception as exc:
        # Inspection is advisory for code generation.  A bad auxiliary file
        # must be visible, but must not prevent the immutable dataset contract
        # from being created for the remaining real files.
        return {**observed, "inspection_error": str(exc)}


def _observe_delimited(path: Path, delimiter: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter=delimiter)
        columns = next(reader, [])
        rows = [row for _, row in zip(range(20), reader)]
    value_types = _column_types(rows, len(columns))
    return {
        "format": "tsv" if delimiter == "\t" else "csv",
        "columns": columns,
        "column_dtypes": {
            name: value_types[index]
            for index, name in enumerate(columns)
            if name
        },
    }


def _observe_mat(path: Path) -> dict[str, Any]:
    scipy_error: Exception | None = None
    try:
        import numpy as np
        from scipy.io import loadmat

        values = loadmat(path)
        return {
            "format": "mat",
            "arrays": [
                {
                    "key": name,
                    "shape": list(np.asarray(value).shape),
                    "dtype": str(np.asarray(value).dtype),
                }
                for name, value in values.items()
                if not name.startswith("__")
            ],
        }
    except Exception as exc:
        scipy_error = exc

    try:
        import h5py

        with h5py.File(path, "r") as archive:
            arrays = [
                {
                    "key": name,
                    "shape": list(value.shape),
                    "dtype": str(value.dtype),
                }
                for name, value in archive.items()
                if not name.startswith("__") and isinstance(value, h5py.Dataset)
            ]
        return {"format": "mat", "arrays": arrays}
    except Exception as h5_error:
        return {
            "format": "mat",
            "inspection_error": (
                "MAT structural read failed: "
                f"scipy={scipy_error}; h5py={h5_error}"
            ),
        }


def _inspect_delimited(path: Path, delimiter: str) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter=delimiter)
        header = next(reader, [])
        rows = []
        for _, row in zip(range(20), reader):
            rows.append(row)
    return {
        "format": "tsv" if delimiter == "\t" else "csv",
        "columns": header,
        "column_count": len(header),
        "sampled_row_count": len(rows),
        "sampled_value_types": _column_types(rows, len(header)),
    }


def _inspect_json_lines(path: Path) -> dict[str, Any]:
    records = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for _, line in zip(range(20), stream):
            if line.strip():
                records.append(json.loads(line))
    return {
        "format": "jsonl",
        "sampled_record_count": len(records),
        "record_schema": _json_schema(records),
    }


def _inspect_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 10 * 1024 * 1024:
        return {"format": "json", "inspection_error": "JSON file exceeds the 10 MB sampling limit."}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    records = value[:20] if isinstance(value, list) else [value]
    return {
        "format": "json",
        "top_level_type": type(value).__name__,
        "record_schema": _json_schema(records),
        "declared_record_count": len(value) if isinstance(value, list) else 1,
    }


def _inspect_numpy(path: Path) -> dict[str, Any]:
    try:
        import numpy as np
    except ImportError:
        return {"format": path.suffix.lower().lstrip("."), "inspection_error": "numpy unavailable"}
    if path.suffix.lower() == ".npy":
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        return {"format": "npy", "shape": list(array.shape), "dtype": str(array.dtype)}
    archive = np.load(path, mmap_mode="r", allow_pickle=False)
    return {
        "format": "npz",
        "arrays": {
            name: {"shape": list(archive[name].shape), "dtype": str(archive[name].dtype)}
            for name in archive.files[:50]
        },
    }


def _inspect_mat(path: Path) -> dict[str, Any]:
    try:
        from scipy.io import whosmat

        variables = whosmat(path)
        return {
            "format": "mat",
            "mat_version": "v4-v7.2",
            "variables": {
                name: {"shape": list(shape), "matlab_class": matlab_class}
                for name, shape, matlab_class in variables[:100]
                if not name.startswith("__")
            },
        }
    except (ImportError, NotImplementedError, OSError, TypeError, ValueError):
        pass

    try:
        import h5py
    except ImportError:
        return {
            "format": "mat",
            "inspection_error": "scipy could not read the MAT header and h5py is unavailable",
        }
    try:
        variables: dict[str, dict[str, Any]] = {}
        with h5py.File(path, "r") as archive:
            for name in list(archive.keys())[:100]:
                value = archive[name]
                if isinstance(value, h5py.Dataset):
                    variables[name] = {
                        "shape": list(value.shape),
                        "dtype": str(value.dtype),
                    }
                else:
                    variables[name] = {"type": "group"}
        return {
            "format": "mat",
            "mat_version": "v7.3",
            "variables": variables,
        }
    except (OSError, TypeError, ValueError) as exc:
        return {"format": "mat", "inspection_error": str(exc)}


def _column_types(rows: list[list[str]], width: int) -> list[str]:
    types = []
    for index in range(width):
        values = [row[index] for row in rows if index < len(row) and row[index] != ""]
        if not values:
            types.append("unknown")
        elif all(_is_int(value) for value in values):
            types.append("integer")
        elif all(_is_float(value) for value in values):
            types.append("number")
        else:
            types.append("string")
    return types


def _json_schema(records: list[Any]) -> dict[str, str]:
    fields: dict[str, set[str]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        for key, value in record.items():
            fields.setdefault(str(key), set()).add(type(value).__name__)
    return {key: "|".join(sorted(values)) for key, values in sorted(fields.items())}


def _is_int(value: str) -> bool:
    try:
        int(value)
        return True
    except ValueError:
        return False


def _is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False
