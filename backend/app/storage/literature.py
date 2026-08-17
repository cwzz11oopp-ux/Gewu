from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
from typing import BinaryIO

from backend.app.models.literature import LocalDocument


DEFAULT_MAX_UPLOAD_BYTES = 30 * 1024 * 1024


class LiteratureError(RuntimeError):
    def __init__(self, code: str, document_id: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.document_id = document_id


class LiteratureLibrary:
    def __init__(
        self,
        root: str | Path,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ) -> None:
        self.root = Path(root)
        self.files_dir = self.root / "files"
        self.text_dir = self.root / "text"
        self.index_path = self.root / "index.json"
        self.max_upload_bytes = max_upload_bytes
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.text_dir.mkdir(parents=True, exist_ok=True)
        if not self.index_path.exists():
            self._write_documents({})

    def upload(
        self,
        stream: BinaryIO,
        filename: str,
        media_type: str,
        metadata: dict,
    ) -> LocalDocument:
        payload = self._read_limited(stream)
        kind, extension = _detect_kind(filename, media_type, payload)
        digest = hashlib.sha256(payload).hexdigest()
        document_id = f"paper_{digest[:12]}"
        documents = self._read_documents()
        duplicate = next(
            (item for item in documents.values() if item.sha256 == digest),
            None,
        )
        if duplicate is not None:
            raise LiteratureError("LITERATURE_DUPLICATE", duplicate.id)

        text, extraction_empty = _extract_text(kind, payload)
        statuses = ["uploaded"]
        if extraction_empty:
            statuses.append("text_extraction_empty")
        else:
            statuses.append("parsed")

        title = str(metadata.get("title") or "").strip()
        authors = _authors(metadata.get("authors"))
        year = _year(metadata.get("year"))
        abstract = str(metadata.get("abstract") or "").strip()
        identifiers = {
            key: str(metadata.get(key) or "").strip()
            for key in ("doi", "arxiv")
            if str(metadata.get(key) or "").strip()
        }
        if title or authors or year or abstract or identifiers:
            statuses.append("metadata_ready")

        document = LocalDocument(
            id=document_id,
            filename=Path(filename).name,
            media_type=_media_type_for(kind),
            sha256=digest,
            size_bytes=len(payload),
            title=title,
            authors=authors,
            year=year,
            abstract=abstract,
            identifiers=identifiers,
            statuses=statuses,
        )
        original_path = self.files_dir / f"{document_id}{extension}"
        text_path = self.text_path(document_id)
        original_path.write_bytes(payload)
        text_path.write_text(text, encoding="utf-8")
        documents[document_id] = document
        self._write_documents(documents)
        return document

    def list_documents(self) -> list[LocalDocument]:
        return sorted(self._read_documents().values(), key=lambda item: item.id)

    def get(self, document_id: str) -> LocalDocument:
        try:
            return self._read_documents()[document_id]
        except KeyError as exc:
            raise LiteratureError("LITERATURE_NOT_FOUND", document_id) from exc

    def save(self, document: LocalDocument) -> LocalDocument:
        documents = self._read_documents()
        if document.id not in documents:
            raise LiteratureError("LITERATURE_NOT_FOUND", document.id)
        documents[document.id] = document
        self._write_documents(documents)
        return document

    def delete(self, document_id: str) -> None:
        documents = self._read_documents()
        if document_id not in documents:
            raise LiteratureError("LITERATURE_NOT_FOUND", document_id)
        del documents[document_id]
        for path in self.files_dir.glob(f"{document_id}.*"):
            path.unlink(missing_ok=True)
        self.text_path(document_id).unlink(missing_ok=True)
        self._write_documents(documents)

    def search(self, query: str, limit: int = 10) -> list[LocalDocument]:
        terms = tuple(dict.fromkeys(_tokens(query)))
        if not terms or limit <= 0:
            return []
        scored: list[tuple[int, str, LocalDocument]] = []
        for document in self.list_documents():
            title = document.title.lower()
            abstract = document.abstract.lower()
            text = self.text_path(document.id).read_text(encoding="utf-8").lower()
            score = sum(
                (4 if term in title else 0)
                + (2 if term in abstract else 0)
                + (1 if term in text else 0)
                for term in terms
            )
            if score:
                scored.append((score, document.id, document))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored[:limit]]

    def file_path(self, document_id: str) -> Path:
        matches = sorted(self.files_dir.glob(f"{document_id}.*"))
        if not matches:
            raise LiteratureError("LITERATURE_NOT_FOUND", document_id)
        return matches[0]

    def text_path(self, document_id: str) -> Path:
        return self.text_dir / f"{document_id}.txt"

    def _read_limited(self, stream: BinaryIO) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = stream.read(min(64 * 1024, self.max_upload_bytes + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > self.max_upload_bytes:
                raise LiteratureError("LITERATURE_TOO_LARGE")
            chunks.append(chunk)
        if total == 0:
            raise LiteratureError("LITERATURE_EMPTY")
        return b"".join(chunks)

    def _read_documents(self) -> dict[str, LocalDocument]:
        if not self.index_path.exists():
            return {}
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        values = raw.get("documents", raw)
        return {
            document_id: LocalDocument.model_validate(value)
            for document_id, value in values.items()
        }

    def _write_documents(self, documents: dict[str, LocalDocument]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.index_path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(
                {"documents": {key: value.model_dump() for key, value in documents.items()}},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temp.replace(self.index_path)


def _detect_kind(filename: str, media_type: str, payload: bytes) -> tuple[str, str]:
    extension = Path(filename).suffix.lower()
    if extension == ".pdf" and media_type == "application/pdf" and payload.startswith(b"%PDF-"):
        return "pdf", ".pdf"
    if extension == ".md" and media_type in {"text/markdown", "text/plain"}:
        return "markdown", ".md"
    if extension == ".txt" and media_type == "text/plain":
        return "text", ".txt"
    raise LiteratureError("LITERATURE_UNSUPPORTED_TYPE")


def _extract_text(kind: str, payload: bytes) -> tuple[str, bool]:
    if kind in {"text", "markdown"}:
        if b"\x00" in payload:
            raise LiteratureError("LITERATURE_UNSUPPORTED_TYPE")
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LiteratureError("LITERATURE_TEXT_DECODE_FAILED") from exc
        return text, not bool(text.strip())

    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(payload))
        text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
    except Exception as exc:
        raise LiteratureError("LITERATURE_PDF_INVALID") from exc
    return text, not bool(text)


def _media_type_for(kind: str) -> str:
    return {
        "pdf": "application/pdf",
        "markdown": "text/markdown",
        "text": "text/plain",
    }[kind]


def _authors(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _year(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        year = int(value)
    except (TypeError, ValueError) as exc:
        raise LiteratureError("LITERATURE_YEAR_INVALID") from exc
    if year < 1000 or year > 9999:
        raise LiteratureError("LITERATURE_YEAR_INVALID")
    return year


def _tokens(value: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[\w-]+", value, flags=re.UNICODE)]
