"""Versioned, append-only research-constraint contract for the production workflow."""
from __future__ import annotations

import re
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


_SEED_MARKER = re.compile(r"(?:\bseeds?\b|种子)", re.IGNORECASE)
_EXPLICIT_SEED_LIST = re.compile(
    r"^\s*(?:(?:values?\s*)?(?:are|is)\s*|[:=]\s*|为\s*|是\s*)?"
    r"(\[[^\]]+\]|\d+\s*(?:[,/，、]\s*\d+)+)",
    re.IGNORECASE,
)


def _seed_policy(value: Any) -> dict[str, Any]:
    """Preserve explicit seed values carried by legacy string shorthand.

    A leading count such as ``3 fixed seeds`` is intentionally not treated as
    a concrete seed.  Only a numeric list written after ``seed(s)``/``种子`` is
    promoted into the structured contract consumed by the seed allocator.
    """
    policy = _mapping(value, "seed_policy")
    if not isinstance(value, str):
        return policy
    description = policy.get("description", "")
    marker = _SEED_MARKER.search(description)
    if marker is None:
        return policy
    match = _EXPLICIT_SEED_LIST.match(description[marker.end():])
    if match is None:
        return policy
    seeds = [int(item) for item in re.findall(r"-?\d+", match.group(1))]
    if (
        not seeds
        or len(seeds) > 5
        or len(set(seeds)) != len(seeds)
        or any(seed < 1 or seed >= 2**31 for seed in seeds)
    ):
        return policy
    policy["seeds"] = seeds
    policy["count"] = len(seeds)
    return policy


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
        "seed_policy": _seed_policy(raw.get("seed_policy")),
        "split_policy": _mapping(raw.get("split_policy"), "split_policy"),
        "preprocessing_policy": _mapping(raw.get("preprocessing_policy"), "preprocessing_policy"),
        "repository": _mapping(raw.get("repository"), "repository"),
        "legacy_constraints": legacy,
    }
