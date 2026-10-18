"""Bounded extractive state: factual references, original history retained, no invented summary."""

import asyncio
import hashlib
import json
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.modules.collaboration.domain import AgentSlot
from workbench.modules.context.transfers import allowed_result
from workbench.modules.conversations import Message
from workbench.modules.files.domain import Artifact
from workbench.modules.files.storage import file_digest, publish_bytes, storage_path
from workbench.modules.interactions import Question
from workbench.modules.runs.domain import Run
from workbench.modules.runs.ownership import assert_owned
from workbench.modules.runs.service import emit
from workbench.modules.tools.domain import Operation


def estimate(messages, tools):
    # Conservative byte estimate, explicitly not provider-measured token usage.
    texts, reserve = [], 0
    for message in messages:
        content = message.get("content") or ""
        if isinstance(content, list):
            reserve += 2048 * sum(block.get("type") == "image" for block in content)
            content = [
                block
                for block in content
                if block.get("type")
                not in {"image", "native_document", "native_audio", "native_video"}
            ]
        for reference in message.get("attachments") or []:
            reserve += {"native_image": 2048, "native_document": 16000, "native_audio": 7200}.get(
                reference["mode"], 3200
            )
        texts.append(
            {
                key: value
                for key, value in {**message, "content": content}.items()
                if key in {"role", "content", "tool_calls", "tool_call_id", "provider_output"}
            }
        )
    return len(json.dumps(texts + tools, ensure_ascii=False).encode()) // 2 + reserve


def units(messages):
    result, index = [], 1
    while index < len(messages):
        start = index
        message = messages[index]
        index += 1
        if message.get("tool_calls"):
            while index < len(messages) and messages[index].get("role") == "tool":
                index += 1
        result.append((start, messages[start:index]))
    return result


