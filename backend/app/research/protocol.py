from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MetricDirection(StrEnum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class ComparisonDecision(StrEnum):
    ALLOWED = "COMPARISON_ALLOWED"
    NOT_ALLOWED = "COMPARISON_NOT_ALLOWED"


class DatasetIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)


class MetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    direction: MetricDirection
    definition: str = Field(min_length=1)
    aggregation: str = Field(min_length=1)
    unit: str = ""


class TrainingBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    epochs: int | None = Field(default=None, gt=0)
    max_steps: int | None = Field(default=None, gt=0)
    wall_time_seconds: int | None = Field(default=None, gt=0)
    samples: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_one_limit(self):
        if all(value is None for value in self.model_dump().values()):
            raise ValueError("TRAINING_BUDGET_EMPTY")
        return self


class SeedPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seeds: list[int] = Field(min_length=1)
    aggregation: str = Field(min_length=1)
    minimum_repetitions: int = Field(default=1, gt=0)

    @field_validator("seeds")
    @classmethod
    def unique_seeds(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("SEED_POLICY_DUPLICATE_SEED")
        return value

    @model_validator(mode="after")
    def repetitions_fit_seed_set(self):
        if self.minimum_repetitions > len(self.seeds):
            raise ValueError("SEED_POLICY_REPETITIONS_EXCEED_SEEDS")
        return self


class ProtocolFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm: str = "sha256"
    value: str = Field(pattern=r"^[0-9a-f]{64}$")
    canonical_schema_version: int = 1


class ExperimentProtocol(BaseModel):
    """The scientific comparison contract, independent of any runtime provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task: str = Field(min_length=1)
    dataset: DatasetIdentity
    split: dict[str, Any]
    preprocessing: dict[str, Any]
    metrics: list[MetricDefinition] = Field(min_length=1)
    training_budget: TrainingBudget
    evaluation_protocol: dict[str, Any]
    seed_policy: SeedPolicy
    training_controls: dict[str, Any]
    schema_version: int = 1

    @field_validator(
        "split",
        "preprocessing",
        "evaluation_protocol",
        "training_controls",
    )
    @classmethod
    def nonempty_contract_section(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("PROTOCOL_SECTION_EMPTY")
        return value

    @field_validator("metrics")
    @classmethod
    def unique_metric_names(cls, value: list[MetricDefinition]) -> list[MetricDefinition]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("PROTOCOL_DUPLICATE_METRIC")
        return value

    def fingerprint(self) -> ProtocolFingerprint:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return ProtocolFingerprint(
            value=hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        )


class ProtocolCompatibilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    compatible: bool
    decision: ComparisonDecision
    baseline_fingerprint: ProtocolFingerprint
    variant_fingerprint: ProtocolFingerprint
    mismatches: list[str] = Field(default_factory=list)
    audit_passed: bool
    reasons: list[str] = Field(default_factory=list)

    @property
    def improvement_claim_allowed(self) -> bool:
        return self.compatible and self.audit_passed


class ProtocolCompatibilityGate:
    """Strict protocol equality gate for direct baseline/variant claims."""

    _SECTIONS = (
        "task",
        "dataset",
        "split",
        "preprocessing",
        "metrics",
        "training_budget",
        "evaluation_protocol",
        "seed_policy",
        "training_controls",
        "schema_version",
    )

    @classmethod
    def evaluate(
        cls,
        baseline: ExperimentProtocol,
        variant: ExperimentProtocol,
        *,
        audit_passed: bool,
    ) -> ProtocolCompatibilityResult:
        left = baseline.model_dump(mode="json")
        right = variant.model_dump(mode="json")
        mismatches = [name for name in cls._SECTIONS if left[name] != right[name]]
        compatible = not mismatches
        reasons: list[str] = []
        if mismatches:
            reasons.append("Protocol mismatch: " + ", ".join(mismatches))
        if not audit_passed:
            reasons.append("Experiment audit did not pass")
        allowed = compatible and audit_passed
        return ProtocolCompatibilityResult(
            compatible=compatible,
            decision=(
                ComparisonDecision.ALLOWED
                if allowed
                else ComparisonDecision.NOT_ALLOWED
            ),
            baseline_fingerprint=baseline.fingerprint(),
            variant_fingerprint=variant.fingerprint(),
            mismatches=mismatches,
            audit_passed=audit_passed,
            reasons=reasons,
        )
