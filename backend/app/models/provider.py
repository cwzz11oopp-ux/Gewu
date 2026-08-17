from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EvidenceCard(BaseModel):
    title: str
    authors: list[str]
    year: int | None
    source: str
    source_kind: Literal["external", "local", "wiki"] = "external"
    local_document_id: str | None = None
    claim: str
    url: str
    identifiers: dict[str, str] = Field(default_factory=dict)
    verified: bool = False
    relevance: float = 0.0
    reliability: float = 0.0
    conflict_notes: list[str] = Field(default_factory=list)
    retrieval_intent: str = "RELATED_APPLICATION"
    target_gap: str = ""

    @property
    def exportable(self) -> bool:
        return self.verified and any(
            bool(str(self.identifiers.get(key) or "").strip())
            for key in ("doi", "arxiv")
        )


class ProviderError(BaseModel):
    code: str
    message: str
    recoverable: bool
    suggested_action: str
