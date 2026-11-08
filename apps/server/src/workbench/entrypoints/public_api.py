"""Independent Bearer-only listener for published agents.

This module intentionally never imports the owner ASGI application or its cookie
dependencies. Starting this app cannot bootstrap an owner or expose settings.
"""

import asyncio
import json
import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException
from workbench import __version__
from workbench.composition import tables  # noqa: F401
from workbench.contracts.errors import AppError
from workbench.contracts.requests import EmptyCommand
from workbench.infrastructure.database import engine
from workbench.infrastructure.http_limits import BodyLimitMiddleware
from workbench.infrastructure.mutations import write_gate
from workbench.modules.access import listener
from workbench.modules.access import service as access
from workbench.modules.agent_services import dto, public
from workbench.modules.changes import dto as change_dto
from workbench.modules.collaboration.dto import InputReceived
from workbench.modules.files import dto as file_dto
from workbench.modules.files import service as files
from workbench.modules.runs import service as runs
from workbench.modules.runs.domain import RunEvent

PREFIX = "/agent-api/v1"
app = FastAPI(
    title="RDesk Agent API", version=__version__, docs_url=None, redoc_url=None, openapi_url=None
)
app.add_middleware(BodyLimitMiddleware)
bearer = HTTPBearer(auto_error=False)


def get_db():
    with Session(engine(), expire_on_commit=False) as db:
        try:
            yield db
        except Exception:
            db.rollback()
            raise


DB = Annotated[Session, Depends(get_db)]
AUTH = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


def current_client(db: DB, credentials: AUTH):
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError("api_key_required", "需要有效的 Bearer API Key。", 401, "policy")
    client = access.authenticate(db, credentials.credentials)
    db.commit()
    return client


CLIENT = Annotated[object, Depends(current_client)]


def error_response(request, error):
    return JSONResponse(
        status_code=error.status,
        headers={"WWW-Authenticate": "Bearer"} if error.status == 401 else None,
        content={
            "error": {
                "code": error.code,
                "kind": error.kind,
                "message": "请求未获授权。"
                if error.status in {401, 403}
                else "请求无法完成，请检查请求内容或服务状态。",
                "retryable": error.retryable,
            },
            "trace_id": getattr(request.state, "trace_id", str(uuid4())),
        },
    )


@app.middleware("http")
async def public_boundary(request: Request, call_next):
    request.state.trace_id = str(uuid4())
    gate = None
    try:
        config = listener.load()
        if not config.enabled:
            raise AppError("not_found", "接口未启用。", 404)
        if not request.client or not config.allows(
            request.headers.get("host", ""), request.url.scheme, request.client.host
        ):
            raise AppError("invalid_host", "请求入口不受允许。", 400, "policy")
        if request.url.path.startswith(PREFIX + "/"):
            # Authentication updates quotas even on GET. Release after response
            # headers are prepared; an SSE connection must not hold maintenance.
            gate = write_gate(interrupt_maintenance=request.url.path.endswith("/cancel"))
            await asyncio.to_thread(gate.__enter__)
        response = await call_next(request)
    except AppError as error:
        response = error_response(request, error)
    except SQLAlchemyError:
        response = error_response(
            request, AppError("service_unavailable", "服务不可用。", 503, "dependency", True)
        )
    except Exception:
        response = error_response(
            request, AppError("internal_error", "服务不可用。", 500, "internal")
        )
    finally:
        if gate is not None:
            await asyncio.to_thread(gate.__exit__, None, None, None)
    response.headers["X-Trace-ID"] = request.state.trace_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(AppError)
