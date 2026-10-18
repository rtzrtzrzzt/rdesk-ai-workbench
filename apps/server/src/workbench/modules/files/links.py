"""Versioned links to authorized artifacts and immutable workspace file snapshots.

Image decoding remains in the existing isolated file worker. Presenting an image
only queues that worker and records its task; it never upgrades an original to a
trusted inline preview.
"""

import hashlib
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, new_id
from workbench.infrastructure.locks import advisory_connection
from workbench.modules import workspace_service
from workbench.modules.files import dto, service
from workbench.modules.files.domain import Artifact
from workbench.modules.files.processors import detect_type
from workbench.modules.files.storage import file_digest, storage_path
from workbench.modules.runs.domain import TERMINAL_STATUSES, Run
from workbench.modules.runs.ownership import assert_owned, current_owner
from workbench.modules.tools.filesystem import authorized_path

# Match the existing upload limit without reading the whole original into memory.
MAX_SNAPSHOT_BYTES = 1_073_741_824
IO_SECONDS = 30
RASTER_MIMES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/tiff",
    "image/bmp",
    "image/webp",
}


def safe_inline_image(row: Artifact) -> bool:
    return row.mime_type == "image/png" and (
        row.kind == "preview" or bool(re.fullmatch(r"frame_[0-9]{4}", row.kind))
    )


def validated_artifact(
    db: Session, user_id: str, workspace_id: str, artifact_id: str, version: str | None = None
) -> tuple[Artifact, Path]:
    row = service.get_artifact(db, user_id, workspace_id, artifact_id)
    if version is not None and row.content_hash != version:
        raise AppError(
            "artifact_version_changed", "文件版本与链接不一致，未打开替代版本。", 409, "conflict"
        )
    path = storage_path(row.storage_path)
    try:
        if row.status != "available" or not path.is_file():
            raise AppError("artifact_unavailable", "此文件原件暂不可读取，请核对存储状态。", 409)
        if (
            path.stat().st_size != row.byte_size
            or file_digest(path, deadline=time.monotonic() + IO_SECONDS) != row.content_hash
        ):
            raise AppError(
                "artifact_version_changed", "文件存储内容已改变，需要核对。", 409, "conflict"
            )
    except TimeoutError:
        raise AppError(
            "artifact_verification_timeout", "文件完整性核对超时，请稍后重试。", 409, "resource"
        ) from None
    except OSError:
        raise AppError(
            "artifact_unavailable", "此文件原件暂不可读取，请核对存储状态。", 409
        ) from None
    return row, path


