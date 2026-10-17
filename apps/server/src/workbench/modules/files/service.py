import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import new_id, now
from workbench.infrastructure.locks import advisory_connection
from workbench.modules import workspace_service
from workbench.modules.files import dto
from workbench.modules.files.domain import Artifact, Upload
from workbench.modules.files.storage import file_digest, publish_bytes, storage_path
from workbench.modules.runs.domain import Job, Run
from workbench.modules.runs.service import emit, idempotent

CHUNK_SIZE = 4 * 1024 * 1024


def artifact_view(row: Artifact) -> dict:
    return {
        "id": row.id,
        "workspace_id": row.workspace_id,
        "parent_id": row.parent_id,
        "run_id": row.run_id,
        "filename": row.display_name,
        "mime_type": row.mime_type,
        "declared_mime": row.declared_mime,
        "byte_size": row.byte_size,
        "version": row.content_hash,
        "kind": row.kind,
        "status": row.status,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
    }


def get_artifact(db: Session, user_id: str, workspace_id: str, artifact_id: str) -> Artifact:
    workspace_service.get(db, user_id, workspace_id)
    row = db.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.user_id == user_id,
            Artifact.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise AppError("artifact_not_found", "文件不存在或不可访问。", 404)
    return row


def list_artifacts(db: Session, user_id: str, workspace_id: str) -> list[dict]:
    workspace_service.get(db, user_id, workspace_id)
    return [
        artifact_view(row)
        for row in db.scalars(
            select(Artifact)
            .where(
                Artifact.user_id == user_id,
                Artifact.workspace_id == workspace_id,
                Artifact.status == "available",
            )
            .order_by(Artifact.created_at.desc())
            .limit(500)
        )
    ]


def upload_view(row: Upload) -> dict:
    return {
        "id": row.id,
        "filename": row.display_name,
        "total_bytes": row.total_bytes,
        "received_bytes": row.received_bytes,
        "chunk_size": CHUNK_SIZE,
        "status": row.status,
        "artifact_id": row.artifact_id,
        "expires_at": row.expires_at,
    }


def get_upload(db: Session, user_id: str, workspace_id: str, upload_id: str) -> Upload:
    workspace_service.get(db, user_id, workspace_id)
    row = db.scalar(
        select(Upload).where(
            Upload.id == upload_id,
            Upload.user_id == user_id,
            Upload.workspace_id == workspace_id,
        )
    )
    if row is None:
        raise AppError("upload_not_found", "上传会话不存在或不可访问。", 404)
    if row.status != "completed" and row.expires_at < now():
        raise AppError("upload_expired", "上传会话已过期，请重新选择文件。", 410)
    return row


def create_upload(db: Session, user_id: str, workspace_id: str, command: dto.UploadCreate) -> dict:
    workspace_service.get(db, user_id, workspace_id)
    if any(char in command.filename for char in ("\0", "\r", "\n")):
        raise AppError("invalid_filename", "文件显示名含有无效字符。")
    values = command.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    request_id = str(command.request_id)
    # Reserve the upload ID in the same transaction; chunks are immutable once accepted.
    from sqlalchemy import text

    key = int.from_bytes(
        hashlib.sha256(f"upload:{user_id}:{request_id}".encode()).digest()[:8], signed=True
    )
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    existing = db.scalar(
        select(Upload).where(
            Upload.user_id == user_id,
            Upload.request_id == request_id,
        )
    )
    if existing:
        if existing.request_digest != digest or existing.workspace_id != workspace_id:
            raise AppError("idempotency_conflict", "此上传请求标识已经用于其他内容。", 409)
        return upload_view(existing)
    if shutil.disk_usage(storage_path("workspaces").parent).free < command.total_bytes * 2 + 2**30:
        raise AppError("storage_budget", "磁盘空间不足以保存原件与上传临时块。", 409, "resource")
    row = Upload(
        user_id=user_id,
        workspace_id=workspace_id,
        request_id=request_id,
        request_digest=digest,
        display_name=command.filename,
        declared_mime=command.mime_type,
        total_bytes=command.total_bytes,
        expires_at=now() + timedelta(hours=24),
    )
    db.add(row)
    db.flush()
    return upload_view(row)


