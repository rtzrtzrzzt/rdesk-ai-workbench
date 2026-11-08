from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


class Run(Entity, Base):
    __tablename__ = "runs"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    kind: Mapped[str] = mapped_column(default="agent", server_default="agent")
    thread_id: Mapped[str | None] = mapped_column(ForeignKey("threads.id"), index=True)
    model_connection_id: Mapped[str | None] = mapped_column(ForeignKey("model_connections.id"))
    root_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    parent_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"))
    status: Mapped[str] = mapped_column(default="queued", index=True)
    input: Mapped[str]
    last_event_seq: Mapped[int] = mapped_column(default=0)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    wait_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    graph_version: Mapped[str] = mapped_column(default="1")
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    error: Mapped[dict | None] = mapped_column(JSONB)
    execution_origin: Mapped[str] = mapped_column(default="owner", server_default="owner")
    execution_binding_id: Mapped[str | None] = mapped_column(
        ForeignKey("execution_bindings.id"), index=True
    )
    final_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", use_alter=True, name="fk_run_final_message")
    )
    final_attempt_id: Mapped[str | None] = mapped_column(
        ForeignKey("attempts.id", use_alter=True, name="fk_run_final_attempt")
    )
    finalized_event_seq: Mapped[int | None]
    __table_args__ = (
        CheckConstraint(
            "(execution_origin = 'owner' AND execution_binding_id IS NULL) OR "
            "(execution_origin = 'api' AND execution_binding_id IS NOT NULL)",
            name="ck_run_execution_origin",
        ),
    )


class Job(Entity, Base):
    __tablename__ = "jobs"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True)
    queue: Mapped[str] = mapped_column(default="agent", server_default="agent", index=True)
    status: Mapped[str] = mapped_column(default="queued")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None]
    lease_epoch: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_status: Mapped[str | None]
    __table_args__ = (Index("ix_jobs_schedule", "status", "available_at"),)


class RunEvent(Entity, Base):
    __tablename__ = "run_events"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    seq: Mapped[int]
    type: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint("run_id", "seq"),)


class ClientRequest(Entity, Base):
    __tablename__ = "client_requests"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    request_digest: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None]
    response: Mapped[dict | None] = mapped_column(JSONB)
    expired: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)


class Step(Entity, Base):
    __tablename__ = "run_steps"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    logical_key: Mapped[str]
    kind: Mapped[str]
    status: Mapped[str] = mapped_column(default="running")
    __table_args__ = (UniqueConstraint("run_id", "logical_key"),)


class Attempt(Entity, Base):
    __tablename__ = "attempts"
    step_id: Mapped[str] = mapped_column(ForeignKey("run_steps.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    number: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(default="created")
    message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict | None] = mapped_column(JSONB)
    input_message_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[str] = mapped_column(primary_key=True)
    queue: Mapped[str] = mapped_column(default="agent", server_default="agent")
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