def state_markdown(db, run, messages):
    parts = [
        "# STATE.md",
        "",
        "这是从真实持久记录提取的任务事实索引，原消息未删除。",
        "",
        "## 当前目标",
        run.input,
    ]
    deliverable = run.config_snapshot.get("deliverable")
    if deliverable:
        parts += ["", "## 子任务交付要求", deliverable]
    previous = [
        message
        for message in messages
        if message.get("role") == "user"
        and not message.get("source_kind")
        and message.get("content") != run.input
    ]
    if previous:
        parts += [
            "",
            "## 相关历史输入",
            "下列为片段；需要完整条件时用read_task_history读取原消息。",
        ]
        for message in previous[-3:]:
            content = message.get("content")
            if isinstance(content, str):
                source = message.get("_message_id", "图状态中的用户消息")
                suffix = "（截断）" if len(content) > 240 else ""
                parts.append(f"- 消息 {source}：{content[:240]}{suffix}")
    decisions = list(
        db.scalars(
            select(Question)
            .where(Question.run_id == run.id, Question.status == "answered")
            .order_by(Question.created_at)
        )
    )
    if decisions:
        parts += ["", "## 用户已提交的决定"]
        for question in decisions[-8:]:
            parts.append(
                json.dumps(
                    {
                        "question_id": question.id,
                        "prompt": question.prompt,
                        "answer": question.answer,
                    },
                    ensure_ascii=False,
                )
            )
    operations = list(
        db.scalars(
            select(Operation)
            .where(Operation.run_id == run.id)
            .order_by(Operation.created_at.desc())
            .limit(12)
        )
    )
    parts += [
        "",
        "## 最近操作事实",
        "路径与版本是当时记录；需要当前内容时重新读取，不推断外部文件仍未改变。",
    ]
    for operation in reversed(operations):
        arguments = {
            key: value
            for key, value in operation.arguments.items()
            if key
            in {
                "path",
                "artifact_id",
                "document_id",
                "agent_id",
                "start_line",
                "end_line",
                "expected_version",
            }
        }
        result = operation.result or {}
        facts = {
            key: value
            for key, value in result.items()
            if key in {"version", "exit_code", "error", "status", "truncated"}
        }
        parts.append(
            json.dumps(
                {
                    "operation_id": operation.id,
                    "tool": operation.tool_name,
                    "status": operation.status,
                    "effect": operation.effect_status,
                    "arguments": arguments,
                    "facts": facts,
                },
                ensure_ascii=False,
            )
        )
    parts += [
        "",
        "## 恢复方式",
        "按需发现read_task_history读取完整原消息，或按上述路径/文件ID重新读取实际内容。",
        "没有在此列出的内容不代表不存在；本索引不作语义推断，不把待确认操作当成功。",
    ]
    slots = list(
        db.scalars(
            select(AgentSlot)
            .where(AgentSlot.root_run_id == run.id)
            .order_by(AgentSlot.created_at.desc())
            .limit(3)
        )
    )
    if slots:
        parts += ["", "## 子Agent实际状态"]
        for slot in reversed(slots):
            child = db.get(Run, slot.run_id)
            content = (
                db.scalar(
                    select(Message.content)
                    .where(
                        Message.run_id == child.id,
                        Message.role == "assistant",
                        Message.status == "completed",
                        Message.content != "",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
                or ""
            )
            parts.append(
                json.dumps(
                    {
                        "agent_id": slot.id,
                        "run_id": child.id,
                        "name": slot.name,
                        "status": child.status,
                        "result_excerpt": content[:600]
                        if allowed_result(db, run, child.id)
                        else "子任务文件内容尚未授权给当前模型；正文保留在本机。",
                        "truncated": len(content) > 600,
                    },
                    ensure_ascii=False,
                )
            )
    return "\n".join(parts) + "\n"


def compact(db, run, messages, tools, context_window, max_output_tokens):
    budget = context_window - max_output_tokens
    target_ratio = run.config_snapshot.get("runtime_options", {}).get("context_target_ratio", 0.9)
    if estimate(messages, tools) <= budget * target_ratio:
        return messages, None
    latest_user = next(
        (
            index
            for index in range(len(messages) - 1, 0, -1)
            if messages[index].get("role") == "user" and not messages[index].get("source_kind")
        ),
        None,
    )
    groups = units(messages)
    pinned = {
        index
        for index, group in groups
        if index == latest_user
        or any(
            item.get("source_kind") == "user_steering" or item.get("_input_pending")
            for item in group
        )
    }
    # The newest observation must reach the model. Dropping it can make a model
    # repeat the same read/discovery forever even though execution succeeded.
    latest = groups[-1] if groups else None
    if latest and latest[1][0].get("tool_calls"):
        pinned.add(latest[0])
    markdown = state_markdown(db, run, messages)
    encoded = markdown.encode()
    digest = hashlib.sha256(encoded).hexdigest()
    identifier = str(uuid5(UUID(run.id), f"context-state:{digest}"))
    operation_facts = []
    for operation in db.scalars(
        select(Operation)
        .where(Operation.run_id == run.id)
        .order_by(Operation.created_at.desc())
        .limit(8)
    ):
        operation_facts.append(
            {
                "operation_id": operation.id,
                "tool": operation.tool_name,
                "status": operation.status,
                "effect": operation.effect_status,
                "path": operation.arguments.get("path"),
                "version": (operation.result or {}).get("version"),
                "error": (operation.result or {}).get("error"),
            }
        )
    children = []
    for slot in db.scalars(select(AgentSlot).where(AgentSlot.root_run_id == run.id)):
        child = db.get(Run, slot.run_id)
        result = (
            db.scalar(
                select(Message.content)
                .where(
                    Message.run_id == child.id,
                    Message.role == "assistant",
                    Message.status == "completed",
                    Message.content != "",
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            or ""
        )
        if result and not allowed_result(db, run, child.id):
            result = "子任务文件内容尚未授权给当前模型；正文保留在本机。"
        children.append(
            {
                "agent_id": slot.id,
                "run_id": child.id,
                "name": slot.name,
                "status": child.status,
                "released": slot.released_at is not None,
                "result_excerpt": result[:300],
                "result_truncated": len(result) > 300,
            }
        )
    decisions = [
        {"question": row.prompt, "answer": row.answer}
        for row in db.scalars(
            select(Question)
            .where(Question.run_id == run.id, Question.status == "answered")
            .order_by(Question.created_at)
        )
    ]
    summary = {
        "role": "user",
        "content": "",
        "source_kind": "task_state",
    }
    chosen = {index: group for index, group in groups if index in pinned}
    if latest and any(
        call.get("function", {}).get("name") in {"inspect_agents", "wait_agents"}
        for call in latest[1][0].get("tool_calls", [])
    ):
        children = [
            {
                key: value
                for key, value in item.items()
                if key not in {"result_excerpt", "result_truncated"}
            }
            for item in children
        ]

    def selected():
        return [messages[0], summary] + [item for index in sorted(chosen) for item in chosen[index]]

    for count in (8, 4, 0):
        summary["content"] = (
            "工作台压缩了历史上下文，原消息未删除，也未增加授权。完整当前目标仍保留在原始输入中。\n"
            + json.dumps(
                {
                    "state_artifact_id": identifier,
                    "recent_operation_facts": operation_facts[:count],
                    "confirmed_user_decisions": decisions,
                    "child_results_from_persisted_records": children,
                    "recovery": (
                        "必要时discover_tools加载read_task_history或read_artifact读取原历史/STATE.md；"
                        "副作用前核对此前结果，不能把未展示当未执行。"
                    ),
                },
                ensure_ascii=False,
            )
        )
        if estimate(selected(), tools) <= budget * target_ratio:
            break
    if estimate(selected(), tools) > budget and latest and latest[0] in pinned:
        # Keep the call/result pair balanced and preserve exact raw results in the
        # ledger. This is a visibly truncated view, never a semantic summary.
        bounded = []
        for item in latest[1]:
            if item.get("role") == "tool" and len(item.get("content") or "") > 1000:
                original = item["content"]
                item = {
                    **item,
                    "content": json.dumps(
                        {
                            "context_excerpt": original[:1000],
                            "truncated": True,
                            "original_characters": len(original),
                            "recovery": (
                                "完整结果仍在操作记录；read_task_history可读取原始工具消息。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                }
            bounded.append(item)
        chosen[latest[0]] = bounded
    if estimate(selected(), tools) > budget:
        # Keep the full active instruction and decisions. Never hide required rules or
        # turn an oversized goal into a fabricated semantic summary.
        raise AppError(
            "context_goal_budget",
            "当前目标、规则或最近操作本身超过模型上下文预算，请缩小任务范围或选更大上下文的连接。",
            422,
            "budget",
        )
    for index, group in reversed(groups):
        if index in chosen:
            continue
        if (
            latest_user is not None
            and index > latest_user
            and index != (latest[0] if latest else None)
            and all(
                item.get("role") == "assistant" and not item.get("tool_calls") for item in group
            )
        ):
            # Intermediate narration is weaker evidence than tool receipts and
            # finished child results. It remains available in the original history.
            continue
        chosen[index] = group
        if estimate(selected(), tools) > budget * 0.65:
            del chosen[index]
    artifact = db.get(Artifact, identifier)
    if artifact is None:
        previous = run.config_snapshot.get("context_compaction", {}).get("state_artifact_id")
        artifact = Artifact(
            id=identifier,
            user_id=run.user_id,
            workspace_id=run.workspace_id,
            run_id=run.id,
            parent_id=previous,
            display_name="STATE.md",
            mime_type="text/markdown",
            byte_size=len(encoded),
            content_hash=digest,
            storage_path=f"workspaces/{run.workspace_id}/artifacts/{identifier}/STATE.md",
            kind="task_state",
            metadata_json={
                "_pending_markdown": markdown,
                "source": "persisted_task_facts",
                "semantic_summary": False,
            },
        )
        db.add(artifact)
        db.flush()
    manifest = {
        "state_artifact_id": identifier,
        "source_messages": len(messages),
        "retained_messages": sum(len(group) for group in chosen.values()),
        "estimated_input_tokens": estimate(selected(), tools),
        "estimate_method": "utf8_bytes_div2_plus_attachment_reserve",
    }
    if manifest != run.config_snapshot.get("context_compaction"):
        run.config_snapshot = {**run.config_snapshot, "context_compaction": manifest}
        emit(db, run.id, "context.compacted", manifest)
    return selected(), identifier if artifact.status != "available" else None


def publish_state(run_id, artifact_id):
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        assert_owned(db, run_id)
        if run.cancel_requested_at:
            raise asyncio.CancelledError
        artifact = db.get(Artifact, artifact_id)
        if artifact.status == "available":
            return
        target = storage_path(artifact.storage_path)
        if target.exists() and file_digest(target) != artifact.content_hash:
            raise AppError("context_state_conflict", "状态文件与持久发布意图不同，需要核对。", 409)
        if not target.exists():
            publish_bytes(target, artifact.metadata_json["_pending_markdown"].encode())
        artifact.status = "available"
        artifact.metadata_json = {
            key: value
            for key, value in artifact.metadata_json.items()
            if key != "_pending_markdown"
        }
        emit(db, run_id, "artifact.created", {"artifact_id": artifact.id, "kind": "task_state"})
