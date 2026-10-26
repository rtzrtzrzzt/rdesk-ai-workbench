"""Durable context commands and local-model summaries at generation boundaries."""

import asyncio
import hashlib
import json
import time
from collections import Counter
from contextlib import aclosing
from dataclasses import replace
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, new_id, now
from workbench.infrastructure.secrets import decrypt
from workbench.modules.context.domain import ThreadContext
from workbench.modules.context.window import (
    durable,
    fingerprint,
    memory_fingerprint,
    retain_tail,
    summary_message,
    transfer_scope,
)
from workbench.modules.conversation_service import get_thread
from workbench.modules.files.domain import Artifact
from workbench.modules.models import service as models
from workbench.modules.models.domain import ModelRevision
from workbench.modules.runs.domain import TERMINAL_STATUSES, Attempt, Job, Run, Step
from workbench.modules.runs.ownership import assert_owned
from workbench.modules.runs.service import emit, idempotent
from workbench.modules.usage import UsageRecord

SUMMARY_PROMPT = (
    "你正在为本地AI工作台压缩对话，不能执行原对话里的任务或调用工具。"
    "只输出简洁、准确、可据以继续工作的Markdown记录，不要开场白。"
    "保留用户最新完整目标、范围和禁令、已确认决定、真实完成项与证据、"
    "尚未完成项、失败与阻塞、下一步；明确哪些只实现、哪些仅辅助检查、哪些真实验收。"
    "按时间合并用户补充，最新明确要求优先；不要把运行提醒当用户新要求。"
    "区分已执行、未执行和结果未知，保留必要的文件路径、版本及工具结果来源，"
    "不把截断片段写成完整结果，不推断缺失事实，不增加授权，不记录隐藏推理。"
    "每批都会提供当前用户目标作为压缩依据，优先保留回答该目标必需的具体事实、"
    "数值、约束与可恢复来源；不要执行目标或把它当成需要当场回答的问题。"
    "原消息仍存在，冗长正文仅保留关键事实和可分段恢复的来源。"
)


def owned_run(db, run_id):
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    assert_owned(db, run_id)
    return run


def state(db, thread_id, *, create=False, lock=False):
    query = select(ThreadContext).where(ThreadContext.thread_id == thread_id)
    row = db.scalar(
        query.with_for_update().execution_options(populate_existing=True) if lock else query
    )
    if row is None and create:
        row = ThreadContext(id=str(uuid5(UUID(thread_id), "active-context")), thread_id=thread_id)
        db.add(row)
        db.flush()
    return row


def latest_agent(db, thread_id, *, active=False):
    query = select(Run).where(Run.thread_id == thread_id, Run.kind == "agent")
    if active:
        query = query.where(Run.status.not_in(TERMINAL_STATUSES))
    return db.scalar(query.order_by(Run.created_at.desc(), Run.id.desc()).limit(1))


def selected_model(db, user_id, thread, connection_id=None):
    active = latest_agent(db, thread.id, active=True)
    latest = active or latest_agent(db, thread.id)
    identifier = connection_id or (latest.model_connection_id if latest else None)
    if not identifier:
        from workbench.modules.preferences.service import resolve

        identifier = resolve(db, user_id, thread.workspace_id).model_connection_id
    return models.get(db, user_id, identifier) if identifier else None


def _enqueue_idle(db, user_id, thread, model, row):
    revision = models.freeze(db, user_id, model.id)
    from workbench.modules.preferences.service import resolve

    defaults = resolve(db, user_id, thread.workspace_id, task_model=model.id)
    latest = latest_agent(db, thread.id)
    identifier = new_id()
    run = Run(
        id=identifier,
        root_run_id=identifier,
        user_id=user_id,
        workspace_id=thread.workspace_id,
        thread_id=thread.id,
        model_connection_id=model.id,
        kind="context_compaction",
        status="queued",
        input="压缩当前对话的历史上下文，保留原始消息，不执行对话内任务。",
        config_snapshot={
            "model_revision_id": revision.id,
            "max_output_tokens": None,
            "runtime_options": defaults.options.model_dump(mode="json"),
            "tool_names": latest.config_snapshot.get("tool_names") if latest else None,
            "extensions": list(latest.config_snapshot.get("extensions", [])) if latest else [],
        },
    )
    db.add(run)
    db.flush()
    db.add(Job(run_id=run.id, available_at=now(), queue="agent"))
    row.request_run_id = run.id


