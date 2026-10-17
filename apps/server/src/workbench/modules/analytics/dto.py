"""Read-only analytics contracts: an unobserved measurement is always null, never zero."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

Window = Literal["24h", "7d", "30d"]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyticsFilters(DTO):
    workspace_id: str | None
    model_connection_id: str | None
    window: Window
    start_at: datetime
    end_at: datetime


class StatusCount(DTO):
    status: str
    count: int


class EntityCounts(DTO):
    total: int
    by_status: list[StatusCount]


class TokenMetric(DTO):
    known_tokens: int | None
    known_attempts: int
    eligible_attempts: int
    coverage_ratio: float | None


class CacheMetrics(DTO):
    observed_input_tokens: int | None
    observed_read_tokens: int | None
    known_attempts: int
    hit_attempts: int
    eligible_attempts: int
    inconsistent_attempts: int
    coverage_ratio: float | None
    token_hit_ratio: float | None
    request_hit_ratio: float | None
    known_input_token_coverage_ratio: float | None


class Distribution(DTO):
    p50: float | None
    p95: float | None
    sample_count: int
    eligible_attempts: int
    coverage_ratio: float | None
    unit: Literal["ms", "tokens_per_second"]
    method: Literal["continuous_linear"]


class OutputRate(Distribution):
    weighted_tokens_per_second: float | None


class CostStatus(DTO):
    status: Literal["unknown"]
    amount: None
    currency: None
    reason: str


class UsageMetrics(DTO):
    attempts: int
    usage_records: int
    known_usage_attempts: int
    known_usage_coverage_ratio: float | None
    by_status: list[StatusCount]
    input_tokens: TokenMetric
    output_tokens: TokenMetric
    cache_read_tokens: TokenMetric
    cache_write_tokens: TokenMetric
    reasoning_tokens: TokenMetric
    cache: CacheMetrics
    first_visible_ms: Distribution
    request_output_rate: OutputRate


class UsageTrendPoint(DTO):
    bucket_start: datetime
    bucket_end: datetime
    metrics: UsageMetrics


class AnalyticsSummary(DTO):
    generated_at: datetime
    filters: AnalyticsFilters
    metrics: UsageMetrics
    runs: EntityCounts
    operations: EntityCounts
    cost: CostStatus
    granularity: Literal["hour", "day"]
    timezone: Literal["UTC"]
    series: list[UsageTrendPoint]
    notes: list[str]


class CallError(DTO):
    code: str
    message: str


class UsageCall(DTO):
    attempt_id: str
    usage_record_id: str | None
    run_id: str
    step_id: str
    workspace_id: str
    workspace_name: str
    model_connection_id: str | None
    model_name: str | None
    model_identifier: str | None
    provider_type: str | None
    attempt_number: int
    status: str
    run_status: str
    created_at: datetime
    finished_at: datetime | None
    usage_source: str | None
    usage_known: bool
    input_tokens_total: int | None
    output_tokens_total: int | None
    cache_read_input_tokens: int | None
    cache_write_input_tokens: int | None
    reasoning_tokens: int | None
    first_visible_ms: float | None
    duration_ms: float | None
    request_output_tokens_per_second: float | None
    finish_reason: str | None
    error: CallError | None
    cost: CostStatus


class AnalyticsCalls(DTO):
    generated_at: datetime
    filters: AnalyticsFilters
    items: list[UsageCall]
    next_cursor: str | None


class MemoryHardware(DTO):
    status: Literal["available", "unavailable"]
    source: str
    total_bytes: int | None
    available_bytes: int | None
    used_bytes: int | None
    swap_total_bytes: int | None
    swap_free_bytes: int | None
    description: str


class CPUHardware(DTO):
    status: Literal["available", "unavailable"]
    source: str
    logical_cpus: int | None
    accessible_cpus: int | None
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None
    utilization_percent: None
    description: str


class GPUDevice(DTO):
    index: int
    name: str
    utilization_percent: float | None
    memory_total_bytes: int | None
    memory_used_bytes: int | None
    temperature_celsius: float | None
    memory_model: Literal["unified", "unknown"]
    system_memory_overlap: bool | None


class GPUHardware(DTO):
    status: Literal["available", "unavailable"]
    source: str
    devices: list[GPUDevice]
    description: str


class StorageHardware(DTO):
    status: Literal["available", "unavailable"]
    source: str
    path: str
    total_bytes: int | None
    used_bytes: int | None
    free_bytes: int | None
    description: str


class HardwareSnapshot(DTO):
    sampled_at: datetime
    platform: str
    architecture: str
    scope: Literal["visible_to_server"]
    memory: MemoryHardware
    cpu: CPUHardware
    gpu: GPUHardware
    storage: StorageHardware
    notes: list[str]
