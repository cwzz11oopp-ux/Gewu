from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.models.experiment import ExperimentBundle
from backend.app.research.protocol import ExperimentProtocol


class ExperimentContract(BaseModel):
    """V2 execution request that can be fulfilled by a legacy runtime adapter."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    branch_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    repository: str = Field(min_length=1)
    base_commit: str | None = None
    code_commit: str | None = None
    protocol: ExperimentProtocol
    baseline_protocol: ExperimentProtocol
    baseline_metrics: dict[str, float]
    config: dict[str, Any]
    bundle: ExperimentBundle

    @model_validator(mode="after")
    def bundle_matches_protocol(self):
        manifest = self.bundle.manifest
        protocol_metrics = {item.name for item in self.protocol.metrics}
        if set(manifest.expected_metrics) != protocol_metrics:
            raise ValueError("CONTRACT_BUNDLE_METRICS_PROTOCOL_MISMATCH")
        if not set(manifest.seeds).issubset(set(self.protocol.seed_policy.seeds)):
            raise ValueError("CONTRACT_BUNDLE_SEEDS_PROTOCOL_MISMATCH")
        if manifest.dataset and manifest.dataset != self.protocol.dataset.name:
            raise ValueError("CONTRACT_BUNDLE_DATASET_PROTOCOL_MISMATCH")
        if set(self.baseline_metrics) != protocol_metrics:
            raise ValueError("CONTRACT_BASELINE_METRICS_PROTOCOL_MISMATCH")
        return self
