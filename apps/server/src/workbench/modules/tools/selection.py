"""Restore tool definitions from the conversation's actual selected history."""

import json

from workbench.modules.tools.registry import CORE_TOOL_NAMES, TOOLS


def from_history(messages):
    selected = list(CORE_TOOL_NAMES)
    pending = {}
    for message in messages:
        if message.get("role") == "assistant":
            pending = {
                call["id"]: call.get("function", {}).get("name")
                for call in message.get("tool_calls") or []
                if call.get("id")
            }
        elif message.get("role") == "tool":
            name = pending.pop(message.get("tool_call_id"), None)
            if name != "discover_tools":
                continue
            try:
                result = json.loads(message.get("content") or "")
            except (ValueError, TypeError):
                continue
            loaded = result.get("loaded") if isinstance(result, dict) else None
            if isinstance(loaded, list) and all(isinstance(item, str) for item in loaded):
                selected = [item for item in loaded if item in TOOLS]
    return selected


def for_run(db, run):
    from workbench.modules.conversation_service import get_thread, history_rows
    from workbench.modules.search.service import get_connection

    selected = run.config_snapshot.get("tool_names")
    if selected is None:
        rows = history_rows(db, get_thread(db, run.user_id, run.thread_id), completed_only=True)
        selected = from_history(
            [
                {
                    **(row.provider_message or {}),
                    "role": row.role,
                    "content": row.content,
                }
                for row in rows
            ]
        )
    permitted = set(run.config_snapshot.get("allowed_tools", TOOLS))
    from workbench.modules.access.service import scope_for_run

    scope = scope_for_run(db, run)
    if scope:
        permitted &= scope.allowed_tools
    selected = set(selected) & permitted & set(TOOLS)
    connection = get_connection(db, run.user_id, run.workspace_id)
    if not connection or not connection.enabled:
        selected = {name for name in selected if TOOLS[name].module != "search"}
    # Selection exposes schemas only. Every operation still goes through the
    # executor's current scope, policy and approval checks.
    return [name for name in CORE_TOOL_NAMES if name in selected] + sorted(
        selected - set(CORE_TOOL_NAMES)
    )
