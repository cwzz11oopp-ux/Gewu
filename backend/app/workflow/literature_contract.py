"""Canonical compatibility helpers for literature source text.

Persisted and newly serialized literature cards use ``abstract``. Historical
artifacts and raw provider fixtures may still carry the same source text under
``claim``; uploaded documents may additionally expose ``available_text``.
Every workflow consumer should resolve those aliases at this boundary instead
of silently dropping valid source text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def literature_text(value: Any, fields: Sequence[str]) -> str:
    """Return the first non-empty source field without inventing content."""
    if isinstance(value, Mapping):
        getter = value.get
    else:
        getter = lambda field: getattr(value, field, None)
    for field in fields:
        candidate = getter(field)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def literature_evidence_text(value: Any) -> str:
    """Resolve the richest traceable text for deterministic evidence mining."""
    return literature_text(value, ("available_text", "abstract", "claim", "summary"))


def literature_summary_text(value: Any) -> str:
    """Resolve bounded summary text for prompts, cards, and report metadata."""
    return literature_text(value, ("abstract", "claim", "summary", "available_text"))
