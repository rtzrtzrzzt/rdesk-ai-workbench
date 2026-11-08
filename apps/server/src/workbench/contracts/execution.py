from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionScope:
    """Business authority, distinct from a Worker's lease ownership token."""

    client_id: str
    namespace_id: str
    service_id: str
    revision_id: str
    workspace_id: str
    allowed_tools: frozenset[str]
    allowed_model_ids: frozenset[str]
    instructions: str
    policy_version: tuple[int, int, int]
    source_revision: str | None = None
    source_paths: tuple[str, ...] = ()


# This is set from a durable binding at each tool boundary, never from HTTP
# headers. asyncio.to_thread carries it into filesystem discovery/read helpers.
current_scope: ContextVar[ExecutionScope | None] = ContextVar("execution_scope", default=None)
