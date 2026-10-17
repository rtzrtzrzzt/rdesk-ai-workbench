"""Bounded, serializable contracts. Never place resolved credentials in these models."""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionConfig(DTO):
    connection_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    transport: Literal["stdio", "streamable_http"]
    mode: Literal["auto", "legacy"] = "auto"
    command: str | None = None
    args: list[str] = Field(default_factory=list, max_length=64)
    cwd: str | None = None
    endpoint: str | None = None
    bearer_secret_ref: str | None = None
    env_secret_refs: dict[str, str] = Field(default_factory=dict, max_length=16)
    allowed_tools: list[str] = Field(default_factory=list, max_length=128)
    connect_timeout_seconds: float = Field(default=10, ge=1, le=60)
    call_timeout_seconds: float = Field(default=60, ge=1, le=600)
    max_input_bytes: int = Field(default=65536, ge=1024, le=262144)
    max_output_bytes: int = Field(default=1048576, ge=1024, le=4194304)
    max_tools: int = Field(default=128, ge=1, le=512)
    snapshot_ttl_seconds: int = Field(default=300, ge=10, le=3600)
    continuation_ttl_seconds: int = Field(default=300, ge=10, le=600)
    max_input_rounds: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def transport_fields(self):
        import re

        if self.transport == "stdio":
            if not self.command or not Path(self.command).is_absolute():
                raise ValueError("stdio requires an explicit absolute executable path")
            if not self.cwd or not Path(self.cwd).is_absolute():
                raise ValueError("stdio requires an explicit absolute working directory")
            if self.endpoint or self.bearer_secret_ref:
                raise ValueError("HTTP settings are not stdio settings")
            if any(not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,79}", k) for k in self.env_secret_refs):
                raise ValueError("Invalid secret environment variable name")
            if any(
                k in {"PATH", "HOME", "PYTHONPATH", "LD_PRELOAD", "LD_LIBRARY_PATH"}
                for k in self.env_secret_refs
            ):
                raise ValueError("Credential refs cannot override process startup paths")
        else:
            parsed = urlsplit(self.endpoint or "")
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
                or parsed.query
            ):
                raise ValueError(
                    "HTTP endpoint requires an explicit origin/path; "
                    "credentials, query and fragment are unsupported"
                )
            if (
                self.bearer_secret_ref
                and parsed.scheme != "https"
                and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            ):
                raise ValueError("Remote bearer authentication requires HTTPS")
            if self.command or self.args or self.cwd or self.env_secret_refs:
                raise ValueError("stdio settings are not HTTP settings")
        if any("\x00" in x or len(x) > 8192 for x in self.args):
            raise ValueError("Invalid command argument")
        return self


class RemoteTool(DTO):
    name: str
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    annotations: dict[str, Any] | None
    schema_digest: str
    requires_approval: bool = True


class ToolSnapshot(DTO):
    snapshot_id: str
    connection_id: str
    connection_version: int
    identity_ref: str
    protocol_version: str
    server_info: dict[str, Any] | None
    server_capabilities: dict[str, Any]
    discovered_at: datetime
    expires_at: datetime
    tools: list[RemoteTool]
    directory_digest: str
    sdk_version: Literal["2.3.0"] = "2.3.0"
    negotiation: dict[str, Any] = Field(default_factory=dict)


class InputQuestion(DTO):
    key: str
    message: str
    requested_schema: dict[str, Any]
    question_version: str


class Continuation(DTO):
    continuation_id: str
    operation_id: str
    attempt_id: str
    connection_id: str
    connection_version: int
    identity_ref: str
    snapshot_id: str
    protocol_version: str
    remote_tool: str
    arguments_ref: str
    arguments_digest: str
    request_state: str | None
    questions: list[InputQuestion]
    round: int
    last_exchange_id: str
    created_at: datetime
    expires_at: datetime
    total_deadline: datetime
    transport_lease_id: str | None


class InputAnswer(DTO):
    question_version: str
    action: Literal["accept", "decline", "cancel"]
    content: dict[str, Any] | None = None


class MCPOutcome(DTO):
    status: Literal[
        "succeeded",
        "business_error",
        "protocol_error",
        "awaiting_input",
        "outcome_unknown",
        "not_started",
        "unsupported",
    ]
    operation_id: str
    exchange_id: str | None = None
    code: str | None = None
    message: str | None = None
    dispatched: bool = False
    response_received: bool = False
    protocol_version: str | None = None
    content: list[dict[str, Any]] = Field(default_factory=list)
    structured_content: dict[str, Any] | None = None
    raw_response: dict[str, Any] | None = None
    continuation: Continuation | None = None
    question_plan: list[InputQuestion] = Field(default_factory=list)
    reconciliation: dict[str, Any] = Field(default_factory=dict)

    def model_view(self) -> dict[str, Any]:
        """Keep opaque state, diagnostic metadata and vault/transport refs out of prompts."""
        return {
            "status": self.status,
            "operation_id": self.operation_id,
            "exchange_id": self.exchange_id,
            "code": self.code,
            "message": self.message,
            "content": self.content,
            "structured_content": self.structured_content,
        }
