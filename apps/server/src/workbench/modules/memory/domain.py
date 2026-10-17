"""Rebuildable memory metadata, durable write journals and permanent forgetting barriers."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class MemoryScope(Entity, Base):
    __tablename__ = "memory_scopes"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"))
    scope: Mapped[str]
    scope_key: Mapped[str] = mapped_column(String(100), unique=True)
    generation: Mapped[int] = mapped_column(default=1)
    index_hash: Mapped[str | None] = mapped_column(String(64))


class MemoryDocument(Entity, Base):
    __tablename__ = "memory_documents"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"))
    scope_id: Mapped[str] = mapped_column(ForeignKey("memory_scopes.id"), index=True)
    title: Mapped[str]
    storage_path: Mapped[str] = mapped_column(unique=True)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(default="active")
    source_refs: Mapped[list] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MemoryProposal(Entity, Base):
    __tablename__ = "memory_proposals"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"))
    scope_id: Mapped[str] = mapped_column(ForeignKey("memory_scopes.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("memory_documents.id"))
    title: Mapped[str]
    storage_path: Mapped[str]
    content_hash: Mapped[str] = mapped_column(String(64))
    base_version: Mapped[str] = mapped_column(String(64))
    scope_generation: Mapped[int]
    status: Mapped[str] = mapped_column(default="pending")
    source_refs: Mapped[list] = mapped_column(JSONB, default=list)
    source_actor: Mapped[str]
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MemoryWriteJournal(Entity, Base):
    __tablename__ = "memory_write_journal"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"))
    scope_id: Mapped[str | None] = mapped_column(ForeignKey("memory_scopes.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("memory_documents.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    request_digest: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str]
    status: Mapped[str] = mapped_column(default="prepared", index=True)
    plan: Mapped[dict] = mapped_column(JSONB, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONB)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)


class MemoryTombstone(Entity, Base):
    __tablename__ = "memory_tombstones"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    scope_id: Mapped[str] = mapped_column(ForeignKey("memory_scopes.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("memory_documents.id"), unique=True)
    generation: Mapped[int]
    forgotten_version: Mapped[str] = mapped_column(String(64))
    journal_id: Mapped[str] = mapped_column(ForeignKey("memory_write_journal.id"))


class MemoryRevision(Entity, Base):
    """Confirmed immutable MD snapshots; never indexes unverified write candidates."""

    __tablename__ = "memory_revisions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"))
    scope_id: Mapped[str] = mapped_column(ForeignKey("memory_scopes.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("memory_documents.id"), index=True)
    scope_generation: Mapped[int]
    content_hash: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str]
    origin: Mapped[str]  # observed_current | managed_publish
    status: Mapped[str] = mapped_column(default="available")
    journal_id: Mapped[str] = mapped_column(ForeignKey("memory_write_journal.id"))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("document_id", "scope_generation", "content_hash"),)
