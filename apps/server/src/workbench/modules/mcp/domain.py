"""Host integration ports: implementations must commit before returning."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from workbench.modules.mcp.dto import ConnectionConfig, MCPOutcome


@dataclass(frozen=True)
class Authorization:
    """Only host policy creates this grant, never a model or remote annotation."""

    operation_id: str
    connection_id: str
    connection_version: int
    identity_ref: str
    snapshot_id: str
    remote_tool: str
    arguments_digest: str
    allowed: bool
    effect_policy: Literal["read_only", "side_effect"] = "side_effect"
    approval_id: str | None = None


@dataclass(frozen=True)
class ExchangeRequest:
    operation_id: str
    attempt_id: str
    sequence: int
    connection_id: str
    connection_version: int
    identity_ref: str
    snapshot_id: str
    protocol_version: str
    remote_tool: str
    arguments_ref: str
    arguments_digest: str
    # Restricted ledger data: includes request_state / continuation on resume.
    request_payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class Reservation:
    exchange_id: str
    acquired: bool
    existing_outcome: MCPOutcome | None = None


class ExchangeStore(Protocol):
    async def reserve(self, request: ExchangeRequest) -> Reservation:
        """Commit UNIQUE(operation_id, attempt_id, sequence) before acquiring send ownership.

        Repeated reserve must return acquired=False. Persist the full restricted
        request payload, including continuation, state and answer references.
        """
        ...

    async def mark_dispatched(self, exchange_id: str) -> None:
        """Commit 'may have been sent' BEFORE transport dispatch (not proof of receipt)."""
        ...

    async def finish(self, exchange_id: str, outcome: MCPOutcome) -> None:
        """Commit response + continuation atomically; do not discard prior request evidence."""
        ...


class SecretResolver(Protocol):
    async def resolve(self, reference: str, identity_ref: str) -> str:
        """Host vault lookup, subject to the authenticated identity; never model-visible."""
        ...


class ConnectionGate(Protocol):
    async def check(self, config: ConnectionConfig, identity_ref: str) -> None:
        """Host confirms stdio execution/network target authorization before any transport opens."""
        ...
