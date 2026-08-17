import hashlib
import io
from pathlib import Path

import pytest

from backend.app.models.literature import LocalDocument
from backend.app.models.provider import EvidenceCard
from backend.app.storage.literature import LiteratureError, LiteratureLibrary


def test_local_document_is_not_verified_by_upload_alone():
    document = LocalDocument(
        id="paper_ab12",
        filename="paper.pdf",
        media_type="application/pdf",
        sha256="a" * 64,
        size_bytes=100,
        title="Local paper",
    )

    assert document.statuses == ["uploaded"]
    assert document.verification.verified is False


def test_local_evidence_requires_external_verification_to_export():
    card = EvidenceCard(
        title="Local paper",
        authors=[],
        year=2026,
        source="local_upload",
        source_kind="local",
        local_document_id="paper_ab12",
        claim="",
        url="",
    )

    assert card.exportable is False


def test_verified_local_evidence_with_identifier_is_exportable():
    card = EvidenceCard(
        title="Local paper",
        authors=[],
        year=2026,
        source="local_upload",
        source_kind="local",
        local_document_id="paper_ab12",
        claim="",
        url="",
        identifiers={"doi": "10.1000/example"},
        verified=True,
    )

    assert card.exportable is True


def test_verified_evidence_with_only_unknown_identifier_or_url_is_not_exportable():
    card = EvidenceCard(
        title="Local paper",
        authors=[],
        year=2026,
        source="local_upload",
        source_kind="local",
        local_document_id="paper_ab12",
        claim="",
        url="https://example.com/paper",
        identifiers={"internal": "paper-1"},
        verified=True,
    )

    assert card.exportable is False


def test_upload_text_document_persists_hash_text_and_index(tmp_path):
    library = LiteratureLibrary(tmp_path / "literature", max_upload_bytes=1024)

    document = library.upload(
        io.BytesIO(b"dropout improves robustness"),
        "paper.txt",
        "text/plain",
        {"title": "Dropout Study"},
    )

    assert document.id == f"paper_{hashlib.sha256(b'dropout improves robustness').hexdigest()[:12]}"
    assert document.statuses == ["uploaded", "parsed", "metadata_ready"]
    assert library.get(document.id).sha256 == document.sha256
    assert library.text_path(document.id).read_text(encoding="utf-8") == (
        "dropout improves robustness"
    )
    assert library.file_path(document.id).name == f"{document.id}.txt"


def test_duplicate_upload_returns_existing_document_id(tmp_path):
    library = LiteratureLibrary(tmp_path / "literature")
    first = library.upload(io.BytesIO(b"same"), "one.txt", "text/plain", {})

    with pytest.raises(LiteratureError, match="LITERATURE_DUPLICATE") as exc:
        library.upload(io.BytesIO(b"same"), "two.txt", "text/plain", {})

    assert exc.value.document_id == first.id


def test_upload_rejects_size_and_unsupported_content(tmp_path):
    library = LiteratureLibrary(tmp_path / "literature", max_upload_bytes=4)

    with pytest.raises(LiteratureError, match="LITERATURE_TOO_LARGE"):
        library.upload(io.BytesIO(b"12345"), "large.txt", "text/plain", {})

    library = LiteratureLibrary(tmp_path / "other-literature")
    with pytest.raises(LiteratureError, match="LITERATURE_UNSUPPORTED_TYPE"):
        library.upload(io.BytesIO(b"not an image"), "paper.png", "image/png", {})


def test_markdown_search_is_deterministic_and_title_weighted(tmp_path):
    library = LiteratureLibrary(tmp_path / "literature")
    title_match = library.upload(
        io.BytesIO(b"general notes"),
        "title.md",
        "text/markdown",
        {"title": "Robust Training"},
    )
    body_match = library.upload(
        io.BytesIO(b"robust training details"),
        "body.txt",
        "text/plain",
        {"title": "Notes"},
    )

    assert [item.id for item in library.search("robust training", 10)] == [
        title_match.id,
        body_match.id,
    ]
    assert library.search("", 10) == []


def test_empty_pdf_is_saved_without_fabricated_text(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    payload = io.BytesIO()
    writer.write(payload)
    payload.seek(0)
    library = LiteratureLibrary(tmp_path / "literature")

    document = library.upload(
        payload,
        "blank.pdf",
        "application/pdf",
        {"title": "Blank Scan"},
    )

    assert "text_extraction_empty" in document.statuses
    assert library.text_path(document.id).read_text(encoding="utf-8") == ""


def test_backend_requirements_pin_supported_pypdf_version():
    backend_requirements = Path("backend/requirements.txt").read_text(encoding="utf-8")
    requirements = Path("requirements/literature.txt").read_text(encoding="utf-8")

    assert "-r ../requirements/literature.txt" in backend_requirements.splitlines()
    assert "pypdf==6.14.2" in requirements.splitlines()
