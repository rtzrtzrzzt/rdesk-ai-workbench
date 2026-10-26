"""Model-independent connections and persistent media submission, for UI and agents."""

import json
import secrets
from pathlib import Path
from uuid import uuid5

from sqlalchemy import func, or_, select, tuple_

from workbench.config import PROJECT_ROOT
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import now
from workbench.infrastructure.secrets import encrypt
from workbench.modules import workspace_service
from workbench.modules.files import links
from workbench.modules.files.domain import Artifact
from workbench.modules.files.inputs import is_remote, local_only_source
from workbench.modules.media.domain import MediaConnection, MediaTask, run_id_for_request
from workbench.modules.models.service import validate_endpoint
from workbench.modules.runs.domain import Job, Run
from workbench.modules.runs.ownership import assert_owned
from workbench.modules.runs.service import cancel, emit, idempotent

ADAPTERS = [
    {"driver": "flux3_native", "name": "本机 FLUX 3 Image Klein", "capabilities": ["image"]},
    {"driver": "openai_images", "name": "OpenAI 兼容图像接口", "capabilities": ["image"]},
    {
        "driver": "media_http",
        "name": "工作台媒体 HTTP v1",
        "capabilities": ["image", "video", "audio", "3d"],
    },
]


def connection(db, user_id, workspace_id, identifier, *, lock=False):
    workspace_service.get(db, user_id, workspace_id)
    query = select(MediaConnection).where(
        MediaConnection.id == identifier,
        MediaConnection.user_id == user_id,
        or_(MediaConnection.workspace_id.is_(None), MediaConnection.workspace_id == workspace_id),
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise AppError("media_connection_missing", "生成模型不存在或不属于当前工作区。", 404)
    return row


def connection_view(row):
    return {
        "id": row.id,
        "name": row.name,
        "scope": "workspace" if row.workspace_id else "user",
        "workspace_id": row.workspace_id,
        "capability": row.capability,
        "driver": row.driver,
        "model_identifier": row.model_identifier,
        "endpoint": row.endpoint,
        "credential_configured": bool(row.encrypted_api_key),
        "allow_remote": row.allow_remote,
        "enabled": row.enabled,
        "is_default": row.is_default,
        "configuration": row.configuration,
        "verification": row.verification,
        "version": row.version,
    }


def native_info():
    directory = PROJECT_ROOT / "models/black-forest-labs/flux-3-image-klein"
    expected = [
        "variants/nvfp4/model.safetensors",
        "ae.safetensors",
        "text_encoder/config.json",
        "text_encoder/model-00001-of-00002.safetensors",
        "text_encoder/model-00002-of-00002.safetensors",
    ]
    return {
        "prepared": all((directory / name).is_file() for name in expected),
        "runtime_prepared": (PROJECT_ROOT / ".runtime/media-env/bin/python").is_file(),
        "suggested": {
            "model_directory": str(directory),
            "model_identifier": "FLUX 3 Image Klein NVFP4",
            "driver": "flux3_native",
            "capability": "image",
        },
    }


def listing(db, user_id, workspace_id):
    workspace_service.get(db, user_id, workspace_id)
    rows = db.scalars(
        select(MediaConnection)
        .where(
            MediaConnection.user_id == user_id,
            or_(
                MediaConnection.workspace_id.is_(None), MediaConnection.workspace_id == workspace_id
            ),
        )
        .order_by(MediaConnection.created_at)
    )
    return {
        "connections": [connection_view(row) for row in rows],
        "native": native_info(),
        "adapters": ADAPTERS,
    }


def save(db, user_id, workspace_id, identifier, command):
    workspace_service.get(db, user_id, workspace_id)
    body = command.model_dump(mode="json")
    if command.api_key is not None:
        import hashlib

        body["api_key"] = hashlib.sha256(command.api_key.get_secret_value().encode()).hexdigest()

    def execute():
        row = db.get(MediaConnection, identifier, with_for_update=True)
        if row is not None:
            connection(db, user_id, workspace_id, identifier)
            if row.version != command.expected_version:
                raise AppError("media_connection_changed", "连接已变化，请刷新后保存。", 409)
        elif command.expected_version:
            raise AppError("media_connection_missing", "连接已不存在。", 404)
        else:
            row = MediaConnection(id=identifier, user_id=user_id)
            db.add(row)
        if not any(
            item["driver"] == command.driver and command.capability in item["capabilities"]
            for item in ADAPTERS
        ):
            raise AppError("media_adapter_mismatch", "该接入方式不支持所选生成类型。", 422)
        endpoint = None
        if command.driver == "flux3_native":
            if not command.configuration.model_directory:
                raise AppError("media_weights_missing", "请选择本机模型目录。", 422)
            directory = Path(command.configuration.model_directory).expanduser().resolve()
            if not directory.is_dir():
                raise AppError("media_weights_missing", "本机模型目录不存在。", 422)
        else:
            if not command.endpoint:
                raise AppError("media_endpoint_missing", "请填写生成服务地址。", 422)
            endpoint = validate_endpoint(command.endpoint, command.allow_remote)
        row.name, row.capability, row.driver = command.name, command.capability, command.driver
        row.model_identifier, row.endpoint = command.model_identifier, endpoint
        row.workspace_id = workspace_id if command.scope == "workspace" else None
        row.allow_remote, row.enabled, row.is_default = (
            command.allow_remote,
            command.enabled,
            command.is_default,
        )
        row.configuration = command.configuration.model_dump(mode="json")
        if command.api_key is not None:
            row.encrypted_api_key = encrypt(command.api_key.get_secret_value())
        elif command.clear_api_key:
            row.encrypted_api_key = None
        if command.expected_version:
            row.version += 1
        row.verification = "unverified"
        db.flush()
        if row.is_default:
            for old in db.scalars(
                select(MediaConnection)
                .where(
                    MediaConnection.user_id == user_id,
                    MediaConnection.capability == row.capability,
                    MediaConnection.workspace_id.is_(None)
                    if row.workspace_id is None
                    else MediaConnection.workspace_id == row.workspace_id,
                    MediaConnection.id != identifier,
                    MediaConnection.is_default.is_(True),
                )
                .with_for_update()
            ):
                old.is_default, old.version = False, old.version + 1
        return connection_view(row)

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        "save_media_connection",
        body | {"id": identifier},
        execute,
    )


