"""Local backup/isolated restore DTOs; secret input is write-only and never in responses."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackupCreate(DTO):
    request_id: UUID
    label: str = Field(default="", max_length=120)
    timeout_seconds: int = Field(default=3600, ge=60, le=86400)


CleanupCategory = Literal["temporary_uploads", "completed_file_staging"]


class CleanupCreate(DTO):
    request_id: UUID
    category: CleanupCategory
    confirm: Literal[True]


class CleanupCategoryView(DTO):
    category: CleanupCategory
    eligible_records: int = Field(ge=0)
    selection_limit: int
    partial: bool
    explanation: str


class CleanupPreview(DTO):
    observed_at: datetime
    categories: list[CleanupCategoryView]
    protected_categories: list[str]


class CleanupResult(DTO):
    request_id: str
    category: CleanupCategory
    status: Literal["complete"]
    selected_records: int
    examined_records: int
    skipped_records: int
    confirmed_removed_files: int | None
    confirmed_removed_bytes: int | None
    recovered_interrupted_operation: bool
    completed_at: datetime
    note: str


class RestoreCreate(DTO):
    request_id: UUID
    backup_id: UUID
    restored_owner_password: SecretStr = Field(min_length=12, max_length=1024)
    timeout_seconds: int = Field(default=3600, ge=60, le=86400)


class BackupFile(DTO):
    path: str
    byte_size: int
    sha256: str
    category: Literal["database", "artifact", "memory", "rules", "upload"]


class WorkspaceReference(DTO):
    id: str
    name: str
    external_root_path: str
    external_files_included: Literal[False]
    remapping_required: Literal[True]


class MemoryReference(DTO):
    id: str
    scope_id: str
    path: str
    status: str
    recorded_version: str | None
    actual_version: str | None


class ScopeReference(DTO):
    id: str
    scope: str
    generation: int


class ForgettingBarrier(DTO):
    document_id: str
    generation: int
    forgotten_version: str
    journal_id: str


class ModelReference(DTO):
    connection_id: str
    revision_id: str | None
    connection_version: int
    provider_type: str
    model_identifier: str
    weights_included: Literal[False]


class ModelAssetReference(DTO):
    repository: str
    revision: str
    path_reference: str
    verification_reference: str
    previously_verified: bool
    declared_bytes: int | None
    weights_included: Literal[False]


class ExtensionReference(DTO):
    name: str
    version: str


class ReconciliationReference(DTO):
    source_table: Literal["operations", "tool_exchanges", "automation_actions"]
    source_id: str
    operation_id: str | None
    run_id: str | None
    status: str
    effect_status: str | None = None


class BackupManifest(DTO):
    format_version: Literal[1]
    backup_id: str
    request_id: str
    request_digest: str
    source_user_id: str
    label: str
    status: Literal["complete", "incomplete", "aborted", "failed"]
    created_at: datetime
    completed_at: datetime | None
    boundary_id: str
    boundary_acquired_at: datetime
    app_version: str
    schema_revisions: list[str]
    checkpoint_migrations: list[int]
    checkpoint_package_version: str
    graph_versions: list[str]
    database_major_version: int
    dump_client_version: str
    extensions: list[ExtensionReference]
    tables: list[str]
    workspaces: list[WorkspaceReference]
    memory_documents: list[MemoryReference]
    memory_scopes: list[ScopeReference]
    forgetting_barriers: list[ForgettingBarrier]
    model_references: list[ModelReference]
    model_assets: list[ModelAssetReference]
    files: list[BackupFile]
    file_count: int
    total_bytes: int
    credentials_policy: str
    excluded: list[str]
    notes: list[str]
    reconciliation_required: list[ReconciliationReference] = Field(default_factory=list)


class BackupView(DTO):
    backup_id: str
    request_id: str
    label: str
    status: Literal["complete", "incomplete", "aborted", "failed"]
    created_at: datetime
    completed_at: datetime | None
    file_count: int
    total_bytes: int
    credentials_policy: str


class RestoreView(DTO):
    restore_id: str
    request_id: str
    backup_id: str
    status: Literal["isolated", "failed", "aborted"]
    created_at: datetime
    database_name: str
    data_dir: str
    connection_file: str
    held_runs: int
    sessions_restored: Literal[False]
    credentials_restored: Literal[False]
    remote_enabled: Literal[False]
    search_enabled: Literal[False]
    user_interface_verified: Literal[False]
    notes: list[str]
