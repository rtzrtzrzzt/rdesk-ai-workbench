from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class AutomationPolicy(Entity, Base):
    __tablename__ = "automation_policies"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict)
    grant_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("user_id", "workspace_id"),)


class AutomationSession(Entity, Base):
    __tablename__ = "automation_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    policy_id: Mapped[str] = mapped_column(ForeignKey("automation_policies.id"))
    policy_version: Mapped[int]
    kind: Mapped[str]
    mode: Mapped[str] = mapped_column(default="observe")
    status: Mapped[str] = mapped_column(default="creating", index=True)
    native_id: Mapped[str | None]
    runtime_nonce: Mapped[str]
    command: Mapped[dict] = mapped_column(JSONB)
    native_view: Mapped[dict] = mapped_column(JSONB, default=dict)
    observation: Mapped[dict | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict | None] = mapped_column(JSONB)


class DesktopWriterLease(Base):
    __tablename__ = "desktop_writer_leases"
    node_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("automation_sessions.id"))
    lease_id: Mapped[str]
    epoch: Mapped[int] = mapped_column(default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(default=False)


class AutomationAction(Entity, Base):
    __tablename__ = "automation_actions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("automation_sessions.id"), index=True)
    arguments_hash: Mapped[str]
    kind: Mapped[str]
    status: Mapped[str] = mapped_column(default="in_progress")
    result: Mapped[dict | None] = mapped_column(JSONB)