def accept_chunk(
    user_id: str, workspace_id: str, upload_id: str, offset: int, content: bytes
) -> dict:
    if not content or len(content) > CHUNK_SIZE:
        raise AppError("upload_chunk_size", "每个上传块必须在 1 字节至 4 MiB 之间。", 422)
    digest = hashlib.sha256(content).hexdigest()
    with advisory_connection(f"upload:{upload_id}") as connection:
        with Session(bind=connection, expire_on_commit=False) as db:
            row = get_upload(db, user_id, workspace_id, upload_id)
            existing = row.chunks.get(str(offset))
            if existing:
                if existing != {"size": len(content), "hash": digest}:
                    raise AppError("upload_chunk_conflict", "此位置已经收到不同的文件内容。", 409)
                return upload_view(row)
            if row.status != "uploading" or offset != row.received_bytes:
                raise AppError("upload_offset", "上传位置不一致，请读取会话进度后继续。", 409)
            if offset + len(content) > row.total_bytes:
                raise AppError("upload_size", "上传内容超过声明的原件大小。", 422)
            target = storage_path(f"workspaces/{workspace_id}/uploads/{upload_id}/{offset}.chunk")
            # A crash before DB commit can leave this same block. Compare before adopting it.
            if target.exists() and file_digest(target) != digest:
                raise AppError("upload_chunk_conflict", "未完成的上传块与当前内容不同。", 409)
            if not target.exists():
                publish_bytes(target, content)
            row.chunks = {**row.chunks, str(offset): {"size": len(content), "hash": digest}}
            row.received_bytes += len(content)
            db.commit()
            return upload_view(row)


def complete_upload(user_id: str, workspace_id: str, upload_id: str) -> dict:
    from workbench.modules.files.processors import detect_type

    with advisory_connection(f"upload:{upload_id}") as connection:
        with Session(bind=connection, expire_on_commit=False) as db:
            row = get_upload(db, user_id, workspace_id, upload_id)
            if row.status == "completed":
                return artifact_view(get_artifact(db, user_id, workspace_id, row.artifact_id))
            if row.received_bytes != row.total_bytes:
                raise AppError("upload_incomplete", "原件尚未上传完整。", 409)
            artifact = db.get(Artifact, row.artifact_id) if row.artifact_id else None
            if artifact is None:
                artifact_id = new_id()
                relative = f"workspaces/{workspace_id}/artifacts/{artifact_id}/original"
                target = storage_path(relative)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                handle, temporary = tempfile.mkstemp(prefix=".assembling-", dir=target.parent)
                try:
                    digest = hashlib.sha256()
                    with os.fdopen(handle, "wb") as output:
                        for offset, chunk in sorted(
                            row.chunks.items(), key=lambda pair: int(pair[0])
                        ):
                            source = storage_path(
                                f"workspaces/{workspace_id}/uploads/{upload_id}/{offset}.chunk"
                            )
                            data = source.read_bytes()
                            if (
                                len(data) != chunk["size"]
                                or hashlib.sha256(data).hexdigest() != chunk["hash"]
                            ):
                                raise AppError(
                                    "upload_chunk_changed", "原件上传块已改变，请重新上传。", 409
                                )
                            output.write(data)
                            digest.update(data)
                        output.flush()
                        os.fsync(output.fileno())
                    mime, detected = detect_type(Path(temporary), row.display_name)
                    artifact = Artifact(
                        id=artifact_id,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        display_name=row.display_name,
                        mime_type=mime,
                        declared_mime=row.declared_mime,
                        byte_size=row.total_bytes,
                        content_hash=digest.hexdigest(),
                        storage_path=relative,
                        metadata_json={"detected_format": detected},
                    )
                    db.add(artifact)
                    db.flush()
                    row.artifact_id, row.status = artifact.id, "publishing"
                    db.commit()  # Durable publication intent precedes the external rename.
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            target = storage_path(artifact.storage_path)
            if not target.exists():
                # A process may have stopped before publication. Keep blocks and permit reassembly.
                row.artifact_id, row.status = None, "uploading"
                db.delete(artifact)
                db.commit()
                raise AppError(
                    "upload_publish_interrupted",
                    "原件发布中断，上传块已保留，请再次完成上传。",
                    409,
                    "transient",
                    True,
                )
            if (
                target.stat().st_size != artifact.byte_size
                or file_digest(target) != artifact.content_hash
            ):
                raise AppError(
                    "artifact_changed", "原件存储内容已改变，需要核对。", 409, "outcome_unknown"
                )
            artifact.status, row.status = "available", "completed"
            db.commit()
            return artifact_view(artifact)


