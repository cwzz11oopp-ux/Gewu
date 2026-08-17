from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from backend.app.errors import not_found
from backend.app.reporting import attachment_headers


class PaperStartRequest(BaseModel):
    venue: str = "未指定"
    language: Literal["zh-CN", "en"] = "zh-CN"
    paper_type: str = "实验研究论文"
    authors: str = ""
    notes: str = ""


class PaperFeedbackRequest(BaseModel):
    feedback: str = ""


def build_router(deps) -> APIRouter:
    router = APIRouter(prefix="/api/runs")

    def manager():
        if deps.paper_writing is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "PAPER_WRITING_UNAVAILABLE", "message": "论文写作服务尚未初始化。"},
            )
        return deps.paper_writing

    @router.get("/{run_id}/paper-writing")
    def get_paper_writing(run_id: str):
        try:
            return manager().get(run_id)
        except KeyError:
            raise not_found("run", run_id)

    @router.post("/{run_id}/paper-writing/start")
    def start_paper_writing(run_id: str, body: PaperStartRequest):
        try:
            return manager().start(run_id, body.model_dump())
        except KeyError:
            raise not_found("run", run_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc), "message": str(exc)})

    @router.post("/{run_id}/paper-writing/confirm-plan")
    def confirm_plan(run_id: str, body: PaperFeedbackRequest):
        try:
            return manager().confirm_plan(run_id, body.feedback)
        except KeyError:
            raise not_found("run", run_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc), "message": str(exc)})

    @router.post("/{run_id}/paper-writing/finalize")
    def finalize_paper(run_id: str, body: PaperFeedbackRequest):
        try:
            return manager().finalize(run_id, body.feedback)
        except KeyError:
            raise not_found("run", run_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc), "message": str(exc)})

    @router.post("/{run_id}/paper-writing/stop")
    def stop_paper_writing(run_id: str):
        try:
            return manager().stop(run_id)
        except KeyError:
            raise not_found("run", run_id)

    @router.get("/{run_id}/paper-writing/download")
    def download_paper(run_id: str, format: Literal["docx", "latex"]):
        try:
            if format == "docx":
                body = manager().word_bytes(run_id)
                return Response(
                    content=body,
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers=attachment_headers(f"{run_id}_论文.docx"),
                )
            body = manager().latex_bytes(run_id)
            return Response(
                content=body,
                media_type="application/zip",
                headers=attachment_headers(f"{run_id}_LaTeX源文件.zip"),
            )
        except KeyError:
            raise not_found("run", run_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": str(exc), "message": str(exc)})

    return router
