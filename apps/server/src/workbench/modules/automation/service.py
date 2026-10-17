"""Owner-facing control plane and the shared tool dispatch boundary."""

import asyncio
import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.automation import dto, runtime
from workbench.modules.automation.domain import (
    AutomationAction,
    AutomationPolicy,
    AutomationSession,
    DesktopWriterLease,
)
from workbench.modules.automation.policy import (
    CLOSED,
    LIVE,
    assert_policy,
    configuration,
    current,
    denied,
)
from workbench.modules.browser.client import BrowserClient
from workbench.modules.browser.dto import (
    BrowserAction,
    BrowserCreate,
    BrowserDownload,
    BrowserObserve,
    BrowserOwner,
)
from workbench.modules.desktop.client import DesktopClient
from workbench.modules.desktop.dto import (
    DesktopAction,
    DesktopCreate,
    DesktopGrant,
    DesktopLease,
    DesktopObserve,
    DesktopOwner,
)
from workbench.modules.files.domain import Artifact
from workbench.modules.files.storage import file_digest, publish_bytes, storage_path
from workbench.modules.runs.domain import Run
from workbench.modules.runs.ownership import assert_owned
from workbench.modules.runs.service import idempotent
from workbench.modules.workspace_service import get as workspace


def policy_view(row):
    return dto.PolicyView(
        id=row.id if row else None,
        version=row.version if row else 0,
        configuration=row.configuration if row else {},
        grant_expires_at=row.grant_expires_at if row else None,
    )


def get_policy(db, user_id, workspace_id):
    workspace(db, user_id, workspace_id)
    return policy_view(current(db, user_id, workspace_id))


def save_policy(db, user_id, workspace_id, command):
    def execute():
        workspace_row = workspace(db, user_id, workspace_id)
        db.refresh(workspace_row, with_for_update=True)
        row = current(db, user_id, workspace_id)
        if (row.version if row else 0) != command.expected_version:
            raise AppError("automation_policy_stale", "授权已更新，请先重新读取。", 409)
        checked = configuration(command.configuration.model_dump())
        if row is None:
            row = AutomationPolicy(user_id=user_id, workspace_id=workspace_id)
            db.add(row)
            db.flush()
        else:
            row.version += 1
        row.configuration = checked.model_dump(mode="json")
        row.grant_expires_at = (
            now() + timedelta(seconds=command.grant_ttl_seconds)
            if checked.desktop_capture
            else None
        )
        for session in db.scalars(
            select(AutomationSession)
            .where(AutomationSession.policy_id == row.id, AutomationSession.status.in_(LIVE))
            .order_by(AutomationSession.id)
            .with_for_update()
        ):
            session.status = "stopping"
            lease = db.get(DesktopWriterLease, "local-desktop", with_for_update=True)
            if lease and lease.session_id == session.id:
                lease.revoked = True
        return policy_view(row).model_dump(mode="json")

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        f"automation:{workspace_id}:policy",
        command.model_dump(mode="json"),
        execute,
    )


def session_view(row):
    return dto.SessionView.model_validate(
        {
            "id": row.id,
            "kind": row.kind,
            "mode": row.mode,
            "run_id": row.run_id,
            "status": row.status,
            "policy_version": row.policy_version,
            "expires_at": row.expires_at,
            "native_view": row.native_view,
            "observation": row.observation,
            "error": row.error,
        }
    )