def _run_scope(db: Session, run_id: str, *, lock: bool = False) -> tuple[Run, Path]:
    query = select(Run).where(Run.id == run_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    run = db.scalar(query)
    if run is None:
        raise AppError("run_not_found", "任务不存在或不可访问。", 404)
    assert_owned(db, run_id)
    if run.cancel_requested_at or run.status in TERMINAL_STATUSES:
        raise AppError("run_finished", "任务已结束或取消，不能继续发布文件。", 409)
    workspace = workspace_service.get(db, run.user_id, run.workspace_id)
    return run, Path(workspace.root_path).resolve()


def _source_path(run: Run, root: Path, value: str) -> Path:
    path = authorized_path(root, value)
    if run.parent_run_id:
        paths = run.config_snapshot.get("allowed_paths", [])
        if paths and not any(path.is_relative_to(authorized_path(root, item)) for item in paths):
            raise AppError("agent_path_scope", "此路径不在子任务允许的文件范围。", 403, "policy")
    return path


def _stage_snapshot(root: Path, source: Path, directory: Path) -> tuple[Path, str, int]:
    """Copy one stable regular file. Never retain a mutable source path as its content."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=directory)
    staged = Path(temporary)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(handle, "wb") as output, os.fdopen(os.open(source, flags), "rb") as original:
            before = os.fstat(original.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise AppError("file_not_regular", "只能呈现工作区中的真实普通文件。", 422)
            if before.st_size > MAX_SNAPSHOT_BYTES:
                raise AppError(
                    "file_snapshot_budget", "文件超过 1 GiB 原件保存限制。", 413, "resource"
                )
            digest, size = hashlib.sha256(), 0
            deadline = time.monotonic() + IO_SECONDS
            while chunk := original.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_SNAPSHOT_BYTES or time.monotonic() >= deadline:
                    raise AppError(
                        "file_snapshot_budget", "文件保存超过容量或时间预算。", 413, "resource"
                    )
                output.write(chunk)
                digest.update(chunk)
            after = os.fstat(original.fileno())
            # Re-check the actual path after IO, including parent symlink replacements.
            current = authorized_path(root, str(source)).stat()

            def signature(info):
                return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

            if (
                signature(before) != signature(after)
                or signature(after) != signature(current)
                or size != before.st_size
            ):
                raise AppError(
                    "file_version_conflict",
                    "文件在保存期间发生变化，请重新读取后呈现。",
                    409,
                    "conflict",
                )
            output.flush()
            os.fsync(output.fileno())
        return staged, digest.hexdigest(), size
    except OSError:
        staged.unlink(missing_ok=True)
        raise AppError(
            "file_unavailable", "文件不存在或无法读取，尚未发布文件链接。", 409
        ) from None
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _snapshot(db: Session, run_id: str, command: dto.PresentFile) -> Artifact:
    run, root = _run_scope(db, run_id)
    source = _source_path(run, root, command.path)
    relative_source = source.relative_to(root).as_posix()
    user_id, workspace_id = run.user_id, run.workspace_id
    db.rollback()
    candidate_id = new_id()
    candidate_path = storage_path(f"workspaces/{workspace_id}/artifacts/{candidate_id}/original")
    staged, digest, size = _stage_snapshot(root, source, candidate_path.parent)
    try:
        if command.version is not None and command.version != digest:
            raise AppError(
                "file_version_conflict",
                "工作区文件版本与预期不一致，请重新读取后呈现。",
                409,
                "conflict",
            )
        run, current_root = _run_scope(db, run_id, lock=True)
        if current_root != root or _source_path(run, current_root, command.path) != source:
            raise AppError(
                "agent_path_scope", "文件授权路径在保存期间发生变化，尚未发布。", 409, "policy"
            )
        previous = db.scalar(
            select(Artifact)
            .where(
                Artifact.user_id == user_id,
                Artifact.workspace_id == workspace_id,
                Artifact.kind == "workspace_snapshot",
                Artifact.metadata_json["source_path"].astext == relative_source,
                Artifact.content_hash == digest,
                Artifact.status.in_(["available", "staging"]),
            )
            .order_by(Artifact.created_at)
            .limit(1)
            .with_for_update()
        )
        if previous is not None and previous.status == "available":
            validated_artifact(db, user_id, workspace_id, previous.id, digest)
            return previous
        row = previous
        if row is None:
            mime, detected = detect_type(staged, source.name)
            row = Artifact(
                id=candidate_id,
                user_id=user_id,
                workspace_id=workspace_id,
                parent_id=None,
                run_id=None,
                display_name=source.name,
                mime_type=mime,
                declared_mime=None,
                byte_size=size,
                content_hash=digest,
                storage_path=f"workspaces/{workspace_id}/artifacts/{candidate_id}/original",
                kind="workspace_snapshot",
                status="staging",
                metadata_json={
                    "source_path": relative_source,
                    "source_run_id": run_id,
                    "source_version": digest,
                    "detected_format": detected,
                },
            )
            db.add(row)
        db.commit()  # Durable publication intent precedes the external rename.
        _run_scope(db, run_id, lock=True)
        target = storage_path(row.storage_path)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.exists():
            if target.stat().st_size != size or file_digest(target) != digest:
                raise AppError(
                    "artifact_version_changed",
                    "未完成快照的内容需要核对，未覆盖。",
                    409,
                    "conflict",
                )
        else:
            os.replace(staged, target)
        row.status = "available"
        db.commit()
        return row
    finally:
        staged.unlink(missing_ok=True)
        # Only this new candidate directory may be empty after reuse or a refused copy.
        if candidate_path.parent.is_dir() and not any(candidate_path.parent.iterdir()):
            candidate_path.parent.rmdir()


def _preview_state(db: Session, row: Artifact) -> dict:
    if safe_inline_image(row):
        return {"preview_artifact_id": row.id}
    if row.mime_type not in RASTER_MIMES:
        return {}
    preview = db.scalar(
        select(Artifact)
        .where(
            Artifact.user_id == row.user_id,
            Artifact.workspace_id == row.workspace_id,
            Artifact.parent_id == row.id,
            Artifact.kind == "preview",
            Artifact.mime_type == "image/png",
            Artifact.status == "available",
        )
        .order_by(Artifact.created_at.desc())
        .limit(1)
    )
    if preview:
        validated_artifact(db, row.user_id, row.workspace_id, preview.id)
        return {"preview_artifact_id": preview.id}
    task = db.scalar(
        select(Run)
        .where(
            Run.user_id == row.user_id,
            Run.workspace_id == row.workspace_id,
            Run.kind == "file",
            Run.config_snapshot["artifact_id"].astext == row.id,
            Run.config_snapshot["operation"].astext == "preview",
        )
        .order_by(Run.created_at.desc())
        .limit(1)
    )
    return {"preview_task_id": task.id} if task else {}


def _ensure_preview(db: Session, row: Artifact, parent: Run, operation_id: str) -> dict:
    # The artifact row lock serializes helper calls with different tool operation IDs.
    state = _preview_state(db, row)
    if state or row.mime_type not in RASTER_MIMES:
        return state
    assert_owned(db, parent.id)
    # As with creating a delegated run, the accepted child has no execution lease yet.
    token = current_owner.set(None)
    try:
        result = service.create_processing(
            db,
            row.user_id,
            row.workspace_id,
            row.id,
            dto.ProcessFile(
                request_id=uuid5(UUID(operation_id), "present-file-preview"), operation="preview"
            ),
        )
    finally:
        current_owner.reset(token)
    child = db.get(Run, result["id"])
    child.parent_run_id, child.root_run_id = parent.id, parent.root_run_id or parent.id
    return {"preview_task_id": child.id}


def artifact_link_view(db: Session, row: Artifact) -> dict:
    result = service.artifact_view(row)
    result["metadata"] = {**row.metadata_json, **_preview_state(db, row)}
    return result


def _markdown(filename: str, href: str) -> str:
    label = re.sub(r"[\x00-\x1f\x7f]", " ", filename)
    label = re.sub(r"([\\`*{}\[\]<>!_])", lambda match: "\\" + match[0], label)
    return f"[{label}]({href})"


def present_file(run_id: str, operation_id: str, values: dict) -> dict:
    """Tool entry. The caller must dispatch only registered/authorized present_file operations."""
    command = dto.PresentFile.model_validate(values)
    with Session(engine()) as probe:
        run, root = _run_scope(probe, run_id)
        resource = (
            str(_source_path(run, root, command.path))
            if command.path is not None
            else str(command.artifact_id)
        )
        workspace_id = run.workspace_id
    with advisory_connection(f"file-link:{workspace_id}:{resource}") as connection:
        with Session(bind=connection, expire_on_commit=False) as db:
            run, _ = _run_scope(db, run_id, lock=True)
            if command.path is not None:
                row = _snapshot(db, run_id, command)
            else:
                row, _ = validated_artifact(
                    db, run.user_id, run.workspace_id, str(command.artifact_id), command.version
                )
            run, _ = _run_scope(db, run_id, lock=True)
            row = db.scalar(select(Artifact).where(Artifact.id == row.id).with_for_update())
            # Existing snapshot IDs must not bypass a restricted subagent's path grant.
            if row.kind == "workspace_snapshot" and run.parent_run_id:
                _source_path(run, root, row.metadata_json["source_path"])
            state = _ensure_preview(db, row, run, operation_id)
            assert_owned(db, run_id)
            db.commit()
            href = (
                f"/api/v1/workspaces/{row.workspace_id}/file-links/{row.id}"
                f"?version={row.content_hash}"
            )
            return {
                "artifact_id": row.id,
                "workspace_id": row.workspace_id,
                "version": row.content_hash,
                "filename": row.display_name,
                "href": href,
                "markdown": _markdown(row.display_name, href),
                **state,
            }
