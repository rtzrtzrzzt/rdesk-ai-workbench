"""Stable agent capabilities over the same connections/jobs used by the UI."""

import asyncio

from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.collaboration.service import lock_root
from workbench.modules.files import links
from workbench.modules.media import dto, service
from workbench.modules.runs.domain import Run
from workbench.modules.runs.ownership import current_owner


def invoke(run_id, operation_id, name, values):
    with Session(engine()) as db, db.begin():
        _, run = lock_root(db, run_id)
        if name == "inspect_media":
            result = service.listing(db, run.user_id, run.workspace_id)
            return {
                "models": [
                    {
                        key: row[key]
                        for key in (
                            "id",
                            "name",
                            "capability",
                            "model_identifier",
                            "enabled",
                            "verification",
                        )
                    }
                    for row in result["connections"]
                ],
                "supported_capabilities": ["image", "video", "audio", "3d"],
                "workflow": (
                    "read_media_guide了解生图提示词；generate_*提交后台任务。"
                    "未接入的类型不能调用，不虚构已生成。"
                ),
            }
        if name == "read_media_guide":
            return service.guide(db, run.user_id, run.workspace_id, dto.MediaGuide(**values))
        if name.startswith("generate_"):
            capability = {
                "generate_image": "image",
                "generate_video": "video",
                "generate_audio": "audio",
                "generate_3d": "3d",
            }[name]
            return service.create(
                db,
                run.user_id,
                run.workspace_id,
                dto.MediaCreate(**values, capability=capability, request_id=operation_id),
                parent_run_id=run_id,
            )
        if name == "view_media_output":
            artifact, _ = links.validated_artifact(
                db, run.user_id, run.workspace_id, values["artifact_id"], values["version"]
            )
            db.refresh(artifact, with_for_update=True)
            state = links._ensure_preview(db, artifact, run, operation_id)
            return {
                "artifact": links.artifact_link_view(db, artifact),
                "markdown_link": links._markdown(
                    artifact.display_name,
                    f"/api/v1/workspaces/{run.workspace_id}/file-links/{artifact.id}?version={artifact.content_hash}",
                ),
                **state,
                "note": (
                    "仅preview_artifact_id就绪且本地模型支持图像时才实际读入视觉。"
                    "远程模型需用户将预览添加为附件授权传输。"
                    "视频/音频/3D元数据不代表看过内容；视频可用文件工具抽帧后查看。"
                ),
            }
        row = service.task(db, run.user_id, run.workspace_id, values["run_id"])
        target = db.get(Run, row.run_id)
        if target.parent_run_id != run.id:
            raise AppError("media_task_scope", "只能管理本任务提交的生成任务。", 403, "policy")
        if name == "cancel_media_task":
            # The parent owns this accepted child, but not its worker lease.
            # Scope was checked above; cancellation uses the ordinary tree API.
            token = current_owner.set(None)
            try:
                return service.cancel_task(
                    db, run.user_id, run.workspace_id, target.id, operation_id
                )
            finally:
                current_owner.reset(token)
        return service.task_view(db, row)


async def execute(run_id, operation_id, name, values):
    return await asyncio.to_thread(invoke, run_id, operation_id, name, values)
