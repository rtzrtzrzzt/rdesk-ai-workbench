from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class ExtensionInstallation(Entity, Base):
    __tablename__ = "extension_installations"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str]
    source: Mapped[dict] = mapped_column(JSONB)
    current_revision_id: Mapped[str]
    enabled: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(default="installed")


class ExtensionRevision(Entity, Base):
    __tablename__ = "extension_revisions"
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("extension_installations.id"), index=True
    )
    content_digest: Mapped[str]
    source_revision: Mapped[str | None]
    manifest: Mapped[dict] = mapped_column(JSONB)
    storage_path: Mapped[str]
    __table_args__ = (UniqueConstraint("installation_id", "content_digest"),)


class ExtensionOverride(Entity, Base):
    __tablename__ = "extension_overrides"
    installation_id: Mapped[str] = mapped_column(ForeignKey("extension_installations.id"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    enabled: Mapped[bool]
    __table_args__ = (UniqueConstraint("installation_id", "workspace_id"),)


class ExtensionMCPBinding(Entity, Base):
    __tablename__ = "extension_mcp_bindings"
    installation_id: Mapped[str] = mapped_column(ForeignKey("extension_installations.id"))
    revision_id: Mapped[str] = mapped_column(ForeignKey("extension_revisions.id"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    connection_id: Mapped[str] = mapped_column(ForeignKey("mcp_connections.id"), unique=True)
