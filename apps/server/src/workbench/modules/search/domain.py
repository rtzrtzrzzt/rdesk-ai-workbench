"""Only user-authorized endpoint configuration is durable; search bodies are not stored here."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class SearchConnection(Entity, Base):
    __tablename__ = "search_connections"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True)
    scope: Mapped[str] = mapped_column(String(16))
    scope_key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    endpoint: Mapped[str] = mapped_column(String(2048))
    enabled: Mapped[bool] = mapped_column(default=True)
    engines: Mapped[list] = mapped_column(JSON, default=list)
    language: Mapped[str] = mapped_column(String(20), default="auto")
    region: Mapped[str | None] = mapped_column(String(2), nullable=True)
    max_results: Mapped[int] = mapped_column(default=5)
    timeout_seconds: Mapped[int] = mapped_column(default=20)
    status: Mapped[str] = mapped_column(String(16), default="unchecked")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
