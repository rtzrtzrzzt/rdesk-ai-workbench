import asyncio
import json
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.command_limits import DEFAULT_COMMAND_TIMEOUT_SECONDS
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.infrastructure.secrets import decrypt
from workbench.modules.context.compaction import estimate
from workbench.modules.context.compiler import compile_context
from workbench.modules.conversation_service import message_view
from workbench.modules.conversations import Message
from workbench.modules.files.inputs import compile_inputs, is_remote
from workbench.modules.models.domain import ModelConnection, ModelRevision
from workbench.modules.models.providers import ProviderConfig
from workbench.modules.models.service import freeze, validate_endpoint
from workbench.modules.runs.domain import Attempt, Run, Step
from workbench.modules.runs.service import emit
from workbench.modules.tools.registry import definitions
from workbench.modules.usage import UsageRecord

MAX_AUTOMATIC_CONTINUATIONS = 16


def begin_attempt(
    run_id: str, round_number: int, input_message_ids: list[str]
) -> tuple[str, str, dict | None]:
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run.cancel_requested_at:
            raise asyncio.CancelledError
        from workbench.modules.collaboration.service import budget_check

        step = db.scalar(
            select(Step).where(
                Step.run_id == run_id,
                Step.logical_key == f"model:{round_number}",
            )
        )
        if step is None:
            step = Step(run_id=run_id, logical_key=f"model:{round_number}", kind="model")
            db.add(step)
            db.flush()
            emit(db, run_id, "step.started", {"step_id": step.id, "kind": "model"})
        attempts = list(
            db.scalars(
                select(Attempt)
                .where(
                    Attempt.step_id == step.id,
                )
                .order_by(Attempt.number)
            )
        )
        if step.status == "succeeded" and attempts:
            message = db.get(Message, attempts[-1].message_id)
            return attempts[-1].id, message.id, message.provider_message
        for attempt in attempts:
            if attempt.status in {"created", "running"}:
                attempt.status = "failed"
                attempt.finished_at = now()
                attempt.error = {
                    "code": "execution_interrupted",
                    "message": "先前执行中断，用量可能不完整。",
                }
                message = db.get(Message, attempt.message_id)
                if message:
                    message.status = "interrupted"
        attempt_limit = (
            run.config_snapshot.get("runtime_options", {}).get("model_retry_limit", 2)
            + 1
            + run.config_snapshot.get("safety", {}).get("manual_retry_grants", 0)
        )
        counted = sum(
            not (item.error or {}).get("auto_continued")
            and not (item.error or {}).get("auto_corrected")
            and (item.error or {}).get("code") not in {"generation_steered", "generation_yielded"}
            for item in attempts
        )
        if counted >= attempt_limit:
            raise AppError("attempt_limit", "本步骤已达到尝试上限，请检查服务后明确重试任务。", 409)
        budget_check(db, run)
        message = Message(
            thread_id=run.thread_id, run_id=run_id, role="assistant", content="", status="streaming"
        )
        db.add(message)
        db.flush()
        attempt = Attempt(
            run_id=run_id,
            step_id=step.id,
            number=len(attempts) + 1,
            status="running",
            message_id=message.id,
            input_message_ids=input_message_ids,
        )
        db.add(attempt)
        db.flush()
        from workbench.modules.collaboration.domain import AgentMessage

        processing = list(
            db.scalars(
                select(AgentMessage.id).where(
                    AgentMessage.to_run_id == run_id,
                    AgentMessage.source == "user",
                    AgentMessage.id.in_(input_message_ids),
                    AgentMessage.delivered_attempt_id.is_(None),
                )
            )
        )
        if processing:
            emit(
                db,
                run_id,
                "run.input_processing",
                {"message_ids": processing, "attempt_id": attempt.id},
            )
        db.add(
            UsageRecord(
                user_id=run.user_id,
                workspace_id=run.workspace_id,
                run_id=run_id,
                attempt_id=attempt.id,
                model_connection_id=run.model_connection_id,
                metrics={"source": "provider", "usage_known": False, "state": "started"},
            )
        )
        emit(
            db,
            run_id,
            "attempt.started",
            {
                "attempt_id": attempt.id,
                "step_id": step.id,
                "message_id": message.id,
                "number": attempt.number,
            },
        )
        return attempt.id, message.id, None


