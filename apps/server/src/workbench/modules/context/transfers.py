"""Historical file text follows its actual receiving model revision."""

import json

from sqlalchemy import select

from workbench.modules.conversations import Message
from workbench.modules.files.domain import Artifact
from workbench.modules.files.inputs import is_remote
from workbench.modules.models.domain import ModelRevision
from workbench.modules.runs.domain import Run
from workbench.modules.tools.domain import Operation


def private_artifacts(db, run_id):
    identifiers = set(
        db.scalars(
            select(Operation.arguments["artifact_id"].astext).where(
                Operation.run_id == run_id,
                Operation.tool_name == "read_artifact",
                Operation.status == "succeeded",
            )
        )
    )
    for references in db.scalars(select(Message.attachments).where(Message.run_id == run_id)):
        identifiers.update(
            item[key]
            for item in references or []
            for key in ("artifact_id", "source_artifact_id")
            if item.get(key)
        )
    identifiers.update(
        db.scalars(
            select(Artifact.id).where(
                Artifact.run_id == run_id,
                Artifact.metadata_json["automation_session_id"].astext.is_not(None),
            )
        )
    )
    return identifiers


def allowed_result(db, target_run, source_run_id):
    revision = db.get(ModelRevision, target_run.config_snapshot.get("model_revision_id"))
    if revision is None or not is_remote(revision.configuration["endpoint"]):
        return True
    source_run = db.get(Run, source_run_id)
    if source_run is None:
        return False
    if source_run.config_snapshot.get("model_revision_id") == revision.id:
        return True
    sources = private_artifacts(db, source_run_id)
    if not sources:
        return True
    target = f"{revision.connection_id}:{revision.connection_version}"
    approved = set()
    for references in db.scalars(
        select(Message.attachments).where(Message.run_id == target_run.id)
    ):
        approved.update(
            item[key]
            for item in references or []
            for key in ("artifact_id", "source_artifact_id")
            if item.get(key)
            if item.get("consent_remote") and item.get("consent_target") == target
        )
    return sources <= approved


def filter_artifact_transfer(db, run, message):
    source = message.get("_run_id")
    if not source and message.get("_operation_id"):
        operation = db.get(Operation, message["_operation_id"])
        source = operation.run_id if operation else None
    if not source and message.get("_message_id"):
        row = db.get(Message, message["_message_id"])
        source = row.run_id if row else None
    if not source or message.get("role") not in {"assistant", "tool"}:
        return message
    if allowed_result(db, run, source):
        return message
    content = json.dumps(
        {
            "error": {
                "code": "historical_file_transfer_withheld",
                "message": "此历史结果涉及尚未授权给当前模型版本的文件，正文已保留在本机。"
                "请用户重新选择文件确认传输，或使用原连接。",
            }
        },
        ensure_ascii=False,
    )
    if message.get("role") == "tool":
        return {**message, "content": content}
    return {"role": "user", "content": content, "source_kind": "withheld_history"}
