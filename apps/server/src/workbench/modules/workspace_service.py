from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.modules.workspaces import Workspace


def view(row: Workspace) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "root_path": row.root_path,
        "archived": row.archived,
        "removed_at": row.removed_at,
        "created_at": row.created_at,
        "version": row.version,
    }


def get(
    db: Session, user_id: str, workspace_id: str, *, include_archived=False, lock=False
) -> Workspace:
    query = select(Workspace).where(
        Workspace.id == workspace_id,
        Workspace.user_id == user_id,
        Workspace.removed_at.is_(None),
    )
    if not include_archived:
        query = query.where(Workspace.archived.is_(False))
    row = db.scalar(
        query.with_for_update().execution_options(populate_existing=True) if lock else query
    )
    if row is None:
        raise AppError("workspace_not_found", "工作区不存在或不可访问。", 404)
    return row


def list_workspaces(db: Session, user_id: str, *, include_archived=False) -> list[dict]:
    query = select(Workspace).where(Workspace.user_id == user_id, Workspace.removed_at.is_(None))
    if not include_archived:
        query = query.where(Workspace.archived.is_(False))
    return [view(row) for row in db.scalars(query.order_by(Workspace.created_at))]


def create(db: Session, user_id: str, name: str, root_path: str) -> dict:
    path = Path(root_path).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise AppError("invalid_workspace_path", "请选择已经存在的项目文件夹的绝对路径。")
    resolved = path.resolve()
    row = Workspace(user_id=user_id, name=name.strip(), root_path=str(resolved))
    db.add(row)
    db.flush()
    return view(row)


def update(db, user_id, workspace_id, command, action):
    from workbench.infrastructure.database import now
    from workbench.modules.automation.domain import AutomationSession
    from workbench.modules.automation.policy import LIVE
    from workbench.modules.runs.domain import TERMINAL_STATUSES, Run
    from workbench.modules.runs.service import idempotent

    def execute():
        row = get(db, user_id, workspace_id, include_archived=True, lock=True)
        if row.version != command.expected_version:
            raise AppError("workspace_stale", "工作区已更新，请重新读取。", 409)
        if action in {"archive", "remove"}:
            busy = db.scalar(
                select(Run.id)
                .where(Run.workspace_id == row.id, Run.status.not_in(TERMINAL_STATUSES))
                .limit(1)
            )
            native = db.scalar(
                select(AutomationSession.id)
                .where(AutomationSession.workspace_id == row.id, AutomationSession.status.in_(LIVE))
                .limit(1)
            )
            if busy or native:
                raise AppError(
                    "workspace_busy",
                    "先停止本工作区未结束的任务和电脑会话，再归档或移除登记。",
                    409,
                )
        if action == "rename":
            row.name = command.name
        elif action in {"archive", "restore"}:
            row.archived = action == "archive"
        elif action == "remove":
            row.removed_at, row.archived = now(), True
        else:
            raise AppError("workspace_action_invalid", "工作区操作未注册。", 422)
        row.version += 1
        return view(row)

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        f"workspace:{workspace_id}:{action}",
        command.model_dump(mode="json"),
        execute,
    )