def get_session(db, user_id, workspace_id, identifier, *, run_id=None, lock=False):
    query = select(AutomationSession).where(
        AutomationSession.id == identifier,
        AutomationSession.user_id == user_id,
        AutomationSession.workspace_id == workspace_id,
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None or run_id is not None and row.run_id != run_id:
        raise AppError("automation_session_missing", "会话不存在或不属于当前工作区/任务。", 404)
    return row


def listing(db, user_id, workspace_id):
    workspace(db, user_id, workspace_id)
    return [
        session_view(row)
        for row in db.scalars(
            select(AutomationSession)
            .where(
                AutomationSession.user_id == user_id, AutomationSession.workspace_id == workspace_id
            )
            .order_by(AutomationSession.created_at.desc())
            .limit(100)
        )
    ]


def client(row):
    root = get_settings().data_dir
    if row.kind == "browser":
        return BrowserClient(root), BrowserOwner(
            user_id=row.user_id, workspace_id=row.workspace_id, run_id=row.run_id
        )
    return DesktopClient(root), DesktopOwner(
        user_id=row.user_id, workspace_id=row.workspace_id, run_id=row.run_id
    )


def reserve_session(user_id, workspace_id, command, run_id):
    native = runtime.status()
    if native.status != "running":
        raise AppError(
            "automation_runtime_required", "请用户先明确启动原生执行器。", 409, "dependency"
        )
    identifier = str(command.request_id)
    with Session(engine()) as db, db.begin():
        from sqlalchemy import text

        from workbench.modules.runs.domain import ClientRequest
        from workbench.modules.runs.service import request_lock_key

        db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {
                "key": request_lock_key(user_id, identifier),
            },
        )
        if run_id:
            db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            assert_owned(db, run_id)
        workspace_row = workspace(db, user_id, workspace_id)
        db.refresh(workspace_row, with_for_update=True)
        existing = db.get(AutomationSession, identifier)
        if existing:
            if (
                existing.user_id != user_id
                or existing.workspace_id != workspace_id
                or existing.run_id != run_id
                or existing.command != command.model_dump(mode="json")
            ):
                raise AppError("automation_request_conflict", "会话请求标识的参数或身份不同。", 409)
            db.expunge(existing)
            return existing, None, None, False
        closed = db.scalar(
            select(ClientRequest).where(
                ClientRequest.user_id == user_id,
                ClientRequest.request_id == identifier,
            )
        )
        if closed:
            raise AppError("request_expired", "此请求已关闭，请明确创建新的会话请求。", 410)
        policy = current(db, user_id, workspace_id)
        if policy is None:
            raise denied("请用户先配置电脑操作授权。")
        row = AutomationSession(
            id=identifier,
            user_id=user_id,
            workspace_id=workspace_id,
            run_id=run_id,
            policy_id=policy.id,
            policy_version=policy.version,
            kind=command.kind,
            mode=command.mode,
            runtime_nonce=native.instance_id,
            command=command.model_dump(mode="json"),
            expires_at=now() + timedelta(seconds=command.ttl_seconds),
        )
        db.add(row)
        db.flush()
        _, config = assert_policy(db, row, transfer=True)
        if row.kind == "desktop":
            row.expires_at = min(row.expires_at, policy.grant_expires_at)
        if row.kind == "desktop" and row.mode == "control":
            db.execute(
                insert(DesktopWriterLease)
                .values(
                    node_id="local-desktop",
                    session_id=None,
                    lease_id=str(uuid4()),
                    epoch=1,
                    expires_at=now(),
                    revoked=True,
                )
                .on_conflict_do_nothing(index_elements=[DesktopWriterLease.node_id])
            )
            holder = db.get(DesktopWriterLease, "local-desktop", with_for_update=True)
            # An expired holder can still have a pending native call. Only actual
            # resource cleanup clears session_id, including after revocation.
            if holder.session_id:
                raise AppError(
                    "desktop_writer_busy", "桌面已有未确认释放的写入者，请先停止或接管。", 409
                )
            holder.session_id, holder.lease_id = row.id, str(uuid4())
            holder.epoch += 1
            holder.expires_at, holder.revoked = row.expires_at, False
        db.flush()
        db.expunge(row)
        db.expunge(policy)
        return row, policy, config, True


def session_failure(identifier, error):
    with Session(engine()) as db, db.begin():
        row = db.get(AutomationSession, identifier, with_for_update=True)
        # A successful companion publication racing its lost HTTP response is
        # stronger evidence than this caller's transport exception.
        if row.status == "creating":
            row.status = "outcome_unknown"
        row.error = {"code": error.code, "message": error.message}


