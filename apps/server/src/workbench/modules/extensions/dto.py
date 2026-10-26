from typing import Literal
from uuid import UUID

from pydantic import Field

from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command


class ExtensionSource(Command):
    kind: Literal["directory", "zip", "git"]
    location: str = Field(min_length=1, max_length=4096)
    revision: str | None = Field(default=None, max_length=200)
    subdirectory: str = Field(default=".", max_length=1000)


class ExtensionInstall(Command):
    source: ExtensionSource
    scope: Literal["user", "workspace"] = "workspace"
    enabled: bool = True
    expected_content_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    request_id: UUID


class ExtensionUpgrade(Command):
    expected_version: int = Field(ge=1)
    source: ExtensionSource | None = None
    expected_content_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    request_id: UUID


class ExtensionChange(Command):
    expected_version: int = Field(ge=1)
    enabled: bool
    scope: Literal["installation", "workspace"] = "installation"
    request_id: UUID


class ExtensionRemove(Command):
    expected_version: int = Field(ge=1)
    request_id: UUID


class ExtensionMCPImport(ExtensionRemove):
    server_name: str = Field(min_length=1, max_length=100)


class ExtensionView(PublicResponse):
    id: str
    name: str
    version: int
    scope: str
    workspace_id: str | None
    enabled: bool
    effective_enabled: bool
    workspace_override: bool | None
    status: str
    revision_id: str
    source: ExtensionSource
    source_revision: str | None
    manifest: dict


class ExtensionList(PublicResponse):
    installations: list[ExtensionView]


class ExtensionInspection(PublicResponse):
    manifest: dict
    source_revision: str | None
    total_bytes: int
    file_count: int
    content_digest: str


class SkillTarget(Command):
    skill_id: str = Field(
        min_length=1, max_length=150,
        description="复制list_skills返回的完整skill_id（安装ID:名称），不要只填名称或revision_id。",
    )


class SkillResource(SkillTarget):
    path: str = Field(default="SKILL.md", min_length=1, max_length=1000)
    start_line: int = Field(default=1, ge=1)
    limit: int = Field(default=200, ge=1, le=1000)


class SkillList(Command):
    query: str = Field(default="", max_length=200)