def append_text(run_id: str, message_id: str, delta: str, *, reasoning: bool = False) -> None:
    if not delta:
        return
    with Session(engine()) as db, db.begin():
        db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        message = db.get(Message, message_id)
        if reasoning:
            message.reasoning += delta
        else:
            message.content += delta
        emit(
            db,
            run_id,
            "message.reasoning_delta" if reasoning else "message.delta",
            {"message_id": message_id, "delta": delta},
        )


def record_tool_preparation(run_id: str, attempt_id: str, index: int, name: str) -> None:
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        attempt = db.get(Attempt, attempt_id)
        if run.cancel_requested_at or attempt.status != "running":
            return
        emit(
            db,
            run_id,
            "tool.preparing",
            {"attempt_id": attempt_id, "index": index, "name": name},
        )


def finish_attempt(
    run_id: str,
    attempt_id: str,
    message_id: str,
    *,
    assistant: dict | None,
    metrics: dict,
    error: AppError | None = None,
) -> AppError | None:
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        attempt = db.get(Attempt, attempt_id)
        message = db.get(Message, message_id)
        from workbench.modules.collaboration.inbox import has_new_directive

        if (error is None or error.code == "model_output_limit") and has_new_directive(
            db, run_id, attempt.input_message_ids
        ):
            error = AppError(
                "generation_steered", "已收到新要求，正在调整当前回复。", kind="steering"
            )
        superseded = error is not None and error.code in {
            "generation_steered",
            "generation_yielded",
        }
        attempt.finished_at = now()
        attempt.status = "succeeded" if error is None else "superseded" if superseded else "failed"
        attempt.error = (
            {
                "code": error.code,
                "message": error.message,
                **({"details": error.details} if error.details else {}),
            }
            if error
            else None
        )
        message.status = (
            "completed" if error is None else "superseded" if superseded else "interrupted"
        )
        message.provider_message = None if superseded else assistant
        step = db.get(Step, attempt.step_id)
        if not error:
            step.status = "succeeded"
            guard = dict(run.config_snapshot.get("safety", {}))
            guard.pop("continue_partial_output", None)
            run.config_snapshot = {**run.config_snapshot, "safety": guard}
            from workbench.modules.collaboration.inbox import mark_delivered

            delivered = mark_delivered(db, run_id, attempt)
            if delivered:
                emit(
                    db,
                    run_id,
                    "run.input_applied",
                    {
                        "message_ids": delivered,
                        "attempt_id": attempt.id,
                    },
                )
        usage = db.scalar(select(UsageRecord).where(UsageRecord.attempt_id == attempt_id))
        if usage:
            usage.metrics = metrics
        else:
            db.add(
                UsageRecord(
                    user_id=run.user_id,
                    workspace_id=run.workspace_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    model_connection_id=run.model_connection_id,
                    metrics=metrics,
                )
            )
        from workbench.modules.context.service import record_usage

        record_usage(db, run, metrics)
        emit(db, run_id, "message.completed", {"message": message_view(message)})
        emit(
            db,
            run_id,
            "attempt.finished",
            {
                "attempt_id": attempt_id,
                "step_id": step.id,
                "status": attempt.status,
                "error": attempt.error,
            },
        )
        emit(db, run_id, "usage.updated", {"attempt_id": attempt_id, "metrics": metrics})
        if not error:
            emit(db, run_id, "step.finished", {"step_id": step.id, "status": "succeeded"})
        return error


