"""Choose safe attachment input and reuse durable file jobs without user route selection."""

from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel
from sqlalchemy import select

from workbench.contracts.requests import AttachmentInput
from workbench.modules.files import dto, inputs, service
from workbench.modules.files.domain import Artifact
from workbench.modules.models.profile import permits
from workbench.modules.runs.domain import ClientRequest, Run


class PreparedAttachment(BaseModel):
    artifact_id: str
    filename: str
    status: Literal["ready", "preparing", "unavailable"]
    remote: bool
    attachment: AttachmentInput | None
    note: str


def _plan(db, user_id, workspace_id, model, artifact):
    return inputs.input_plan(db, user_id, workspace_id, model, [artifact.id])[0]


def _available(plan, mode):
    return any(route["mode"] == mode and route["available"] for route in plan["routes"])


def _operation(source, model):
    if source.mime_type.startswith("image/") and source.mime_type != "image/svg+xml":
        return "preview" if permits(model.capabilities, "image") else None
    if source.mime_type.startswith(("audio/", "video/")):
        return "extract" if permits(model.capabilities, "audio") else None
    detected = source.metadata_json.get("detected_format")
    if (
        source.mime_type.startswith("text/")
        or source.mime_type
        in {"application/json", "application/xml", "image/svg+xml", "application/pdf"}
        or detected in {"docx", "xlsx", "pptx", "zip", "tar", "gz", "epub"}
    ):
        return "extract"
    return None


def _ready(base, source, artifact, mode, note):
    return {
        **base,
        "status": "ready",
        "attachment": {
            "artifact_id": artifact.id,
            "version": artifact.content_hash,
            "mode": mode,
            "source_artifact_id": source.id,
            "source_version": source.content_hash,
            "consent_remote": False,
        },
        "note": note,
    }


def _choose(source, artifact, plan):
    for mode in ("native_document", "native_audio", "native_image", "extracted_text", "text"):
        if not _available(plan, mode):
            continue
        maximum = inputs.NATIVE_LIMITS.get(
            mode, 5 * 1024**2 if mode == "native_image" else 8 * 1024**2
        )
        if artifact.byte_size > maximum:
            continue
        # The bounded worker normalizes the encoding of uploaded originals.
        if mode == "text" and artifact.id == source.id and artifact.kind == "original":
            continue
        return mode
    return None


def _label(artifact, mode):
    if mode == "extracted_text" and not artifact.metadata_json.get("complete", True):
        return "已准备部分内容，原文件完整保留"
    return {
        "native_image": "图像已准备好",
        "native_document": "文档已准备好",
        "native_audio": "音频已准备好",
        "extracted_text": "文件内容已准备好",
        "text": "文本已准备好",
    }[mode]


def prepare(db, user_id, workspace_id, model, identifiers):
    inputs.lock_attachments(
        db, user_id, workspace_id, [{"artifact_id": item} for item in identifiers]
    )
    results, images = [], 0
    for identifier in identifiers:
        source = service.get_artifact(db, user_id, workspace_id, identifier)
        base = {
            "artifact_id": source.id,
            "filename": source.display_name,
            "remote": inputs.is_remote(model.endpoint),
        }
        if source.status != "available":
            results.append(
                {
                    **base,
                    "status": "unavailable",
                    "attachment": None,
                    "note": "文件尚未上传完整，请重试上传。",
                }
            )
            continue
        source_plan = _plan(db, user_id, workspace_id, model, source)
        if not _available(source_plan, "file"):
            results.append(
                {
                    **base,
                    "status": "unavailable",
                    "attachment": None,
                    "note": "此截图仅允许发送给本地模型。",
                }
            )
            continue

        image = (
            source.mime_type.startswith("image/")
            and source.mime_type != "image/svg+xml"
            and permits(model.capabilities, "image")
        )
        if image and images >= 2:
            results.append(
                _ready(
                    base,
                    source,
                    source,
                    "file",
                    "每条消息自动准备前两张图像；此文件已作为原文件添加。",
                )
            )
            continue
        images += image
        selected = _choose(source, source, source_plan)
        if selected:
            results.append(_ready(base, source, source, selected, _label(source, selected)))
            continue
        if source.byte_size > 64 * 1024**2:
            results.append(
                _ready(base, source, source, "file", "文件较大，已添加原件；可在文件页按范围处理。")
            )
            continue
        operation = _operation(source, model)
        if operation:
            children = list(
                db.scalars(
                    select(Artifact)
                    .where(
                        Artifact.parent_id == source.id,
                        Artifact.user_id == user_id,
                        Artifact.workspace_id == workspace_id,
                        Artifact.status == "available",
                        Artifact.kind == ("preview" if operation == "preview" else "extracted"),
                    )
                    .order_by(Artifact.created_at.desc())
                    .limit(20)
                )
            )
            for child in children:
                # Audio metadata is a preflight proof, never a transcript.
                if source.mime_type.startswith(("audio/", "video/")):
                    break
                selected = _choose(source, child, _plan(db, user_id, workspace_id, model, child))
                if selected:
                    break
            if selected:
                results.append(_ready(base, source, child, selected, _label(child, selected)))
                continue
            request_id = str(
                uuid5(UUID(source.id), f"attachment:v1:{source.content_hash}:{operation}")
            )
            previous = db.scalar(
                select(ClientRequest).where(
                    ClientRequest.user_id == user_id, ClientRequest.request_id == request_id
                )
            )
            task = db.get(Run, previous.resource_id) if previous and previous.resource_id else None
            if task is None and previous is None:
                receipt = service.create_processing(
                    db,
                    user_id,
                    workspace_id,
                    source.id,
                    dto.ProcessFile(request_id=request_id, operation=operation, limit=200),
                )
                task = db.get(Run, receipt["id"])
            if task and task.status in {"queued", "running", "retry_wait", "waiting_resources"}:
                results.append(
                    {
                        **base,
                        "status": "preparing",
                        "attachment": None,
                        "note": "正在准备图像…" if operation == "preview" else "正在读取文件内容…",
                    }
                )
                continue
            note = (task.error or {}).get("message") if task else None
            results.append(
                _ready(
                    base,
                    source,
                    source,
                    "file",
                    f"原文件已添加；{note or '未能自动准备内容，可在文件库查看处理详情。'}",
                )
            )
            continue
        note = "原文件已添加；此格式不会直接读入模型内容。"
        if source.mime_type.startswith("image/"):
            note = "原文件已添加；当前模型未启用图像理解。"
        elif source.mime_type.startswith(("audio/", "video/")):
            note = "原文件已添加；当前模型不能直接理解此音视频，可在文件页转录或提取。"
        elif source.byte_size == 0:
            note = "空文件已添加。"
        results.append(_ready(base, source, source, "file", note))
    return results
