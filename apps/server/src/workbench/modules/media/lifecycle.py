"""Cancellation and explicit reconciliation of accepted remote generation jobs."""

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.infrastructure.secrets import decrypt
from workbench.modules.media import history, providers, resources, service
from workbench.modules.media.domain import MediaTask
from workbench.modules.media.locking import lock_run
from workbench.modules.runs.domain import TERMINAL_STATUSES, Job, Run
from workbench.modules.runs.service import emit, idempotent, set_status


def cleanup_pending(db, run_id):
    return resources.cleanup_pending(db, run_id) or bool(
        db.scalar(
            select(MediaTask.id).where(
                MediaTask.run_id == run_id, MediaTask.resources_closed.is_(False)
            )
        )
    )


def request_cleanup(db, run_id):
    return int(cleanup_pending(db, run_id))


async def maintain():
    await resources.maintain()

    def pending():
        with Session(engine()) as db:
            return [
                (row.run_id, row.provider_snapshot, row.encrypted_credential, row.provider_job_id)
                for row in db.scalars(
                    select(MediaTask)
                    .join(Run, Run.id == MediaTask.run_id)
                    .join(Job, Job.run_id == Run.id)
                    .where(
                        MediaTask.resources_closed.is_(False),
                        MediaTask.provider_snapshot["driver"].astext != "flux3_native",
                        (Run.cancel_requested_at.is_not(None))
                        | (Job.completion_status.is_not(None)),
                    )
                    .limit(16)
                )
            ]

    for run_id, snapshot, credential, identifier in await asyncio.to_thread(pending):
        if not identifier or snapshot["driver"] != "media_http":
            continue  # Unknown effects require a provider receipt or explicit human reconciliation.
        try:
            key = await asyncio.to_thread(decrypt, credential)
            response = await providers.poll(snapshot, key, identifier)
            if response.get("status") not in {"succeeded", "cancelled", "failed"}:
                response = await providers.stop(snapshot, key, identifier)
            if response.get("status") in {"succeeded", "cancelled", "failed"}:
                with Session(engine()) as db, db.begin():
                    lock_run(db, run_id, owned=False)
                    row = db.scalar(
                        select(MediaTask).where(MediaTask.run_id == run_id).with_for_update()
                    )
                    row.resources_closed, row.dispatch_state = True, "resolved"
                    emit(
                        db,
                        run_id,
                        "media.resources_closed",
                        {"provider_status": response["status"]},
                    )
        except AppError:
            continue

    await asyncio.to_thread(history.finalize_pending)


def reconcile(db, user_id, workspace_id, run_id, command):
    def execute():
        row = service.task(db, user_id, workspace_id, run_id)
        run = lock_run(db, run_id, owned=False)
        job = db.scalar(select(Job).where(Job.run_id == run_id))
        db.refresh(row, with_for_update=True)
        if row.deleted_at is not None:
            raise AppError(
                "media_history_removed", "该生成记录已删除，不会恢复其任务。", 409
            )
        if job.status == "running" and job.lease_until and job.lease_until > now():
            raise AppError("media_still_running", "任务仍在执行，请先停止或等待。", 409)
        if row.provider_snapshot["driver"] == "flux3_native":
            raise AppError(
                "media_reconcile_native", "本机任务按实际进程回执回收，不能手动声明结束。", 409
            )
        if command.confirmed_not_submitted:
            if command.provider_job_id or row.provider_job_id:
                raise AppError(
                    "media_reconcile_conflict", "已有提供方任务标识，请查询或取消该任务。", 409
                )
            row.resources_closed, row.dispatch_state = True, "confirmed_absent"
            job.completion_status = None
            job.status = "cancelled"
            set_status(db, run, "cancelled")
        elif command.provider_job_id:
            if row.provider_snapshot["driver"] != "media_http":
                raise AppError(
                    "media_reconcile_unsupported",
                    "该同步接口不支持按任务ID查询，请在提供方核对结果。",
                    409,
                )
            removing = row.deletion_requested_at is not None
            if not removing:
                parent = db.get(Run, run.parent_run_id) if run.parent_run_id else None
                if row.metadata_json.get("user_cancel_requested") or (
                    parent is not None and parent.cancel_requested_at
                ):
                    raise AppError(
                        "media_cancelled_by_user",
                        "该任务已明确要求取消，请核对提供方并取消原任务。",
                        409,
                    )
                if run.cancel_requested_at and job.completion_status != "failed":
                    raise AppError(
                        "media_cancelled", "该任务已取消，不会以核对操作恢复生成。", 409
                    )
            row.provider_job_id, row.dispatch_state, row.resources_closed = (
                command.provider_job_id,
                "accepted",
                False,
            )
            row.error = None
            if removing:
                # An unknown submission still needs a real provider receipt to
                # stop it. Reconciliation may identify it, never resume it.
                run.cancel_requested_at = run.cancel_requested_at or now()
                job.status, job.completion_status = "cleanup", "cancelled"
                if run.status not in TERMINAL_STATUSES:
                    set_status(db, run, "cancelling")
            else:
                run.cancel_requested_at, run.error = None, None
                job.status, job.completion_status, job.available_at = "queued", None, now()
                set_status(db, run, "queued")
        else:
            raise AppError(
                "media_reconcile_required",
                "请填写实际提供方任务ID，或确认提供方没有接受此提交。",
                422,
            )
        emit(
            db,
            run_id,
            "media.reconciled",
            {"provider_job_id": row.provider_job_id, "state": row.dispatch_state},
        )
        return service.task_view(db, row)

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        "media_reconcile:" + run_id,
        command.model_dump(mode="json"),
        execute,
    )


async def check(snapshot, credential):
    if snapshot["driver"] == "flux3_native":
        from pathlib import Path

        from workbench.config import PROJECT_ROOT

        directory = Path(snapshot["configuration"]["model_directory"])
        for name in (
            "variants/nvfp4/model.safetensors",
            "variants/nvfp4/config.json",
            "ae.safetensors",
            "text_encoder/config.json",
            "text_encoder/model.safetensors.index.json",
        ):
            if not (directory / name).is_file():
                raise AppError(
                    "media_native_incomplete", "模型目录尚缺必需文件，请按原生安装说明补齐。", 409
                )
        if not (PROJECT_ROOT / ".runtime/media-env/bin/python").is_file():
            raise AppError("media_runtime_missing", "本机生图环境尚未安装。", 409)
        return "files_checked"  # An actual generation is the GPU compatibility check.
    key = await asyncio.to_thread(decrypt, credential)
    response = await providers.request(
        snapshot,
        key,
        "GET",
        "/models" if snapshot["driver"] == "openai_images" else "/capabilities",
    )
    if snapshot["driver"] == "media_http" and (
        response.get("protocol") != "workbench-media/1"
        or snapshot["capability"] not in response.get("capabilities", [])
    ):
        raise AppError(
            "media_protocol_mismatch", "该服务未声明工作台媒体v1协议或所选生成类型。", 409
        )
    if snapshot["driver"] == "openai_images" and snapshot["model_identifier"] not in {
        item.get("id") for item in response.get("data", []) if isinstance(item, dict)
    }:
        raise AppError("media_model_missing", "提供方模型列表中没有此模型，请核对模型标识。", 409)
    return "connection_checked"
