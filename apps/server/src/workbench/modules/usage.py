from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class UsageRecord(Entity, Base):
    __tablename__ = "usage_records"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id"), unique=True)
    model_connection_id: Mapped[str] = mapped_column(ForeignKey("model_connections.id"))
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)