async def create_session(user_id, workspace_id, command, *, run_id=None):
    row, policy, config, fresh = await asyncio.to_thread(
        reserve_session, user_id, workspace_id, command, run_id
    )
    if fresh:
        wire, owner = client(row)
        try:
            if row.kind == "browser":
                await wire.create(
                    owner,
                    BrowserCreate(
                        request_id=row.id,
                        enabled=True,
                        allowed_origins=config.browser_origins,
                        local_origins=config.browser_local_origins,
                        ttl_seconds=command.ttl_seconds,
                    ),
                )
            else:
                await wire.create(
                    owner,
                    DesktopCreate(
                        request_id=row.id,
                        mode=row.mode,
                        target_window_id=config.target_window_id,
                        ttl_seconds=command.ttl_seconds,
                        manual_takeover_ack=config.manual_takeover_ack,
                    ),
                    DesktopGrant(
                        grant_id=policy.id,
                        capture=config.desktop_capture,
                        control=config.desktop_control,
                        target_window_id=config.target_window_id,
                        target_window_pid=config.target_window_pid,
                        expires_at=policy.grant_expires_at,
                    ),
                )
        except AppError as exc:
            await asyncio.to_thread(session_failure, row.id, exc)
            raise
        except asyncio.CancelledError:
            await asyncio.to_thread(
                session_failure,
                row.id,
                AppError(
                    "automation_create_unknown",
                    "创建会话的传输被中断，等待实际原生状态核对。",
                    409,
                    "outcome_unknown",
                ),
            )
            raise
    with Session(engine()) as db:
        return session_view(get_session(db, user_id, workspace_id, row.id, run_id=run_id))


def action_view(row):
    return dto.ActionView(
        id=row.id, session_id=row.session_id, kind=row.kind, status=row.status, result=row.result
    )


