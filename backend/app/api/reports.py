from io import BytesIO
from typing import Literal
from zipfile import ZipFile

from fastapi import APIRouter, HTTPException, Response

from backend.app.errors import not_found
from backend.app.reporting import attachment_headers, build_experiment_package, build_report_docx, build_report_zip


def build_router(deps) -> APIRouter:
    router = APIRouter(prefix="/api/runs")

    def report_context(run_id: str):
        try:
            run = deps.repository.get_run(run_id)
        except KeyError:
            raise not_found("run", run_id)
        report_artifacts = [artifact for artifact in run.artifacts if artifact.type == "report"]
        if not report_artifacts:
            raise HTTPException(
                status_code=404,
                detail={"code": "REPORT_NOT_FOUND", "message": "尚未生成报告。"},
            )
        return run, report_artifacts[-1].content

    @router.get("/{run_id}/report")
    def get_report(run_id: str):
        _, report = report_context(run_id)
        return report

    @router.get("/{run_id}/report/download")
    def download_report(run_id: str, format: Literal["zip", "docx"] = "zip"):
        run, report = report_context(run_id)
        if format == "docx":
            document = build_report_docx(report, run_id=run_id, run_title=run.title)
            return Response(
                content=document,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers=attachment_headers(f"{run_id}_科学假设与研究报告.docx"),
            )
        package = build_report_zip(
            report,
            run_id=run_id,
            run_title=run.title,
            artifacts=run.artifacts,
        )
        return Response(
            content=package,
            media_type="application/zip",
            headers=attachment_headers(f"{run_id}_研究产物.zip"),
        )

    @router.get("/{run_id}/experiment-package/download")
    def download_experiment_package(run_id: str):
        try:
            run = deps.repository.get_run(run_id)
        except KeyError:
            raise not_found("run", run_id)
        bundles = [artifact for artifact in run.artifacts if artifact.type == "experiment_bundle"]
        results = [artifact for artifact in run.artifacts if artifact.type == "experiment_result"]
        if not bundles and not results:
            raise HTTPException(status_code=404, detail={"code": "EXPERIMENT_PACKAGE_NOT_FOUND", "message": "当前研究尚无真实实验文件"})
        package = build_experiment_package(run_id=run_id, artifacts=run.artifacts)
        return Response(content=package, media_type="application/zip", headers=attachment_headers(f"{run_id}_实验代码包.zip"))

    return router
