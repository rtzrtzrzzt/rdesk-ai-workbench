from uuid import UUID

from pydantic import Field, SecretStr

from workbench.contracts.base import PublicResponse
from workbench.contracts.requests import Command
from workbench.modules.mcp.dto import ConnectionConfig, ToolSnapshot


class MCPSave(Command):
    expected_version: int = Field(ge=0)
    configuration: ConnectionConfig
    execution_authorized: bool = False
    secrets: dict[str, SecretStr] = Field(default_factory=dict, max_length=16)
    clear_secrets: list[str] = Field(default_factory=list, max_length=16)
    request_id: UUID


class MCPView(PublicResponse):
    id: str
    version: int
    configuration: ConnectionConfig
    execution_authorized: bool
    secret_names: list[str]


class MCPDiscover(Command):
    expected_version: int = Field(ge=1)
    request_id: UUID


class SnapshotView(PublicResponse):
    id: str
    snapshot: ToolSnapshot


class MCPList(PublicResponse):
    connections: list[MCPView]
    snapshots: list[SnapshotView]
