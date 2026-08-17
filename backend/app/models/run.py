from __future__ import annotations

from uuid import uuid4
from typing import Any

from pydantic import BaseModel, Field

from backend.app.models.artifact import Artifact, EventRecord, utc_now


class StepRecord(BaseModel):
    id: str
    name: str
    status: str = "pending"
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    can_rerun: bool = True
    started_at: str | None = None
    completed_at: str | None = None
    error: dict | None = None


class RunRecord(BaseModel):
    id: str = Field(default_factory=lambda: f"run_{uuid4().hex[:12]}")
    title: str
    domain: str = "code-centered deep learning"
    problem_input: str
    constraints: str = ""
    research_constraints: dict[str, Any] = Field(default_factory=dict)
    research_constraints_artifact_id: str | None = None
    # Optional public repository context; absent on historic checkpoints.
    github_repository_url: str | None = None
    # Empty keeps historic runs on the established content-based language
    # detection path; callers can explicitly persist zh-CN or en.
    language: str = ""
    provider_modes: dict[str, str] = Field(default_factory=dict)
    status: str = "created"
    current_step: str = "problem_understanding"
    automatic: bool = False
    stop_requested: bool = False
    feedback_iteration: int = 0
    force_new_attempt_experiment_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
    steps: list[StepRecord] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    events: list[EventRecord] = Field(default_factory=list)
    paper_writing: dict[str, Any] = Field(default_factory=dict)
    # Parsed current scientific facts.  Artifacts remain append-only source of
    # truth; this compact index is intentionally optional for legacy runs.
    scientific_world_state: dict[str, Any] = Field(default_factory=dict)
