from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentVerification(BaseModel):
    verified: bool = False
    provider: str = ""
    verified_at: str | None = None


class LocalDocument(BaseModel):
    id: str
    filename: str
    media_type: str
    sha256: str
    size_bytes: int
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str = ""
    identifiers: dict[str, str] = Field(default_factory=dict)
    source: str = "local_upload"
    statuses: list[str] = Field(default_factory=lambda: ["uploaded"])
    verification: DocumentVerification = Field(default_factory=DocumentVerification)
    wiki_node_id: str | None = None
    wiki_knowledge_base_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    knowledge_base_ids: list[str] = Field(default_factory=lambda: ["default"])


class LiteratureQueryResult(BaseModel):
    documents: list[LocalDocument] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
