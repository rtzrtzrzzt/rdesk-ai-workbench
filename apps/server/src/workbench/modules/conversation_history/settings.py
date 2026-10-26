from sqlalchemy import or_, select

from workbench.modules.preferences.configuration import get_state, save_state
from workbench.modules.runs.domain import Run
from workbench.modules.runs.service import idempotent

from .dto import ConversationHistoryView
from .policy import effective_scope


def view(db, user_id):
    row = get_state(db, user_id, "conversation_history")
    return ConversationHistoryView(
        scope=row.values.get("scope", "workspace") if row else "workspace",
        version=row.version if row else 0,
        initialized=row is not None,
    )


def save(db, user_id, command):
    def apply():
        save_state(
            db, user_id, "conversation_history", {"scope": command.scope}, command.expected_version
        )
        return view(db, user_id).model_dump()

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        "conversation_history:save",
        command.model_dump(mode="json"),
        apply,
    )


def scope_for(db, actor):
    current = view(db, actor.user_id)
    return effective_scope(current.scope, bool(actor.parent_run_id)), current.version


def task_threads(db, actor):
    root_id = actor.root_run_id or actor.id
    return list(
        db.scalars(
            select(Run.thread_id)
            .where(
                Run.user_id == actor.user_id,
                Run.workspace_id == actor.workspace_id,
                Run.thread_id.is_not(None),
                or_(Run.id == root_id, Run.root_run_id == root_id),
            )
            .distinct()
        )
    )
