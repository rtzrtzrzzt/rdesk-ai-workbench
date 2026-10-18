"""Small, paged chat activity; private parameters are fetched only on expansion."""

import json
from datetime import datetime

from pydantic import Field
from sqlalchemy import or_, select, tuple_

from workbench.contracts.base import PublicResponse
from workbench.contracts.errors import AppError
from workbench.modules.conversation_service import get_thread, history_rows
from workbench.modules.runs.domain import Run
from workbench.modules.tools.domain import Operation

TITLES = {
    "discover_tools": "准备工具",
    "web_search": "搜索网页",
    "read_webpage": "阅读网页",
    "list_files": "浏览文件",
    "read_file": "阅读文件",
    "search_files": "搜索文件",
    "write_file": "写入文件",
    "apply_patch": "修改文件",
    "present_file": "展示文件",
    "list_artifacts": "浏览文件库",
    "read_artifact": "阅读附件",
    "export_file": "生成文件",
    "file_task_status": "检查文件处理",
    "spawn_agent": "启动子任务",
    "inspect_agents": "查看子任务进度",
    "wait_agents": "等待子任务",
    "send_agent_message": "补充子任务要求",
    "cancel_agent": "停止子任务",
    "release_agent": "结束子任务协作",
    "read_task_history": "查阅任务记录",
    "ask_user": "等待你的回答",
    "exec_command": "执行命令",
    "start_command": "启动命令",
    "read_command_output": "查看执行结果",
    "stop_command": "停止命令",
    "memory_search": "查找记忆",
    "memory_read": "阅读记忆",
    "memory_propose": "整理记忆",
    "mcp_call": "使用扩展工具",
    "mcp_list_tools": "浏览扩展工具",
    "browser_open": "打开网页",
    "browser_observe": "查看网页",
    "browser_action": "操作网页",
    "browser_download": "下载文件",
    "computer_observe": "查看桌面",
    "computer_action": "操作桌面",
    "computer_open": "连接桌面",
    "automation_stop": "结束电脑操作",
    "present_ui": "准备展示内容",
    "present_a2ui": "准备展示内容",
    "inspect_ui_catalog": "准备展示组件",
    "inspect_automation": "检查电脑连接",
}


class ActivityItem(PublicResponse):
    id: str
    run_id: str
    title: str
    summary: str
    status: str
    effect_status: str
    error: str | None
    created_at: datetime


class ActivityPage(PublicResponse):
    items: list[ActivityItem]
    next_before: str | None


class ActivityDetails(PublicResponse):
    tool_name: str
    arguments: dict
    result: dict | None
    truncated: bool = Field(default=False)


def visible_operations(db, user_id, thread_id):
    thread = get_thread(db, user_id, thread_id)
    # Branch prefixes retain original operation IDs. Never include later work
    # from their source runs just because one prefix message belongs to a run.
    inherited = [
        row.operation_id
        for row in history_rows(db, thread)
        if row.thread_id != thread.id and row.operation_id
    ]
    own_runs = select(Run.id).where(Run.thread_id == thread.id, Run.user_id == user_id)
    return select(Operation).where(or_(Operation.run_id.in_(own_runs), Operation.id.in_(inherited)))


def activity_view(row):
    summary = ""
    keys = {
        "web_search": "query",
        "search_files": "query",
        "read_webpage": "url",
        "browser_open": "url",
        "spawn_agent": "name",
        "present_file": "path",
        "list_files": "path",
        "read_file": "path",
        "write_file": "path",
        "apply_patch": "path",
        "export_file": "filename",
    }
    value = row.arguments.get(keys.get(row.tool_name, ""))
    if isinstance(value, str):
        summary = " ".join(value.split())[:180]
    error = (row.result or {}).get("error")
    superseded = isinstance(error, dict) and error.get("code") == "tool_skipped_for_steering"
    return {
        "id": row.id,
        "run_id": row.run_id,
        "title": TITLES.get(row.tool_name, "使用工具"),
        "summary": summary,
        "status": "superseded" if superseded else row.status,
        "effect_status": row.effect_status,
        "error": error.get("message")
        if not superseded and isinstance(error, dict) and isinstance(error.get("message"), str)
        else None,
        "created_at": row.created_at,
    }


def page(db, user_id, thread_id, before=None, limit=100):
    query = visible_operations(db, user_id, thread_id)
    if before:
        anchor = db.scalar(query.where(Operation.id == before))
        if anchor is None:
            raise AppError("activity_cursor_invalid", "活动记录已变化，请刷新对话。", 409)
        query = query.where(
            tuple_(Operation.created_at, Operation.id) < (anchor.created_at, anchor.id)
        )
    rows = list(
        db.scalars(
            query.order_by(Operation.created_at.desc(), Operation.id.desc()).limit(limit + 1)
        )
    )
    selected = rows[:limit]
    return {
        "items": [activity_view(row) for row in reversed(selected)],
        "next_before": selected[-1].id if len(rows) > limit else None,
    }


def details(db, user_id, thread_id, operation_id):
    row = db.scalar(visible_operations(db, user_id, thread_id).where(Operation.id == operation_id))
    if row is None:
        raise AppError("activity_not_found", "此工具记录不在当前对话中。", 404)
    serialized = json.dumps(row.result, ensure_ascii=False) if row.result else ""
    return {
        "tool_name": row.tool_name,
        "arguments": row.arguments,
        "result": {"preview": serialized[:16000]} if len(serialized) > 16000 else row.result,
        "truncated": len(serialized) > 16000,
    }