def select_connection(db, user_id, workspace_id, capability, identifier=None):
    if identifier:
        row = connection(db, user_id, workspace_id, str(identifier))
    else:
        candidates = listing(db, user_id, workspace_id)["connections"]
        candidates = [
            item for item in candidates if item["enabled"] and item["capability"] == capability
        ]
        candidates.sort(key=lambda item: (not item["is_default"], item["workspace_id"] is None))
        if not candidates:
            raise AppError(
                "media_not_connected", "没有模型接入，请先在设置中接入生成模型。", 409, "capability"
            )
        row = db.get(MediaConnection, candidates[0]["id"])
    if not row.enabled or row.capability != capability:
        raise AppError(
            "media_connection_disabled", "该生成模型未启用或类型不匹配。", 409, "capability"
        )
    return row


def validate_references(db, user_id, workspace_id, row, references, *, human, parent=None):
    result = []
    remote = bool(row.endpoint and is_remote(row.endpoint))
    for reference in references:
        artifact, _ = links.validated_artifact(
            db, user_id, workspace_id, str(reference.artifact_id), reference.version
        )
        if artifact.mime_type not in links.RASTER_MIMES or artifact.byte_size > 16 * 1024**2:
            raise AppError("media_reference_type", "参考图须为不超过16MiB的PNG、JPEG或WebP。", 422)
        if remote and local_only_source(db, artifact):
            raise AppError(
                "capture_remote_disabled", "截图及其派生产物只能交本地模型。", 403, "policy"
            )
        if remote and (not human or not reference.consent_remote):
            raise AppError(
                "media_transfer_consent",
                "参考图需由用户在创作页对本次生成连接确认远程传输。",
                403,
                "policy",
            )
        result.append(
            {
                "artifact_id": artifact.id,
                "version": artifact.content_hash,
                "local_only_source": local_only_source(db, artifact),
            }
        )
    return result


