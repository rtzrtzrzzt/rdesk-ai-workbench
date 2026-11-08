from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.infrastructure.database import Base, Entity


class AgentService(Entity, Base):
    __tablename__ = "agent_services"
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    current_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_service_revisions.id", use_alter=True, name="fk_agent_current_revision")
    )
    enabled: Mapped[bool] = mapped_column(default=True)


class AgentServiceRevision(Entity, Base):
    __tablename__ = "agent_service_revisions"
    service_id: Mapped[str] = mapped_column(ForeignKey("agent_services.id"), index=True)
    number: Mapped[int]
    definition: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("service_id", "number"),)


class AgentSessionBinding(Entity, Base):
    __tablename__ = "agent_session_bindings"
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), unique=True)
    namespace_id: Mapped[str] = mapped_column(ForeignKey("external_namespaces.id"), index=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("agent_service_revisions.id"), index=True)
