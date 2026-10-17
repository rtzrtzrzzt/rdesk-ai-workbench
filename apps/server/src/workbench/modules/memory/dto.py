"""Stable memory HTTP/service contracts; Markdown content is never an authority grant."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Scope = Literal["user", "workspace"]
FileVersion = Annotated[str, StringConstraints(pattern=r"^(missing|[a-f0-9]{64})$")]
Body = Annotated[str, StringConstraints(max_length=500_000)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
References = Annotated[
    list[Annotated[str, StringConstraints(min_length=1, max_length=2048)]], Field(max_length=32)
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryCreate(Contract):
    scope: Scope
    title: Title
    body: Body
    expected_scope_generation: int = Field(ge=1)
    request_id: UUID
    source_refs: References = Field(default_factory=list)


class MemoryUpdate(Contract):
    body: Body
    expected_version: FileVersion
    request_id: UUID


class MemoryForget(Contract):
    expected_version: FileVersion
    request_id: UUID


class GlobalRulesUpdate(Contract):
    body: Body
    expected_version: FileVersion
    request_id: UUID


class ProposalCreate(Contract):
    scope: Scope
    document_id: UUID | None = None
    title: Title
    body: Body
    base_version: FileVersion = "missing"
    expected_scope_generation: int = Field(ge=1)
    source_refs: References = Field(default_factory=list)
    request_id: UUID


class ProposalDecision(Contract):
    approved: bool
    expected_version: int = Field(ge=1)
    request_id: UUID


class DocumentSummary(Contract):
    id: str
    scope: Scope
    workspace_id: str | None
    title: str
    path: str
    version: str
    status: Literal["active", "forgotten", "missing", "conflict", "outcome_unknown"]
    source_refs: list[str]
    updated_at: datetime | None
    scope_generation: int


class Document(DocumentSummary):
    body: str | None


class WriteNotice(Contract):
    id: str
    operation: str
    document_id: str | None
    status: str
    created_at: datetime
    message: str


class DocumentList(Contract):
    scope: Scope
    workspace_id: str | None
    scope_generation: int
    documents: list[DocumentSummary]
    pending_writes: list[WriteNotice]


class ForgetResult(Contract):
    document_id: str
    status: Literal["forgotten"]
    scope_generation: int
    journal_id: str


class SearchHit(Contract):
    document_id: str
    title: str
    path: str
    version: str
    scope: Scope
    workspace_id: str | None
    start_line: int
    end_line: int
    text: str
    source_refs: list[str]


class SearchResult(Contract):
    scope: Scope
    workspace_id: str | None
    scope_generation: int
    query: str
    hits: list[SearchHit]
    truncated: bool


class GlobalRules(Contract):
    body: str
    version: str
    source_path: str
    exists: bool
    editable: Literal[True]
    scope: Literal["user"]
    workspace_id: None


class RuleSource(Contract):
    source_path: str
    version: str
    scope: Literal["user", "project", "directory"]
    applies_to: str
    body: str
    editable: bool
    precedence: int


class RulesSnapshot(Contract):
    global_rules: GlobalRules
    rules: list[RuleSource]
    version_digest: str
    relative_paths: list[str]


class Proposal(Contract):
    id: str
    scope: Scope
    workspace_id: str | None
    document_id: str | None
    title: str
    body: str | None
    base_version: str
    scope_generation: int
    status: Literal["pending", "approved", "rejected", "invalidated", "conflict", "outcome_unknown"]
    source_refs: list[str]
    source_actor: Literal["user", "agent", "subagent"]
    created_at: datetime
    decided_at: datetime | None
    version: int


RevisionVersion = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class MemoryRestore(Contract):
    target_version: RevisionVersion
    expected_current_version: RevisionVersion
    expected_scope_generation: int = Field(ge=1)
    request_id: UUID


class MemoryRevisionSummary(Contract):
    document_id: str
    version: str
    scope_generation: int
    title: str
    origin: Literal["observed_current", "managed_publish"]
    captured_at: datetime
    journal_id: str
    journal_status: str
    status: Literal["available", "unavailable"]
    restorable: bool
    unavailable_reason: (
        Literal["scope_generation_changed", "current_document_not_active", "snapshot_unavailable"]
        | None
    )


class MemoryRevisionView(MemoryRevisionSummary):
    body: str
    source_refs: list[str]


class MemoryRevisionList(Contract):
    document_id: str
    current_version: str
    current_status: Literal["active", "forgotten", "missing", "conflict", "outcome_unknown"]
    scope_generation: int
    history_complete: Literal[False]
    history_gap_reason: str
    history_started_at: datetime | None
    revisions: list[MemoryRevisionSummary]
    truncated: bool
    warnings: list[str]


class MemoryRevisionComparison(Contract):
    document_id: str
    target_version: str
    current_version: str
    scope_generation: int
    target_title: str
    current_title: str
    title_changed: bool
    target_source_refs: list[str]
    current_source_refs: list[str]
    diff: str
    truncated: bool
