import hashlib
import json
import sys
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.files.domain import Artifact
from workbench.modules.files.storage import publish_bytes, storage_path
from workbench.modules.runs.domain import Attempt, Run, Step
from workbench.modules.runs.ownership import assert_owned
from workbench.modules.runs.service import emit
from workbench.modules.tools.filesystem import run_process


async def process_run(run_id: str) -> dict:
    with Session(engine()) as db, db.begin():
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        assert_owned(db, run_id)
        step = db.scalar(select(Step).where(Step.run_id == run_id, Step.logical_key == "file:1"))
        if step and step.status == "succeeded":
            return {}
        if step is None:
            step = Step(run_id=run_id, logical_key="file:1", kind="file")
            db.add(step)
            db.flush()
            emit(db, run_id, "step.started", {"step_id": step.id, "kind": "file"})
        attempts = list(db.scalars(select(Attempt).where(Attempt.step_id == step.id)))
        for old in attempts:
            if old.status == "running":
                old.status, old.finished_at = "interrupted", now()
        if len(attempts) >= 3:
            raise AppError(
                "file_attempt_limit", "此处理达到重试上限，原件和已确认结果仍保留。", 409
            )
        attempt = Attempt(
            step_id=step.id, run_id=run_id, number=len(attempts) + 1, status="running"
        )
        db.add(attempt)
        db.flush()
        artifact = db.get(Artifact, run.config_snapshot["artifact_id"])
        if artifact is None or artifact.status != "available":
            raise AppError("artifact_not_ready", "文件原件不可用，未开始解析。", 409)
        output = storage_path(f"workspaces/{run.workspace_id}/staging/{run.id}/{attempt.id}")
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        plan = {
            "data_root": str(get_settings().data_dir.resolve()),
            "input": str(storage_path(artifact.storage_path)),
            "filename": artifact.display_name,
            "artifact_id": artifact.id,
            "expected_hash": artifact.content_hash,
            "expected_bytes": artifact.byte_size,
            "output": str(output),
            "operation": run.config_snapshot["operation"],
            "start": run.config_snapshot["start"],
            "limit": run.config_snapshot["limit"],
        }
        if plan["operation"] == "export":
            plan["export_spec"] = run.config_snapshot["export_spec"]
        for key in ("ocr", "video", "asr", "asr_runtime"):
            if run.config_snapshot.get(key) is not None:
                plan[key] = run.config_snapshot[key]
        workspace_id = run.workspace_id
        plan_path = output / "plan.json"
        publish_bytes(plan_path, json.dumps(plan).encode())
        attempt_id, step_id = attempt.id, step.id
        emit(
            db,
            run_id,
            "attempt.started",
            {"attempt_id": attempt.id, "step_id": step.id, "number": attempt.number},
        )
        emit(
            db,
            run_id,
            "file.progress",
            {"phase": "processing", "artifact_id": artifact.id, "operation": plan["operation"]},
        )
    result = await run_process(
        [sys.executable, "-m", "workbench.entrypoints.file_processor", str(plan_path)],
        output,
        180,
    )
    if result.get("timed_out"):
        error = AppError(
            "file_processing_timeout", "处理超时已停止，原件仍保留，请缩小范围。", 409, "resource"
        )
    elif result["exit_code"] != 0:
        try:
            detail = json.loads(result["stdout"].strip().splitlines()[-1])["error"]
            error = AppError(detail["code"], detail["message"], 422)
        except (KeyError, ValueError, IndexError, TypeError):
            error = AppError(
                "file_processor_stopped",
                "处理器提前停止，原件已保留，请检查处理范围和组件状态。",
                422,
            )
    else:
        error = None
    if error:
        with Session(engine()) as db, db.begin():
            db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            assert_owned(db, run_id)
            attempt = db.get(Attempt, attempt_id)
            attempt.status, attempt.finished_at = "failed", now()
            attempt.error = {"code": error.code, "message": error.message}
            emit(
                db,
                run_id,
                "attempt.finished",
                {"attempt_id": attempt_id, "status": "failed", "error": attempt.error},
            )
        raise error
    parsed_path = output / "result.json"
    if parsed_path.stat().st_size > 8 * 1024 * 1024:
        raise AppError(
            "processor_output_budget", "处理结果超过容量限制，原件已保留。", 422, "resource"
        )
    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    manifest_kind = "manifest" if plan["operation"] == "export" else "extracted"
    produced = [(manifest_kind, parsed_path, "extracted.json", "application/json", None, {})]
    if parsed.get("preview"):
        produced.append(("preview", output / "preview.png", "preview.png", "image/png", None, {}))
    for item in parsed.get("exports", []):
        filename = item["filename"]
        if filename not in {
            f"export.{format}"
            for format in (
                "txt",
                "md",
                "csv",
                "json",
                "docx",
                "xlsx",
                "pptx",
                "png",
                "jpeg",
                "webp",
                "pdf",
            )
        } and filename not in (
            {f"frame-{index:04d}.png" for index in range(1, 13)}
            | {"transcript.txt", "transcript.json"}
        ):
            raise AppError("export_output_invalid", "导出处理器返回了未经允许的文件名。", 422)
        candidate = output / filename
        if candidate.is_symlink() or candidate.stat().st_size > 16 * 1024**2:
            raise AppError("export_output_budget", "导出结果不符合路径或大小限制。", 422)
        frame = filename.startswith("frame-")
        kind = (
            "frame_" + filename[6:10]
            if frame
            else {"transcript.txt": "asr_text", "transcript.json": "asr_segments"}.get(
                filename, "export"
            )
        )
        if item.get("kind", kind) != kind or (frame and item["mime_type"] != "image/png"):
            raise AppError("export_output_invalid", "派生文件种类或帧格式无效。", 422)
        produced.append(
            (
                kind,
                candidate,
                filename,
                item["mime_type"],
                item["display_name"],
                {
                    key: item[key]
                    for key in ("source_refs", "timestamp_seconds", "requested_timestamp_seconds")
                    if key in item
                },
            )
        )
    if len({item[0] for item in produced}) != len(produced):
        raise AppError("export_output_duplicate", "派生输出种类重复，未发布覆盖文件。", 422)
    for kind, source, suffix, mime, display_name, output_metadata in produced:
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        artifact_id = str(uuid5(UUID(run_id), kind))
        relative = f"workspaces/{workspace_id}/artifacts/{artifact_id}/{suffix}"
        with Session(engine()) as db, db.begin():
            current = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            assert_owned(db, run_id)
            existing = db.get(Artifact, artifact_id)
            if existing and existing.status == "available":
                continue
            if existing is None:
                db.add(
                    Artifact(
                        id=artifact_id,
                        user_id=current.user_id,
                        workspace_id=current.workspace_id,
                        parent_id=current.config_snapshot["artifact_id"],
                        run_id=run_id,
                        display_name=display_name or f"{plan['filename']}.{suffix}",
                        mime_type=mime,
                        byte_size=len(content),
                        content_hash=digest,
                        storage_path=relative,
                        kind=kind,
                        metadata_json={
                            **parsed["metadata"],
                            "warnings": parsed["warnings"],
                            "complete": parsed["complete"],
                            "processor_revision": parsed["metadata"].get(
                                "processor_revision", "native:1"
                            ),
                            "attempt_id": attempt_id,
                            **output_metadata,
                        },
                    )
                )
            else:
                existing.content_hash, existing.byte_size = digest, len(content)
        publish_bytes(storage_path(relative), content)
        with Session(engine()) as db, db.begin():
            db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            assert_owned(db, run_id)
            db.get(Artifact, artifact_id).status = "available"
            emit(
                db,
                run_id,
                "artifact.created",
                {"artifact_id": artifact_id, "parent_id": plan.get("artifact_id"), "kind": kind},
            )
    with Session(engine()) as db, db.begin():
        db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        assert_owned(db, run_id)
        attempt = db.get(Attempt, attempt_id)
        attempt.status, attempt.finished_at = "succeeded", now()
        db.get(Step, step_id).status = "succeeded"
        if plan["operation"] != "export":
            original = db.get(Artifact, plan["artifact_id"])
            original.mime_type = parsed["metadata"]["mime_type"]
            original.metadata_json = {
                **original.metadata_json,
                "detected_format": parsed["metadata"]["format"],
            }
        emit(db, run_id, "attempt.finished", {"attempt_id": attempt_id, "status": "succeeded"})
        emit(db, run_id, "step.finished", {"step_id": step_id, "status": "succeeded"})
    # Keep only published immutable outputs, not duplicate input plans and private temp files.
    import shutil

    shutil.rmtree(output)
    return {}
