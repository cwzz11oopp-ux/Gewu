"""Versioned, append-only research-constraint contract for the production workflow."""
from __future__ import annotations
from copy import deepcopy
from typing import Any

TASK_TYPES = {"classification", "forecasting", "anomaly_detection"}


def _mapping(value: Any, field: str, *, shorthand_key: str = "description") -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return deepcopy(value)
    if isinstance(value, str):
        return {shorthand_key: value.strip()}
    raise ValueError(f"RESEARCH_CONSTRAINT_{field.upper()}_INVALID")


def _items(value: Any, field: str) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value.strip()]
    if isinstance(value, (list, tuple)):
        return list(value)
    raise ValueError(f"RESEARCH_CONSTRAINT_{field.upper()}_INVALID")


def normalize_constraints(value: dict[str, Any] | None, legacy: str = "") -> dict[str, Any]:
    raw = deepcopy(value or {})
    task_type = str(raw.get("task_type") or "").strip()
    if task_type and task_type not in TASK_TYPES:
        raise ValueError("RESEARCH_CONSTRAINT_TASK_TYPE_INVALID")
    return {
        "schema_version": 1,
        "task_type": task_type,
        "dataset": _mapping(raw.get("dataset"), "dataset", shorthand_key="name"),
        "dataset_description": str(raw.get("dataset_description") or ""),
        "baseline": _mapping(raw.get("baseline"), "baseline"),
        "modifiable": _items(raw.get("modifiable"), "modifiable"),
        "frozen": _items(raw.get("frozen"), "frozen"),
        "primary_metrics": _items(raw.get("primary_metrics"), "primary_metrics"),
        "secondary_metrics": _items(raw.get("secondary_metrics"), "secondary_metrics"),
        "epochs": raw.get("epochs"),
        "seed_policy": _mapping(raw.get("seed_policy"), "seed_policy"),
        "split_policy": _mapping(raw.get("split_policy"), "split_policy"),
        "preprocessing_policy": _mapping(raw.get("preprocessing_policy"), "preprocessing_policy"),
        "repository": _mapping(raw.get("repository"), "repository"),
        "legacy_constraints": legacy,
    }
