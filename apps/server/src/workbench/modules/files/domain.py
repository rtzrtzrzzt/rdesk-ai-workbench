from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class Artifact(Entity, Base):
    __tablename__ = "artifacts"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    display_name: Mapped[str]
    mime_type: Mapped[str]
    declared_mime: Mapped[str | None]
    byte_size: Mapped[int] = mapped_column(BigInteger)
    content_hash: Mapped[str]
    storage_path: Mapped[str] = mapped_column(unique=True)
    kind: Mapped[str] = mapped_column(default="original")
    status: Mapped[str] = mapped_column(default="staging")
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)


class Upload(Entity, Base):
    __tablename__ = "uploads"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    request_id: Mapped[str]
    request_digest: Mapped[str]
    display_name: Mapped[str]
    declared_mime: Mapped[str | None]
    total_bytes: Mapped[int] = mapped_column(BigInteger)
    received_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    chunks: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(default="uploading")
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)