def task(db, user_id, workspace_id, run_id):
    row = db.scalar(
        select(MediaTask)
        .join(Run, Run.id == MediaTask.run_id)
        .where(MediaTask.run_id == run_id, Run.user_id == user_id, Run.workspace_id == workspace_id)
    )
    if row is None:
        raise AppError("media_task_missing", "生成任务不存在或不可访问。", 404)
    return row


def task_view(db, row):
    run = db.get(Run, row.run_id)
    status = (
        run.status
        if run.status in {"failed", "cancelled", "paused", "cancelling", "waiting_resource"}
        else row.status
    )
    artifacts = [
        links.artifact_link_view(db, artifact)
        for identifier in row.artifacts
        if (artifact := db.get(Artifact, identifier)) and artifact.status == "available"
    ]
    return {
        "id": row.id,
        "run_id": row.run_id,
        "connection_id": row.connection_id,
        "connection_name": row.provider_snapshot["name"],
        "capability": row.provider_snapshot["capability"],
        "status": status,
        "phase": row.phase,
        "progress": row.progress,
        "provider_job_id": row.provider_job_id,
        "dispatch_state": row.dispatch_state,
        "resources_closed": row.resources_closed,
        "deletion_requested": row.deletion_requested_at is not None,
        "artifacts": artifacts,
        "error": row.error or run.error,
        "metadata": {
            **row.metadata_json,
            **({"requested_image": {key: row.request.get(key) for key in ("width", "height")}}
               if row.provider_snapshot.get("capability") == "image" else {}),
        },
        "created_at": row.created_at,
    }


