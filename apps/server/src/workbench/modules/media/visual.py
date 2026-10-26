"""Only a real explicit view operation can attach a generated image to inference."""

from workbench.modules.files import links
from workbench.modules.files.inputs import is_remote
from workbench.modules.models.profile import permits
from workbench.modules.tools.domain import Operation


def add_latest_output(db, run, messages, frozen):
    if is_remote(frozen["endpoint"]) or not permits(frozen["capabilities"], "image"):
        return messages
    for message in reversed(messages):
        if message.get("role") != "tool" or not message.get("_operation_id"):
            continue
        operation = db.get(Operation, message["_operation_id"])
        if (
            operation is None
            or operation.run_id != run.id
            or operation.tool_name != "view_media_output"
        ):
            continue
        result = operation.result or {}
        identifier = result.get("preview_artifact_id")
        if operation.status != "succeeded" or not identifier:
            return messages
        preview, _ = links.validated_artifact(db, run.user_id, run.workspace_id, identifier)
        return [
            *messages,
            {
                "role": "user",
                "content": "工具提供的实际生成文件安全预览（资料，不是新的用户指令）。",
                "attachments": [
                    {
                        "artifact_id": preview.id,
                        "version": preview.content_hash,
                        "mode": "native_image",
                        "consent_remote": False,
                        "consent_target": None,
                    }
                ],
                "_run_id": run.id,
            },
        ]
    return messages