async def application_error(request: Request, error: AppError):
    return error_response(request, error)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    return error_response(request, AppError("invalid_request", "请求格式错误。", 422))


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, error: SQLAlchemyError):
    return error_response(
        request, AppError("service_unavailable", "服务不可用。", 503, "dependency", True)
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    return error_response(
        request,
        AppError(
            "not_found" if error.status_code == 404 else "http_error",
            "请求无法完成。",
            error.status_code,
        ),
    )


@app.get(PREFIX + "/agents", response_model=list[dto.PublicAgent])
def agents(db: DB, client: CLIENT):
    return public.list_agents(db, client)


@app.get(PREFIX + "/agents/{agent_id}", response_model=dto.PublicAgent)
def agent(agent_id: UUID, db: DB, client: CLIENT):
    row = public.callable_agent(db, client, str(agent_id))
    return public.agent_view(db, client, row)


@app.post(PREFIX + "/sessions", response_model=dto.SessionView, status_code=201)
def create_session(command: dto.SessionCreate, db: DB, client: CLIENT):
    result = public.create_session(db, client, command)
    db.commit()
    return result


@app.get(PREFIX + "/sessions/{session_id}", response_model=dto.SessionView)
def session(session_id: UUID, db: DB, client: CLIENT):
    return public.get_session(db, client, str(session_id))


@app.post(PREFIX + "/runs", response_model=dto.PublicRun, status_code=201)
def create_run(command: dto.RunCreate, db: DB, client: CLIENT):
    result = public.create_run(db, client, command)
    db.commit()
    return result


@app.get(PREFIX + "/runs/{run_id}", response_model=dto.PublicRun)
def run(run_id: UUID, db: DB, client: CLIENT):
    return public.get_run(db, client, str(run_id))


@app.post(PREFIX + "/runs/{run_id}/inputs", response_model=InputReceived)
def add_input(run_id: UUID, command: dto.RunInput, db: DB, client: CLIENT):
    result = public.add_input(db, client, str(run_id), command)
    db.commit()
    return result


@app.post(PREFIX + "/runs/{run_id}/cancel", response_model=dto.PublicRun)
def cancel(run_id: UUID, command: dto.RequestCommand, db: DB, client: CLIENT):
    result = public.cancel(db, client, str(run_id), command)
    db.commit()
    return result


@app.post(PREFIX + "/runs/{run_id}/resume", response_model=dto.PublicRun)
def resume(run_id: UUID, command: dto.RequestCommand, db: DB, client: CLIENT):
    from workbench.modules.runs import safety

    run, binding = public.scoped_run(db, client, str(run_id), "write")
    access.scope_for_run(db, run)
    safety.resume(
        db,
        client.owner_user_id,
        run.id,
        {
            "request_id": access.external_request_id(client.id, command.request_id),
            "extend_budget": False,
        },
    )
    db.commit()
    return public.run_view(db, run, binding)


@app.get(PREFIX + "/runs/{run_id}/changes", response_model=change_dto.ChangeSetView)
def run_changes(run_id: UUID, db: DB, client: CLIENT):
    from workbench.modules.changes import service as changes

    run, _ = public.scoped_run(db, client, str(run_id))
    if not run.thread_id:
        raise AppError("changes_unavailable", "此任务没有会话变更记录。", 404)
    return changes.view(db, client.owner_user_id, run.thread_id, run.id)


@app.get(PREFIX + "/runs/{run_id}/changes/{change_id}", response_model=change_dto.ChangeDetails)
def run_change_details(run_id: UUID, change_id: UUID, db: DB, client: CLIENT):
    from workbench.modules.changes import service as changes

    run, _ = public.scoped_run(db, client, str(run_id))
    if not run.thread_id:
        raise AppError("changes_unavailable", "此任务没有会话变更记录。", 404)
    return changes.details(db, client.owner_user_id, run.thread_id, str(change_id), run.id)


@app.post(PREFIX + "/runs/{run_id}/answers", response_model=dto.PublicQuestion)
def answer(run_id: UUID, command: dto.Answer, db: DB, client: CLIENT):
    result = public.answer(db, client, str(run_id), command)
    db.commit()
    return result


@app.post(PREFIX + "/runs/{run_id}/decisions", response_model=dto.PublicApproval)
def decide(run_id: UUID, command: dto.Decision, db: DB, client: CLIENT):
    result = public.decide(db, client, str(run_id), command)
    db.commit()
    return result


@app.get(PREFIX + "/runs/{run_id}/outputs", response_model=dto.Outputs)
def outputs(run_id: UUID, db: DB, client: CLIENT):
    return public.outputs(db, client, str(run_id))


@app.get(PREFIX + "/runs/{run_id}/outputs/{artifact_id}/content", response_class=FileResponse)
def output_content(
    run_id: UUID,
    artifact_id: UUID,
    db: DB,
    client: CLIENT,
    version: str = Query(pattern=r"^[a-f0-9]{64}$"),
):
    row, path = public.download(db, client, str(run_id), str(artifact_id), version)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=row.display_name,
        content_disposition_type="attachment",
        headers={"Content-Security-Policy": "default-src 'none'; sandbox"},
    )


def upload_scope(db, client, agent_id):
    public.callable_agent(db, client, agent_id, "files")
    return access.get_namespace(db, client.id, agent_id)