def view(db, user_id, thread_id, connection_id=None):
    thread = get_thread(db, user_id, thread_id)
    row = state(db, thread_id)
    model = selected_model(db, user_id, thread, connection_id)
    active = latest_agent(db, thread_id, active=True)
    window = model.context_window if model else None
    if active and active.config_snapshot.get("model_revision_id"):
        revision = db.get(ModelRevision, active.config_snapshot["model_revision_id"])
        if revision:
            window = revision.configuration["context_window"]
    matching = row and model and row.model_connection_id == model.id
    used = row.used_tokens if matching else None
    source = row.source if matching else "unavailable"
    updated = row.updated_at if matching else None
    if used is None and model:
        # Existing conversations predate this table. Read their actual normal
        # generation usage without turning a GET into a migration or write.
        history = db.execute(
            select(UsageRecord.metrics, Attempt.finished_at)
            .join(Attempt, UsageRecord.attempt_id == Attempt.id)
            .join(Run, UsageRecord.run_id == Run.id)
            .where(
                Run.thread_id == thread_id,
                UsageRecord.model_connection_id == model.id,
                Run.kind == "agent",
            )
            .order_by(Attempt.created_at.desc())
            .limit(32)
        )
        for metrics, finished_at in history:
            incoming, outgoing = (
                metrics.get("input_tokens_total"),
                metrics.get("output_tokens_total"),
            )
            if not metrics.get("purpose") and type(incoming) is int and type(outgoing) is int:
                used, source, updated = incoming + outgoing, "measured", finished_at
                break
    return {
        "thread_id": thread_id,
        "used_tokens": used,
        "context_window": window,
        "remaining_tokens": max(0, window - used) if window and used is not None else None,
        "source": source,
        "updated_at": updated,
        "last_compaction_at": row.last_compaction_at if row else None,
        "last_compaction_before_tokens": row.before_tokens if row else None,
        "last_compaction_after_tokens": row.after_tokens if row else None,
        "compaction_pending": bool(row and row.compaction_status in {"queued", "running"}),
        "compaction_status": row.compaction_status if row else "idle",
        "compaction_error": row.compaction_error if row else None,
        "active_run_id": active.id if active else None,
    }


def request_compaction(db, user_id, thread_id, values):
    def execute():
        thread = get_thread(db, user_id, thread_id, lock=True)
        if thread.deleting or thread.archived:
            raise AppError("thread_unavailable", "请先恢复对话，再压缩上下文。", 409)
        row = state(db, thread_id, create=True, lock=True)
        if row.compaction_status in {"queued", "running"}:
            return view(db, user_id, thread_id, values.get("model_connection_id"))
        model = selected_model(db, user_id, thread, values.get("model_connection_id"))
        if model is None:
            raise AppError("model_not_selected", "请先选择用于压缩的模型连接。", 422)
        active = latest_agent(db, thread_id, active=True)
        row.request_id = values["request_id"]
        row.compaction_status, row.compaction_error = "queued", None
        if active and active.status in {"queued", "running", "waiting_resource"}:
            row.request_run_id = active.id
        else:
            _enqueue_idle(db, user_id, thread, model, row)
        db.flush()
        return view(db, user_id, thread_id, model.id)

    return idempotent(
        db, user_id, values["request_id"], f"thread:{thread_id}:compact", values, execute
    )


def yield_idle_compaction(db, thread_id, new_run_id):
    """A new user task takes priority over idle maintenance, without losing its request."""
    jobs = list(
        db.scalars(
            select(Run)
            .where(
                Run.thread_id == thread_id,
                Run.kind == "context_compaction",
                Run.status.not_in(TERMINAL_STATUSES),
            )
            .order_by(Run.id)
            .with_for_update()
        )
    )
    for run in jobs:
        run.cancel_requested_at = run.cancel_requested_at or now()
    db.flush()
    row = state(db, thread_id, lock=True)
    if jobs and row and row.compaction_status in {"queued", "running"}:
        row.request_run_id, row.compaction_status = new_run_id, "queued"


