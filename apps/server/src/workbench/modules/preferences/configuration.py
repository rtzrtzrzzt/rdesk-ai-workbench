"""Versioned user preferences whose values are not run generation options."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import ForeignKey, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import Base, Entity


class ConfigurationState(Entity, Base):
    __tablename__ = "configuration_states"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    section: Mapped[str]
    scope_key: Mapped[str]
    values: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint("user_id", "section", "scope_key"),)


class Experience(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: Literal["system", "light", "dark"] = "system"
    input_mode: Literal["queue", "steer"] = "queue"


class ExperienceSave(Experience):
    expected_version: int = Field(ge=0)
    request_id: UUID


class ExperienceView(Experience):
    version: int
    initialized: bool


class LanguageServer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(min_length=1, max_length=50)
    command: list[str] = Field(min_length=1, max_length=32)
    settings: dict = Field(default_factory=dict)
    initialization_options: dict = Field(default_factory=dict)

    @field_validator("command")
    @classmethod
    def command_parts(cls, value):
        if any(not part or "\0" in part or len(part) > 8192 for part in value):
            raise ValueError("命令参数须为非空、无NUL的有界字符串。")
        return value


class DeveloperTools(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language_servers: list[LanguageServer] | None = Field(default=None, max_length=8)


def get_state(db, user_id, section, workspace_id=None):
    return db.scalar(
        select(ConfigurationState).where(
            ConfigurationState.user_id == user_id,
            ConfigurationState.section == section,
            ConfigurationState.scope_key
            == (f"workspace:{workspace_id}" if workspace_id else "user"),
        )
    )


def save_state(db, user_id, section, values, expected_version, workspace_id=None):
    from workbench.modules.identity import User
    from workbench.modules.workspace_service import get as workspace

    db.scalar(select(User).where(User.id == user_id).with_for_update())
    if workspace_id:
        workspace(db, user_id, workspace_id, lock=True)
    row = get_state(db, user_id, section, workspace_id)
    if (row.version if row else 0) != expected_version:
        raise AppError("settings_conflict", "设置已经更新，请重新读取后修改。", 409)
    if row is None:
        row = ConfigurationState(
            user_id=user_id,
            section=section,
            scope_key=f"workspace:{workspace_id}" if workspace_id else "user",
        )
        db.add(row)
    else:
        row.version += 1
    row.values = values
    db.flush()
    return row


def experience(db, user_id):
    row = get_state(db, user_id, "experience")
    return ExperienceView(
        **Experience.model_validate(row.values if row else {}).model_dump(),
        version=row.version if row else 0,
        initialized=row is not None,
    )


def save_experience(db, user_id, command):
    from workbench.modules.runs.service import idempotent

    def apply():
        save_state(
            db,
            user_id,
            "experience",
            command.model_dump(include={"theme", "input_mode"}),
            command.expected_version,
        )
        return experience(db, user_id).model_dump()

    return idempotent(
        db,
        user_id,
        str(command.request_id),
        "experience:save",
        command.model_dump(mode="json"),
        apply,
    )
