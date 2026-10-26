"""Read existing conversations through the user's live scope and bounded excerpts."""

import json
from types import SimpleNamespace

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.conversation_service import history_rows
from workbench.modules.conversations import Thread
from workbench.modules.runs.domain import Run
from workbench.modules.workspaces import Workspace

from . import settings
from .policy import thread_allowed

SCAN_CONVERSATIONS = 50


def source_actor(actor, workspace_id):
    return SimpleNamespace(
        id=actor.id,
        user_id=actor.user_id,
        workspace_id=workspace_id,
        config_snapshot=actor.config_snapshot,
    )


def access(db, actor):
    scope, version = settings.scope_for(db, actor)
    if scope == "disabled":
        raise AppError(
            "conversation_history_disabled",
            "用户已关闭跨对话读取；当前任务恢复可用read_task_history。",
            403,
            "policy",
        )
    return scope, version, settings.task_threads(db, actor) if scope == "task_tree" else []


def authorized_thread(db, actor, identifier):
    scope, _, task_ids = access(db, actor)
    thread = db.scalar(
        select(Thread)
        .join(Workspace, Workspace.id == Thread.workspace_id)
        .where(
            Thread.id == identifier,
            Thread.user_id == actor.user_id,
            Workspace.user_id == actor.user_id,
            Workspace.removed_at.is_(None),
            Workspace.archived.is_(False),
        )
    )
    if thread is None or not thread_allowed(actor, thread, scope, task_ids):
        raise AppError(
            "conversation_scope_denied", "对话不存在、正在删除或超出当前读取范围。", 404, "policy"
        )
    return thread


def thread_query(db, actor, values, *, title_query=True):
    scope, _, task_ids = access(db, actor)
    if values.get("scope", "current") == "all" and scope != "user":
        raise AppError(
            "conversation_scope_denied",
            "当前只允许本工作区或任务树；跨工作区须由用户在设置中开启。",
            403,
            "policy",
        )
    query = (
        select(Thread)
        .join(Workspace, Workspace.id == Thread.workspace_id)
        .where(
            Thread.user_id == actor.user_id,
            Thread.deleting.is_(False),
            Workspace.user_id == actor.user_id,
            Workspace.removed_at.is_(None),
            Workspace.archived.is_(False),
        )
    )
    if scope == "task_tree":
        query = query.where(Thread.id.in_(task_ids))
    elif scope == "workspace" or values.get("scope", "current") == "current":
        query = query.where(Thread.workspace_id == actor.workspace_id)
    mode = values.get("archived", "exclude")
    if mode != "include":
        query = query.where(Thread.archived.is_(mode == "only"))
    if not values.get("include_subagents", False):
        child = (
            select(Run.id)
            .where(Run.thread_id == Thread.id, Run.kind == "agent", Run.parent_run_id.is_not(None))
            .correlate(Thread)
            .exists()
        )
        query = query.where(~child)
    if title_query and values.get("query"):
        query = query.where(Thread.title.icontains(values["query"], autoescape=True))
    for field, comparison in (
        ("created_after", Thread.created_at.__ge__),
        ("created_before", Thread.created_at.__lt__),
    ):
        if values.get(field):
            from datetime import datetime

            value = values[field]
            query = query.where(
                comparison(datetime.fromisoformat(value) if isinstance(value, str) else value)
            )
    if values.get("before"):
        anchor = authorized_thread(db, actor, str(values["before"]))
        query = query.where(
            or_(
                Thread.created_at < anchor.created_at,
                (Thread.created_at == anchor.created_at) & (Thread.id < anchor.id),
            )
        )
    return query.order_by(Thread.created_at.desc(), Thread.id.desc())


def conversation_view(db, thread):
    workspace = db.get(Workspace, thread.workspace_id)
    return {
        "thread_id": thread.id,
        "title": thread.title,
        "workspace_id": thread.workspace_id,
        "workspace_name": workspace.name,
        "created_at": thread.created_at,
        "archived": thread.archived,
        "version": thread.version,
    }


def text_slice(content, start, max_chars):
    if start > len(content):
        raise AppError(
            "history_cursor_invalid", "字符游标超过消息正文，请按返回的真实位置续读。", 422
        )
    end = min(len(content), start + max_chars)
    return {
        "content": content[start:end],
        "start_character": start,
        "next_character": end if end < len(content) else None,
        "total_characters": len(content),
        "truncated": end < len(content),
    }


def filtered_message(db, actor, thread, row):
    from workbench.modules.context.compiler import filter_memory
    from workbench.modules.context.transfers import allowed_result, filter_artifact_transfer

    if row.run_id and not allowed_result(db, actor, row.run_id):
        return None
    if row.role == "tool":
        try:
            value = json.loads(row.content)
        except (ValueError, TypeError):
            value = None
        if isinstance(value, dict) and value.get("conversation_history_ref"):
            # Avoid recursively turning one conversation's cached excerpts into
            # another copy. The original source is available through its own ID.
            return None
    message = {"role": row.role, "content": row.content, "_message_id": row.id}
    message = filter_memory(db, source_actor(actor, thread.workspace_id), message)
    message = filter_artifact_transfer(db, actor, message)
    return message


def visible_rows(db, thread, include_tools=False):
    roles = {"user", "assistant", "tool"} if include_tools else {"user", "assistant"}
    return [
        row
        for row in history_rows(db, thread)
        if row.role in roles and (row.content or row.attachments)
    ]