def reserve_action(user_id, workspace_id, identifier, command, kind, run_id):
    request_id = str(command.request_id)
    digest = hashlib.sha256(
        json.dumps(command.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    with Session(engine()) as db, db.begin():
        if run_id:
            db.scalar(select(Run).where(Run.id == run_id).with_for_update())
            assert_owned(db, run_id)
        row = get_session(db, user_id, workspace_id, identifier, run_id=run_id, lock=True)
        previous = db.get(AutomationAction, request_id)
        if previous:
            if (
                previous.user_id != user_id
                or previous.session_id != row.id
                or previous.arguments_hash != digest
                or previous.kind != kind
            ):
                raise AppError("automation_action_conflict", "动作标识的参数或会话不同。", 409)
            saved = action_view(previous)
            db.expunge(row)
            return row, saved, None, False
        assert_policy(db, row, transfer=True)
        if row.native_id is None:
            raise AppError(
                "automation_native_unknown",
                "会话尚无已确认原生身份，不能执行动作。",
                409,
                "outcome_unknown",
            )
        if runtime.status().instance_id != row.runtime_nonce:
            raise AppError(
                "automation_runtime_changed",
                "执行器实例已改变，不能自动重建旧会话。",
                409,
                "outcome_unknown",
            )
        if run_id:
            assert_owned(db, run_id)
        action = AutomationAction(
            id=request_id, user_id=user_id, session_id=row.id, arguments_hash=digest, kind=kind
        )
        db.add(action)
        db.flush()
        lease_value = None
        if row.kind == "desktop" and kind == "action":
            holder = db.get(DesktopWriterLease, "local-desktop")
            if holder is None or holder.session_id != row.id or holder.revoked:
                raise denied("此会话没有当前桌面输入租约。")
            lease_value = DesktopLease(
                lease_id=holder.lease_id,
                grant_id=row.policy_id,
                epoch=holder.epoch,
                expires_at=holder.expires_at,
            )
        db.expunge(row)
        return row, action_view(action), lease_value, True


def finish_action(identifier, result, *, status="succeeded", observation=None):
    with Session(engine()) as db, db.begin():
        reference = db.get(AutomationAction, identifier)
        session = db.get(AutomationSession, reference.session_id)
        if session.run_id:
            db.scalar(select(Run).where(Run.id == session.run_id).with_for_update())
        row = db.get(AutomationSession, reference.session_id, with_for_update=True)
        action = db.scalar(
            select(AutomationAction)
            .where(AutomationAction.id == identifier)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        action.status, action.result = status, result
        if observation:
            row.observation = observation
        return action_view(action)


def publish_capture(row, request_id, path, observation):
    root = storage_path(
        f"workspaces/{row.workspace_id}/staging/{row.run_id or row.id}/{request_id}"
    )
    source = path.resolve()
    if not source.is_relative_to(root) or path.is_symlink() or source.stat().st_size > 16 * 1024**2:
        raise AppError("capture_output_invalid", "截图输出不属于本次受管目录。", 409)
    content = source.read_bytes()
    with Image.open(io.BytesIO(content)) as image:
        if image.format != "PNG" or image.width * image.height > 32_000_000:
            raise AppError("capture_image_invalid", "实际截图格式或大小超出预算。", 409)
        original_size = image.size
        image.thumbnail((1600, 1600))
        preview = io.BytesIO()
        image.convert("RGB").save(preview, format="PNG")
        preview_size = image.size
    payloads = {"screenshot": content, "preview": preview.getvalue()}
    refs = {}
    for kind, data in payloads.items():
        identifier = str(uuid5(UUID(request_id), kind))
        digest = hashlib.sha256(data).hexdigest()
        relative = f"workspaces/{row.workspace_id}/artifacts/{identifier}/capture.png"
        metadata = {
            "automation_session_id": row.id,
            "observation_id": observation["observation_id"],
            "original_width": original_size[0],
            "original_height": original_size[1],
            "source_refs": [
                {"automation_session_id": row.id, "observation_id": observation["observation_id"]}
            ],
        }
        with Session(engine()) as db, db.begin():
            if row.run_id:
                db.scalar(select(Run).where(Run.id == row.run_id).with_for_update())
                assert_owned(db, row.run_id)
            current_row = get_session(db, row.user_id, row.workspace_id, row.id, lock=True)
            assert_policy(db, current_row, transfer=bool(row.run_id))
            artifact = db.get(Artifact, identifier)
            if artifact is None:
                artifact = Artifact(
                    id=identifier,
                    user_id=row.user_id,
                    workspace_id=row.workspace_id,
                    run_id=row.run_id,
                    display_name=f"{row.kind}-{kind}-{request_id[:8]}.png",
                    mime_type="image/png",
                    byte_size=len(data),
                    content_hash=digest,
                    storage_path=relative,
                    kind=kind,
                    status="publishing",
                    metadata_json=metadata,
                )
                db.add(artifact)
            elif artifact.content_hash != digest:
                raise AppError("capture_publication_conflict", "本次截图发布内容冲突。", 409)
        destination = storage_path(relative)
        if destination.exists():
            if file_digest(destination) != digest:
                raise AppError("capture_publication_conflict", "截图存储内容冲突。", 409)
        else:
            publish_bytes(destination, data)
        with Session(engine()) as db, db.begin():
            if row.run_id:
                db.scalar(select(Run).where(Run.id == row.run_id).with_for_update())
                assert_owned(db, row.run_id)
            current_row = get_session(db, row.user_id, row.workspace_id, row.id, lock=True)
            assert_policy(db, current_row)
            db.get(Artifact, identifier).status = "available"
        refs["artifact_id" if kind == "screenshot" else "preview_artifact_id"] = identifier
        if kind == "preview":
            refs["preview_version"] = digest
    return {
        **refs,
        "original_width": original_size[0],
        "original_height": original_size[1],
        "preview_width": preview_size[0],
        "preview_height": preview_size[1],
    }


def publish_download(row, request_id, native):
    from workbench.modules.files.processors import detect_type

    root = storage_path(
        f"workspaces/{row.workspace_id}/staging/{row.run_id or row.id}/{request_id}"
    )
    expected = root / f"browser-download-{request_id}.candidate"
    candidate = Path(native.candidate_path)
    if candidate != expected or candidate.is_symlink() or not candidate.is_file():
        raise AppError("download_output_invalid", "下载候选不属于本次受管目录。", 409)
    if candidate.stat().st_size != native.bytes or not 0 <= native.bytes <= 16 * 1024**2:
        raise AppError("download_output_invalid", "下载长度不符合已确认回执。", 409)
    data = candidate.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != native.sha256:
        raise AppError("download_output_invalid", "下载候选在发布前已变化。", 409)
    mime, detected = detect_type(candidate, native.filename)
    identifier = str(uuid5(UUID(request_id), "browser_download"))
    relative = f"workspaces/{row.workspace_id}/artifacts/{identifier}/original"
    metadata = {
        "automation_session_id": row.id,
        "source_url": native.source_url,
        "final_url": native.final_url,
        "detected_format": detected,
        "response_mime_source": native.mime_source,
        "cookies_inherited": False,
    }
    with Session(engine()) as db, db.begin():
        if row.run_id:
            db.scalar(select(Run).where(Run.id == row.run_id).with_for_update())
            assert_owned(db, row.run_id)
        current_row = get_session(db, row.user_id, row.workspace_id, row.id, lock=True)
        assert_policy(db, current_row)
        artifact = db.get(Artifact, identifier)
        if artifact is None:
            db.add(
                Artifact(
                    id=identifier,
                    user_id=row.user_id,
                    workspace_id=row.workspace_id,
                    run_id=row.run_id,
                    display_name=native.filename,
                    mime_type=mime,
                    declared_mime=native.mime,
                    byte_size=len(data),
                    content_hash=digest,
                    storage_path=relative,
                    kind="original",
                    status="publishing",
                    metadata_json=metadata,
                )
            )
        elif artifact.content_hash != digest:
            raise AppError("download_publication_conflict", "本次下载发布内容不一致。", 409)
    target = storage_path(relative)
    if target.exists():
        if file_digest(target) != digest:
            raise AppError("download_publication_conflict", "下载原件存储内容不一致。", 409)
    else:
        publish_bytes(target, data)
    with Session(engine()) as db, db.begin():
        if row.run_id:
            db.scalar(select(Run).where(Run.id == row.run_id).with_for_update())
            assert_owned(db, row.run_id)
        assert_policy(db, get_session(db, row.user_id, row.workspace_id, row.id, lock=True))
        db.get(Artifact, identifier).status = "available"
        if row.run_id:
            from workbench.modules.runs.service import emit

            emit(
                db, row.run_id, "artifact.created", {"artifact_id": identifier, "kind": "original"}
            )
    candidate.unlink()
    return {
        "artifact_id": identifier,
        "version": digest,
        "filename": native.filename,
        "bytes": len(data),
    }


async def download(user_id, workspace_id, identifier, command, *, run_id=None):
    row, action, _, fresh = await asyncio.to_thread(
        reserve_action, user_id, workspace_id, identifier, command, "download", run_id
    )
    if not fresh:
        return action
    try:
        if row.kind != "browser":
            raise AppError("download_browser_required", "只有受管浏览器支持链接下载。", 422)
        wire, owner = client(row)
        output = storage_path(
            f"workspaces/{row.workspace_id}/staging/{row.run_id or row.id}/{action.id}"
        )
        output.mkdir(mode=0o700, parents=True, exist_ok=True)
        native = await wire.download(
            owner,
            row.native_id,
            BrowserDownload(**command.model_dump(mode="json"), output_dir=str(output)),
        )
        artifact = await asyncio.to_thread(publish_download, row, action.id, native)
        result = native.model_dump(mode="json", exclude={"candidate_path"})
        result.update(session_id=row.id, artifact=artifact)
        return await asyncio.to_thread(finish_action, action.id, result)
    except AppError as exc:
        await asyncio.to_thread(
            finish_action,
            action.id,
            {"error": {"code": exc.code, "message": exc.message}},
            status="outcome_unknown" if exc.kind == "outcome_unknown" else "failed",
        )
        raise
    except asyncio.CancelledError:
        await asyncio.shield(
            asyncio.to_thread(
                finish_action,
                action.id,
                {
                    "error": {
                        "code": "download_interrupted",
                        "message": "下载或发布被中断，请核对。",
                    }
                },
                status="outcome_unknown",
            )
        )
        raise


async def observe(user_id, workspace_id, identifier, command, *, run_id=None):
    row, action, _, fresh = await asyncio.to_thread(
        reserve_action, user_id, workspace_id, identifier, command, "observe", run_id
    )
    if not fresh:
        return action
    wire, owner = client(row)
    output = storage_path(
        f"workspaces/{row.workspace_id}/staging/{row.run_id or row.id}/{action.id}"
    )
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        if row.kind == "browser":
            native = await wire.observe(
                owner,
                row.native_id,
                BrowserObserve(
                    screenshot=command.screenshot,
                    output_dir=str(output) if command.screenshot else None,
                ),
            )
        else:
            native = await wire.observe(
                owner, row.native_id, DesktopObserve(output_dir=str(output))
            )
        result = native.model_dump(mode="json")
        result["session_id"] = row.id
        screenshot_path = result.pop("screenshot_path", None)
        if screenshot_path:
            from pathlib import Path

            result["screenshot"] = await asyncio.to_thread(
                publish_capture, row, action.id, Path(screenshot_path), result
            )
        return await asyncio.to_thread(finish_action, action.id, result, observation=result)
    except AppError as exc:
        await asyncio.to_thread(
            finish_action,
            action.id,
            {"error": {"code": exc.code, "message": exc.message}},
            status="outcome_unknown" if exc.kind == "outcome_unknown" else "failed",
        )
        raise
    except asyncio.CancelledError:
        await asyncio.shield(
            asyncio.to_thread(
                finish_action,
                action.id,
                {
                    "error": {
                        "code": "capture_interrupted",
                        "message": "观察被中断，请核对原动作状态。",
                    }
                },
                status="outcome_unknown",
            )
        )
        raise


async def act(user_id, workspace_id, identifier, command, *, kind, run_id=None):
    if str(command.session_id) != identifier:
        raise AppError("automation_action_session", "动作必须指向当前持久会话。", 422)
    row, action, lease, fresh = await asyncio.to_thread(
        reserve_action, user_id, workspace_id, identifier, command, "action", run_id
    )
    if not fresh:
        return action
    if row.kind != kind:
        await asyncio.to_thread(
            finish_action,
            action.id,
            {"error": {"code": "automation_action_kind", "message": "动作类型与会话不一致。"}},
            status="failed",
        )
        raise AppError("automation_action_kind", "动作类型与会话不一致。", 422)
    wire, owner = client(row)
    native_type = BrowserAction if kind == "browser" else DesktopAction
    native_command = native_type.model_validate(
        {**command.model_dump(mode="json"), "session_id": row.native_id}
    )
    try:
        result = (
            await wire.action(owner, native_command)
            if kind == "browser"
            else await wire.action(owner, native_command, lease)
        )
        data = result.model_dump(mode="json")
        data["session_id"] = row.id
        observation = data.get("observation")
        if observation:
            observation.pop("screenshot_path", None)
            observation["session_id"] = row.id
        return await asyncio.to_thread(
            finish_action,
            action.id,
            data,
            status={
                "executed": "succeeded",
                "not_executed": "failed",
                "outcome_unknown": "outcome_unknown",
            }[result.execution_status],
            observation=observation,
        )
    except AppError as exc:
        await asyncio.to_thread(
            finish_action,
            action.id,
            {"error": {"code": exc.code, "message": exc.message}},
            status="outcome_unknown",
        )
        raise
    except asyncio.CancelledError:
        await asyncio.shield(
            asyncio.to_thread(
                finish_action,
                action.id,
                {
                    "error": {
                        "code": "native_action_interrupted",
                        "message": "动作传输中断，不能自动重放。",
                    }
                },
                status="outcome_unknown",
            )
        )
        raise


def mark_stop(user_id, workspace_id, identifier, run_id):
    with Session(engine()) as db, db.begin():
        row = get_session(db, user_id, workspace_id, identifier, run_id=run_id, lock=True)
        if row.status not in CLOSED:
            row.status = "stopping"
            holder = db.get(DesktopWriterLease, "local-desktop", with_for_update=True)
            if holder and holder.session_id == row.id:
                holder.revoked = True
        db.expunge(row)
        return row


async def stop_session(user_id, workspace_id, identifier, *, takeover=False, run_id=None):
    row = await asyncio.to_thread(mark_stop, user_id, workspace_id, identifier, run_id)
    if row.native_id and row.status not in CLOSED:
        wire, owner = client(row)
        result = (
            await wire.stop(owner, row.native_id, takeover=takeover)
            if row.kind == "desktop"
            else await wire.stop(owner, row.native_id)
        )
        from workbench.modules.automation.native import confirm_closed

        await asyncio.to_thread(confirm_closed, row.id, result)
    with Session(engine()) as db:
        return session_view(get_session(db, user_id, workspace_id, identifier, run_id=run_id))


def request_cleanup(db, run_id):
    pending = 0
    for row in db.scalars(
        select(AutomationSession)
        .where(AutomationSession.run_id == run_id, AutomationSession.status.in_(LIVE))
        .order_by(AutomationSession.id)
        .with_for_update()
    ):
        row.status = "stopping"
        pending += 1
    holder = db.get(DesktopWriterLease, "local-desktop", with_for_update=True)
    if holder and holder.session_id:
        row = db.get(AutomationSession, holder.session_id)
        if row and row.run_id == run_id:
            holder.revoked = True
    return pending


def cleanup_pending(db, run_id):
    return (
        db.scalar(
            select(AutomationSession.id)
            .where(AutomationSession.run_id == run_id, AutomationSession.status.in_(LIVE))
            .limit(1)
        )
        is not None
    )


async def capabilities(user_id, workspace_id):
    with Session(engine()) as db:
        workspace(db, user_id, workspace_id)
        from workbench.modules.models.domain import ModelConnection

        models = [
            {
                "id": row.id,
                "version": row.version,
                "name": row.name,
                "endpoint": row.endpoint,
                "target": f"{row.id}:{row.version}",
            }
            for row in db.scalars(select(ModelConnection).where(ModelConnection.user_id == user_id))
        ]
    native = await asyncio.to_thread(runtime.status)
    browser, desktop, foreground = None, None, None
    if native.status == "running":

        async def load_browser():
            return (await BrowserClient(get_settings().data_dir).capability()).model_dump(
                mode="json"
            )

        async def load_desktop():
            return (await DesktopClient(get_settings().data_dir).probe()).model_dump(mode="json")

        async def load_foreground():
            from pydantic import BaseModel

            class Foreground(BaseModel):
                window_id: str | None
                window_pid: int | None
                locked: bool | None
                geometry_id: str

            return (
                await DesktopClient(get_settings().data_dir)._request(
                    "GET", "/foreground", None, Foreground
                )
            ).model_dump(mode="json")

        loaded = await asyncio.gather(
            load_browser(), load_desktop(), load_foreground(), return_exceptions=True
        )
        browser, desktop, foreground = [
            value
            if isinstance(value, dict)
            else {
                "status": "unavailable",
                "reason": value.message if isinstance(value, AppError) else "原生能力读取失败。",
            }
            for value in loaded
        ]
    return dto.AutomationCapabilities(
        runtime=native,
        browser=browser,
        desktop=desktop,
        foreground=foreground,
        remote_models=models,
        limitations=[
            "可选原生执行器按需启动；每次网页/输入动作仍需独立批准。",
            "网络代理是应用约束，不提供操作系统级沙箱。",
            "只能发现部分人工输入；停止不能撤销已发送的页面或原生动作。",
            "Windows/macOS真实设备验证待完成，Wayland需要Portal适配。",
        ],
    )
