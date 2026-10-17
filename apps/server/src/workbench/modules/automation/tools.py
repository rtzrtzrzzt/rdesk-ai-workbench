"""Finite tool arguments. Runtime, grants, output paths and request IDs are host-owned."""

import asyncio
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.contracts.requests import Command
from workbench.infrastructure.database import engine
from workbench.modules.automation import dto, service
from workbench.modules.browser.dto import BrowserAction
from workbench.modules.desktop.dto import DesktopAction
from workbench.modules.runs.domain import Run


class BrowserOpen(Command):
    ttl_seconds: int = Field(default=300, ge=30, le=900)


class ComputerOpen(BrowserOpen):
    mode: Literal["observe", "control"] = "observe"


class SessionTarget(Command):
    session_id: UUID


class BrowserObserve(SessionTarget):
    screenshot: bool = False


class BrowserDownload(SessionTarget):
    last_observation: UUID
    target_ref: str = Field(pattern=r"^e[0-9]{1,4}$")


class BrowserStep(SessionTarget):
    last_observation: UUID
    target_ref: str = Field(min_length=1, max_length=64)
    kind: Literal["navigate", "click", "type", "scroll", "key"]
    url: str | None = Field(default=None, max_length=4096)
    text: str | None = Field(default=None, max_length=16000)
    key: (
        Literal["Enter", "Tab", "Escape", "ArrowUp", "ArrowDown", "Home", "End", "Space"] | None
    ) = None
    delta_y: int | None = Field(default=None, ge=-3000, le=3000)


class ComputerStep(SessionTarget):
    last_observation: UUID
    generation: int = Field(ge=1)
    geometry_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    target_window_id: str = Field(min_length=1, max_length=80)
    kind: Literal["click", "type", "key", "scroll"]
    x: int | None = Field(default=None, ge=0, le=32768)
    y: int | None = Field(default=None, ge=0, le=32768)
    text: str | None = Field(default=None, max_length=256)
    key: (
        Literal[
            "Enter",
            "Tab",
            "Escape",
            "Backspace",
            "Delete",
            "ArrowUp",
            "ArrowDown",
            "ArrowLeft",
            "ArrowRight",
            "Home",
            "End",
            "PageUp",
            "PageDown",
            "Space",
        ]
        | None
    ) = None
    scroll_steps: int | None = Field(default=None, ge=-10, le=10)


async def execute(run_id, operation_id, name, values):
    with Session(engine()) as db:
        run = db.get(Run, run_id)
        user_id, workspace_id = run.user_id, run.workspace_id
    if name == "inspect_automation":
        result = await service.capabilities(user_id, workspace_id)
        with Session(engine()) as db:
            sessions = [
                row.model_dump(mode="json")
                for row in service.listing(db, user_id, workspace_id)
                if row.run_id == run_id
            ]
        return {"capabilities": result.model_dump(mode="json"), "sessions": sessions}
    if name in {"browser_open", "computer_open"}:
        return (
            await service.create_session(
                user_id,
                workspace_id,
                dto.SessionCreate(
                    kind="browser" if name == "browser_open" else "desktop",
                    mode=values.get("mode", "observe"),
                    ttl_seconds=values["ttl_seconds"],
                    request_id=operation_id,
                ),
                run_id=run_id,
            )
        ).model_dump(mode="json")
    identifier = values["session_id"]
    if name in {"browser_observe", "computer_observe"}:
        if name == "computer_observe" or values.get("screenshot"):
            from workbench.modules.files.inputs import is_remote
            from workbench.modules.models.domain import ModelRevision

            with Session(engine()) as db:
                revision = db.get(ModelRevision, run.config_snapshot.get("model_revision_id"))
                if revision is None or is_remote(revision.configuration["endpoint"]):
                    raise AppError(
                        "capture_remote_disabled",
                        "截图工具当前只向本地视觉模型提供图像。",
                        403,
                        "policy",
                    )
        result = await service.observe(
            user_id,
            workspace_id,
            identifier,
            dto.Observe(screenshot=values.get("screenshot", True), request_id=operation_id),
            run_id=run_id,
        )
    elif name == "browser_download":
        result = await service.download(
            user_id,
            workspace_id,
            identifier,
            dto.BrowserDownload(
                request_id=operation_id,
                last_observation=values["last_observation"],
                target_ref=values["target_ref"],
            ),
            run_id=run_id,
        )
    elif name == "automation_stop":
        return (
            await service.stop_session(user_id, workspace_id, identifier, run_id=run_id)
        ).model_dump(mode="json")
    else:
        try:
            command = (BrowserAction if name == "browser_action" else DesktopAction).model_validate(
                {**values, "request_id": operation_id}
            )
        except ValidationError:
            raise AppError(
                "automation_action_invalid", "动作参数不符合有限输入契约，请重新观察后修正。", 422
            ) from None
        result = await service.act(
            user_id,
            workspace_id,
            identifier,
            command,
            kind="browser" if name == "browser_action" else "desktop",
            run_id=run_id,
        )
    view = result.model_dump(mode="json")
    if result.status == "outcome_unknown":
        raise AppError(
            "automation_action_unknown", "原生动作结果待核对，不会重放。", 409, "outcome_unknown"
        )
    if result.status == "failed":
        view["error"] = (result.result or {}).get("error") or {
            "code": "native_action_failed",
            "message": "原生预检或动作未完成。",
        }
    return view


async def stop_owned_run(run_id):
    with Session(engine()) as db:
        run = db.get(Run, run_id)
        rows = [
            row
            for row in service.listing(db, run.user_id, run.workspace_id)
            if row.run_id == run_id and row.status not in {"stopped", "taken_over", "expired"}
        ]
        user_id, workspace_id = run.user_id, run.workspace_id
    return await asyncio.gather(
        *(service.stop_session(user_id, workspace_id, row.id, run_id=run_id) for row in rows),
        return_exceptions=True,
    )