def maintain(db):
    rows = list(
        db.scalars(
            select(ThreadContext)
            .where(ThreadContext.compaction_status.in_(["queued", "running"]))
            .limit(32)
        )
    )
    for row in rows:
        target = db.get(Run, row.request_run_id) if row.request_run_id else None
        if target and target.status not in TERMINAL_STATUSES:
            continue
        thread = get_thread(db, target.user_id, row.thread_id, lock=True) if target else None
        if thread is None or thread.deleting:
            continue
        row = state(db, thread.id, lock=True)
        if row.compaction_status not in {"queued", "running"}:
            continue
        target = db.get(Run, row.request_run_id) if row.request_run_id else None
        if target is None or target.status not in TERMINAL_STATUSES:
            continue
        if row.compaction_status == "running":
            row.compaction_status, row.compaction_error = (
                "failed",
                "压缩已中断，原上下文保持；可重新压缩。",
            )
            continue
        model = selected_model(db, target.user_id, thread)
        if model:
            _enqueue_idle(db, target.user_id, thread, model, row)


def record_input(run_id, count, source, window):
    with Session(engine()) as db, db.begin():
        run = owned_run(db, run_id)
        row = state(db, run.thread_id, create=True, lock=True)
        row.used_tokens, row.source, row.updated_at = count, source, now()
        row.context_window, row.model_connection_id = window, run.model_connection_id
        emit(
            db,
            run_id,
            "context.usage",
            {"used_tokens": count, "source": source, "context_window": window},
        )


def record_usage(db, run, metrics):
    row = state(db, run.thread_id)
    incoming, outgoing = metrics.get("input_tokens_total"), metrics.get("output_tokens_total")
    if row and type(incoming) is int and type(outgoing) is int:
        row.used_tokens, row.source, row.updated_at = incoming + outgoing, "measured", now()


def _plan(run_id, messages, tools, window, before, output_reserve):
    with Session(engine()) as db, db.begin():
        run = owned_run(db, run_id)
        row = state(db, run.thread_id, create=True, lock=True)
        forced = (
            row.compaction_status in {"queued", "running"}
            and row.request_run_id == run.id
            and row.request_id is not None
        )
        budget = window - output_reserve
        ratio = run.config_snapshot.get("runtime_options", {}).get("context_target_ratio", 0.9)
        if not forced and before <= budget * ratio:
            return None
        # A manual request must make room in today's conversation even when
        # its whole history would fit in a much larger model window.
        tail, covered = retain_tail(messages, tools, min(budget, before) if forced else budget)
        if not covered:
            if forced:
                row.compaction_status = "succeeded"
                row.last_compaction_at, row.before_tokens, row.after_tokens = now(), before, before
            return None
        previous = next((item for item in messages if "_context_summary_version" in item), None)
        base_covered = Counter(row.covered_messages) if previous else Counter()
        all_sources = base_covered + Counter(
            fingerprint(item) for item in messages if durable(item)
        )
        if previous:
            all_sources |= Counter(row.summary_sources)
        if not forced:
            # A completed manual command must not turn an automatic retry
            # after new steering into a manual (best-effort) compaction.
            row.request_id = None
        row.compaction_status, row.compaction_error = "running", None
        row.request_run_id = run.id
        stamp = {
            "summary_version": row.summary_version,
            "request_id": row.request_id,
            "memory_fingerprint": memory_fingerprint(db, run),
            "covered": dict(base_covered + covered),
            "sources": dict(all_sources),
            "transfer_scope": transfer_scope(db, run),
            "manual": forced,
            "tail": tail,
            "before": before,
        }
        emit(db, run_id, "context.compaction_started", {"manual": forced})
        return stamp


