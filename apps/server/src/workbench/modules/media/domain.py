from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


def run_id_for_request(request_id: UUID) -> str:
    return str(uuid5(request_id, "media-run"))


class MediaConnection(Entity, Base):
    __tablename__ = "media_connections"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str]
    capability: Mapped[str]
    driver: Mapped[str]
    model_identifier: Mapped[str]
    endpoint: Mapped[str | None]
    encrypted_api_key: Mapped[str | None]
    allow_remote: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    is_default: Mapped[bool] = mapped_column(default=False)
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict)
    verification: Mapped[str] = mapped_column(default="unverified")


class MediaTask(Entity, Base):
    __tablename__ = "media_tasks"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("media_connections.id"))
    connection_version: Mapped[int]
    provider_snapshot: Mapped[dict] = mapped_column(JSONB)
    encrypted_credential: Mapped[str | None]
    request: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(default="queued")
    phase: Mapped[str] = mapped_column(default="queued")
    progress: Mapped[float | None]
    dispatch_state: Mapped[str] = mapped_column(default="not_started")
    provider_job_id: Mapped[str | None]
    artifacts: Mapped[list] = mapped_column(JSONB, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[dict | None] = mapped_column(JSONB)
    resources_closed: Mapped[bool] = mapped_column(default=True)
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index(
            "ix_media_tasks_history",
            "created_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class MediaResidency(Entity, Base):
    __tablename__ = "media_residencies"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    phase: Mapped[str]
    status: Mapped[str] = mapped_column(default="reserved", index=True)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger)
    headroom_bytes: Mapped[int] = mapped_column(BigInteger)
    command_directory: Mapped[str]
    supervisor_identity: Mapped[dict | None] = mapped_column(JSONB)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("run_id", "phase"),)
