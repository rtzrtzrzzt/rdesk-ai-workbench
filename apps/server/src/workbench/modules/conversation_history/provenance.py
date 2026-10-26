"""Recheck excerpt sources before replaying cached tool data into a model context."""

import hashlib
import json

from workbench.contracts.errors import AppError
from workbench.modules.conversations import Message

from . import settings


def message_digest(row):
    value = {
        key: getattr(row, key)
        for key in ("id", "run_id", "role", "status", "content", "attachments")
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def memory_state(db, actor, workspace_id):
    from workbench.modules.context.window import memory_fingerprint

    from .service import source_actor

    return memory_fingerprint(db, source_actor(actor, workspace_id))


def stamp(db, actor, threads, sources):
    unique = {row.id: row for row in threads}
    return {
        "version": 1,
        "settings_version": settings.view(db, actor.user_id).version,
        "threads": [{"id": row.id, "version": row.version} for row in unique.values()],
        "messages": [
            {"thread_id": thread.id, "id": row.id, "digest": message_digest(row)}
            for thread, row in sources
        ],
        "memory_scopes": [
            {"workspace_id": identifier, "fingerprint": memory_state(db, actor, identifier)}
            for identifier in sorted({row.workspace_id for row in unique.values()})
        ],
    }


def reference_valid(db, actor, reference):
    from workbench.modules.context.transfers import allowed_result

    from .service import authorized_thread, visible_rows

    if (
        reference.get("version") != 1
        or settings.view(db, actor.user_id).version != reference.get("settings_version")
        or len(reference.get("threads", [])) > 50
        or len(reference.get("messages", [])) > 50
        or len(reference.get("memory_scopes", [])) > 50
    ):
        return False
    scope, _ = settings.scope_for(db, actor)
    if scope == "disabled":
        return False
    threads = {}
    for item in reference.get("threads", []):
        thread = authorized_thread(db, actor, item["id"])
        if thread.version != item["version"]:
            return False
        threads[thread.id] = thread
    visible = {}
    for item in reference.get("messages", []):
        thread = threads.get(item["thread_id"])
        if thread is None:
            return False
        if thread.id not in visible:
            visible[thread.id] = {row.id for row in visible_rows(db, thread, include_tools=True)}
        row = db.get(Message, item["id"])
        if (
            row is None
            or row.id not in visible[thread.id]
            or message_digest(row) != item["digest"]
            or (row.run_id and not allowed_result(db, actor, row.run_id))
        ):
            return False
    workspace_ids = {thread.workspace_id for thread in threads.values()}
    for item in reference.get("memory_scopes", []):
        if (
            item["workspace_id"] not in workspace_ids
            or memory_state(db, actor, item["workspace_id"]) != item["fingerprint"]
        ):
            return False
    return len(reference.get("memory_scopes", [])) == len(workspace_ids)


def filter_cached(db, actor, message, result):
    try:
        valid = reference_valid(db, actor, result["conversation_history_ref"])
    except (AppError, KeyError, TypeError, ValueError):
        valid = False
    if valid:
        return message
    return {
        **message,
        "content": json.dumps(
            {
                "error": {
                    "code": "conversation_history_stale",
                    "message": (
                        "对话读取权限、原始消息或资料来源已变化，"
                        "此旧片段不再注入；请按当前权限重新查询。"
                    ),
                }
            },
            ensure_ascii=False,
        ),
    }
