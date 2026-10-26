"""Persistent generation worker. Provider side effects are never replayed blindly."""

import asyncio
import time
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine
from workbench.infrastructure.secrets import decrypt
from workbench.modules.files import links
from workbench.modules.files.domain import Artifact
from workbench.modules.files.processors import detect_type
from workbench.modules.files.storage import file_digest, publish_bytes, storage_path
from workbench.modules.media import providers, resources, service
from workbench.modules.media.domain import MediaConnection, MediaTask
from workbench.modules.media.journal import load as read_json
from workbench.modules.media.journal import save as write_json
from workbench.modules.media.locking import lock_run
from workbench.modules.models.admission import model_permit
from workbench.modules.runs.domain import Run
from workbench.modules.runs.service import emit


def load(run_id):
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id)
        row = db.scalar(select(MediaTask).where(MediaTask.run_id == run_id).with_for_update())
        run = db.get(Run, run_id)
        current = service.connection(db, run.user_id, run.workspace_id, row.connection_id)
        if not current.enabled or (
            row.provider_snapshot["endpoint"]
            and not current.allow_remote
            and service.is_remote(row.provider_snapshot["endpoint"])
        ):
            raise AppError(
                "media_connection_revoked", "生成模型已停用或远程传输授权已撤回。", 403, "policy"
            )
        if row.status != "succeeded":
            row.status = "running"
            emit(db, run_id, "media.progress", {"phase": row.phase, "status": row.status})
        return row.provider_snapshot, row.request, row.encrypted_credential, row.status


def change(run_id, **values):
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id)
        row = db.scalar(select(MediaTask).where(MediaTask.run_id == run_id).with_for_update())
        for key, value in values.items():
            setattr(row, key, value)
        emit(
            db,
            run_id,
            "media.progress",
            {"phase": row.phase, "status": row.status, "progress": row.progress},
        )


def read_references(run_id, request, *, safe=False):
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id)
        run = db.get(Run, run_id)
        result = []
        for item in request["references"]:
            row, path = links.validated_artifact(
                db, run.user_id, run.workspace_id, item["artifact_id"], item["version"]
            )
            if safe:
                db.refresh(row, with_for_update=True)
                state = links._ensure_preview(db, row, run, str(uuid5(UUID(run_id), row.id)))
                identifier = state.get("preview_artifact_id")
                if not identifier:
                    task = db.get(Run, state.get("preview_task_id"))
                    if task and task.status in {"failed", "cancelled", "paused"}:
                        raise AppError(
                            "media_reference_preview",
                            "参考图安全预览未完成，请检查文件处理记录。",
                            409,
                        )
                    return None
                row, path = links.validated_artifact(db, run.user_id, run.workspace_id, identifier)
            result.append((row.display_name, row.mime_type, path))
        return result


async def native_generate(run_id, snapshot, request):
    configuration = snapshot["configuration"]
    directory = storage_path(f"media/jobs/{run_id}")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    await asyncio.to_thread(change, run_id, phase="preparing_references")
    references = await asyncio.to_thread(read_references, run_id, request, safe=True)
    while references is None:
        await asyncio.sleep(0.5)
        references = await asyncio.to_thread(read_references, run_id, request, safe=True)
    if configuration.get("expand_prompt", True):
        from workbench.modules.media.prompts import expand

        await asyncio.to_thread(change, run_id, phase="expanding_prompt")
        async with model_permit(run_id):
            caption = await expand(run_id, snapshot, request)
        request = {**request, "prompt": caption["prompt"]}
    plan = {
        **request,
        "references": [str(item[2]) for item in references],
        "model_directory": configuration["model_directory"],
        "output_directory": str(directory),
        "allocator_bytes": int(configuration["allocator_limit_gib"] * 1024**3),
        "attention_backend": configuration["attention_backend"],
    }
    path = directory / "request.json"
    if path.exists():
        if read_json(path) != plan:
            raise AppError("media_plan_changed", "已接受的生图计划发生变化，未重复执行。", 409)
    else:
        await asyncio.to_thread(write_json, path, plan)
    phases = ["kernels", "encode", *(["references"] if references else []), "sample", "decode"]
    for phase in phases:
        async with model_permit(run_id):
            # Recheck live permissions after resource waiting; updates do not
            # rewrite accepted parameters, but explicit revocation still wins.
            await asyncio.to_thread(load, run_id)
            await resources.run_phase(run_id, phase, configuration)
    result = directory / "result.png"
    if not result.is_file() or result.stat().st_size > 64 * 1024**2:
        raise AppError("media_native_output", "本机生图未产出可用PNG文件。", 409)
    await asyncio.to_thread(change, run_id, dispatch_state="resolved", resources_closed=True)
    return [("generated.png", "image/png", await asyncio.to_thread(result.read_bytes))]


def dispatch_snapshot(run_id):
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id)
        row = db.scalar(select(MediaTask).where(MediaTask.run_id == run_id).with_for_update())
        return row.dispatch_state, row.provider_job_id


def journal_response(run_id, response):
    write_json(storage_path(f"media/jobs/{run_id}/response.json"), response)


