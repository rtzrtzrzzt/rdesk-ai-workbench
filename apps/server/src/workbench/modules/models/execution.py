import asyncio
import json
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.infrastructure.secrets import decrypt
from workbench.modules.context.compaction import compact, estimate, publish_state
from workbench.modules.context.compiler import compile_context
from workbench.modules.conversation_service import message_view
from workbench.modules.conversations import Message
from workbench.modules.files.inputs import compile_inputs, is_remote
from workbench.modules.models.domain import ModelConnection, ModelRevision
from workbench.modules.models.providers import ProviderConfig, stream_model
from workbench.modules.models.service import freeze, validate_endpoint
from workbench.modules.runs.domain import Attempt, Run, Step
from workbench.modules.runs.service import emit
from workbench.modules.tools.registry import CORE_TOOL_NAMES, definitions
from workbench.modules.usage import UsageRecord


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
            run.config_snapshot.get("runtime_options", {}).get("model_retry_limit", 2) + 1
        )
        if len(attempts) >= attempt_limit:
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


def append_text(run_id: str, message_id: str, delta: str) -> None:
    if not delta:
        return
    with Session(engine()) as db, db.begin():
        db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        message = db.get(Message, message_id)
        message.content += delta
        emit(db, run_id, "message.delta", {"message_id": message_id, "delta": delta})


def finish_attempt(
    run_id: str,
    attempt_id: str,
    message_id: str,
    *,
    assistant: dict | None,
    metrics: dict,
    error: AppError | None = None,
) -> None:
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        attempt = db.get(Attempt, attempt_id)
        message = db.get(Message, message_id)
        attempt.finished_at = now()
        attempt.status = "succeeded" if error is None else "failed"
        attempt.error = {"code": error.code, "message": error.message} if error else None
        message.status = "completed" if error is None else "interrupted"
        message.provider_message = assistant
        step = db.get(Step, attempt.step_id)
        if not error:
            step.status = "succeeded"
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


def configuration(
    run_id: str, messages: list[dict]
) -> tuple[ProviderConfig, list[dict], list[dict], str | None]:
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

        messages = add_latest_observation(db, run, messages, frozen)
        if is_remote(frozen["endpoint"]) and not model.allow_remote:
            raise AppError(
                "remote_permission_revoked",
                "此连接已撤回外部传输授权，任务已停在下一次请求前。",
                403,
                "policy",
            )
        validate_endpoint(frozen["endpoint"], frozen["allow_remote"])
        generation = frozen["generation"]
        config = ProviderConfig(
            provider_type=frozen["provider_type"],
            endpoint=frozen["endpoint"],
            model_identifier=frozen["model_identifier"],
            credential_ciphertext=revision.encrypted_api_key,
            max_output_tokens=run.config_snapshot.get("max_output_tokens", 2048),
            timeout_seconds=generation["timeout_seconds"],
            temperature=generation["temperature"],
            cache_observation=generation.get("cache_observation", "standard"),
        )
        tools = definitions(run.config_snapshot.get("tool_names", CORE_TOOL_NAMES))
        tool_capability = frozen["capabilities"].get("tool_calling", {})
        if tool_capability.get("support") == "unsupported" or not tool_capability.get(
            "enabled", True
        ):
            tools = []
        messages = compile_context(db, run, messages, tools)
        messages, state_artifact = compact(
            db, run, messages, tools, frozen["context_window"], config.max_output_tokens
        )
        messages, native_reserve = compile_inputs(db, run, messages, frozen)
        if (
            estimate(messages, tools) + native_reserve + config.max_output_tokens
            > frozen["context_window"]
        ):
            raise AppError(
                "context_budget",
                "当前上下文超过配置预算，请缩小范围或调整模型上下文。",
                422,
                "budget",
            )
    return config, tools, messages, state_artifact


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
    async with model_permit(run_id):
        return await generate_permitted(run_id, messages, round_number)


async def generate_permitted(run_id, source_messages, round_number):
    with Session(engine()) as db:
        options = db.get(Run, run_id).config_snapshot.get("runtime_options", {})
        maximum = options.get("max_model_rounds", get_settings().max_model_rounds)
        retry_limit = options.get("model_retry_limit", 2)
        retry_backoff = options.get("retry_backoff_seconds", 1)
    if round_number >= maximum:
        raise AppError(
            "round_limit", "任务已达到模型调用轮次上限，请核对已完成结果。", 409, "budget"
        )
    for retry in range(retry_limit + 1):
        # Rebuild from the uncompiled graph history after waiting and on every
        # retry. Frozen model settings stay fixed; live consent, memory versions,
        # attachments and newly accepted user input must be checked again.
        config, tools, messages, state_artifact = await asyncio.to_thread(
            configuration, run_id, source_messages
        )
        if state_artifact:
            await asyncio.to_thread(publish_state, run_id, state_artifact)
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
        metrics = {"source": "provider", "usage_known": False, "started_at": now().isoformat()}
        buffer = ""
        flushed = started
        full_text = ""
        calls = []
        provider_output = None
        finished = False
        try:
            async for event in stream_model(config, messages, tools):
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
                elif event.kind == "tool_call":
                    calls.append(event.data)
                elif event.kind == "provider_output":
                    provider_output = event.data
                elif event.kind == "usage":
                    metrics.update(event.data)
                    metrics["usage_known"] = event.data.get("input_tokens_total") is not None
                elif event.kind == "done":
                    metrics["finish_reason"] = event.data.get("finish_reason")
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
            full_text += buffer
            assistant = {"role": "assistant", "content": full_text, "_run_id": run_id}
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
            await asyncio.to_thread(
                finish_attempt, run_id, attempt_id, message_id, assistant=assistant, metrics=metrics
            )
            return assistant
        except asyncio.CancelledError:
            await asyncio.to_thread(append_text, run_id, message_id, buffer)
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
            await asyncio.to_thread(append_text, run_id, message_id, buffer)
            metrics["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
            await asyncio.to_thread(
                finish_attempt,
                run_id,
                attempt_id,
                message_id,
                assistant=None,
                metrics=metrics,
                error=error,
            )
            if not error.retryable or retry >= retry_limit:
                raise
            delay = retry_backoff * (retry + 1)
            await asyncio.to_thread(report_retry, run_id, error, delay)
            await asyncio.sleep(delay)
        finally:
            config.api_key = None
    raise RuntimeError("Unreachable attempt state")
