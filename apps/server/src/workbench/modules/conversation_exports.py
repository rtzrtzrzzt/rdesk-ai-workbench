"""User-requested portable conversation snapshots, without execution credentials."""

import json
from typing import Literal

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import now
from workbench.modules.conversation_service import (
    get_thread,
    history_rows,
    message_view,
    thread_view,
)
from workbench.modules.files.domain import Artifact
from workbench.modules.runs.domain import TERMINAL_STATUSES, Run
from workbench.modules.tools.domain import Operation

MAX_EXPORT_BYTES = 16 * 1024**2


def snapshot(db, user_id, thread_id):
    thread = get_thread(db, user_id, thread_id)
    messages = [
        message_view(row) for row in history_rows(db, thread) if row.role in {"user", "assistant"}
    ]
    runs = list(
        db.scalars(
            select(Run)
            .where(Run.thread_id == thread.id, Run.user_id == user_id)
            .order_by(Run.created_at)
            .limit(1001)
        )
    )
    if len(runs) > 1000:
        raise AppError("conversation_export_budget", "此对话任务过多，请按较短历史分支导出。", 422)
    summaries = []
    for run in runs:
        operations = list(
            db.scalars(
                select(Operation)
                .where(Operation.run_id == run.id)
                .order_by(Operation.created_at)
                .limit(1001)
            )
        )
        artifacts = list(
            db.scalars(
                select(Artifact)
                .where(Artifact.run_id == run.id, Artifact.status == "available")
                .order_by(Artifact.created_at)
                .limit(1001)
            )
        )
        if len(operations) > 1000 or len(artifacts) > 1000:
            raise AppError(
                "conversation_export_budget", "任务记录超过单次导出预算，请拆分导出。", 422
            )
        summaries.append(
            {
                "id": run.id,
                "status": run.status,
                "created_at": run.created_at,
                "root_run_id": run.root_run_id,
                "parent_run_id": run.parent_run_id,
                "error_code": (run.error or {}).get("code"),
                "operations": [
                    {
                        "id": operation.id,
                        "tool_name": operation.tool_name,
                        "status": operation.status,
                        "effect_status": operation.effect_status,
                    }
                    for operation in operations
                ],
                "artifacts": [
                    {
                        "id": artifact.id,
                        "filename": artifact.display_name,
                        "mime_type": artifact.mime_type,
                        "bytes": artifact.byte_size,
                        "sha256": artifact.content_hash,
                    }
                    for artifact in artifacts
                ],
            }
        )
    return jsonable_encoder(
        {
            "schema": "workbench-conversation/1",
            "exported_at": now(),
            "thread": thread_view(thread),
            "messages": messages,
            "tasks": summaries,
            "has_unfinished_tasks": any(run.status not in TERMINAL_STATUSES for run in runs),
            "notes": [
                "导出当前已保存的用户和助手消息；运行中消息可能尚不完整。",
                "历史分支仅包含用户选择的来源前缀；任务状态为导出时读取的状态。",
                "附件与产物仅保留原件引用，不包含文件字节；请在文件页另行下载。",
                "不包含模型密钥、内部提示词、工具参数、原生进程身份或可继续执行的授权。",
            ],
        }
    )


def render(db, user_id, thread_id, format_: Literal["markdown", "json"]):
    data = snapshot(db, user_id, thread_id)
    if format_ == "json":
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode()
        extension, media_type = "json", "application/json"
    elif format_ == "markdown":
        lines = [f"# {data['thread']['title']}", "", f"导出时间：{data['exported_at']}", ""]
        lines.extend(note + "\n" for note in data["notes"])
        for message in data["messages"]:
            lines.extend(
                [
                    f"## {message['role']} · {message['created_at']}",
                    "",
                    f"消息：{message['id']} · 状态：{message['status']}",
                    "",
                    message["content"],
                    "",
                ]
            )
            for attachment in message["attachments"]:
                lines.extend(
                    [
                        f"附件：{attachment['filename']} · {attachment['mode']} · "
                        f"artifact={attachment['artifact_id']} · version={attachment['version']}",
                        "",
                    ]
                )
        lines.extend(
            [
                "## 已保存的任务事实",
                "",
                "```json",
                json.dumps(data["tasks"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
        payload = "\n".join(lines).encode()
        extension, media_type = "md", "text/markdown; charset=utf-8"
    else:
        raise AppError("conversation_export_format", "请选择 Markdown 或 JSON 导出。", 422)
    if len(payload) > MAX_EXPORT_BYTES:
        raise AppError("conversation_export_budget", "导出超过16 MiB，请选择较短历史分支。", 422)
    return payload, f"conversation-{thread_id}.{extension}", media_type
