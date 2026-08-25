from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from backend.app.models.artifact import utc_now
from backend.app.models.literature import DocumentVerification
from backend.app.storage.literature import LiteratureError
from backend.app.storage.research_wiki import WikiChangeSet


def build_router(deps) -> APIRouter:
    router = APIRouter()

    @router.post("/api/literature/documents")
    def upload_document(
        file: UploadFile = File(...),
        title: str = Form(""),
        authors: str = Form(""),
        year: str = Form(""),
        abstract: str = Form(""),
        doi: str = Form(""),
        arxiv: str = Form(""),
        knowledge_base_id: str = Form("default"),
    ):
        try:
            return deps.literature_library.upload(
                file.file,
                file.filename or "upload",
                file.content_type or "application/octet-stream",
                {
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "abstract": abstract,
                    "doi": doi,
                    "arxiv": arxiv,
                },
                knowledge_base_id=knowledge_base_id,
            )
        except LiteratureError as exc:
            raise _literature_error(exc)

    @router.get("/api/literature/documents")
    def list_documents(knowledge_base_id: str | None = Query(default=None)):
        return deps.literature_library.list_documents(knowledge_base_id)

    @router.get("/api/literature/search")
    def search_online_literature(
        query: str = Query(min_length=1),
        limit: int = Query(default=8, ge=1, le=20),
    ):
        try:
            cards = deps.literature_provider.search(query.strip(), limit)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "LITERATURE_SEARCH_FAILED", "message": str(exc)},
            ) from exc
        return {
            "query": query.strip(),
            "provider": deps.literature_provider.provider_name,
            "results": [card.model_dump(mode="json") for card in cards],
        }

    @router.get("/api/literature/documents/{paper_id}")
    def get_document(paper_id: str):
        try:
            return deps.literature_library.get(paper_id)
        except LiteratureError as exc:
            raise _literature_error(exc)

    @router.get("/api/literature/documents/{paper_id}/file")
    def download_document(paper_id: str):
        try:
            document = deps.literature_library.get(paper_id)
            path = deps.literature_library.file_path(paper_id)
        except LiteratureError as exc:
            raise _literature_error(exc)
        return FileResponse(path, media_type=document.media_type, filename=document.filename)

    @router.get("/api/research-wiki/stats")
    def research_wiki_stats(
        knowledge_base_id: str = Query(default="default", min_length=1, max_length=100),
    ):
        return {
            "knowledge_base_id": knowledge_base_id,
            **deps.research_wiki.stats(knowledge_base_id),
        }

    @router.get("/api/research-wiki/knowledge-bases")
    def list_research_knowledge_bases():
        """Return named Wiki scopes that can be safely selected for a new Run.

        Wiki data for non-default scopes is stored under a hash, so its directory
        name cannot recover the original label.  Runs and local literature retain
        that label and together form the durable index of selectable scopes.
        """
        scopes = {"default"}
        for document in deps.literature_library.list_documents():
            scopes.update(item.strip() for item in document.knowledge_base_ids if item.strip())
            scopes.update(item.strip() for item in document.wiki_knowledge_base_ids if item.strip())
        for run in deps.repository.list_runs():
            if run.knowledge_base_id.strip():
                scopes.add(run.knowledge_base_id.strip())
        return [
            {
                "knowledge_base_id": scope,
                **deps.research_wiki.stats(scope),
            }
            for scope in sorted(scopes, key=lambda item: (item != "default", item.casefold()))
        ]

    @router.delete("/api/literature/documents/{paper_id}")
    def delete_document(paper_id: str):
        try:
            deps.literature_library.delete(paper_id)
        except LiteratureError as exc:
            raise _literature_error(exc)
        return {"deleted": True, "document_id": paper_id}

    @router.post("/api/literature/documents/{paper_id}/verify")
    def verify_document(paper_id: str):
        try:
            document = deps.literature_library.get(paper_id)
        except LiteratureError as exc:
            raise _literature_error(exc)
        if not document.identifiers.get("doi") and not document.identifiers.get("arxiv"):
            raise HTTPException(
                status_code=422,
                detail={"code": "LITERATURE_REFERENCE_UNVERIFIED"},
            )
        try:
            card = deps.literature_provider.verify_identifier(document.identifiers)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={"code": "LITERATURE_VERIFICATION_FAILED", "message": str(exc)},
            )
        if card is None or not card.verified:
            raise HTTPException(
                status_code=422,
                detail={"code": "LITERATURE_REFERENCE_UNVERIFIED"},
            )
        updated = document.model_copy(
            update={
                "title": card.title or document.title,
                "authors": card.authors or document.authors,
                "year": card.year or document.year,
                "abstract": card.abstract or document.abstract,
                "identifiers": {
                    key: value
                    for key, value in card.identifiers.items()
                    if key in {"doi", "arxiv"} and value
                },
                "verification": DocumentVerification(
                    verified=True,
                    provider=deps.literature_provider.provider_name,
                    verified_at=utc_now(),
                ),
            }
        )
        saved = deps.literature_library.save(updated)
        for run_id in saved.run_ids:
            try:
                deps.repository.attach_local_document(run_id, saved)
            except KeyError:
                continue
        if saved.wiki_node_id:
            for knowledge_base_id in saved.wiki_knowledge_base_ids or ["default"]:
                deps.engine.supervisor_agent.commit_wiki_changes(
                    _wiki_change_for_document(saved, knowledge_base_id),
                    deps.research_wiki,
                )
        return saved

    @router.post("/api/runs/{run_id}/literature/{paper_id}/attach")
    def attach_document(run_id: str, paper_id: str):
        try:
            document = deps.literature_library.get(paper_id)
            run = deps.repository.get_run(run_id)
        except LiteratureError as exc:
            raise _literature_error(exc)
        except KeyError:
            raise HTTPException(status_code=404, detail={"code": "RUN_NOT_FOUND"})
        run_ids = list(dict.fromkeys([*document.run_ids, run_id]))
        knowledge_base_ids = list(
            dict.fromkeys([*document.knowledge_base_ids, run.knowledge_base_id])
        )
        document = deps.literature_library.save(
            document.model_copy(
                update={
                    "run_ids": run_ids,
                    "knowledge_base_ids": knowledge_base_ids,
                }
            )
        )
        artifact = deps.repository.attach_local_document(run_id, document)
        return artifact

    @router.post("/api/literature/documents/{paper_id}/wiki")
    def add_document_to_wiki(
        paper_id: str,
        knowledge_base_id: str = Query(default="default", min_length=1, max_length=100),
    ):
        try:
            document = deps.literature_library.get(paper_id)
        except LiteratureError as exc:
            raise _literature_error(exc)
        changes = _wiki_change_for_document(document, knowledge_base_id)
        result = deps.engine.supervisor_agent.commit_wiki_changes(changes, deps.research_wiki)
        node_id = result.node_ids[0]
        scopes = list(dict.fromkeys([*document.knowledge_base_ids, knowledge_base_id]))
        wiki_scopes = list(
            dict.fromkeys([*document.wiki_knowledge_base_ids, knowledge_base_id])
        )
        deps.literature_library.save(
            document.model_copy(
                update={
                    "wiki_node_id": node_id,
                    "knowledge_base_ids": scopes,
                    "wiki_knowledge_base_ids": wiki_scopes,
                }
            )
        )
        return result

    return router


def _literature_error(exc: LiteratureError) -> HTTPException:
    if exc.code == "LITERATURE_DUPLICATE":
        return HTTPException(
            status_code=409,
            detail={"code": exc.code, "document_id": exc.document_id},
        )
    status = 404 if exc.code == "LITERATURE_NOT_FOUND" else 422
    return HTTPException(status_code=status, detail={"code": exc.code})


def _wiki_change_for_document(
    document,
    knowledge_base_id: str = "default",
) -> WikiChangeSet:
    return WikiChangeSet(
        papers=[
            {
                "title": document.title or document.filename,
                "abstract": document.abstract,
                "authors": document.authors,
                "year": document.year,
                "identifiers": document.identifiers,
                "verified": document.verification.verified,
                "local_document_id": document.id,
                "source": "local_upload",
            }
        ],
        origin_run_id=document.run_ids[-1] if document.run_ids else "local_upload",
        knowledge_base_id=knowledge_base_id,
    )
