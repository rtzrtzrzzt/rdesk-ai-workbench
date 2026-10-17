"""Explicit cleanup of reproducible temporary data; originals are never targets."""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.infrastructure.vault.private import read_json, write_json
from workbench.modules.files.domain import Artifact, Upload
from workbench.modules.files.service import clean_uploads
from workbench.modules.files.storage import file_digest, storage_path
from workbench.modules.maintenance import dto
from workbench.modules.maintenance.service import _owner, _root
from workbench.modules.runs.domain import Job, Run

LIMITS = {"temporary_uploads": 100, "completed_file_staging": 20}


def _candidates(db, user_id, category):
    if category == "temporary_uploads":
        query = (
            select(Upload.id)
            .where(
                Upload.user_id == user_id,
                Upload.chunks != {},
                (Upload.status == "completed")
                | ((Upload.status == "uploading") & (Upload.expires_at < now())),
            )
            .order_by(Upload.created_at)
        )
    else:
        query = (
            select(Run.id, Run.workspace_id)
            .join(Job, Job.run_id == Run.id)
            .where(
                Run.user_id == user_id,
                Run.kind == "file",
                Run.status == "succeeded",
                Job.lease_owner.is_(None),
            )
            .order_by(Run.created_at.desc())
        )
        deadline, selected = time.monotonic() + 2, []
        for index, (identifier, workspace_id) in enumerate(db.execute(query.limit(10001))):
            if index >= 10000 or time.monotonic() > deadline:
                return selected, True
            if storage_path(f"workspaces/{workspace_id}/staging/{identifier}").exists():
                selected.append(identifier)
                if len(selected) > LIMITS[category]:
                    return selected, True
        return selected, False
    selected = list(db.scalars(query.limit(LIMITS[category] + 1)))
    return selected, len(selected) > LIMITS[category]


def preview(db, user_id):
    _owner(db, user_id)
    explanations = {
        "temporary_uploads": (
            "已发布原件的重复上传块，或已过24小时恢复期的未完成上传块。只删除登记块。"
        ),
        "completed_file_staging": (
            "已成功文件任务的私有临时目录；逐项确认全部已登记产物可读取且版本一致后才清理。"
        ),
    }
    categories = []
    for category, maximum in LIMITS.items():
        candidates, partial = _candidates(db, user_id, category)
        categories.append(
            dto.CleanupCategoryView(
                category=category,
                eligible_records=min(len(candidates), maximum),
                selection_limit=maximum,
                partial=partial,
                explanation=explanations[category],
            )
        )
    return dto.CleanupPreview(
        observed_at=now(),
        categories=categories,
        protected_categories=[
            "originals",
            "artifacts",
            "memory",
            "checkpoints",
            "command_logs",
            "database",
            "models",
            "ple",
            "active_browser_profiles",
            "backups",
        ],
    )


def _staging(run):
    return storage_path(f"workspaces/{run.workspace_id}/staging/{run.id}")


def _clean_staging(user_id, identifier, check):
    with Session(engine()) as db:
        row = db.get(Run, identifier)
        job = db.scalar(select(Job).where(Job.run_id == identifier))
        if (
            not row
            or row.user_id != user_id
            or row.kind != "file"
            or row.status != "succeeded"
            or not job
            or job.lease_owner
        ):
            return None
        directory = _staging(row)
        if not directory.exists():
            return (0, 0)
        artifacts = list(db.scalars(select(Artifact).where(Artifact.run_id == identifier)))
        if not artifacts or any(item.status != "available" for item in artifacts):
            return None
        if sum(item.byte_size for item in artifacts) > 256 * 1024**2:
            return None
        for artifact in artifacts:
            check()
            path = storage_path(artifact.storage_path)
            if (
                not path.is_file()
                or path.stat().st_size != artifact.byte_size
                or file_digest(path) != artifact.content_hash
            ):
                return None
    size, count = 0, 0
    for parent, dirs, files in os.walk(directory, followlinks=False):
        check()
        for name in dirs + files:
            path = Path(parent) / name
            if path.is_symlink():
                return None
        for name in files:
            path = Path(parent) / name
            info = path.stat()
            if not path.is_file() or info.st_nlink != 1:
                return None
            count, size = count + 1, size + info.st_size
            if count > 20000 or size > 512 * 1024**2:
                return None
    shutil.rmtree(directory)
    return count, size


def execute(db, user_id, command, boundary):
    _owner(db, user_id)
    boundary.assert_held()
    identifier = str(command.request_id)
    path = _root("cleanups") / (identifier + ".json")
    digest = hashlib.sha256(
        json.dumps({"user_id": user_id, **command.model_dump(mode="json")}, sort_keys=True).encode()
    ).hexdigest()
    resumed = path.exists()
    if resumed:
        intent = read_json(path)
        if intent.get("digest") != digest:
            raise AppError("idempotency_conflict", "此清理标识已用于另一组参数。", 409)
        if intent.get("result"):
            return dto.CleanupResult.model_validate(intent["result"])
    else:
        candidates, _ = _candidates(db, user_id, command.category)
        candidates = candidates[: LIMITS[command.category]]
        intent = {"schema": 1, "digest": digest, "candidates": candidates, "result": None}
        write_json(path, intent)
    deadline = time.monotonic() + 15

    def check():
        boundary.assert_held()
        if time.monotonic() > deadline:
            raise AppError(
                "cleanup_budget", "本轮清理已达到时间预算；保留原请求以核对，未触碰原件。", 409
            )

    files, size, examined, skipped = 0, 0, 0, 0
    for target in intent["candidates"]:
        check()
        examined += 1
        try:
            if command.category == "temporary_uploads":
                result = clean_uploads(user_id=user_id, upload_ids=[target])
                files += result["confirmed_removed_files"]
                size += result["confirmed_removed_bytes"]
            else:
                result = _clean_staging(user_id, target, check)
                if result is None:
                    skipped += 1
                else:
                    files += result[0]
                    size += result[1]
        except OSError:
            skipped += 1
    result = dto.CleanupResult(
        request_id=identifier,
        category=command.category,
        status="complete",
        selected_records=len(intent["candidates"]),
        examined_records=examined,
        skipped_records=skipped,
        confirmed_removed_files=None if resumed else files,
        confirmed_removed_bytes=None if resumed else size,
        recovered_interrupted_operation=resumed,
        completed_at=now(),
        note="仅处理本请求冻结的临时记录；跳过不满足条件的数据。"
        + (
            "此前执行曾中断，累计释放字节未知；没有据当前缺失倒推删除量。"
            if resumed
            else "删除字节为本次实际unlink前的文件大小，不等于文件系统实际释放块数。"
        ),
    )
    write_json(path, {**intent, "result": result.model_dump(mode="json")}, exclusive=False)
    return result
