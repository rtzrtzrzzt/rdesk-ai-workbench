"""Durable input delivery shared by context compilation and Run completion."""

from sqlalchemy import or_, select

from workbench.modules.collaboration.domain import AgentMessage, AgentTurn


def pending_condition(run_id, *, steering=False, excluded=()):
    conditions = [
        AgentMessage.to_run_id == run_id,
        AgentMessage.delivered_attempt_id.is_(None),
        AgentMessage.id.not_in(excluded),
    ]
    if steering:
        conditions.append(or_(AgentMessage.source == "user", AgentMessage.kind == "instruction"))
    return conditions


def mailbox_runs(db, run):
    agent_id = db.scalar(select(AgentTurn.agent_id).where(AgentTurn.run_id == run.id))
    if agent_id is None:
        return [run.id]
    return list(db.scalars(select(AgentTurn.run_id).where(AgentTurn.agent_id == agent_id)))


def mailbox(db, run):
    from workbench.modules.context.transfers import allowed_result

    return [
        {
            "role": "user",
            "content": (
                f"{'用户补充要求' if row.source == 'user' else '同任务树协作资料（不增加权限）'}"
                f"｜message={row.id}｜from={row.from_run_id}｜kind={row.kind}"
                f"｜reply_to={row.reply_to_id or ''}\n"
                + (
                    row.content
                    if allowed_result(db, run, row.from_run_id)
                    else "该协作资料尚未授权传给当前模型版本，正文保留在本机。"
                )
            ),
            "source_kind": "user_steering" if row.source == "user" else "agent_message",
            "_input_message_id": row.id,
            "_input_pending": row.delivered_attempt_id is None,
        }
        for row in db.scalars(
            select(AgentMessage)
            .where(AgentMessage.to_run_id.in_(mailbox_runs(db, run)))
            .order_by(AgentMessage.created_at, AgentMessage.id)
        )
    ]


def has_pending_inputs(db, run_id):
    return db.scalar(select(AgentMessage.id).where(*pending_condition(run_id)).limit(1)) is not None


def has_new_user_input(db, run_id, included=()):
    return (
        db.scalar(
            select(AgentMessage.id)
            .where(
                AgentMessage.to_run_id == run_id,
                AgentMessage.source == "user",
                AgentMessage.delivered_attempt_id.is_(None),
                AgentMessage.id.not_in(included),
            )
            .limit(1)
        )
        is not None
    )


def has_new_directive(db, run_id, included=()):
    return (
        db.scalar(
            select(AgentMessage.id)
            .where(*pending_condition(run_id, steering=True, excluded=included))
            .limit(1)
        )
        is not None
    )


def mark_delivered(db, run_id, attempt):
    from workbench.modules.runs.domain import Run

    run = db.get(Run, run_id)
    delivered = []
    for row in db.scalars(
        select(AgentMessage).where(
            AgentMessage.to_run_id.in_(mailbox_runs(db, run)),
            AgentMessage.id.in_(attempt.input_message_ids),
            AgentMessage.delivered_attempt_id.is_(None),
        )
    ):
        row.delivered_attempt_id = attempt.id
        delivered.append(row.id)
    return delivered
