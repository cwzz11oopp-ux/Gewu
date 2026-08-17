from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AccessLevel(StrEnum):
    METADATA_ONLY = "metadata_only"
    ABSTRACT_ONLY = "abstract_only"
    FULL_TEXT = "full_text"


class PaperCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    abstract: str = ""
    problem: str = ""
    method: str = ""
    datasets: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    main_results: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    future_work: list[str] = Field(default_factory=list)
    access_level: AccessLevel
    source: str = Field(min_length=1)
    url: str = ""
    identifiers: dict[str, str] = Field(default_factory=dict)
    verified: bool = False
    provenance: dict[str, Any] = Field(default_factory=dict)