async def remote_generate(run_id, snapshot, request, credential):
    key = await asyncio.to_thread(decrypt, credential)
    receipt = storage_path(f"media/jobs/{run_id}/response.json")
    state, identifier = await asyncio.to_thread(dispatch_snapshot, run_id)
    if identifier and snapshot["driver"] == "media_http":
        # A provider ID accepted by explicit reconciliation supersedes any
        # malformed or lost acknowledgement left in the private POST journal.
        response = await providers.poll(snapshot, key, identifier)
    elif receipt.is_file():
        response = await asyncio.to_thread(read_json, receipt)
    elif state == "not_started":
        references = await asyncio.to_thread(read_references, run_id, request)
        loaded = [
            (name, mime, await asyncio.to_thread(path.read_bytes))
            for name, mime, path in references
        ]
        # Intent is committed before the only POST. If a connection or worker
        # disappears, lack of a result is never interpreted as no side effect.
        await asyncio.to_thread(
            change, run_id, dispatch_state="submitting", resources_closed=False, phase="submitting"
        )
        response = await providers.submit(snapshot, key, request, loaded, run_id)
        await asyncio.to_thread(journal_response, run_id, response)
    elif identifier and snapshot["driver"] == "media_http":
        response = await providers.poll(snapshot, key, identifier)
    else:
        raise AppError(
            "media_submission_unknown",
            "此前提交结果待核对，未自动重复生成；请核对提供方任务。",
            409,
            "outcome_unknown",
        )
    if snapshot["driver"] == "media_http":
        identifier = providers.job_identifier(response)
        await asyncio.to_thread(
            change,
            run_id,
            dispatch_state="accepted",
            provider_job_id=identifier,
            phase="generating",
            resources_closed=False,
        )
        expires = time.monotonic() + snapshot["configuration"]["timeout_seconds"]
        while response.get("status") not in {"succeeded", "failed", "cancelled"}:
            if time.monotonic() >= expires:
                raise AppError(
                    "media_poll_timeout",
                    "生成尚未完成，已保留任务标识，请核对或取消提供方任务。",
                    409,
                    "outcome_unknown",
                )
            progress = response.get("progress")
            await asyncio.to_thread(
                change,
                run_id,
                progress=(
                    progress if type(progress) in {int, float} and 0 <= progress <= 1 else None
                ),
            )
            await asyncio.sleep(2)
            await asyncio.to_thread(load, run_id)
            response = await providers.poll(snapshot, key, identifier)
            await asyncio.to_thread(journal_response, run_id, response)
        await asyncio.to_thread(change, run_id, dispatch_state="resolved", resources_closed=True)
        if response["status"] != "succeeded":
            raise AppError(
                "media_provider_stopped", "提供方生成任务已失败或取消，未重新提交。", 409
            )
    else:
        await asyncio.to_thread(change, run_id, dispatch_state="resolved", resources_closed=True)
    return await providers.outputs(snapshot, key, response)


def publish(run_id, snapshot, request, outputs):
    with Session(engine()) as db, db.begin():
        lock_run(db, run_id)
        run = db.get(Run, run_id)
        row = db.scalar(select(MediaTask).where(MediaTask.run_id == run_id).with_for_update())
        local_only = any(item["local_only_source"] for item in request["references"])
        identifiers = []
        for index, (name, declared, data) in enumerate(outputs):
            identifier = str(uuid5(UUID(run_id), f"media-output:{index}"))
            artifact = db.get(Artifact, identifier)
            if artifact is None:
                relative = f"workspaces/{run.workspace_id}/media/{run_id}/{identifier}"
                path = storage_path(relative)
                publish_bytes(path, data)
                mime, detected = detect_type(path, name)
                artifact = Artifact(
                    id=identifier,
                    user_id=run.user_id,
                    workspace_id=run.workspace_id,
                    run_id=run_id,
                    parent_id=request["references"][0]["artifact_id"]
                    if request["references"]
                    else None,
                    display_name=name,
                    mime_type=mime,
                    declared_mime=declared,
                    byte_size=len(data),
                    content_hash=file_digest(path),
                    storage_path=relative,
                    kind="generated",
                    status="available",
                    metadata_json={
                        "media_capability": snapshot["capability"],
                        "model_identifier": snapshot["model_identifier"],
                        "connection_id": row.connection_id,
                        "connection_version": row.connection_version,
                        "seed": request["seed"],
                        "steps": request["steps"],
                        "detected_format": detected,
                        "local_only_source": local_only,
                        "source_refs": request["references"],
                    },
                )
                db.add(artifact)
                db.flush()
            links._ensure_preview(db, artifact, run, identifier)
            identifiers.append(identifier)
        row.artifacts, row.progress, row.phase, row.status = (
            identifiers,
            1.0,
            "completed",
            "succeeded",
        )
        if snapshot["driver"] == "flux3_native":
            connection = db.get(MediaConnection, row.connection_id)
            if connection.version == row.connection_version:
                connection.verification = "generation_verified"
        emit(db, run_id, "media.completed", {"artifact_ids": identifiers})


async def process_run(run_id):
    snapshot, request, credential, status = await asyncio.to_thread(load, run_id)
    if status == "succeeded":
        return {}
    try:
        if snapshot["driver"] == "flux3_native":
            outputs = await native_generate(run_id, snapshot, request)
        else:
            outputs = await remote_generate(run_id, snapshot, request, credential)
        await asyncio.to_thread(publish, run_id, snapshot, request, outputs)
        return {}
    except AppError as error:
        await asyncio.to_thread(
            change, run_id, error={"code": error.code, "message": error.message}
        )
        raise
