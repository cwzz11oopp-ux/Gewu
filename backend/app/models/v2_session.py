from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ResearchEventKind(StrEnum):
    SESSION_CREATED = "SESSION_CREATED"
    BASELINE_ACCEPTED = "BASELINE_ACCEPTED"
    BRANCH_GATE = "BRANCH_GATE"
    ACTION_SELECTED = "ACTION_SELECTED"
    EXPERIMENT_RECORDED = "EXPERIMENT_RECORDED"
    CRITIQUE_RECORDED = "CRITIQUE_RECORDED"
    PARAMETER_SWEEP_RECORDED = "PARAMETER_SWEEP_RECORDED"
    CLAIM_GRAPH_UPDATED = "CLAIM_GRAPH_UPDATED"
    REPORT_EXPORTED = "REPORT_EXPORTED"
    SESSION_STOPPED = "SESSION_STOPPED"


class ResearchSessionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"event_{uuid4().hex[:12]}")
    session_id: str = Field(min_length=1)
    kind: ResearchEventKind
    iteration: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)
