import asyncio
import json
import re
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from workbench.contracts.errors import AppError
from workbench.contracts.requests import CancelRun, EmptyCommand
from workbench.contracts.responses import ERROR_RESPONSES
from workbench.entrypoints.dependencies import DB, USER
from workbench.modules.files import dto, exports, service
from workbench.modules.files.exports_dto import ExportCreate
from workbench.modules.files.processors import capabilities
from workbench.modules.files.storage import storage_path
from workbench.modules.runs import service as run_service

router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}", responses=ERROR_RESPONSES)


@router.get("/artifacts", response_model=list[dto.ArtifactView])
def artifacts(workspace_id: UUID, db: DB, user: USER):
    return service.list_artifacts(db, user.id, str(workspace_id))


@router.post("/uploads", response_model=dto.UploadStatus, status_code=201)
def upload(workspace_id: UUID, command: dto.UploadCreate, db: DB, user: USER):
    result = service.create_upload(db, user.id, str(workspace_id), command)
    db.commit()
    return result


@router.get("/uploads/{upload_id}", response_model=dto.UploadStatus)
def upload_status(workspace_id: UUID, upload_id: UUID, db: DB, user: USER):
    return service.upload_view(service.get_upload(db, user.id, str(workspace_id), str(upload_id)))


@router.post("/uploads/{upload_id}/chunks", response_model=dto.UploadStatus)
async def chunk(
    workspace_id: UUID,
    upload_id: UUID,
    request: Request,
    db: DB,
    user: USER,
    offset: int = Query(ge=0),
):
    user_id = user.id
    service.get_upload(db, user_id, str(workspace_id), str(upload_id))
    db.rollback()
    content = bytearray()
    async for part in request.stream():
        if len(content) + len(part) > service.CHUNK_SIZE:
            raise AppError("upload_chunk_size", "上传块超过4 MiB限制。", 413, "resource")
        content.extend(part)
    return await asyncio.to_thread(
        service.accept_chunk,
        user_id,
        str(workspace_id),
        str(upload_id),
        offset,
        bytes(content),
    )


@router.post("/uploads/{upload_id}/complete", response_model=dto.ArtifactView)
async def complete(workspace_id: UUID, upload_id: UUID, command: EmptyCommand, db: DB, user: USER):
    user_id = user.id
    db.rollback()
    return await asyncio.to_thread(
        service.complete_upload, user_id, str(workspace_id), str(upload_id)
    )


@router.get("/artifacts/{artifact_id}", response_model=dto.ArtifactView)
def artifact(workspace_id: UUID, artifact_id: UUID, db: DB, user: USER):
    return service.artifact_view(
        service.get_artifact(db, user.id, str(workspace_id), str(artifact_id))
    )


@router.get("/artifacts/{artifact_id}/content", response_class=FileResponse)
def content(workspace_id: UUID, artifact_id: UUID, db: DB, user: USER, download: bool = True):
    row = service.get_artifact(db, user.id, str(workspace_id), str(artifact_id))
    path = storage_path(row.storage_path)
    if row.status != "available" or not path.is_file():
        raise AppError("artifact_unavailable", "此文件原件暂不可读取，请核对存储状态。", 409)
    safe_image = row.kind == "preview" or re.fullmatch(r"frame_[0-9]{4}", row.kind)
    inline = not download and safe_image and row.mime_type == "image/png"
    return FileResponse(
        path,
        media_type="image/png" if inline else "application/octet-stream",
        filename=row.display_name,
        content_disposition_type="inline" if inline else "attachment",
        headers={"Content-Security-Policy": "default-src 'none'; sandbox"},
    )


@router.get("/artifacts/{artifact_id}/preview", response_model=dto.FilePreview)
def preview(workspace_id: UUID, artifact_id: UUID, db: DB, user: USER):
    row = service.get_artifact(db, user.id, str(workspace_id), str(artifact_id))
    path = storage_path(row.storage_path)
    if row.status != "available" or not path.is_file():
        raise AppError("artifact_unavailable", "文件尚不可预览，请先确认原件已保存。", 409)
    text, truncated = None, False
    if row.kind == "extracted" and row.mime_type == "application/json":
        if path.stat().st_size > 8 * 1024 * 1024:
            raise AppError("preview_budget", "此预览过大，请下载后查看。", 422)
        parsed = json.loads(path.read_text(encoding="utf-8"))
        sections = []
        for segment in parsed.get("segments", []):
            sections.append(
                f"[{json.dumps(segment['locator'], ensure_ascii=False)}]\n{segment['text']}"
            )
        text = "\n\n".join(sections)
    elif row.mime_type.startswith("text/") or row.mime_type in {
        "application/json",
        "application/xml",
    }:
        with path.open("rb") as stream:
            data = stream.read(128_001)
        truncated = len(data) > 128_000
        text = data[:128_000].decode("utf-8-sig", errors="replace")
    if text and len(text) > 128_000:
        text, truncated = text[:128_000], True
    return {
        "artifact_id": row.id,
        "text": text,
        "metadata": row.metadata_json,
        "truncated": truncated,
    }


@router.post("/artifacts/{artifact_id}/process", response_model=dto.FileTask, status_code=201)
def process(workspace_id: UUID, artifact_id: UUID, command: dto.ProcessFile, db: DB, user: USER):
    result = service.create_processing(db, user.id, str(workspace_id), str(artifact_id), command)
    db.commit()
    return result


def _task(db, user_id, workspace_id, run_id):
    run = run_service.get(db, user_id, run_id)
    if run.workspace_id != workspace_id or run.kind != "file":
        raise AppError("file_task_not_found", "文件处理任务不存在或不可访问。", 404)
    return run


@router.get("/file-tasks", response_model=list[dto.FileTask])
def tasks(workspace_id: UUID, db: DB, user: USER):
    return service.list_tasks(db, user.id, str(workspace_id))


@router.get("/file-tasks/{run_id}", response_model=dto.FileTask)
def task(workspace_id: UUID, run_id: UUID, db: DB, user: USER):
    return service.task_view(db, _task(db, user.id, str(workspace_id), str(run_id)))


@router.post("/file-tasks/{run_id}/cancel", response_model=dto.FileTask)
def cancel(workspace_id: UUID, run_id: UUID, command: CancelRun, db: DB, user: USER):
    run = _task(db, user.id, str(workspace_id), str(run_id))
    run_service.cancel(db, user.id, run.id, str(command.request_id))
    db.commit()
    return service.task_view(db, run)


@router.get("/file-processors", response_model=list[dto.ProcessorCapability])
def processors(workspace_id: UUID, db: DB, user: USER):
    from workbench.modules.workspace_service import get

    get(db, user.id, str(workspace_id))
    return capabilities()


@router.post("/exports", response_model=dto.FileTask, status_code=201)
def export_file(workspace_id: UUID, command: ExportCreate, db: DB, user: USER):
    return exports.create_export(db, user.id, str(workspace_id), command)


@router.get("/export-capabilities", response_model=list[dto.ExportCapability])
def export_capabilities(workspace_id: UUID, db: DB, user: USER):
    from workbench.modules.workspace_service import get

    get(db, user.id, str(workspace_id))
    return exports.capabilities()
