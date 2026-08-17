from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: f"art_{uuid4().hex[:12]}")
    run_id: str
    type: str
    version: int
    title: str
    content: dict[str, Any]
    source_step: str
    locked: bool = False
    created_by: str
    created_at: str = Field(default_factory=utc_now)
    parent_artifact_id: str | None = None


class EventRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"evt_{uuid4().hex[:12]}")
    run_id: str
    step_id: str
    level: str = "info"
    actor: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    provider_mode: str = ""
    fallback_used: bool = False
    fallback_reason: str = ""
    timestamp: str = Field(default_factory=utc_now)
