"""Versioned, append-only research-constraint contract for the production workflow."""
from __future__ import annotations
from copy import deepcopy
from typing import Any

TASK_TYPES = {"classification", "forecasting", "anomaly_detection"}

def normalize_constraints(value: dict[str, Any] | None, legacy: str = "") -> dict[str, Any]:
    raw = deepcopy(value or {})
    task_type = str(raw.get("task_type") or "").strip()
    if task_type and task_type not in TASK_TYPES:
        raise ValueError("RESEARCH_CONSTRAINT_TASK_TYPE_INVALID")
    return {"schema_version": 1, "task_type": task_type, "dataset": dict(raw.get("dataset") or {}), "dataset_description": str(raw.get("dataset_description") or ""), "baseline": dict(raw.get("baseline") or {}), "modifiable": list(raw.get("modifiable") or []), "frozen": list(raw.get("frozen") or []), "primary_metrics": list(raw.get("primary_metrics") or []), "secondary_metrics": list(raw.get("secondary_metrics") or []), "epochs": raw.get("epochs"), "seed_policy": dict(raw.get("seed_policy") or {}), "split_policy": dict(raw.get("split_policy") or {}), "preprocessing_policy": dict(raw.get("preprocessing_policy") or {}), "repository": dict(raw.get("repository") or {}), "legacy_constraints": legacy}
