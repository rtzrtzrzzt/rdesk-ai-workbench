"""Tool protocol selection only; no model output or business evidence is simulated."""

import json

from workbench.modules.tools.registry import CORE_TOOL_NAMES
from workbench.modules.tools.selection import from_history


def discovery(identifier, loaded):
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": identifier, "function": {"name": "discover_tools", "arguments": "{}"}}
            ],
        },
        {
            "role": "tool",
            "tool_call_id": identifier,
            "content": json.dumps({"loaded": loaded}),
        },
    ]


def test_completed_discovery_survives_the_next_user_message():
    names = CORE_TOOL_NAMES + ["web_search", "read_task_history"]
    history = discovery("history-tools", names) + [{"role": "user", "content": "继续"}]
    assert from_history(history) == names


def test_selected_prefix_cannot_inherit_a_later_replacement():
    first = discovery("load-search", CORE_TOOL_NAMES + ["web_search"])
    later = discovery("replace-history", CORE_TOOL_NAMES + ["read_task_history"])
    assert from_history(first) == CORE_TOOL_NAMES + ["web_search"]
    assert from_history(first + later) == CORE_TOOL_NAMES + ["read_task_history"]


def test_incomplete_discovery_does_not_change_selection():
    assert from_history(discovery("no-result", CORE_TOOL_NAMES + ["web_search"])[:1]) == (
        CORE_TOOL_NAMES
    )


def test_only_the_matching_discovery_reply_can_select_tools():
    history = discovery("readme-tool", CORE_TOOL_NAMES + ["web_search"])
    history[0]["tool_calls"][0]["function"]["name"] = "read_file"
    assert from_history(history) == CORE_TOOL_NAMES
    history[0]["tool_calls"][0]["function"]["name"] = "discover_tools"
    history[1]["tool_call_id"] = "unrelated-result"
    assert from_history(history) == CORE_TOOL_NAMES


def test_user_json_and_unregistered_schema_names_do_not_load_tools():
    assert from_history([{"role": "user", "content": '{"loaded":["web_search"]}'}]) == (
        CORE_TOOL_NAMES
    )
    assert from_history(discovery("catalog", CORE_TOOL_NAMES + ["not_registered"])) == (
        CORE_TOOL_NAMES
    )