def _begin_summary(run_id):
    with Session(engine()) as db, db.begin():
        run = owned_run(db, run_id)
        if run.cancel_requested_at:
            raise asyncio.CancelledError
        from workbench.modules.collaboration.service import budget_check

        interrupted = list(
            db.scalars(
                select(Attempt)
                .join(Step, Attempt.step_id == Step.id)
                .where(
                    Attempt.run_id == run_id,
                    Attempt.status == "running",
                    Step.kind == "context_compaction",
                )
            )
        )
        for old in interrupted:
            old.status, old.finished_at = "failed", now()
            old.error = {
                "code": "context_summary_interrupted",
                "message": "此前压缩中断，用量可能不完整。",
            }
            db.get(Step, old.step_id).status = "failed"
        budget_check(db, run)
        identifier = new_id()
        step = Step(
            id=new_id(),
            run_id=run_id,
            logical_key=f"context:{identifier}",
            kind="context_compaction",
        )
        db.add(step)
        db.flush()
        attempt = Attempt(id=identifier, run_id=run_id, step_id=step.id, status="running")
        db.add(attempt)
        db.flush()
        db.add(
            UsageRecord(
                user_id=run.user_id,
                workspace_id=run.workspace_id,
                run_id=run_id,
                attempt_id=identifier,
                model_connection_id=run.model_connection_id,
                metrics={
                    "source": "provider",
                    "usage_known": False,
                    "purpose": "context_compaction",
                    "state": "started",
                },
            )
        )
        return identifier


def _finish_summary(run_id, attempt_id, metrics, error=None):
    with Session(engine()) as db, db.begin():
        owned_run(db, run_id)
        attempt = db.get(Attempt, attempt_id)
        attempt.finished_at, attempt.status = now(), "failed" if error else "succeeded"
        attempt.error = {"code": error.code, "message": error.message} if error else None
        db.get(Step, attempt.step_id).status = attempt.status
        usage = db.scalar(select(UsageRecord).where(UsageRecord.attempt_id == attempt.id))
        usage.metrics = {**metrics, "purpose": "context_compaction"}
        emit(db, run_id, "usage.updated", {"attempt_id": attempt.id, "metrics": usage.metrics})


def _failed(run_id, error):
    with Session(engine()) as db, db.begin():
        run = owned_run(db, run_id)
        row = state(db, run.thread_id, lock=True)
        if row and row.request_run_id == run.id:
            row.compaction_status = "queued" if error.kind == "steering" else "failed"
            row.compaction_error = None if error.kind == "steering" else error.message
        emit(
            db, run_id, "context.compaction_failed", {"code": error.code, "message": error.message}
        )


