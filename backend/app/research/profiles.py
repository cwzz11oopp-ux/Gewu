from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.research.protocol import DatasetIdentity, ExperimentProtocol


class BaselineReproductionStatus(StrEnum):
    NOT_STARTED = "not_started"
    ENVIRONMENT_FAILED = "environment_failed"
    RUN_FAILED = "run_failed"
    PROTOCOL_MISMATCH = "protocol_mismatch"
    REPRODUCED_WITHIN_TOLERANCE = "reproduced_within_tolerance"
    REPRODUCED_BUT_REPORTED_MISMATCH = "reproduced_but_reported_mismatch"
    VALIDATED = "validated"


class ProblemProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"problem_{uuid4().hex[:12]}")
    question: str = Field(min_length=1)
    task: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    dataset: DatasetIdentity
    compute_constraints: dict[str, Any] = Field(default_factory=dict)
    research_constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(min_length=1)
    open_questions: list[str] = Field(default_factory=list)


class BaselineProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=1)
    commit: str = Field(min_length=1)
    task: str = Field(min_length=1)
    dataset: DatasetIdentity
    entrypoint: str = Field(min_length=1)
    environment: dict[str, Any]
    protocol: ExperimentProtocol
    reported_metrics: dict[str, float] = Field(default_factory=dict)
    local_metrics: dict[str, float] = Field(default_factory=dict)
    seeds: list[int] = Field(default_factory=list)
    reproduction_status: BaselineReproductionStatus = (
        BaselineReproductionStatus.NOT_STARTED
    )
    validation_reason: str = ""
    audit_passed: bool = False

    @model_validator(mode="after")
    def validated_baseline_has_local_authority(self):
        if self.task != self.protocol.task:
            raise ValueError("BASELINE_TASK_PROTOCOL_MISMATCH")
        if self.dataset != self.protocol.dataset:
            raise ValueError("BASELINE_DATASET_PROTOCOL_MISMATCH")
        if self.reproduction_status == BaselineReproductionStatus.VALIDATED:
            if not self.local_metrics:
                raise ValueError("VALIDATED_BASELINE_LOCAL_METRICS_REQUIRED")
            if not self.seeds:
                raise ValueError("VALIDATED_BASELINE_SEEDS_REQUIRED")
            if not self.audit_passed:
                raise ValueError("VALIDATED_BASELINE_AUDIT_REQUIRED")
        return self

    @property
    def can_be_comparison_denominator(self) -> bool:
        return (
            self.reproduction_status == BaselineReproductionStatus.VALIDATED
            and self.audit_passed
            and bool(self.local_metrics)
        )
