from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.research.protocol import (
    ComparisonDecision,
    ExperimentProtocol,
    ProtocolCompatibilityResult,
    ProtocolFingerprint,
)


class ExperimentResultStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExperimentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(default_factory=lambda: f"v2exp_{uuid4().hex[:12]}")
    branch_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    base_commit: str | None = None
    code_commit: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    diff_summary: str = ""
    protocol: ExperimentProtocol
    protocol_fingerprint: ProtocolFingerprint
    config: dict[str, Any]
    seeds: list[int] = Field(min_length=1)
    metrics: dict[str, float] = Field(default_factory=dict)
    baseline_metrics: dict[str, float] = Field(default_factory=dict)
    comparison: ProtocolCompatibilityResult
    audit_passed: bool
    environment: dict[str, Any]
    logs: list[str] = Field(default_factory=list)
    figures: list[str] = Field(default_factory=list)
    result_status: ExperimentResultStatus
    analysis: str = ""

    @model_validator(mode="after")
    def validate_authority(self):
        if self.protocol_fingerprint != self.protocol.fingerprint():
            raise ValueError("EXPERIMENT_PROTOCOL_FINGERPRINT_MISMATCH")
        if self.comparison.audit_passed != self.audit_passed:
            raise ValueError("EXPERIMENT_AUDIT_STATUS_MISMATCH")
        if self.result_status == ExperimentResultStatus.SUCCEEDED and not self.metrics:
            raise ValueError("SUCCEEDED_EXPERIMENT_METRICS_REQUIRED")
        declared_metrics = {item.name for item in self.protocol.metrics}
        if self.metrics and not set(self.metrics).issubset(declared_metrics):
            raise ValueError("EXPERIMENT_UNDECLARED_METRIC")
        if not set(self.seeds).issubset(set(self.protocol.seed_policy.seeds)):
            raise ValueError("EXPERIMENT_SEED_OUTSIDE_PROTOCOL")
        if (
            self.comparison.decision == ComparisonDecision.ALLOWED
            and (not self.baseline_metrics or not self.audit_passed)
        ):
            raise ValueError("ALLOWED_COMPARISON_REQUIRES_AUDITED_BASELINE_METRICS")
        return self

    @property
    def improvement_claim_allowed(self) -> bool:
        return (
            self.result_status == ExperimentResultStatus.SUCCEEDED
            and self.audit_passed
            and self.comparison.improvement_claim_allowed
            and bool(self.baseline_metrics)
        )