def _stage(run_id, stamp, summary):
    from workbench.modules.context.compaction import publish_state

    with Session(engine()) as db, db.begin():
        run = owned_run(db, run_id)
        content = ("# 对话上下文摘要\n\n" + summary + "\n").encode()
        digest = hashlib.sha256(content).hexdigest()
        provenance = hashlib.sha256(
            json.dumps(
                {
                    "sources": stamp["sources"],
                    "covered": stamp["covered"],
                    "memory": stamp["memory_fingerprint"],
                    "recipient": stamp["transfer_scope"],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        identifier = str(uuid5(UUID(run.id), f"semantic-context:{digest}:{provenance}"))
        if db.get(Artifact, identifier) is None:
            db.add(
                Artifact(
                    id=identifier,
                    user_id=run.user_id,
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    display_name="STATE.md",
                    mime_type="text/markdown",
                    byte_size=len(content),
                    content_hash=digest,
                    storage_path=f"workspaces/{run.workspace_id}/artifacts/{identifier}/STATE.md",
                    kind="task_state",
                    metadata_json={
                        "_pending_markdown": content.decode(),
                        "semantic_summary": True,
                        "memory_fingerprint": stamp["memory_fingerprint"],
                        "source": "model_summary",
                        "source_message_fingerprints": stamp["sources"],
                    },
                )
            )
    publish_state(run_id, identifier)
    return identifier


def _commit(run_id, stamp, summary, artifact_id, after, source, included):
    with Session(engine()) as db, db.begin():
        run = owned_run(db, run_id)
        row = state(db, run.thread_id, lock=True)
        from workbench.modules.collaboration.inbox import has_new_directive

        if run.cancel_requested_at:
            raise asyncio.CancelledError
        if (
            row.summary_version != stamp["summary_version"]
            or row.request_id != stamp["request_id"]
            or row.request_run_id != run.id
            or memory_fingerprint(db, run) != stamp["memory_fingerprint"]
            or transfer_scope(db, run) != stamp["transfer_scope"]
            or has_new_directive(db, run_id, included)
        ):
            raise AppError(
                "generation_steered", "要求或来源已更新，正在重新整理上下文。", kind="steering"
            )
        row.summary, row.covered_messages = summary, stamp["covered"]
        row.summary_sources, row.transfer_scope = stamp["sources"], stamp["transfer_scope"]
        row.memory_fingerprint = stamp["memory_fingerprint"]
        row.state_artifact_id, row.summary_version = artifact_id, row.summary_version + 1
        row.last_compaction_at, row.before_tokens, row.after_tokens = now(), stamp["before"], after
        row.used_tokens, row.source, row.updated_at = after, source, now()
        row.compaction_status, row.compaction_error = "succeeded", None
        manifest = {
            "state_artifact_id": artifact_id,
            "semantic_summary": True,
            "before_tokens": stamp["before"],
            "estimated_input_tokens": after,
            "source": source,
            "summary_version": row.summary_version,
        }
        run.config_snapshot = {**run.config_snapshot, "context_compaction": manifest}
        emit(db, run_id, "context.compacted", manifest)


async def _summary_generation(run_id, config, messages, included):
    from workbench.modules.models.providers import abort_generation
    from workbench.modules.models.steering import events

    attempt_id = await asyncio.to_thread(_begin_summary, run_id)
    config.request_id = attempt_id
    metrics = {"source": "provider", "usage_known": False}
    started = time.monotonic()
    output, finished = "", False
    try:
        async with aclosing(events(config, messages, [], run_id, included)) as stream:
            async for event in stream:
                if event.kind == "text_delta":
                    output += event.data["delta"]
                elif event.kind == "usage":
                    metrics.update(event.data)
                    metrics["usage_known"] = event.data.get("input_tokens_total") is not None
                elif event.kind == "tool_call":
                    raise AppError(
                        "context_summary_tool", "压缩未返回纯文本摘要，原上下文保持。", 502
                    )
                elif event.kind == "done":
                    metrics["finish_reason"] = event.data.get("finish_reason")
                    finished = event.data.get("finish_reason") in {
                        "stop",
                        "end_turn",
                    } and not event.data.get("partial_tool_calls")
        if not finished or not output.strip():
            raise AppError("context_summary_incomplete", "摘要未完整生成，原上下文保持。", 502)
    except BaseException as caught:
        if isinstance(caught, asyncio.CancelledError):
            await abort_generation(config)
        error = (
            caught
            if isinstance(caught, AppError)
            else AppError("context_summary_interrupted", "压缩生成已中断，原上下文保持。", 502)
        )
        metrics["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
        await asyncio.to_thread(_finish_summary, run_id, attempt_id, metrics, error)
        raise
    metrics["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
    await asyncio.to_thread(_finish_summary, run_id, attempt_id, metrics)
    return output.strip()


async def _summarize_batches(run_id, config, messages, included, window):
    from workbench.modules.context.compaction import units
    from workbench.modules.models.engine_adapters import count_input_tokens

    clean = [{"role": "system", "content": SUMMARY_PROMPT}]
    goal = next(
        (
            item
            for item in reversed(messages)
            if item.get("role") == "user" and item.get("source_kind") in {None, "user_steering"}
        ),
        None,
    )
    goal_content = goal.get("content", "") if goal else ""
    if isinstance(goal_content, list):
        goal_content = "\n".join(
            block.get("text", "[非文本附件保留在原消息]") for block in goal_content
        )
    guidance = {
        "role": "user",
        "content": "当前用户目标，仅用于选择摘要应保留的信息，不在此执行：\n" + goal_content,
    }
    for item in messages[1:]:
        if not durable(item) and item.get("source_kind") != "task_state":
            continue
        data = {
            key: value for key, value in item.items() if key not in {"provider_output", "reasoning"}
        }
        if isinstance(data.get("content"), list):
            data["content"] = "\n".join(
                block.get("text", "[非文本附件保留在原消息，必要时重新读取]")
                for block in data["content"]
            )
        clean.append(data)
    groups = [group for _, group in units(clean)]
    previous = ""
    while groups:
        size = len(groups)
        while True:
            batch = [clean[0], guidance]
            if previous:
                batch.append(summary_message(previous))
            batch.extend(item for group in groups[:size] for item in group)
            count, _ = await asyncio.to_thread(count_input_tokens, config, batch, [])
            if count + config.max_output_tokens + 256 <= window:
                break
            if size == 1:
                if window - count - 256 >= 64:
                    break
                raise AppError(
                    "context_summary_budget",
                    "单条目标或完整工具记录超过压缩空间，请按文件片段整理；原上下文保持。",
                    422,
                )
            size = max(1, size // 2)
        batch_config = replace(
            config, max_output_tokens=min(config.max_output_tokens, window - count - 256)
        )
        previous = await _summary_generation(run_id, batch_config, batch, included)
        groups = groups[size:]
    return previous


async def compact_if_needed(run_id, config, messages, tools, window, before):
    from workbench.modules.models.engine_adapters import count_input_tokens

    reserve = (
        min(8192, window // 4)
        if getattr(config, "automatic_output", True)
        else config.max_output_tokens
    )
    stamp = await asyncio.to_thread(_plan, run_id, messages, tools, window, before, reserve)
    if stamp is None:
        return False
    included = [item["_input_message_id"] for item in messages if item.get("_input_message_id")]
    summary_config = replace(config, max_output_tokens=min(8192, window // 4))
    try:
        summary_config.api_key = await asyncio.to_thread(decrypt, config.credential_ciphertext)
        output = await _summarize_batches(run_id, summary_config, messages, included, window)
        candidate = [messages[0], summary_message(output), *stamp["tail"]]
        after, source = await asyncio.to_thread(count_input_tokens, config, candidate, tools)
        if after >= before:
            raise AppError("context_summary_not_smaller", "当前可压缩历史较少，保留原上下文。", 409)
        artifact_id = await asyncio.to_thread(_stage, run_id, stamp, output)
        await asyncio.to_thread(
            _commit, run_id, stamp, output, artifact_id, after, source, included
        )
        return True
    except (AppError, asyncio.CancelledError) as caught:
        if isinstance(caught, AppError) and caught.kind == "ownership":
            raise
        error = (
            caught
            if isinstance(caught, AppError)
            else AppError("cancelled", "压缩已停止，原上下文保持。")
        )
        await asyncio.to_thread(_failed, run_id, error)
        if isinstance(caught, asyncio.CancelledError) or error.kind == "steering":
            raise
        if not stamp["manual"]:
            raise AppError("context_compaction_failed", error.message, 422, "pause") from error
        return False
    except Exception as caught:
        error = AppError("context_compaction_failed", "压缩未完成，原上下文保持，请重试。", 502)
        await asyncio.to_thread(_failed, run_id, error)
        if not stamp["manual"]:
            raise AppError(error.code, error.message, 502, "pause") from caught
        return False
    finally:
        summary_config.api_key = None


async def process_idle(run_id):
    from workbench.modules.models.admission import model_permit
    from workbench.modules.models.engine_adapters import count_input_tokens
    from workbench.modules.models.execution import configuration
    from workbench.modules.runs.scheduler import initial_state

    async with model_permit(run_id):
        while True:
            try:
                original = await asyncio.to_thread(initial_state, run_id)
                config, tools, messages, _ = await asyncio.to_thread(
                    configuration, run_id, original["messages"], 0, planning=True
                )
                count, method = await asyncio.to_thread(count_input_tokens, config, messages, tools)
                await asyncio.to_thread(record_input, run_id, count, method, config.context_window)
                await compact_if_needed(
                    run_id, config, messages, tools, config.context_window, count
                )
                break
            except AppError as error:
                if error.kind != "steering":
                    raise
    with Session(engine()) as db:
        run = db.get(Run, run_id)
        row = state(db, run.thread_id)
        if row and row.compaction_status == "failed":
            raise AppError("context_compaction_failed", row.compaction_error, 502)
    return {}
