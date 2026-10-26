"""One caption expansion through the frozen existing local model, without a second PUP."""

import asyncio
import json
from contextlib import aclosing
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.config import PROJECT_ROOT
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.files.storage import storage_path
from workbench.modules.media import journal
from workbench.modules.media.locking import lock_run
from workbench.modules.models import providers
from workbench.modules.models.domain import ModelRevision
from workbench.modules.models.engine_adapters import supported_reasoning_efforts
from workbench.modules.models.service import get, validate_endpoint
from workbench.modules.runs.domain import Attempt, Run, Step
from workbench.modules.runs.service import emit
from workbench.modules.usage import UsageRecord


def begin(run_id, revision_id):
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id)
        run, revision = db.get(Run, run_id), db.get(ModelRevision, revision_id)
        current = get(db, run.user_id, revision.connection_id)
        frozen = revision.configuration
        validate_endpoint(frozen["endpoint"], False)
        if current.user_id != run.user_id:
            raise AppError("media_prompt_model", "提示词模型已不可访问。", 403)
        step = db.scalar(
            select(Step).where(Step.run_id == run_id, Step.logical_key == "media:prompt")
        )
        if step is None:
            step = Step(run_id=run_id, logical_key="media:prompt", kind="media_prompt")
            db.add(step)
            db.flush()
        identifier = str(uuid5(UUID(run_id), "media-prompt"))
        attempt = db.get(Attempt, identifier)
        if attempt is None:
            attempt = Attempt(id=identifier, run_id=run_id, step_id=step.id, status="running")
            db.add(attempt)
            db.flush()
            db.add(
                UsageRecord(
                    run_id=run_id,
                    user_id=run.user_id,
                    workspace_id=run.workspace_id,
                    attempt_id=identifier,
                    model_connection_id=current.id,
                    metrics={"source": "provider", "usage_known": False, "purpose": "media_prompt"},
                )
            )
        config = providers.ProviderConfig(
            provider_type=frozen["provider_type"],
            endpoint=frozen["endpoint"],
            model_identifier=frozen["model_identifier"],
            credential_ciphertext=revision.encrypted_api_key,
            max_output_tokens=4096,
            context_window=frozen["context_window"],
            timeout_seconds=frozen["generation"]["timeout_seconds"],
            engine_kind=frozen["generation"].get("engine_kind", "auto"),
            thinking_mode="disabled"
            if "disabled" in supported_reasoning_efforts(frozen)
            else "default",
            reasoning_effort=None,
            request_id=identifier,
        )
        emit(db, run_id, "media.progress", {"phase": "expanding_prompt", "status": "running"})
        return config, frozen


def with_references(run_id, messages, request, frozen):
    if not request["references"]:
        return messages
    from workbench.modules.files import links
    from workbench.modules.files.inputs import compile_inputs
    from workbench.modules.models.profile import permits

    if not permits(frozen["capabilities"], "image"):
        raise AppError(
            "media_prompt_vision_required",
            "编辑提示词优化需要本地视觉模型；可在生成模型设置中关闭自动优化后直接编辑。",
            409,
        )
    if len(request["references"]) > 2:
        raise AppError(
            "media_prompt_reference_budget",
            "当前对话模型每次支持两张安全参考图；多图编辑请关闭自动提示词优化。",
            422,
        )
    with Session(engine()) as db:
        lock_run(db, run_id)
        run = db.get(Run, run_id)
        attachments = []
        for item in request["references"]:
            artifact, _ = links.validated_artifact(
                db, run.user_id, run.workspace_id, item["artifact_id"], item["version"]
            )
            preview_id = links._preview_state(db, artifact).get("preview_artifact_id")
            if not preview_id:
                raise AppError("media_prompt_reference_pending", "参考图安全预览尚未就绪。", 409)
            preview, _ = links.validated_artifact(db, run.user_id, run.workspace_id, preview_id)
            attachments.append(
                {
                    "artifact_id": preview.id,
                    "version": preview.content_hash,
                    "mode": "native_image",
                    "consent_remote": False,
                    "consent_target": None,
                }
            )
        messages[-1] = {**messages[-1], "attachments": attachments}
        compiled, _ = compile_inputs(db, run, messages, frozen)
        return compiled


def finish(run_id, text, usage, error=None):
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id)
        identifier = str(uuid5(UUID(run_id), "media-prompt"))
        attempt = db.get(Attempt, identifier)
        attempt.status, attempt.finished_at = "failed" if error else "succeeded", now()
        attempt.error = {"code": error.code, "message": error.message} if error else None
        row = db.scalar(select(UsageRecord).where(UsageRecord.attempt_id == identifier))
        row.metrics = {
            **row.metrics,
            **usage,
            "usage_known": usage.get("input_tokens_total") is not None,
        }
        db.get(Step, attempt.step_id).status = attempt.status
        if not error:
            journal.save(storage_path(f"media/jobs/{run_id}/caption.json"), {"prompt": text})


async def expand(run_id, snapshot, request):
    path = storage_path(f"media/jobs/{run_id}/caption.json")
    if path.is_file():
        return await asyncio.to_thread(journal.load, path)
    config, frozen = await asyncio.to_thread(begin, run_id, snapshot["prompt_model_revision_id"])
    from workbench.infrastructure.secrets import decrypt

    config.api_key = await asyncio.to_thread(decrypt, config.credential_ciphertext)
    guides = json.loads(
        (PROJECT_ROOT / ".runtime/media-source/src/flux3/pup_prompts.json").read_text()
    )
    messages = [
        {
            "role": "system",
            "content": guides["i2i" if request["references"] else "t2i"]
            + "\nKeep the user's intent. Output only the image caption, no reasoning or tools. "
            "For editing use the actual attached references, "
            "preserve the requested changes and reference numbering.",
        },
        {"role": "user", "content": request["prompt"]},
    ]
    messages = await asyncio.to_thread(with_references, run_id, messages, request, frozen)
    output, usage, finished = "", {}, False
    try:
        async with aclosing(providers.stream_model(config, messages, [])) as stream:
            async for event in stream:
                if event.kind == "text_delta":
                    output += event.data["delta"]
                elif event.kind == "usage":
                    usage.update(event.data)
                elif event.kind == "done":
                    finished = event.data.get("finish_reason") in {"stop", "end_turn"}
        if not finished or not output.strip():
            raise AppError("media_prompt_incomplete", "提示词优化未完整完成，未加载生图权重。", 409)
        await asyncio.to_thread(finish, run_id, output, usage)
        return {"prompt": output}
    except asyncio.CancelledError:
        await providers.abort_generation(config)
        raise
    except AppError as error:
        await asyncio.to_thread(finish, run_id, output, usage, error)
        raise
    finally:
        config.api_key = None