def configuration(
    run_id: str, messages: list[dict], round_number: int = 0, *, planning: bool = False
) -> tuple[ProviderConfig, list[dict], list[dict], str | None]:
    graph_inputs = {item.get("_input_message_id") for item in messages}
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        model = db.get(ModelConnection, run.model_connection_id)
        revision = (
            db.get(ModelRevision, run.config_snapshot.get("model_revision_id"))
            if run.config_snapshot.get("model_revision_id")
            else None
        )
        if revision is None:
            revision = freeze(db, run.user_id, model.id)
            run.config_snapshot = {**run.config_snapshot, "model_revision_id": revision.id}
        frozen = revision.configuration
        from workbench.modules.automation.visual import add_latest_observation

        messages = add_latest_observation(db, run, [dict(item) for item in messages], frozen)
        from workbench.modules.media.visual import add_latest_output

        messages = add_latest_output(db, run, messages, frozen)
        if is_remote(frozen["endpoint"]) and not model.allow_remote:
            raise AppError(
                "remote_permission_revoked",
                "此连接已撤回外部传输授权，任务已停在下一次请求前。",
                403,
                "policy",
            )
        validate_endpoint(frozen["endpoint"], frozen["allow_remote"])
        generation = {
            **frozen["generation"],
            **run.config_snapshot.get("generation_overrides", {}),
        }
        output_limit = run.config_snapshot.get("max_output_tokens", 2048)
        automatic_output = output_limit is None
        output_reserve = (
            min(8192, frozen["context_window"] // 4) if automatic_output else output_limit
        )
        config = ProviderConfig(
            provider_type=frozen["provider_type"],
            endpoint=frozen["endpoint"],
            model_identifier=frozen["model_identifier"],
            credential_ciphertext=revision.encrypted_api_key,
            max_output_tokens=output_reserve,
            context_window=frozen["context_window"],
            timeout_seconds=generation["timeout_seconds"],
            temperature=generation["temperature"],
            cache_observation=generation.get("cache_observation", "standard"),
            engine_kind=generation.get("engine_kind", "auto"),
            thinking_mode=generation.get("thinking_mode", "default"),
            reasoning_effort=generation.get("reasoning_effort"),
            reasoning_adapter=generation.get("reasoning_adapter", "auto"),
            native_reasoning_efforts=tuple(generation.get("native_reasoning_efforts", [])),
            prompt_template=generation.get("prompt_template"),
        )
        from workbench.modules.tools.selection import for_run as selected_tools

        names = selected_tools(db, run)
        if names != run.config_snapshot.get("tool_names"):
            run.config_snapshot = {**run.config_snapshot, "tool_names": names}
        tools = definitions(
            names,
            command_timeout_seconds=run.config_snapshot.get("runtime_options", {}).get(
                "command_timeout_seconds", DEFAULT_COMMAND_TIMEOUT_SECONDS
            ),
        )
        tool_capability = frozen["capabilities"].get("tool_calling", {})
        if tool_capability.get("support") == "unsupported" or not tool_capability.get(
            "enabled", True
        ):
            tools = []
        continue_output = run.config_snapshot.get("safety", {}).get("continue_partial_output")
        partial_codes = ["generation_steered", "generation_yielded"] + (
            ["model_output_limit"] if continue_output else []
        )
        if partial_codes:
            partials = list(
                db.scalars(
                    select(Message)
                    .join(Attempt, Attempt.message_id == Message.id)
                    .join(Step, Step.id == Attempt.step_id)
                    .where(
                        Attempt.run_id == run_id,
                        Step.logical_key == f"model:{round_number}",
                        Attempt.error["code"].astext.in_(partial_codes),
                    )
                    .order_by(Message.created_at, Message.id)
                )
            )
            for partial in partials:
                if partial.content.strip():
                    messages += [{"role": "assistant", "content": partial.content}]
            if partials:
                messages += [
                    {
                        "role": "user",
                        "source_kind": "runtime_continuation",
                        "content": (
                            "此前回复未完整结束，已显示的文字保留如下。"
                            "请以用户最新要求为准继续任务，避免重复已有内容；"
                            "未完整提交的工具调用尚未执行。"
                        ),
                    }
                ]
            config.continuation_messages = [
                {"role": "assistant", "content": row.content, "_message_id": row.id}
                for row in partials
                if row.content.strip()
            ]
        messages = compile_context(db, run, messages, tools)
        # Keep accepted mail at this boundary in the next graph checkpoint.
        # Otherwise every later request appends old instructions at the end
        # again, making them look like a newly received user command.
        new_inputs = [
            dict(item)
            for item in messages
            if item.get("_input_message_id") and item["_input_message_id"] not in graph_inputs
        ]
        if new_inputs:
            config.continuation_messages = [
                *getattr(config, "continuation_messages", []),
                *new_inputs,
            ]
        notice = run.config_snapshot.get("safety", {}).get("notice")
        if notice:
            messages = messages + [
                {
                    "role": "user",
                    "source_kind": "runtime_guard",
                    "content": f"工作台执行提醒：{notice}",
                }
            ]
        from workbench.modules.context.window import apply_window

        messages = apply_window(db, run, messages)
        messages, native_reserve = compile_inputs(db, run, messages, frozen)
        config.native_reserve = native_reserve
        config.automatic_output = automatic_output
        config.context_trigger_ratio = run.config_snapshot.get("runtime_options", {}).get(
            "context_target_ratio", 0.9
        )
        if automatic_output and not planning:
            # estimate() is a conservative input estimate, not measured token
            # usage. Keep room for provider framing without a separate small cap.
            config.max_output_tokens = (
                frozen["context_window"] - estimate(messages, tools) - native_reserve - 256
            )
            if config.max_output_tokens < 64:
                raise AppError(
                    "context_budget",
                    "当前上下文已无足够生成空间，请缩小输入或继续压缩历史。",
                    422,
                    "budget",
                )
        if not planning and (
            estimate(messages, tools) + native_reserve + config.max_output_tokens
            > frozen["context_window"]
        ):
            raise AppError(
                "context_budget",
                "当前上下文超过配置预算，请缩小范围或调整模型上下文。",
                422,
                "budget",
            )
    return config, tools, messages, None


async def prepare_configuration(run_id, source_messages, round_number):
    """Count and summarize outside database locks, then recheck the live boundary."""
    from workbench.modules.context.service import compact_if_needed, record_input
    from workbench.modules.models.engine_adapters import count_input_tokens

    config, tools, messages, _ = await asyncio.to_thread(
        configuration, run_id, source_messages, round_number, planning=True
    )
    count, source = await asyncio.to_thread(count_input_tokens, config, messages, tools)
    if await compact_if_needed(run_id, config, messages, tools, config.context_window, count):
        config, tools, messages, _ = await asyncio.to_thread(
            configuration, run_id, source_messages, round_number, planning=True
        )
        count, source = await asyncio.to_thread(count_input_tokens, config, messages, tools)
    # Automatic output uses the actual rendered input when the engine can count
    # it. The fallback is explicitly estimated and retains media reserves.
    remaining = config.context_window - count - getattr(config, "native_reserve", 0) - 256
    if config.automatic_output:
        config.max_output_tokens = remaining
    if config.max_output_tokens < 64 or config.max_output_tokens > remaining:
        raise AppError(
            "context_budget", "当前上下文没有足够生成空间，请压缩历史后继续。", 422, "pause"
        )
    await asyncio.to_thread(record_input, run_id, count, source, config.context_window)
    return config, tools, messages


def prepare_protocol_correction(run_id, attempt_id, error):
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        attempt = db.get(Attempt, attempt_id)
        attempt.error = {**(attempt.error or {}), "auto_corrected": True}
        guard = dict(run.config_snapshot.get("safety", {}))
        guard["notice"] = (
            "上一条回复的工具调用格式或名称不符合当前提供的工具定义，整批尚未执行。"
            "保留此前已完成结果；仅调用本次tools中的工具，缺少schema时先discover_tools，"
            "收到下一轮工具定义后再调用，不重复此前已完成的操作。"
        )
        run.config_snapshot = {**run.config_snapshot, "safety": guard}
        emit(db, run_id, "run.protocol_correcting", {"code": error.code, "attempt_id": attempt_id})


def report_retry(run_id: str, error: AppError, delay: float) -> None:
    with Session(engine()) as db, db.begin():
        emit(
            db,
            run_id,
            "error.reported",
            {
                "code": error.code,
                "message": error.message,
                "retrying": True,
                "retry_after_ms": round(delay * 1000),
            },
        )


def cached_output(run_id, round_number):
    with Session(engine()) as db:
        message = db.scalar(
            select(Message)
            .join(Attempt, Attempt.message_id == Message.id)
            .join(Step, Step.id == Attempt.step_id)
            .where(
                Step.run_id == run_id,
                Step.logical_key == f"model:{round_number}",
                Step.status == "succeeded",
                Attempt.status == "succeeded",
            )
            .order_by(Attempt.number.desc())
            .limit(1)
        )
        return message.provider_message if message else None


async def generate(run_id: str, messages: list[dict], round_number: int) -> dict:
    from workbench.modules.models.admission import model_permit

    existing = await asyncio.to_thread(cached_output, run_id, round_number)
    if existing is not None:
        return existing
    while True:
        try:
            async with model_permit(run_id):
                while True:
                    try:
                        return await generate_permitted(run_id, messages, round_number)
                    except AppError as error:
                        if error.code != "generation_steered":
                            raise
                        # User steering keeps this generation slot so the
                        # updated request need not wait behind long child turns.
        except AppError as error:
            if error.code == "generation_yielded":
                continue
            if error.code != "model_output_limit" or not await asyncio.to_thread(
                prepare_continuation, run_id, round_number
            ):
                raise
        # Each continuation is a real attempt and re-enters the admission queue.


def prepare_continuation(run_id: str, round_number: int) -> bool:
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        revision = db.get(ModelRevision, run.config_snapshot.get("model_revision_id"))
        if (
            run.cancel_requested_at
            or not revision
            or revision.configuration["provider_type"] != "openai_compatible"
        ):
            return False
        attempts = list(
            db.scalars(
                select(Attempt)
                .join(Step, Step.id == Attempt.step_id)
                .where(Step.run_id == run_id, Step.logical_key == f"model:{round_number}")
                .order_by(Attempt.number)
            )
        )
        if (
            not attempts
            or sum(bool((item.error or {}).get("auto_continued")) for item in attempts)
            >= MAX_AUTOMATIC_CONTINUATIONS
        ):
            return False
        latest = attempts[-1]
        usage = db.scalar(select(UsageRecord).where(UsageRecord.attempt_id == latest.id))
        message = db.get(Message, latest.message_id)
        if (
            (latest.error or {}).get("code") != "model_output_limit"
            or not message
            or not message.content.strip()
            or not usage
            or usage.metrics.get("partial_tool_calls")
        ):
            return False
        previous = [db.get(Message, item.message_id) for item in attempts[:-1]]
        if len(message.content.strip()) >= 80 and any(
            item and message.content.strip() in item.content for item in previous
        ):
            return False
        latest.error = {**latest.error, "auto_continued": True}
        message.status = "continued"
        guard = {**run.config_snapshot.get("safety", {}), "continue_partial_output": True}
        run.config_snapshot = {**run.config_snapshot, "safety": guard}
        emit(db, run_id, "run.output_continuing", {"message_id": message.id})
        return True


async def generate_permitted(run_id, source_messages, round_number):
    with Session(engine()) as db:
        options = db.get(Run, run_id).config_snapshot.get("runtime_options", {})
        maximum = options.get("max_model_rounds", 1024)
        retry_limit = options.get("model_retry_limit", 2)
        retry_backoff = options.get("retry_backoff_seconds", 1)
    if round_number >= maximum:
        raise AppError(
            "round_limit", "任务已达到模型调用轮次上限，请核对已完成结果。", 409, "budget"
        )
    retry, corrections = 0, 0
    while True:
        # Rebuild from the uncompiled graph history after waiting and on every
        # retry. Frozen model settings stay fixed; live consent, memory versions,
        # attachments and newly accepted user input must be checked again.
        config, tools, messages = await prepare_configuration(run_id, source_messages, round_number)
        config.api_key = await asyncio.to_thread(decrypt, config.credential_ciphertext)
        attempt_id, message_id, existing = await asyncio.to_thread(
            begin_attempt,
            run_id,
            round_number,
            [
                message["_input_message_id"]
                for message in messages
                if message.get("_input_message_id")
            ],
        )
        if existing is not None:
            config.api_key = None
            return existing
        started = time.monotonic()
        config.request_id = attempt_id
        metrics = {
            "source": "provider",
            "usage_known": False,
            "started_at": now().isoformat(),
            "requested_output_tokens": config.max_output_tokens,
        }
        buffer = ""
        flushed = started
        full_text = ""
        reasoning_buffer = ""
        reasoning_length = 0
        reasoning_flushed = started
        calls = []
        preparing_tools = set()
        provider_output = None
        phase = None
        finished = False
        finalized = False
        response_events = None
        try:
            from workbench.modules.models.steering import events

            response_events = events(
                config,
                messages,
                tools,
                run_id,
                [
                    message["_input_message_id"]
                    for message in messages
                    if message.get("_input_message_id")
                ],
            )
            async for event in response_events:
                if event.kind == "text_delta":
                    delta = event.data["delta"]
                    if len(full_text) + len(buffer) + len(delta) > 2_000_000:
                        raise AppError("output_budget", "模型输出超出本次任务容量。", 422, "budget")
                    buffer += delta
                    if "first_visible_ms" not in metrics:
                        metrics["first_visible_ms"] = round((time.monotonic() - started) * 1000, 2)
                    if len(buffer) > 512 or time.monotonic() - flushed > 0.15:
                        await asyncio.to_thread(append_text, run_id, message_id, buffer)
                        full_text += buffer
                        buffer = ""
                        flushed = time.monotonic()
                elif event.kind == "reasoning_delta":
                    delta = event.data["delta"]
                    if reasoning_length + len(reasoning_buffer) + len(delta) > 2_000_000:
                        raise AppError("output_budget", "模型思考超过本次任务容量。", 422, "budget")
                    reasoning_buffer += delta
                    if len(reasoning_buffer) > 512 or time.monotonic() - reasoning_flushed > 0.15:
                        await asyncio.to_thread(
                            append_text, run_id, message_id, reasoning_buffer, reasoning=True
                        )
                        reasoning_length += len(reasoning_buffer)
                        reasoning_buffer = ""
                        reasoning_flushed = time.monotonic()
                elif event.kind == "message_phase":
                    phase = event.data["phase"]
                elif event.kind == "tool_preparing":
                    key = (event.data["index"], event.data["name"])
                    if key not in preparing_tools:
                        # Flush preceding commentary before starting its tool row.
                        await asyncio.to_thread(append_text, run_id, message_id, buffer)
                        full_text += buffer
                        buffer = ""
                        await asyncio.to_thread(
                            append_text, run_id, message_id, reasoning_buffer, reasoning=True
                        )
                        reasoning_length += len(reasoning_buffer)
                        reasoning_buffer = ""
                        await asyncio.to_thread(record_tool_preparation, run_id, attempt_id, *key)
                        preparing_tools.add(key)
                elif event.kind == "tool_call":
                    calls.append(event.data)
                elif event.kind == "provider_output":
                    provider_output = event.data
                elif event.kind == "usage":
                    metrics.update(event.data)
                    metrics["usage_known"] = event.data.get("input_tokens_total") is not None
                elif event.kind == "done":
                    metrics["finish_reason"] = event.data.get("finish_reason")
                    metrics["partial_tool_calls"] = event.data.get("partial_tool_calls", False)
                    finished = True
            if not finished:
                raise AppError(
                    "model_stream_interrupted", "模型流在完成前断开。", 502, "transient", True
                )
            if metrics.get("finish_reason") in {
                "length",
                "max_tokens",
                "max_output_tokens",
                "incomplete",
            }:
                raise AppError(
                    "model_output_limit",
                    "模型达到输出上限，已保留部分结果，请调整生成预算后继续。",
                    422,
                    "budget",
                )
            if not (full_text + buffer).strip() and not calls:
                raise AppError(
                    "model_empty_output",
                    "模型没有返回可见内容或工具调用，请检查推理参数与服务日志。",
                    502,
                    "protocol",
                )
            await asyncio.to_thread(append_text, run_id, message_id, buffer)
            await asyncio.to_thread(
                append_text, run_id, message_id, reasoning_buffer, reasoning=True
            )
            full_text += buffer
            buffer = reasoning_buffer = ""
            assistant = {
                "role": "assistant",
                "content": full_text,
                "_run_id": run_id,
                "_message_id": message_id,
            }
            if phase:
                assistant["phase"] = phase
            if getattr(config, "continuation_messages", None):
                assistant["_continuation_messages"] = config.continuation_messages
            if calls:
                assistant["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                        },
                    }
                    for call in calls
                ]
            if provider_output:
                assistant["provider_output"] = provider_output
            metrics["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
            completion_error = await asyncio.to_thread(
                finish_attempt, run_id, attempt_id, message_id, assistant=assistant, metrics=metrics
            )
            finalized = True
            if completion_error:
                raise completion_error
            return assistant
        except asyncio.CancelledError:
            await asyncio.to_thread(append_text, run_id, message_id, buffer)
            await asyncio.to_thread(
                append_text, run_id, message_id, reasoning_buffer, reasoning=True
            )
            metrics["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
            await asyncio.to_thread(
                finish_attempt,
                run_id,
                attempt_id,
                message_id,
                assistant=None,
                metrics=metrics,
                error=AppError("cancelled", "本次生成已停止，用量可能不完整。"),
            )
            raise
        except AppError as error:
            if not finalized:
                await asyncio.to_thread(append_text, run_id, message_id, buffer)
                await asyncio.to_thread(
                    append_text, run_id, message_id, reasoning_buffer, reasoning=True
                )
                metrics["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
                completion_error = await asyncio.to_thread(
                    finish_attempt,
                    run_id,
                    attempt_id,
                    message_id,
                    assistant=None,
                    metrics=metrics,
                    error=error,
                )
                error = completion_error or error
            if error.code == "provider_tool_identity" and not calls:
                if corrections == 0:
                    corrections += 1
                    await asyncio.to_thread(prepare_protocol_correction, run_id, attempt_id, error)
                    continue
                raise AppError(
                    error.code,
                    "工具调用未能纠正，已保留结果；可继续任务重新规划。",
                    502,
                    "pause",
                    details=error.details,
                ) from error
            if not error.retryable or retry >= retry_limit:
                raise error
            delay = retry_backoff * (retry + 1)
            await asyncio.to_thread(report_retry, run_id, error, delay)
            retry += 1
            await asyncio.sleep(delay)
        finally:
            if response_events is not None:
                await response_events.aclose()
            config.api_key = None
    raise RuntimeError("Unreachable attempt state")
