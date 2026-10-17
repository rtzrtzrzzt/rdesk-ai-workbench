"""Derive protocol-complete context without altering original historical evidence."""

import json


def complete_tool_boundaries(messages):
    result, index = [], 0
    while index < len(messages):
        message = messages[index]
        index += 1
        if message.get("role") == "tool":
            continue  # Its interrupted assistant record was excluded from replay.
        result.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            continue
        replies = {}
        while index < len(messages) and messages[index].get("role") == "tool":
            reply = messages[index]
            replies[reply.get("tool_call_id")] = reply
            index += 1
        for call in calls:
            result.append(
                replies.get(call["id"])
                or {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps(
                        {
                            "error": {
                                "code": "historical_result_unconfirmed",
                                "message": (
                                    "前次执行没有已确认回传；此为历史缺失标记，不是执行结果。"
                                    "禁止据此盲目重做副作用。"
                                ),
                            }
                        },
                        ensure_ascii=False,
                    ),
                }
            )
    return result
