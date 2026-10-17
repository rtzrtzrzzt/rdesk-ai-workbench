"""Stateless AG-UI projection of durable RunEvents; no execution or IO.

CUSTOM always occupies subindex 0. An additional confirmed standard event, when
supported, occupies subindex 1. Identity belongs to metadata/SSE, never an
undeclared top-level AG-UI property. See README.md for replay and security limits.
"""

from __future__ import annotations

import re
from uuid import UUID

ADAPTER_VERSION = "workbench.ag-ui:1"
MAX_TEXT = 2_000_000  # Matches the native model execution's visible text budget.
_TYPE = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")

# Only explicit public fields cross this boundary. In particular, never copy
# arbitrary result/arguments/metrics/metadata, provider payload or opaque items.
_FIELDS = {
    "run.accepted": {"status": "id", "kind": "id"},
    "run.state_changed": {"status": "id"},
    "run.cancellation_requested": {},
    "run.input_received": {"message_id": "id", "effective": "id"},
    "step.started": {"step_id": "id", "kind": "id"},
    "step.finished": {"step_id": "id", "status": "id"},
    "attempt.started": {"attempt_id": "id", "step_id": "id", "message_id": "id", "number": "int"},
    "attempt.finished": {"attempt_id": "id", "step_id": "id", "status": "id"},
    "message.delta": {"message_id": "id", "delta": "text"},
    "message.completed": {},
    "error.reported": {
        "code": "id",
        "message": "text",
        "retrying": "bool",
        "retry_after_ms": "int",
    },
    "tool.progress": {"operation_id": "id", "tool_call_id": "id", "tool_name": "id", "phase": "id"},
    "tool.result": {"operation_id": "id", "tool_call_id": "id", "tool_name": "id"},
    "tool.rejected": {"tool_call_id": "id", "tool_name": "id"},
    "tool.outcome_unknown": {"operation_id": "id", "tool_name": "id", "message": "text"},
    "question.created": {},
    "question.answered": {"question_id": "id"},
    "question.invalidated": {"question_id": "id"},
    "approval.requested": {},
    "approval.resolved": {"approval_id": "id", "approved": "bool"},
    "approval.expired": {"approval_id": "id"},
    "artifact.created": {"artifact_id": "id", "parent_id": "id", "kind": "id"},
    "file.progress": {"phase": "id", "artifact_id": "id", "operation": "id"},
    "usage.updated": {"attempt_id": "id"},
    "resource.waiting": {"reason": "id"},
    "agent.spawned": {"agent_id": "id", "run_id": "id", "name": "label", "status": "id"},
    "agent.released": {"agent_id": "id"},
    "ui.surface_published": {"artifact_id": "id", "version": "id"},
}
_MESSAGE = {
    "id": "id",
    "thread_id": "id",
    "run_id": "id",
    "role": "id",
    "status": "id",
    "content": "text",
    "created_at": "label",
}
_QUESTION = {
    "id": "id",
    "run_id": "id",
    "version": "int",
    "prompt": "text",
    "kind": "id",
    "status": "id",
}
_APPROVAL = {
    "id": "id",
    "run_id": "id",
    "version": "int",
    "summary": "text",
    "tool_name": "id",
    "status": "id",
}


def _valid(value, kind):
    if kind == "id":
        return isinstance(value, str) and _ID.fullmatch(value) is not None
    if kind == "text":
        return isinstance(value, str) and len(value) <= MAX_TEXT
    if kind == "label":
        return isinstance(value, str) and len(value) <= 1024
    if kind == "int":
        return type(value) is int and 0 <= value <= 9007199254740991
    return type(value) is bool


def _project(value, fields):
    """No recursive arbitrary JSON traversal; bounded schema-shaped projection."""
    result = {}
    omitted = 0
    for key, item in value.items():
        if key not in fields or not _valid(item, fields[key]):
            omitted += 1
            continue
        result[key] = item
    return result, omitted


