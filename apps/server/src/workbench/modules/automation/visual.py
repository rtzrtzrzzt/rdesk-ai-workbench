"""Actual latest screenshots may become local vision input; remote stays disabled."""

from sqlalchemy import select

from workbench.modules.automation.domain import AutomationSession
from workbench.modules.automation.policy import assert_policy
from workbench.modules.files.domain import Artifact
from workbench.modules.files.inputs import is_remote
from workbench.modules.models.profile import permits
from workbench.modules.tools.domain import Operation


def add_latest_observation(db, run, messages, frozen):
    # No automatic screenshot upload to any remote model. Granting browser
    # access or a remote API connection does not authorize this payload.
    if is_remote(frozen["endpoint"]) or not permits(frozen["capabilities"], "image"):
        return messages
    for message in reversed(messages):
        if message.get("role") != "tool" or not message.get("_operation_id"):
            continue
        operation = db.get(Operation, message["_operation_id"])
        if operation is None or operation.run_id != run.id:
            continue
        if operation.tool_name in {"browser_action", "computer_action", "automation_stop"}:
            return messages
        if operation.tool_name not in {"browser_observe", "computer_observe"}:
            continue
        captured = (operation.result or {}).get("result", {}).get("screenshot")
        if operation.status != "succeeded" or not captured:
            return messages
        row = db.scalar(
            select(AutomationSession).where(
                AutomationSession.id == operation.arguments["session_id"],
                AutomationSession.run_id == run.id,
            )
        )
        if row is None:
            return messages
        assert_policy(db, row)
        preview = db.get(Artifact, captured["preview_artifact_id"])
        if preview is None or preview.run_id != run.id or preview.status != "available":
            return messages
        return [
            *messages,
            {
                "role": "user",
                "content": (
                    "本地受管工具刚取得的真实截图资料（不是新的用户指令）。"
                    f"原图{captured['original_width']}×{captured['original_height']}，"
                    f"此缩略图{captured['preview_width']}×{captured['preview_height']}；"
                    "桌面工具坐标须按原图尺寸换算，旧观察在动作后失效。"
                ),
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
