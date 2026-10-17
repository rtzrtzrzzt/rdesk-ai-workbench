import hashlib
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select, text

from workbench.contracts.errors import AppError
from workbench.contracts.requests import Command, RunOptionOverrides, RunOptions
from workbench.modules.preferences.domain import RuntimeDefaults, WorkspaceRuntimeDefaults


class DefaultsView(BaseModel):
    scope: Literal["user"] = "user"
    effective_at: Literal["new_runs"] = "new_runs"
    version: int
    options: RunOptions
    model_connection_id: str | None = None
    model_generation_capacity: Literal[1] = 1


class DefaultsSave(Command):
    expected_version: int = Field(ge=0)
    options: RunOptions
    model_connection_id: UUID | None = None


def get(db, user_id):
    row = db.scalar(select(RuntimeDefaults).where(RuntimeDefaults.user_id == user_id))
    return DefaultsView(
        version=row.version if row else 0,
        options=RunOptions.model_validate(row.options if row else {}),
        model_connection_id=row.model_connection_id if row else None,
    )


def save(db, user_id, command):
    if command.model_connection_id:
        from workbench.modules.models.service import get as model

        model(db, user_id, str(command.model_connection_id))
    key = int.from_bytes(
        hashlib.sha256(f"runtime-defaults:{user_id}".encode()).digest()[:8], signed=True
    )
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    row = db.scalar(select(RuntimeDefaults).where(RuntimeDefaults.user_id == user_id))
    if (row.version if row else 0) != command.expected_version:
        raise AppError("settings_conflict", "默认设置已经更新，请重新读取后修改。", 409)
    if row:
        row.options, row.version = command.options.model_dump(mode="json"), row.version + 1
    else:
        row = RuntimeDefaults(user_id=user_id, options=command.options.model_dump(mode="json"))
        db.add(row)
    row.model_connection_id = (
        str(command.model_connection_id) if command.model_connection_id else None
    )
    db.flush()
    return get(db, user_id)


class ResolvedDefaults(BaseModel):
    options: RunOptions
    sources: dict[str, str]
    model_connection_id: str | None
    model_source: str
    user_version: int
    workspace_version: int


class WorkspaceDefaultsView(BaseModel):
    scope: Literal["workspace"] = "workspace"
    effective_at: Literal["new_runs"] = "new_runs"
    version: int
    options: RunOptionOverrides
    model_connection_id: str | None
    effective: ResolvedDefaults


class WorkspaceDefaultsSave(Command):
    expected_version: int = Field(ge=0)
    options: RunOptionOverrides
    model_connection_id: UUID | None = None


def resolve(db, user_id, workspace_id, *, task_options=None, task_model=None):
    from workbench.modules.workspace_service import get as workspace

    workspace(db, user_id, workspace_id)
    user = db.scalar(select(RuntimeDefaults).where(RuntimeDefaults.user_id == user_id))
    row = db.scalar(
        select(WorkspaceRuntimeDefaults).where(
            WorkspaceRuntimeDefaults.workspace_id == workspace_id,
            WorkspaceRuntimeDefaults.user_id == user_id,
        )
    )
    values = RunOptions().model_dump(mode="json")
    sources = dict.fromkeys(values, "system")
    for patch, source in (
        (user.options if user else {}, f"user:{user.version if user else 0}"),
        (row.options if row else {}, f"workspace:{row.version if row else 0}"),
        (task_options or {}, "task"),
    ):
        for key, value in patch.items():
            if key in values and value is not None:
                values[key], sources[key] = value, source
    model = (
        task_model
        or (row.model_connection_id if row else None)
        or (user.model_connection_id if user else None)
    )
    model_source = (
        "task" if task_model else "workspace" if row and row.model_connection_id else "user"
    )
    return ResolvedDefaults(
        options=RunOptions.model_validate(values),
        sources=sources,
        model_connection_id=str(model) if model else None,
        model_source=model_source if model else "unconfigured",
        user_version=user.version if user else 0,
        workspace_version=row.version if row else 0,
    )


def get_workspace(db, user_id, workspace_id):
    effective = resolve(db, user_id, workspace_id)
    row = db.scalar(
        select(WorkspaceRuntimeDefaults).where(
            WorkspaceRuntimeDefaults.workspace_id == workspace_id,
            WorkspaceRuntimeDefaults.user_id == user_id,
        )
    )
    return WorkspaceDefaultsView(
        version=row.version if row else 0,
        options=RunOptionOverrides.model_validate(row.options if row else {}),
        model_connection_id=row.model_connection_id if row else None,
        effective=effective,
    )


def save_workspace(db, user_id, workspace_id, command):
    from workbench.modules.workspace_service import get as workspace

    workspace(db, user_id, workspace_id, lock=True)
    if command.model_connection_id:
        from workbench.modules.models.service import get as model

        model(db, user_id, str(command.model_connection_id))
    row = db.scalar(
        select(WorkspaceRuntimeDefaults).where(
            WorkspaceRuntimeDefaults.workspace_id == workspace_id,
            WorkspaceRuntimeDefaults.user_id == user_id,
        )
    )
    if (row.version if row else 0) != command.expected_version:
        raise AppError("settings_conflict", "工作区默认设置已经变化，请重新读取。", 409)
    if row is None:
        row = WorkspaceRuntimeDefaults(user_id=user_id, workspace_id=workspace_id)
        db.add(row)
    else:
        row.version += 1
    row.options = command.options.model_dump(mode="json", exclude_none=True)
    row.model_connection_id = (
        str(command.model_connection_id) if command.model_connection_id else None
    )
    db.flush()
    return get_workspace(db, user_id, workspace_id)
