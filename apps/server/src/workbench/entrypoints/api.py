"""Local HTTP and replayable SSE entry point; business state belongs to services."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from workbench import __version__
from workbench.composition import tables  # noqa: F401
from workbench.config import PROJECT_ROOT, get_settings
from workbench.contracts.errors import AppError
from workbench.contracts.requests import (
    ApprovalDecision,
    CancelRun,
    CapabilitiesUpdate,
    EmptyCommand,
    InputPlan,
    Login,
    ModelCreate,
    ModelUpdate,
    OwnerCreate,
    QuestionAnswer,
    RunCreate,
    ThreadCreate,
    ThreadFork,
    WorkspaceCreate,
    WorkspaceLifecycle,
    WorkspaceRename,
)
from workbench.contracts.responses import (
    ERROR_RESPONSES,
    SSE_RESPONSE,
    Approval,
    ConversationSnapshot,
    LiveStatus,
    Message,
    ModelConnection,
    OwnerSession,
    Question,
    RequestClosure,
    RequestResult,
    SetupStatus,
    SystemStatus,
    Thread,
    Workspace,
    register_event_schema,
)
from workbench.contracts.responses import (
    Run as RunResponse,
)
from workbench.entrypoints.dependencies import COOKIE, DB, SNAPSHOT, USER, WRITES
from workbench.entrypoints.dependencies import authenticate as _authenticate
from workbench.entrypoints.routes.account import router as account_router
from workbench.entrypoints.routes.analytics import router as analytics_router
from workbench.entrypoints.routes.automation import router as automation_router
from workbench.entrypoints.routes.collaboration import router as collaboration_router
from workbench.entrypoints.routes.credentials import router as credentials_router
from workbench.entrypoints.routes.diagnostics import router as diagnostics_router
from workbench.entrypoints.routes.files import router as files_router
from workbench.entrypoints.routes.generated import router as generated_router
from workbench.entrypoints.routes.maintenance import router as maintenance_router
from workbench.entrypoints.routes.mcp import router as mcp_router
from workbench.entrypoints.routes.memory import router as memory_router
from workbench.entrypoints.routes.preferences import router as preferences_router
from workbench.entrypoints.routes.processes import router as processes_router
from workbench.entrypoints.routes.sandbox import router as sandbox_router
from workbench.entrypoints.routes.search import router as search_router
from workbench.infrastructure.database import database_ready, engine, now
from workbench.infrastructure.http_limits import BodyLimitMiddleware
from workbench.infrastructure.mutations import write_gate
from workbench.infrastructure.secrets import csrf_token
from workbench.modules import conversation_service, identity_service, workspace_service
from workbench.modules.files.inputs import ArtifactInputPlan, input_plan
from workbench.modules.identity import User
from workbench.modules.models import service as model_service
from workbench.modules.runs import service as run_service
from workbench.modules.runs.domain import ClientRequest, Run, RunEvent, WorkerHeartbeat

ACCESS = get_settings().access_policy()
DIST = get_settings().frontend_dir or PROJECT_ROOT / "apps/web/dist"


@asynccontextmanager
async def lifespan(application):
    await asyncio.to_thread(ACCESS.validate_tls_files)
    if ACCESS.startup_owner_required:
        with Session(engine()) as db:
            if not identity_service.has_owner(db):
                raise RuntimeError("启用局域网前，必须先在回环地址完成所有者初始化。")
    yield


app = FastAPI(
    title="AI 工作台",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ACCESS.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)
api = APIRouter(prefix="/api/v1", responses=ERROR_RESPONSES)


def _error_response(request: Request, error: AppError) -> JSONResponse:
    retry_seconds = error.details.get("retry_after_seconds")
    retry_ms = (
        retry_seconds * 1000 if type(retry_seconds) is int and 0 < retry_seconds <= 3600 else None
    )
    return JSONResponse(
        status_code=error.status,
        headers={"Retry-After": str(retry_seconds)} if retry_ms else None,
        content={
            "error": {
                "kind": error.kind,
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
                "retry_after_ms": retry_ms,
                "details_ref": None,
            },
            "request_id": None,
            "trace_id": getattr(request.state, "trace_id", str(uuid4())),
        },
    )


@app.middleware("http")
async def security_and_trace(request: Request, call_next):
    request.state.trace_id = str(uuid4())
    gate = None
    try:
        host = request.headers.get("host", "")
        if not ACCESS.allows_host(host, request.url.scheme):
            raise AppError("invalid_host", "请求地址不符合已配置的工作台访问来源。", 400, "policy")
        if request.method in WRITES:
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
                raise AppError("cross_site_request", "拒绝跨站写入请求。", 403, "policy")
            if origin and not ACCESS.allows_origin(origin, request.url.scheme, host):
                raise AppError("invalid_origin", "请求来源不受允许。", 403, "policy")
            if not request.url.path.startswith("/api/v1/maintenance/"):
                gate = write_gate(
                    interrupt_maintenance=request.url.path.endswith(("/cancel", "/stop"))
                )
                await asyncio.to_thread(gate.__enter__)
        response = await call_next(request)
    except AppError as error:
        response = _error_response(request, error)
    except (ValueError, SQLAlchemyError):
        response = _error_response(
            request,
            AppError(
                "request_unavailable",
                "请求暂时无法完成，请检查工作台状态。",
                503,
                "dependency",
                True,
            ),
        )
    except Exception:
        response = _error_response(
            request, AppError("internal_error", "工作台暂时无法完成请求。", 500, "internal")
        )
    finally:
        if gate is not None:
            await asyncio.to_thread(gate.__exit__, None, None, None)
    response.headers["X-Trace-ID"] = request.state.trace_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    if ACCESS.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=86400"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(AppError)
async def app_error(request: Request, error: AppError):
    return _error_response(request, error)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, error: SQLAlchemyError):
    return _error_response(
        request,
        AppError(
            "database_unavailable",
            "数据库操作未完成，请检查本机数据库状态。",
            503,
            "dependency",
            True,
        ),
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    # Pydantic error inputs may contain passwords and provider credentials.
    return _error_response(
        request, AppError("invalid_request", "请求格式或字段不符合要求，请检查输入内容。", 422)
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    return _error_response(
        request,
        AppError(
            "not_found" if error.status_code == 404 else "http_error",
            "请求的资源不存在。" if error.status_code == 404 else "请求无法完成。",
            error.status_code,
        ),
    )


def _identity(user: User, token: str) -> dict:
    return {"user": {"id": user.id, "username": user.username}, "csrf_token": csrf_token(token)}


def _set_cookie(response: Response, token: str):
    settings = get_settings()
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="strict",
        max_age=settings.session_hours * 3600,
        path="/",
    )


@app.get("/health/live", response_model=LiveStatus, responses=ERROR_RESPONSES)
def health():
    return {"status": "alive", "version": __version__}


@api.get("/setup/status", response_model=SetupStatus)
def setup_status():
    ready, configured = database_ready(), False
    if ready:
        try:
            with Session(engine()) as db:
                configured = identity_service.has_owner(db)
        except (SQLAlchemyError, AppError):
            ready = False
    return {"configured": configured, "database_ready": ready, "version": __version__}


@api.post("/setup/owner", status_code=201, response_model=OwnerSession)
def initialize(command: OwnerCreate, request: Request, response: Response, db: DB):
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise AppError("setup_local_only", "首次初始化只能在本机完成。", 403, "policy")
    user, token = identity_service.create_owner(
        db, command.username, command.password.get_secret_value()
    )
    result = _identity(user, token)
    db.commit()
    _set_cookie(response, token)
    return result


@api.post("/auth/login", response_model=OwnerSession)
def login(command: Login, request: Request, response: Response, db: DB):
    user, token = identity_service.login(
        db,
        command.username,
        command.password.get_secret_value(),
        peer=request.client.host if request.client else None,
    )
    result = _identity(user, token)
    db.commit()
    _set_cookie(response, token)
    return result


@api.get("/auth/session", response_model=OwnerSession)
def auth_session(request: Request, db: DB, user: USER):
    return _identity(user, request.cookies[COOKIE])


@api.post("/auth/logout", status_code=204)
def logout(command: EmptyCommand, request: Request, response: Response, db: DB, user: USER):
    identity_service.logout(db, request.cookies.get(COOKIE))
    db.commit()
    response.delete_cookie(
        COOKIE, path="/", httponly=True, secure=get_settings().secure_cookies, samesite="strict"
    )


@api.get("/workspaces", response_model=list[Workspace])
def workspaces(db: DB, user: USER, include_archived: bool = False):
    return workspace_service.list_workspaces(db, user.id, include_archived=include_archived)


@api.post("/workspaces", status_code=201, response_model=Workspace)
def create_workspace(command: WorkspaceCreate, db: DB, user: USER):
    result = workspace_service.create(db, user.id, command.name, command.root_path)
    db.commit()
    return result


@api.post("/workspaces/{workspace_id}/rename", response_model=Workspace)
def rename_workspace(workspace_id: UUID, command: WorkspaceRename, db: DB, user: USER):
    result = workspace_service.update(db, user.id, str(workspace_id), command, "rename")
    db.commit()
    return result


def _workspace_lifecycle(workspace_id, action, command, db, user):
    result = workspace_service.update(db, user.id, str(workspace_id), command, action)
    db.commit()
    return result


@api.post("/workspaces/{workspace_id}/archive", response_model=Workspace)
def archive_workspace(workspace_id: UUID, command: WorkspaceLifecycle, db: DB, user: USER):
    return _workspace_lifecycle(workspace_id, "archive", command, db, user)


@api.post("/workspaces/{workspace_id}/restore", response_model=Workspace)
def restore_workspace(workspace_id: UUID, command: WorkspaceLifecycle, db: DB, user: USER):
    return _workspace_lifecycle(workspace_id, "restore", command, db, user)


@api.post("/workspaces/{workspace_id}/remove", response_model=Workspace)
def remove_workspace(workspace_id: UUID, command: WorkspaceLifecycle, db: DB, user: USER):
    return _workspace_lifecycle(workspace_id, "remove", command, db, user)


@api.get("/workspaces/{workspace_id}/threads", response_model=list[Thread])
def threads(workspace_id: UUID, db: DB, user: USER):
    return conversation_service.list_threads(db, user.id, str(workspace_id))


@api.post("/workspaces/{workspace_id}/threads", status_code=201, response_model=Thread)
def create_thread(workspace_id: UUID, command: ThreadCreate, db: DB, user: USER):
    result = conversation_service.create_thread(db, user.id, str(workspace_id), command.title)
    db.commit()
    return result


@api.get("/threads/{thread_id}/messages", response_model=list[Message])
def messages(thread_id: UUID, db: DB, user: USER):
    return conversation_service.list_messages(db, user.id, str(thread_id))


@api.post("/threads/{thread_id}/fork", status_code=201, response_model=Thread)
def fork_thread(thread_id: UUID, command: ThreadFork, db: DB, user: USER):
    result = conversation_service.fork_thread(db, user.id, str(thread_id), command)
    db.commit()
    return result


@api.get("/threads/{thread_id}/export", response_class=Response)
def export_thread(
    thread_id: UUID, db: DB, user: USER, format: Literal["markdown", "json"] = "markdown"
):
    from workbench.modules.conversation_exports import render

    payload, filename, media_type = render(db, user.id, str(thread_id), format)
    return Response(
        payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@api.get("/model-connections", response_model=list[ModelConnection])
def models(db: DB, user: USER):
    return model_service.list_connections(db, user.id)


@api.post("/model-connections", status_code=201, response_model=ModelConnection)
def create_model(command: ModelCreate, db: DB, user: USER):
    values = command.model_dump(exclude={"api_key"})
    values["api_key"] = command.api_key.get_secret_value() if command.api_key else None
    result = model_service.create(db, user.id, values)
    db.commit()
    return result


@api.post("/model-connections/{connection_id}/configuration", response_model=ModelConnection)
def update_model(connection_id: UUID, command: ModelUpdate, db: DB, user: USER):
    values = command.model_dump(exclude={"api_key"})
    values["api_key"] = command.api_key.get_secret_value() if command.api_key else None
    result = model_service.update(db, user.id, str(connection_id), values)
    db.commit()
    return result


@api.post("/model-connections/{connection_id}/capabilities", response_model=ModelConnection)
def capabilities_model(connection_id: UUID, command: CapabilitiesUpdate, db: DB, user: USER):
    result = model_service.declare_capabilities(db, user.id, str(connection_id), command)
    db.commit()
    return result


@api.post(
    "/model-connections/{connection_id}/capabilities/import-managed", response_model=ModelConnection
)
def import_capabilities_model(connection_id: UUID, command: EmptyCommand, db: DB, user: USER):
    result = model_service.import_managed(db, user.id, str(connection_id))
    db.commit()
    return result


@api.post("/workspaces/{workspace_id}/input-plan", response_model=list[ArtifactInputPlan])
def plan_inputs(workspace_id: UUID, command: InputPlan, db: DB, user: USER):
    model = model_service.get(db, user.id, str(command.model_connection_id))
    return input_plan(
        db, user.id, str(workspace_id), model, [str(item) for item in command.artifact_ids]
    )


@api.post("/model-connections/{connection_id}/checks", response_model=ModelConnection)
async def check_model(connection_id: UUID, command: EmptyCommand, request: Request):
    # Network I/O is async; this session lasts only for the connection check request.
    with Session(engine(), expire_on_commit=False) as db:
        try:
            user = _authenticate(request, db)
            result = await model_service.check_connection(db, user.id, str(connection_id))
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise


@api.get(
    "/threads/{thread_id}/runs", response_model=list[RunResponse], response_model_exclude_unset=True
)
def runs(thread_id: UUID, request: Request, db: SNAPSHOT):
    user = _authenticate(request, db)
    conversation_service.get_thread(db, user.id, str(thread_id))
    rows = db.scalars(
        select(Run)
        .where(Run.thread_id == str(thread_id), Run.user_id == user.id)
        .order_by(Run.created_at.desc())
    ).all()
    return [run_service.view(db, row) for row in rows]


@api.post(
    "/threads/{thread_id}/runs",
    status_code=201,
    response_model=RunResponse,
    response_model_exclude_unset=True,
)
def create_run(thread_id: UUID, command: RunCreate, db: DB, user: USER):
    values = command.model_dump(mode="json")
    if command.options is not None:
        values["options"] = command.options.model_dump(mode="json", exclude_unset=True)
    result = run_service.create_run(db, user.id, str(thread_id), values)
    db.commit()
    return result


@api.get("/runs/{run_id}", response_model=RunResponse, response_model_exclude_unset=True)
def run_snapshot(run_id: UUID, request: Request, db: SNAPSHOT):
    user = _authenticate(request, db)
    return run_service.view(db, run_service.get(db, user.id, str(run_id)))


@api.get("/runs/{run_id}/conversation-snapshot", response_model=ConversationSnapshot)
def conversation_snapshot(run_id: UUID, request: Request, db: SNAPSHOT):
    user = _authenticate(request, db)
    run = run_service.get(db, user.id, str(run_id))
    if run.thread_id is None:
        raise AppError("conversation_unavailable", "此任务没有对话记录。", 404)
    return {
        "run": run_service.view(db, run),
        "messages": conversation_service.list_messages(db, user.id, run.thread_id),
    }


@api.post("/runs/{run_id}/cancel", response_model=RunResponse, response_model_exclude_unset=True)
def cancel_run(run_id: UUID, command: CancelRun, db: DB, user: USER):
    result = run_service.cancel(db, user.id, str(run_id), str(command.request_id))
    db.commit()
    return result


@api.post(
    "/questions/{question_id}/answers", response_model=Question, response_model_exclude_unset=True
)
def answer_question(question_id: UUID, command: QuestionAnswer, db: DB, user: USER):
    result = run_service.answer_question(
        db, user.id, str(question_id), command.model_dump(mode="json")
    )
    db.commit()
    return result


@api.post("/approvals/{approval_id}/decisions", response_model=Approval)
def decide_approval(approval_id: UUID, command: ApprovalDecision, db: DB, user: USER):
    result = run_service.decide_approval(
        db, user.id, str(approval_id), command.model_dump(mode="json")
    )
    db.commit()
    return result


@api.get("/requests/{request_id}", response_model=RequestResult, response_model_exclude_unset=True)
def request_record(request_id: UUID, request: Request, db: SNAPSHOT):
    user = _authenticate(request, db)
    record = db.scalar(
        select(ClientRequest).where(
            ClientRequest.user_id == user.id, ClientRequest.request_id == str(request_id)
        )
    )
    if record is None:
        raise AppError("request_not_found", "请求记录不存在或不可访问。", 404)
    if record.expired:
        raise AppError("request_expired", "请求记录已过期，请明确创建新的请求。", 410)
    if record.resource_id:
        run = db.scalar(select(Run).where(Run.id == record.resource_id, Run.user_id == user.id))
        if run is not None:
            return run_service.view(db, run)
    return record.response


@api.post("/requests/{request_id}/close", response_model=RequestClosure)
def close_request(request_id: UUID, command: EmptyCommand, db: DB, user: USER):
    result = run_service.close_request(db, user.id, str(request_id))
    db.commit()
    return result


def _event_batch(token: str | None, run_id: str, after: int) -> list[dict]:
    with Session(engine().execution_options(isolation_level="REPEATABLE READ")) as db:
        user, _ = identity_service.authenticate(db, token)
        run = run_service.get(db, user.id, run_id)
        if after > run.last_event_seq:
            raise AppError("event_cursor_invalid", "事件游标超出当前任务，请重新读取快照。", 409)
        rows = db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.seq > after)
            .order_by(RunEvent.seq)
            .limit(100)
        ).all()
        if any(row.seq != after + index + 1 for index, row in enumerate(rows)) or (
            not rows and after < run.last_event_seq
        ):
            raise AppError("event_cursor_expired", "事件游标已不可回放，请重新读取快照。", 410)
        return jsonable_encoder([run_service.event_view(row) for row in rows])


@api.get("/runs/{run_id}/events", response_class=StreamingResponse, responses=SSE_RESPONSE)
async def events(run_id: UUID, request: Request, after: int = Query(default=0, ge=0)):
    identifier, cursor = str(run_id), after
    previous = request.headers.get("last-event-id")
    if previous:
        try:
            previous_run, previous_seq = previous.rsplit(":", 1)
            parsed_seq = int(previous_seq)
            if str(UUID(previous_run)) != identifier or parsed_seq < 0:
                raise ValueError
            cursor = max(cursor, parsed_seq)
        except ValueError:
            raise AppError(
                "event_cursor_invalid", "事件游标格式无效，请重新读取快照。", 400
            ) from None
    token = request.cookies.get(COOKIE)
    initial = await asyncio.to_thread(_event_batch, token, identifier, cursor)

    async def replay():
        position, batch = cursor, initial
        heartbeat = time.monotonic()
        while not await request.is_disconnected():
            for event in batch:
                position = event["seq"]
                data = json.dumps(event, ensure_ascii=False)
                yield f"id: {identifier}:{position}\ndata: {data}\n\n"
                heartbeat = time.monotonic()
            if not batch:
                if time.monotonic() - heartbeat >= 15:
                    yield ": heartbeat\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(0.75)
            try:
                # Re-authenticate and re-check ownership each batch; no session spans a yield.
                batch = await asyncio.to_thread(_event_batch, token, identifier, position)
            except (AppError, SQLAlchemyError):
                # Headers are already sent. Close without inventing a persistent run event;
                # reconnect receives the current HTTP authentication/dependency error.
                return

    return StreamingResponse(
        replay(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@api.get("/runs/{run_id}/ag-ui/events", response_class=StreamingResponse, responses=SSE_RESPONSE)
async def agui_events(run_id: UUID, request: Request, after: int = Query(default=0, ge=0)):
    from workbench.modules.events import translate

    identifier = str(run_id)
    cursor = (after, 2)  # Native after skips the whole durable event.
    previous = request.headers.get("last-event-id")
    if previous:
        try:
            source, seq, subindex = previous.rsplit(":", 2)
            parsed = (int(seq), int(subindex))
            if str(UUID(source)) != identifier or parsed[0] < 1 or parsed[1] not in {0, 1}:
                raise ValueError
            cursor = max(cursor, parsed)
        except ValueError:
            raise AppError(
                "event_cursor_invalid", "AG-UI游标必须指向本任务的真实事件分项。", 400
            ) from None
    token = request.cookies.get(COOKIE)
    source_after = max(0, cursor[0] - 1) if cursor[1] != 2 else cursor[0]
    initial = await asyncio.to_thread(_event_batch, token, identifier, source_after)
    if cursor[1] != 2 and (
        not initial
        or initial[0]["seq"] != cursor[0]
        or cursor[1]
        >= len(translate(identifier, initial[0]["seq"], initial[0]["type"], initial[0]["payload"]))
    ):
        raise AppError("event_cursor_invalid", "AG-UI游标分项不存在，请重新读取快照。", 409)

    async def replay():
        position, emitted, batch = source_after, cursor, initial
        heartbeat = time.monotonic()
        while not await request.is_disconnected():
            for event in batch:
                position = event["seq"]
                for index, item in enumerate(
                    translate(identifier, position, event["type"], event["payload"])
                ):
                    key = (position, index)
                    if key <= emitted:
                        continue
                    emitted = key
                    data = json.dumps(item, ensure_ascii=False)
                    yield f"id: {identifier}:{position}:{index}\ndata: {data}\n\n"
                    heartbeat = time.monotonic()
            if not batch:
                if time.monotonic() - heartbeat >= 15:
                    yield ": heartbeat\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(0.75)
            try:
                batch = await asyncio.to_thread(_event_batch, token, identifier, position)
            except (AppError, SQLAlchemyError):
                return

    return StreamingResponse(
        replay(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@api.get("/system/status", response_model=SystemStatus)
def system_status(db: DB, user: USER):
    latest = db.scalar(
        select(WorkerHeartbeat)
        .where(WorkerHeartbeat.queue == "agent")
        .order_by(WorkerHeartbeat.last_seen_at.desc())
        .limit(1)
    )
    last_seen = latest.last_seen_at if latest else None
    age = now() - last_seen if last_seen else None
    recent = age is not None and timedelta(0) <= age <= timedelta(
        seconds=max(30, get_settings().worker_lease_seconds * 2)
    )
    model = PROJECT_ROOT / "models/nvidia/Qwen3.8-Flash-Next-NVFP4/config.json"
    assets = (
        [
            {
                "name": "Qwen3.8-Flash-Next-NVFP4",
                "status": "downloaded",
                "verification": "previously_verified",
            }
        ]
        if model.is_file()
        else []
    )
    return {
        "version": __version__,
        "database": {"status": "ready"},
        "worker": {"status": "running" if recent else "offline", "last_seen_at": last_seen},
        "model_assets": assets,
    }


@api.get("/schema", response_model=dict)
def api_schema(user: USER):
    return app.openapi()


# OpenAPI generation is static: no sessions, settings credentials or runtime data are read.
_default_openapi = app.openapi


def _contract_openapi() -> dict:
    schema = _default_openapi()
    register_event_schema(schema)
    schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schemes["SessionCookie"] = {"type": "apiKey", "in": "cookie", "name": COOKIE}
    schemes["CSRFToken"] = {"type": "apiKey", "in": "header", "name": "X-CSRF-Token"}
    public = {"/api/v1/setup/status", "/api/v1/setup/owner", "/api/v1/auth/login"}
    for path, operations in schema.get("paths", {}).items():
        if not path.startswith("/api/v1/") or path in public:
            continue
        for method, operation in operations.items():
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            required = {"SessionCookie": []}
            if method.upper() in WRITES:
                required["CSRFToken"] = []
            operation["security"] = [required]
    return schema


app.openapi = _contract_openapi


app.include_router(api)
app.include_router(account_router)
app.include_router(automation_router)
app.include_router(memory_router)
app.include_router(mcp_router)
app.include_router(files_router)
app.include_router(generated_router)
app.include_router(search_router)
app.include_router(analytics_router)
app.include_router(collaboration_router)
app.include_router(credentials_router)
app.include_router(maintenance_router)
app.include_router(diagnostics_router)
app.include_router(preferences_router)
app.include_router(processes_router)
app.include_router(sandbox_router)


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    if path == "api" or path.startswith("api/") or path == "health" or path.startswith("health/"):
        raise AppError("not_found", "请求的接口不存在。", 404)
    root = DIST.resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root):
        raise AppError("not_found", "请求的资源不存在。", 404)
    if path and candidate.is_file():
        return FileResponse(candidate)
    if path.startswith("assets/") or (path and "." in path.rsplit("/", 1)[-1]):
        raise AppError("not_found", "请求的静态资源不存在。", 404)
    index = root / "index.html"
    if not index.is_file():
        raise AppError(
            "frontend_not_built", "前端尚未构建，请通过开发启动器准备。", 503, "dependency"
        )
    return FileResponse(index)
