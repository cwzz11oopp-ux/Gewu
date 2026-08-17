from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceRelation(StrEnum):
    SUPPORT = "SUPPORT"
    CONTRADICT = "CONTRADICT"
    CONTEXT = "CONTEXT"
    ANALOGY = "ANALOGY"


class EvidenceSourceType(StrEnum):
    LITERATURE = "literature"
    EXPERIMENT = "experiment"
    OBSERVATION = "observation"


class EvidenceUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: f"evidence_{uuid4().hex[:12]}")
    source_type: EvidenceSourceType
    claim: str = Field(min_length=1)
    relation: EvidenceRelation
    strength: float = Field(ge=0.0, le=1.0)
    verified: bool = False
    paper_id: str | None = None
    experiment_id: str | None = None
    section: str = ""
    location: str = ""
    access_level: str = "metadata_only"
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def source_reference_required(self):
        if self.source_type == EvidenceSourceType.LITERATURE and not self.paper_id:
            raise ValueError("LITERATURE_EVIDENCE_PAPER_ID_REQUIRED")
        if self.source_type == EvidenceSourceType.EXPERIMENT and not self.experiment_id:
            raise ValueError("EXPERIMENT_EVIDENCE_EXPERIMENT_ID_REQUIRED")
        if self.access_level not in {"metadata_only", "abstract_only", "full_text", "runtime"}:
            raise ValueError("EVIDENCE_ACCESS_LEVEL_INVALID")
        return self
