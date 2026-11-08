"""Tool discovery describes prerequisites; schema presence is not readiness."""

import shutil

from sqlalchemy import or_, select


def availability(db, run, name, module):
    def result(status, reason, next_step=""):
        return {
            "available": True
            if status == "ready"
            else False
            if status in {"disabled", "not_configured", "unsupported"}
            else None,
            "availability": status,
            "reason": reason,
            "next_step": next_step,
        }

    if module == "conversations":
        from workbench.modules.conversation_history.settings import scope_for

        scope, _ = scope_for(db, run)
        return result(
            "disabled" if scope == "disabled" else "ready",
            "用户已关闭跨对话读取。"
            if scope == "disabled"
            else f"当前有效读取范围：{scope}；每次调用实时复核。",
            "read_settings:conversation_history；扩大范围须用户在设置→权限与隐私操作。",
        )
    if module == "search":
        from workbench.modules.search.service import get_connection

        row = get_connection(db, run.user_id, run.workspace_id)
        return (
            result("not_configured", "尚未接入搜索连接。", "read_settings:search")
            if row is None
            else result(
                "configured" if row.enabled else "disabled",
                "搜索连接已启用，实际请求时检查网络。" if row.enabled else "搜索连接已停用。",
                "read_settings:search",
            )
        )
    if module == "media" and name.startswith("generate_"):
        from workbench.modules.media.domain import MediaConnection

        capability = {
            "generate_image": "image",
            "generate_video": "video",
            "generate_audio": "audio",
            "generate_3d": "3d",
        }[name]
        row = db.scalar(
            select(MediaConnection)
            .where(
                MediaConnection.user_id == run.user_id,
                MediaConnection.capability == capability,
                or_(
                    MediaConnection.workspace_id.is_(None),
                    MediaConnection.workspace_id == run.workspace_id,
                ),
                MediaConnection.enabled.is_(True),
            )
            .limit(1)
        )
        return result(
            "configured" if row else "not_configured",
            "已有启用连接；实际能力和资源在提交时检查。" if row else "此类型没有启用的生成模型。",
            "inspect_media",
        )
    if module == "automation" and name != "inspect_automation":
        from workbench.modules.automation.settings import enabled

        return result(
            "requires_runtime_check" if enabled(db, run.user_id) else "disabled",
            "实际设备/会话/观察及动作范围须检查。"
            if enabled(db, run.user_id)
            else "用户已关闭电脑操作。",
            "inspect_automation",
        )
    if module == "mcp":
        from workbench.modules.mcp.host import listing

        items = listing(db, run.user_id, run.workspace_id)
        configured = any(
            row["execution_authorized"] and row["configuration"]["enabled"]
            for row in items["connections"]
        )
        return result(
            "requires_runtime_check" if configured else "not_configured",
            "连接目录、版本、有效期与资源能力在调用前复核。"
            if configured
            else "没有已启用并授权的MCP连接。",
            "read_settings:mcp / mcp_list_tools",
        )
    if name == "list_commands":
        return result("ready", "只读核对持久命令占位与有效并发配置。")
    if module == "processes" or name == "exec_command":
        from workbench.modules.processes.native import capability

        state = capability()
        return result("ready" if state["supported"] else "unsupported", state["reason"])
    if name == "search_files" and not shutil.which("rg"):
        return result("not_configured", "本机缺少ripgrep。", "准备系统rg组件")
    if name == "code_intelligence":
        return result(
            "requires_runtime_check",
            "按语言、目标文件和受信服务器配置确定能力。",
            "code_intelligence operation=capabilities",
        )
    if name in {"process_file", "export_file"}:
        return result(
            "requires_runtime_check",
            "按文件格式和所选操作检查真实组件。",
            "inspect_file_processors",
        )
    return result("ready", "内置接口可用；具体对象、权限与参数在执行时校验。")
