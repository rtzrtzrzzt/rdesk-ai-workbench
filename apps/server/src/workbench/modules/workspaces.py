from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class Workspace(Entity, Base):
    __tablename__ = "workspaces"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str]
    root_path: Mapped[str]
    archived: Mapped[bool] = mapped_column(default=False)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