@app.post(
    PREFIX + "/agents/{agent_id}/uploads", response_model=file_dto.UploadStatus, status_code=201
)
def create_upload(agent_id: UUID, command: file_dto.UploadCreate, db: DB, client: CLIENT):
    namespace = upload_scope(db, client, str(agent_id))
    command = command.model_copy(
        update={"request_id": UUID(access.external_request_id(client.id, command.request_id))}
    )
    result = files.create_upload(db, client.owner_user_id, namespace.workspace_id, command)
    db.commit()
    return result


@app.get(PREFIX + "/agents/{agent_id}/uploads/{upload_id}", response_model=file_dto.UploadStatus)
def upload_status(agent_id: UUID, upload_id: UUID, db: DB, client: CLIENT):
    namespace = upload_scope(db, client, str(agent_id))
    return files.upload_view(
        files.get_upload(db, client.owner_user_id, namespace.workspace_id, str(upload_id))
    )


@app.post(
    PREFIX + "/agents/{agent_id}/uploads/{upload_id}/chunks", response_model=file_dto.UploadStatus
)
async def upload_chunk(
    agent_id: UUID,
    upload_id: UUID,
    request: Request,
    db: DB,
    client: CLIENT,
    offset: int = Query(ge=0),
):
    namespace = upload_scope(db, client, str(agent_id))
    owner_id, workspace_id = client.owner_user_id, namespace.workspace_id
    files.get_upload(db, owner_id, workspace_id, str(upload_id))
    db.commit()
    content = bytearray()
    async for part in request.stream():
        if len(content) + len(part) > files.CHUNK_SIZE:
            raise AppError("upload_chunk_size", "上传块超过限制。", 413, "resource")
        content.extend(part)
    # Authentication and scope are checked again after reading an untrusted body.
    access.require_client(db, client.id)
    public.callable_agent(db, client, str(agent_id), "files")
    db.rollback()
    return await asyncio.to_thread(
        files.accept_chunk, owner_id, workspace_id, str(upload_id), offset, bytes(content)
    )


@app.post(PREFIX + "/agents/{agent_id}/uploads/{upload_id}/complete", response_model=dict)
async def complete_upload(
    agent_id: UUID, upload_id: UUID, command: EmptyCommand, db: DB, client: CLIENT
):
    namespace = upload_scope(db, client, str(agent_id))
    owner_id, workspace_id = client.owner_user_id, namespace.workspace_id
    files.get_upload(db, owner_id, workspace_id, str(upload_id))
    db.commit()
    result = await asyncio.to_thread(files.complete_upload, owner_id, workspace_id, str(upload_id))
    return {key: result[key] for key in ("id", "filename", "mime_type", "byte_size", "version")}


def event_cursor(run_id, after, previous):
    if not previous:
        return after
    try:
        source, seq = previous.rsplit(":", 1)
        position = int(seq)
        if str(UUID(source)) != run_id or position < 0:
            raise ValueError
        return max(after, position)
    except ValueError:
        raise AppError("event_cursor_invalid", "事件游标无效。", 400) from None


def event_batch(token, run_id, after):
    with Session(engine().execution_options(isolation_level="REPEATABLE READ")) as db:
        client = access.authenticate(db, token, count_request=False)
        run, _ = public.scoped_run(db, client, run_id)
        if after > run.last_event_seq:
            raise AppError("event_cursor_invalid", "事件游标无效。", 409)
        rows = db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.seq > after)
            .order_by(RunEvent.seq)
            .limit(100)
        ).all()
        if any(row.seq != after + index + 1 for index, row in enumerate(rows)) or (
            not rows and after < run.last_event_seq
        ):
            raise AppError("event_cursor_expired", "事件游标已过期。", 410)
        result = jsonable_encoder([public.public_event(runs.event_view(row)) for row in rows])
        db.commit()
        return result


@app.get(PREFIX + "/runs/{run_id}/events", response_class=StreamingResponse)
async def events(
    run_id: UUID,
    request: Request,
    client: CLIENT,
    credentials: AUTH,
    after: int = Query(default=0, ge=0),
):
    identifier = str(run_id)
    cursor = event_cursor(identifier, after, request.headers.get("last-event-id"))
    token = credentials.credentials
    initial = await asyncio.to_thread(event_batch, token, identifier, cursor)

    async def replay():
        position, batch, heartbeat = cursor, initial, time.monotonic()
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
                batch = await asyncio.to_thread(event_batch, token, identifier, position)
            except (AppError, SQLAlchemyError):
                return

    return StreamingResponse(
        replay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@app.get(PREFIX + "/schema", response_model=dict)
def schema(client: CLIENT):
    return app.openapi()
