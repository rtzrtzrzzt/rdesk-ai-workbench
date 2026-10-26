"""Communication is available to children; delegation and control stay with the root."""

CHILD_COLLABORATION_TOOLS = frozenset(
    {"inspect_agents", "send_agent_message", "wait_agent_message"}
)


def child_tool_allowed(name, module):
    return name not in {"exec_command", "start_command"} and (
        module != "collaboration" or name in CHILD_COLLABORATION_TOOLS
    )