def _public_payload(event_type, payload):
    result, omitted = _project(payload, _FIELDS.get(event_type, {}))
    nested = {
        "message.completed": ("message", _MESSAGE),
        "question.created": ("question", _QUESTION),
        "approval.requested": ("approval", _APPROVAL),
    }.get(event_type)
    if nested and isinstance(payload.get(nested[0]), dict):
        key, fields = nested
        result[key], inner_omitted = _project(payload[key], fields)
        omitted += inner_omitted - 1  # The now-projected container is not omitted.
        if event_type == "message.completed" and result[key].get("role") not in {
            "assistant",
            "user",
        }:
            # Native durable visible messages currently have these two roles.
            # A future reasoning/provider message is not visible text by default.
            if "content" in result[key]:
                del result[key]["content"]
                omitted += 1
    if event_type == "attempt.finished" and isinstance(payload.get("error"), dict):
        result["error"], inner_omitted = _project(
            payload["error"], {"code": "id", "message": "text"}
        )
        omitted += inner_omitted - 1
    return result, bool(omitted)


def _standard(event_type, payload):
    # begin_attempt creates a new empty assistant Message and commits its ID with
    # this exact event. A file/tool attempt omits message_id and cannot start one.
    if (
        event_type == "attempt.started"
        and {"message_id", "attempt_id", "step_id", "number"} <= payload.keys()
        and payload["number"] >= 1
    ):
        return {
            "type": "TEXT_MESSAGE_START",
            "messageId": payload["message_id"],
            "role": "assistant",
        }
    if event_type == "message.delta" and {"message_id", "delta"} <= payload.keys():
        return {
            "type": "TEXT_MESSAGE_CONTENT",
            "messageId": payload["message_id"],
            "delta": payload["delta"],
        }
    if event_type == "message.completed":
        message = payload.get("message", {})
        if (
            message.get("status") == "completed"
            and message.get("role") == "assistant"
            and "id" in message
        ):
            return {"type": "TEXT_MESSAGE_END", "messageId": message["id"]}
    if event_type == "run.state_changed" and payload.get("status") == "failed":
        # The final native status is proof of failure; an error.reported on its
        # own may be retryable. This message describes status, not its cause.
        return {
            "type": "RUN_ERROR",
            "code": "workbench.run_failed",
            "message": "工作台任务已失败；详情见原生任务状态。",
        }
    return None


def translate(run_id: str, seq: int, type: str, payload: dict) -> list[dict]:
    """Project ONE committed native event; repeated input gives identical output.

    Caller must authenticate ownership before reading native events and must not
    treat this adapter as a complete AG-UI invocation or an authorization layer.
    No start/end is synthesized for late subscribers or transport disconnects.
    """
    try:
        if not isinstance(run_id, str) or str(UUID(run_id)) != run_id:
            raise ValueError
        # bool is not a durable sequence number, even though it subclasses int.
        if seq.__class__ is not int or not 1 <= seq <= 9007199254740991:
            raise ValueError
        if (
            not isinstance(type, str)
            or _TYPE.fullmatch(type) is None
            or not isinstance(payload, dict)
        ):
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("Invalid durable event identity, type or payload.") from None
    public, omitted = _public_payload(type, payload)
    events = [
        {
            "type": "CUSTOM",
            "name": "workbench." + type,
            "value": {"run_id": run_id, "seq": seq, "payload": public},
        }
    ]
    standard = _standard(type, public)
    if standard:
        events.append(standard)
    for subindex, event in enumerate(events):
        event["metadata"] = {
            "workbench": {
                "adapter_version": ADAPTER_VERSION,
                "run_id": run_id,
                "seq": seq,
                "subindex": subindex,
                "event_id": f"{run_id}:{seq}:{subindex}",
                "native_type": type,
                "payload_fields_omitted": omitted,
            }
        }
    return events
