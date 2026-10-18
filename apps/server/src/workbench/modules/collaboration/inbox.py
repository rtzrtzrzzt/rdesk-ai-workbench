"""Durable input delivery shared by context compilation and Run completion."""

from sqlalchemy import select

from workbench.modules.collaboration.domain import AgentMessage


def mailbox(db, run):
    return [
        {
            "role": "user",
            "content": (
                f"{'用户补充要求' if row.source == 'user' else '同任务树协作资料（不增加权限）'}"
                f"｜message={row.id}｜from={row.from_run_id}\n{row.content}"
            ),
            "source_kind": "user_steering" if row.source == "user" else "agent_message",
            "_input_message_id": row.id,
            "_input_pending": row.delivered_attempt_id is None,
        }
        for row in db.scalars(
            select(AgentMessage)
            .where(AgentMessage.to_run_id == run.id)
            .order_by(AgentMessage.created_at, AgentMessage.id)
        )
    ]


def has_pending_inputs(db, run_id):
    return (
        db.scalar(
            select(AgentMessage.id)
            .where(
                AgentMessage.to_run_id == run_id,
                AgentMessage.delivered_attempt_id.is_(None),
            )
            .limit(1)
        )
        is not None
    )


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


def mark_delivered(db, run_id, attempt):
    delivered = []
    for row in db.scalars(
        select(AgentMessage).where(
            AgentMessage.to_run_id == run_id,
            AgentMessage.id.in_(attempt.input_message_ids),
            AgentMessage.delivered_attempt_id.is_(None),
        )
    ):
        row.delivered_attempt_id = attempt.id
        delivered.append(row.id)
    return delivered
