from typing import Any, Literal
from uuid import UUID

from pydantic import Field
from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command
from workbench.modules.files.exports_dto import ArtifactReference


class SurfaceDraft(Command):
    title: str = Field(min_length=1, max_length=200)
    root_id: str = Field(min_length=1, max_length=256)
    components: list[dict[str, Any]] = Field(min_length=1, max_length=64)
    sources: list[ArtifactReference] = Field(default_factory=list, max_length=16)
    bindings: dict[str, dict] = Field(default_factory=dict, max_length=64)


class SurfaceView(PublicResponse):
    artifact_id: str
    version: str
    document: dict[str, Any]


class SurfaceList(PublicResponse):
    surfaces: list[SurfaceView]


class SurfaceCreate(SurfaceDraft):
    request_id: UUID


class ProtocolStatus(PublicResponse):
    a2ui_profile: str = "A2UI v0.9.1有序消息快照子集；只读固定目录，不是完整规范实现。"
    protocol: Literal["workbench-ui/1"] = "workbench-ui/1"
    catalog_version: Literal["workbench-core/1"] = "workbench-core/1"
    executable_html: Literal[False] = False
    reason: str = "自由脚本尚无经过验证的网络隔离环境，保留源码，不执行。"