def task_view(db: Session, run: Run) -> dict:
    return {
        "id": run.id,
        "artifact_id": run.config_snapshot["artifact_id"],
        "status": run.status,
        "operation": run.config_snapshot["operation"],
        "error": run.error,
        "results": [
            artifact_view(item)
            for item in db.scalars(
                select(Artifact)
                .where(
                    Artifact.run_id == run.id,
                    Artifact.status == "available",
                )
                .order_by(Artifact.created_at)
            )
        ],
    }


def list_tasks(db: Session, user_id: str, workspace_id: str) -> list[dict]:
    workspace_service.get(db, user_id, workspace_id)
    return [
        task_view(db, run)
        for run in db.scalars(
            select(Run)
            .where(
                Run.user_id == user_id,
                Run.workspace_id == workspace_id,
                Run.kind == "file",
            )
            .order_by(Run.created_at.desc())
            .limit(100)
        )
    ]


def create_processing(
    db: Session, user_id: str, workspace_id: str, artifact_id: str, command: dto.ProcessFile
) -> dict:
    def execute():
        artifact = get_artifact(db, user_id, workspace_id, artifact_id)
        if artifact.status != "available":
            raise AppError("artifact_not_ready", "原件尚未发布完成。", 409)
        runtime = {}
        if command.operation == "transcribe":
            from workbench.modules.files.asr import freeze_plan

            runtime["asr_runtime"] = freeze_plan(command.asr.model_dump(mode="json"))
        run = Run(
            id=new_id(),
            kind="file",
            user_id=user_id,
            workspace_id=workspace_id,
            input=f"{command.operation}: {artifact.display_name}",
            graph_version="file:1",
            config_snapshot={
                "artifact_id": artifact.id,
                **command.model_dump(mode="json", exclude={"request_id"}),
                **runtime,
            },
        )
        run.root_run_id = run.id
        db.add(run)
        db.flush()
        db.add(Job(run_id=run.id, queue="files", available_at=now()))
        emit(db, run.id, "run.accepted", {"status": "queued", "kind": "file"})
        return task_view(db, run)

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        f"artifact:{artifact_id}:process",
        command.model_dump(mode="json"),
        execute,
    )


def clean_uploads(*, user_id: str | None = None, upload_ids: list[str] | None = None) -> dict:
    """Remove confirmed duplicate chunks and expired incomplete uploads, never originals."""
    from workbench.infrastructure.database import engine

    removed_files, removed_bytes = 0, 0
    deadline = time.monotonic() + 5
    with Session(engine()) as db:
        query = select(Upload.id).where(
            (Upload.status == "completed")
            | ((Upload.status == "uploading") & (Upload.expires_at < now())),
            Upload.chunks != {},
        )
        if user_id is not None:
            query = query.where(Upload.user_id == user_id)
        if upload_ids is not None:
            query = query.where(Upload.id.in_(upload_ids))
        candidates = list(db.scalars(query.limit(100)))
    for upload_id in candidates:
        if time.monotonic() >= deadline:
            break
        with advisory_connection(f"upload:{upload_id}") as connection:
            with Session(bind=connection) as db, db.begin():
                row = db.get(Upload, upload_id)
                if row.status == "completed":
                    original = db.get(Artifact, row.artifact_id)
                    if (
                        original is None
                        or original.status != "available"
                        or not storage_path(original.storage_path).is_file()
                        or storage_path(original.storage_path).stat().st_size != original.byte_size
                    ):
                        continue
                    try:
                        if (
                            file_digest(storage_path(original.storage_path), deadline=deadline)
                            != original.content_hash
                        ):
                            continue
                    except TimeoutError:
                        break
                elif row.status != "uploading" or row.expires_at >= now():
                    continue
                directory = storage_path(f"workspaces/{row.workspace_id}/uploads/{row.id}")
                if directory.is_dir():
                    # Remove only registered chunk filenames. Unexpected entries
                    # are retained rather than sweeping a whole directory.
                    for offset in row.chunks:
                        if not offset.isdigit():
                            raise AppError(
                                "upload_chunk_invalid", "上传块索引无效，未继续清理。", 409
                            )
                        chunk = directory / (offset + ".chunk")
                        if chunk.is_symlink():
                            raise AppError("upload_chunk_invalid", "上传块不能是符号链接。", 409)
                        if chunk.is_file():
                            size = chunk.stat().st_size
                            chunk.unlink()
                            removed_files += 1
                            removed_bytes += size
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                row.chunks = {}
                if row.status != "completed":
                    row.status = "expired"
    return {"confirmed_removed_files": removed_files, "confirmed_removed_bytes": removed_bytes}