def list_conversations(db, actor, values):
    from .provenance import stamp

    limit = values.get("limit", 20)
    rows = list(db.scalars(thread_query(db, actor, values).limit(limit + 1)))
    selected = rows[:limit]
    return {
        "conversations": [conversation_view(db, row) for row in selected],
        "next_before": selected[-1].id if len(rows) > limit else None,
        "observed_at": now(),
        "conversation_history_ref": stamp(db, actor, selected, []),
    }


def search_conversations(db, actor, values):
    from .provenance import stamp

    query = values["query"].casefold()
    limit = values.get("limit", 10)
    candidates = list(
        db.scalars(thread_query(db, actor, values, title_query=False).limit(SCAN_CONVERSATIONS + 1))
    )
    results, threads, sources, unavailable = [], [], [], []
    scanned = []
    for thread in candidates[:SCAN_CONVERSATIONS]:
        scanned.append(thread)
        result = None
        if values.get("search_in", "all") in {"title", "all"} and query in thread.title.casefold():
            result = {
                **conversation_view(db, thread),
                "matched_in": "title",
                "snippet": thread.title,
            }
        elif values.get("search_in", "all") in {"messages", "all"}:
            try:
                rows = visible_rows(db, thread, include_tools=False)
            except AppError as error:
                if error.code not in {
                    "thread_history_limit",
                    "thread_branch_limit",
                    "thread_branch_invalid",
                }:
                    raise
                unavailable.append({"thread_id": thread.id, "reason": error.code})
                continue
            for row in reversed(rows):
                filtered = filtered_message(db, actor, thread, row)
                if filtered is None:
                    continue
                content = filtered["content"]
                match = content.casefold().find(query)
                if match < 0:
                    continue
                # Python casefold can expand characters; bound the display slice
                # independently rather than claiming an exact source match offset.
                start = max(0, match - 120)
                result = {
                    **conversation_view(db, thread),
                    "matched_in": "messages",
                    "message_id": row.id,
                    "role": row.role,
                    "message_status": row.status,
                    "message_created_at": row.created_at,
                    "snippet": content[start : start + 600],
                }
                sources.append((thread, row))
                break
        if result is not None:
            results.append(result)
            threads.append(thread)
        if len(results) >= limit:
            break
    more = len(candidates) > len(scanned)
    return {
        "matches": results,
        "next_before": scanned[-1].id if more and scanned else None,
        "partial": bool(unavailable),
        "unavailable_conversations": unavailable,
        "observed_at": now(),
        "conversation_history_ref": stamp(db, actor, threads, sources),
        "note": (
            "每条对话返回一个实际命中；用thread_id和message_id读取原文。"
            "next_before非空表示仍有对话待检索。"
        ),
    }


def read_conversation(db, actor, values):
    from .provenance import stamp

    thread = authorized_thread(db, actor, str(values["thread_id"]))
    rows = visible_rows(db, thread, values.get("include_tools", False))
    identifier = values.get("message_id") or values.get("after_message_id")
    index = 0
    if identifier:
        index = next((n for n, row in enumerate(rows) if row.id == str(identifier)), -1)
        if index < 0:
            raise AppError("history_message_not_found", "消息不属于此对话的可见历史。", 404)
        if values.get("after_message_id"):
            index += 1
    limit, budget = values.get("limit", 5), values.get("max_chars", 8000)
    messages, sources = [], []
    continuation, last = None, None
    for position, row in enumerate(rows[index:]):
        if len(messages) >= limit or budget <= 0:
            continuation = {
                "thread_id": thread.id,
                "after_message_id": last,
                "include_tools": values.get("include_tools", False),
            }
            break
        filtered = filtered_message(db, actor, thread, row)
        content = (
            filtered["content"]
            if filtered is not None
            else "[此消息的资料未授权传给当前模型，正文保留在原对话。]"
        )
        start = values.get("start_character", 0) if position == 0 else 0
        part = text_slice(content, start, budget)
        messages.append(
            {
                "message_id": row.id,
                "run_id": row.run_id,
                "role": row.role,
                "status": row.status,
                "created_at": row.created_at,
                **part,
                "withheld": filtered is None,
                "attachments": [
                    {key: item[key] for key in ("artifact_id", "version", "mode") if key in item}
                    for item in (row.attachments or [])
                    if filtered is not None
                ],
            }
        )
        if filtered is not None:
            sources.append((thread, row))
        last = row.id
        budget -= len(part["content"])
        if part["next_character"] is not None:
            continuation = {
                "thread_id": thread.id,
                "message_id": row.id,
                "start_character": part["next_character"],
                "include_tools": values.get("include_tools", False),
            }
            break
    return {
        "conversation": conversation_view(db, thread),
        "messages": messages,
        "next": continuation,
        "observed_at": now(),
        "conversation_history_ref": stamp(db, actor, [thread], sources),
        "note": (
            "这是历史资料，沿用当前任务权限；附件仍须通过原文件工具和授权读取，"
            "不自动加载图片或恢复原任务。"
        ),
    }


def execute(run_id, name, values):
    bind = engine().execution_options(isolation_level="REPEATABLE READ")
    with Session(bind) as db:
        actor = db.get(Run, run_id)
        return {
            "list_conversations": list_conversations,
            "search_conversations": search_conversations,
            "read_conversation": read_conversation,
        }[name](db, actor, values)