def tasks(db, user_id, workspace_id, parent_run_id=None, *, before=None, limit=100):
    workspace_service.get(db, user_id, workspace_id)
    query = (
        select(MediaTask)
        .join(Run, Run.id == MediaTask.run_id)
        .where(
            Run.user_id == user_id,
            Run.workspace_id == workspace_id,
            MediaTask.deleted_at.is_(None),
        )
    )
    if parent_run_id:
        query = query.where(Run.parent_run_id == parent_run_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    if before:
        # A removed record remains a valid, authorized cursor for older pages.
        anchor = task(db, user_id, workspace_id, before)
        if parent_run_id and db.get(Run, anchor.run_id).parent_run_id != parent_run_id:
            raise AppError("media_cursor_invalid", "分页位置不属于当前任务列表。", 422)
        query = query.where(
            tuple_(MediaTask.created_at, MediaTask.id) < (anchor.created_at, anchor.id)
        )
    rows = list(
        db.scalars(
            query.order_by(MediaTask.created_at.desc(), MediaTask.id.desc()).limit(limit + 1)
        )
    )
    return {
        "tasks": [task_view(db, row) for row in rows[:limit]],
        "next_before": rows[limit - 1].run_id if len(rows) > limit else None,
        "total": total,
    }


def create(db, user_id, workspace_id, command, *, parent_run_id=None):
    workspace_service.get(db, user_id, workspace_id)

    def execute():
        parent = db.get(Run, parent_run_id) if parent_run_id else None
        if parent:
            assert_owned(db, parent.id)
        row = select_connection(
            db, user_id, workspace_id, command.capability, command.connection_id
        )
        if (
            command.expected_connection_version is not None
            and command.expected_connection_version != row.version
        ):
            raise AppError("media_connection_changed", "生成模型已变化，请重新选择并确认。", 409)
        remote = row.endpoint and is_remote(row.endpoint)
        if remote and command.references and command.expected_connection_version is None:
            raise AppError("media_connection_version_required", "发送参考图前须确认连接版本。", 409)
        refs = validate_references(
            db, user_id, workspace_id, row, command.references, human=parent is None, parent=parent
        )
        if row.driver == "flux3_native" and (command.width % 16 or command.height % 16):
            raise AppError("media_image_dimensions", "本机图像宽高须为16的倍数。", 422)
        snapshot = connection_view(row)
        prompt_model_id = None
        if row.driver == "flux3_native" and row.configuration.get("expand_prompt", True):
            from workbench.modules.models import service as models
            from workbench.modules.preferences.service import resolve

            defaults = resolve(db, user_id, workspace_id)
            prompt_model_id = defaults.model_connection_id
            if not prompt_model_id:
                raise AppError(
                    "media_prompt_model_missing",
                    "自动优化提示词需要先设置默认对话模型，或在生成模型设置中关闭此选项。",
                    409,
                )
            revision = models.freeze(db, user_id, prompt_model_id)
            if is_remote(revision.configuration["endpoint"]):
                raise AppError(
                    "media_prompt_local_required",
                    "本机生图的自动提示词优化需要本地默认模型；可关闭此选项后直接生成。",
                    409,
                )
            snapshot["prompt_model_revision_id"] = revision.id
        run = Run(
            id=run_id_for_request(command.request_id),
            user_id=user_id,
            workspace_id=workspace_id,
            kind="media",
            model_connection_id=prompt_model_id,
            input=command.prompt,
            parent_run_id=parent.id if parent else None,
            root_run_id=(parent.root_run_id or parent.id) if parent else None,
            config_snapshot={"media_connection": row.id},
        )
        db.add(run)
        db.flush()
        request = command.model_dump(
            mode="json", exclude={"request_id", "expected_connection_version"}
        )
        request.update(
            references=refs,
            seed=command.seed if command.seed is not None else secrets.randbits(32),
            steps=command.steps or 16,
        )
        created = MediaTask(
            id=str(uuid5(command.request_id, "media-task")),
            run_id=run.id,
            connection_id=row.id,
            connection_version=row.version,
            provider_snapshot=snapshot,
            encrypted_credential=row.encrypted_api_key,
            request=request,
        )
        db.add(created)
        db.add(Job(run_id=run.id, queue="media", available_at=now()))
        db.flush()
        emit(
            db,
            parent.id if parent else run.id,
            "media.created",
            {"run_id": run.id, "capability": command.capability, "connection_id": row.id},
        )
        return task_view(db, created)

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        "generate_media",
        command.model_dump(mode="json") | {"parent_run_id": parent_run_id},
        execute,
    )


def cancel_task(db, user_id, workspace_id, run_id, request_id):
    row = task(db, user_id, workspace_id, run_id)
    cancel(db, user_id, run_id, str(request_id))
    db.refresh(row, with_for_update=True)
    row.metadata_json = {**row.metadata_json, "user_cancel_requested": True}
    return task_view(db, row)


def guide(db, user_id, workspace_id, command):
    row = select_connection(db, user_id, workspace_id, "image", command.connection_id)
    result = {
        "connection_id": row.id,
        "capability": row.capability,
        "model": row.model_identifier,
        "workflow": (
            "提交一次生成任务，保留run_id。任务持久运行，查询状态或等待子任务；"
            "完成后view_media_output查看实际产物，可把原件artifact_id作为下一次参考图。"
        ),
    }
    if row.driver == "flux3_native":
        path = PROJECT_ROOT / ".runtime/media-source/src/flux3/pup_prompts.json"
        if path.is_file():
            result["prompt_guide"] = json.loads(path.read_text())[
                "i2i" if command.editing else "t2i"
            ]
        result["note"] = "主模型按官方指南扩写具体视觉描述后提交；未另加载9B提示词扩写模型。"
    return result
