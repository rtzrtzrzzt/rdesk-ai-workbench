from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command


class ClientCreate(Command):
    request_id: UUID
    name: str = Field(min_length=1, max_length=160)
    requests_per_minute: int = Field(default=120, ge=1, le=6000)
    max_active_runs: int = Field(default=3, ge=1, le=32)


class ClientSave(Command):
    expected_version: int = Field(ge=1)
    enabled: bool
    requests_per_minute: int = Field(default=120, ge=1, le=6000)
    max_active_runs: int = Field(default=3, ge=1, le=32)


class KeyIssue(Command):
    request_id: UUID
    expires_at: datetime | None = None


class GrantSave(Command):
    expected_version: int = Field(default=0, ge=0)
    enabled: bool = True
    actions: list[Literal["invoke", "read", "write", "files", "approve"]] = Field(
        default_factory=lambda: ["invoke", "read", "write", "files"]
    )
    allowed_tools: list[str] | None = Field(default=None, max_length=128)


class ClientView(PublicResponse):
    id: str
    name: str
    enabled: bool
    version: int
    requests_per_minute: int
    max_active_runs: int
    created_at: datetime


class KeyView(PublicResponse):
    id: str
    client_id: str
    prefix: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class IssuedKey(KeyView):
    key: str | None
    shown_once: bool


class GrantView(PublicResponse):
    id: str
    client_id: str
    service_id: str
    enabled: bool
    actions: list[str]
    allowed_tools: list[str] | None
    version: int
