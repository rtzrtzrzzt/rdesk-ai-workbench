"""Public diagnostics are a closed schema with no user content or identity fields."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["observed", "partial", "unknown", "unavailable", "reference_only"]
Reason = Literal[
    "ok",
    "missing",
    "not_installed",
    "invalid_metadata",
    "query_unavailable",
    "scan_limit",
    "path_denied",
    "permission_denied",
    "unsupported",
    "clock_skew",
    "no_heartbeat",
    "stale",
    "reference_only",
    "database_scope",
    "other",
]
Category = Literal[
    "artifacts",
    "staging",
    "uploads",
    "memory",
    "command_logs",
    "logs",
    "browser_profiles",
    "backups",
    "restore_staging",
    "database",
]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Dependency(DTO):
    name: Literal[
        "ai-workbench",
        "fastapi",
        "sqlalchemy",
        "psycopg",
        "langgraph",
        "langgraph-checkpoint-postgres",
        "httpx",
        "pydantic",
        "cryptography",
        "keyring",
        "mcp",
        "jsonschema",
        "psutil",
    ]
    version: str | None = Field(default=None, max_length=80)
    status: Status
    reason_code: Reason


class Runtime(DTO):
    app_version: str | None = Field(max_length=80)
    python_version: str = Field(max_length=30)
    python_implementation: Literal["CPython", "PyPy", "other"]
    platform: Literal["linux", "darwin", "win32", "other"]
    architecture: Literal["arm64", "x86_64", "x86", "other"]
    source: Literal["application_and_installed_distribution_metadata"] = (
        "application_and_installed_distribution_metadata"
    )
    dependencies: list[Dependency] = Field(max_length=13)


class Database(DTO):
    status: Status
    read_only_transaction: Literal[True] = True
    postgres_version: str | None = Field(default=None, max_length=30)
    schema_revisions: list[str] = Field(default_factory=list, max_length=16)
    schema_status: Status = "unknown"
    byte_size: int | None = Field(default=None, ge=0)
    source: Literal["postgresql_read_only_queries"] = "postgresql_read_only_queries"
    reason_code: Reason = "ok"


class Queue(DTO):
    queue: Literal["agent", "files"]
    latest_heartbeat_at: datetime | None = None
    heartbeat_age_seconds: float | None = Field(default=None, ge=0)
    heartbeat_status: Literal["recent", "stale", "unknown"] = "unknown"
    recent_worker_count: int | None = Field(default=None, ge=0)
    counts: dict[str, int] = Field(default_factory=dict, max_length=16)
    status: Status = "unknown"
    reason_code: Reason = "no_heartbeat"
    source: Literal["worker_heartbeats_and_owner_jobs"] = "worker_heartbeats_and_owner_jobs"


class ErrorCount(DTO):
    source: Literal["run_error", "attempt_error"]
    code: str = Field(max_length=80)
    count: int = Field(ge=1)


class RecentErrors(DTO):
    status: Status
    lookback_hours: Literal[24] = 24
    window_basis: Literal["record_created_at"] = "record_created_at"
    maximum_rows_per_source: Literal[200] = 200
    sampled_rows: int = Field(ge=0, le=400)
    sample_limit_reached: bool
    counts: list[ErrorCount] = Field(max_length=54)
    reason_code: Reason = "ok"
    source: Literal["bounded_owner_error_code_columns_only"] = (
        "bounded_owner_error_code_columns_only"
    )


class StorageCategory(DTO):
    category: Category
    apparent_bytes: int | None = Field(default=None, ge=0)
    file_count: int | None = Field(default=None, ge=0)
    status: Status
    reason_code: Reason
    source: Literal["nofollow_stat_only", "pg_database_size"] = "nofollow_stat_only"
    skipped_entries: int = Field(default=0, ge=0)


class Storage(DTO):
    status: Status
    data_volume_total_bytes: int | None = Field(default=None, ge=0)
    data_volume_free_bytes: int | None = Field(default=None, ge=0)
    data_volume_available_bytes: int | None = Field(default=None, ge=0)
    categories: list[StorageCategory] = Field(max_length=10)
    examined_entries: int = Field(ge=0, le=20000)
    elapsed_ms: float = Field(ge=0)
    reason_code: Reason = "ok"
    capacity_source: Literal["data_directory_fstatvfs", "unavailable"]
    live_non_atomic_measurement: Literal[True] = True
    external_workspace_files_included: Literal[False] = False
    contents_read: Literal[False] = False


class ModelReferences(DTO):
    connection_count: int | None = Field(default=None, ge=0)
    managed_manifest_present: bool
    previously_verified_asset_bytes: int | None = Field(default=None, ge=0)
    engine: Literal["sglang", "unknown"] = "unknown"
    engine_version: str | None = Field(default=None, max_length=80)
    status: Literal["reference_only"] = "reference_only"
    reason_code: Literal["reference_only"] = "reference_only"
    weights_scanned: Literal[False] = False
    weights_included: Literal[False] = False
    ple_usage: Literal["not_measured"] = "not_measured"


class DiagnosticsSnapshot(DTO):
    schema_version: Literal[1] = 1
    generated_at: datetime
    redaction: Literal["structural_allowlist_v1"] = "structural_allowlist_v1"
    runtime: Runtime
    database: Database
    queues: list[Queue] = Field(min_length=2, max_length=2)
    recent_errors: RecentErrors
    storage: Storage
    models: ModelReferences
