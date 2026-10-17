"""Read-only PostgreSQL aggregates over actual attempts; no telemetry or billing synthesis."""

import base64
import hashlib
import json
import math
import re
from datetime import datetime, timedelta
from functools import wraps
from uuid import UUID

from sqlalchemy import Numeric, and_, case, cast, func, literal, or_, select, tuple_, union_all
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import now
from workbench.modules import workspace_service
from workbench.modules.analytics import dto
from workbench.modules.models import service as model_service
from workbench.modules.models.domain import ModelConnection
from workbench.modules.runs.domain import Attempt, Run, Step
from workbench.modules.tools.domain import Operation
from workbench.modules.usage import UsageRecord
from workbench.modules.workspaces import Workspace

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
TOKEN_FIELDS = (
    "input_tokens_total",
    "output_tokens_total",
    "cache_read_input_tokens",
    "cache_write_input_tokens",
    "reasoning_tokens",
)
MAX_COUNT = 2**63 - 1


def _cost():
    return dto.CostStatus(
        status="unknown",
        amount=None,
        currency=None,
        reason="当前未记录价格修订或提供方账单；本地无 API 账单也不代表总成本为零。",
    )


def _filters(db, user_id, workspace_id, window, model_connection_id, end_at=None):
    if window not in WINDOWS:
        raise AppError("analytics_window_invalid", "统计窗口仅支持 24h、7d 或 30d。", 422)
    if workspace_id is not None:
        workspace_service.get(db, user_id, workspace_id)
    if model_connection_id is not None:
        model_service.get(db, user_id, model_connection_id)
    end_at = end_at or now()
    return dto.AnalyticsFilters(
        workspace_id=workspace_id,
        model_connection_id=model_connection_id,
        window=window,
        start_at=end_at - WINDOWS[window],
        end_at=end_at,
    )


def _run_conditions(user_id, filters):
    conditions = [Run.user_id == user_id, Workspace.user_id == user_id]
    if filters.workspace_id is not None:
        conditions.append(Run.workspace_id == filters.workspace_id)
    if filters.model_connection_id is not None:
        conditions.append(Run.model_connection_id == filters.model_connection_id)
    return conditions


def _metric(key, *, integer=True):
    value = UsageRecord.metrics[key]
    text = value.astext
    numeric = case(
        (
            and_(
                func.jsonb_typeof(value) == "number",
                text.op("~")(r"^[0-9]+$" if integer else r"^[0-9]+(?:\.[0-9]+)?$"),
            ),
            cast(text, Numeric),
        ),
        else_=None,
    )
    # Invalid/out-of-range historical values remain unknown, never coerced to zero.
    return case((numeric <= (MAX_COUNT if integer else 2**53 - 1), numeric), else_=None).label(key)


def _attempts(user_id, filters):
    return (
        select(
            Attempt.id.label("attempt_id"),
            Attempt.run_id,
            Attempt.step_id,
            Attempt.number.label("attempt_number"),
            Attempt.status,
            Attempt.created_at,
            Attempt.finished_at,
            Attempt.error["code"].astext.label("error_code"),
            Run.status.label("run_status"),
            Run.workspace_id,
            Workspace.name.label("workspace_name"),
            Run.model_connection_id,
            ModelConnection.name.label("model_name"),
            ModelConnection.model_identifier,
            ModelConnection.provider_type,
            UsageRecord.id.label("usage_record_id"),
            UsageRecord.metrics["source"].astext.label("usage_source"),
            UsageRecord.metrics["finish_reason"].astext.label("finish_reason"),
            *(_metric(key) for key in TOKEN_FIELDS),
            _metric("first_visible_ms", integer=False),
            _metric("duration_ms", integer=False),
        )
        .select_from(Attempt)
        .join(
            Step,
            and_(Attempt.step_id == Step.id, Step.run_id == Attempt.run_id, Step.kind == "model"),
        )
        .join(Run, Run.id == Attempt.run_id)
        .join(Workspace, Workspace.id == Run.workspace_id)
        .outerjoin(
            ModelConnection,
            and_(ModelConnection.id == Run.model_connection_id, ModelConnection.user_id == user_id),
        )
        .outerjoin(
            UsageRecord,
            and_(
                UsageRecord.attempt_id == Attempt.id,
                UsageRecord.run_id == Run.id,
                UsageRecord.user_id == user_id,
                UsageRecord.workspace_id == Run.workspace_id,
                UsageRecord.model_connection_id == Run.model_connection_id,
            ),
        )
        .where(
            *_run_conditions(user_id, filters),
            Attempt.created_at >= filters.start_at,
            Attempt.created_at < filters.end_at,
        )
    )


def _ratio(numerator, denominator):
    return float(numerator / denominator) if numerator is not None and denominator else None


def _int(value):
    return int(value) if value is not None else None


def _float(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _metric_projection(base):
    pair = and_(
        base.c.input_tokens_total.is_not(None),
        base.c.cache_read_input_tokens.is_not(None),
        base.c.cache_read_input_tokens <= base.c.input_tokens_total,
    )
    rate = case(
        (
            and_(base.c.output_tokens_total.is_not(None), base.c.duration_ms > 0),
            base.c.output_tokens_total * 1000 / base.c.duration_ms,
        ),
        else_=None,
    )
    fields = [
        func.count().label("attempts"),
        func.count(base.c.usage_record_id).label("usage_records"),
        func.count()
        .filter(or_(*(base.c[key].is_not(None) for key in TOKEN_FIELDS)))
        .label("known_usage_attempts"),
    ]
    for key in TOKEN_FIELDS:
        fields.extend(
            (func.sum(base.c[key]).label(key), func.count(base.c[key]).label(key + "_count"))
        )
    fields.extend(
        (
            func.count().filter(pair).label("cache_count"),
            func.count().filter(and_(pair, base.c.cache_read_input_tokens > 0)).label("cache_hits"),
            func.count()
            .filter(base.c.cache_read_input_tokens > base.c.input_tokens_total)
            .label("cache_inconsistent"),
            func.sum(base.c.input_tokens_total).filter(pair).label("cache_input"),
            func.sum(base.c.cache_read_input_tokens).filter(pair).label("cache_read"),
            func.count(base.c.first_visible_ms).label("visible_count"),
            func.percentile_cont(0.5).within_group(base.c.first_visible_ms).label("visible_p50"),
            func.percentile_cont(0.95).within_group(base.c.first_visible_ms).label("visible_p95"),
            func.count(rate).label("rate_count"),
            func.percentile_cont(0.5).within_group(rate).label("rate_p50"),
            func.percentile_cont(0.95).within_group(rate).label("rate_p95"),
            (
                func.sum(base.c.output_tokens_total).filter(rate.is_not(None))
                * 1000
                / func.sum(base.c.duration_ms).filter(rate.is_not(None))
            ).label("weighted_rate"),
        )
    )
    return fields


def _usage_metrics(row):
    total = row["attempts"]

    def token(key):
        count = row[key + "_count"]
        return dto.TokenMetric(
            known_tokens=_int(row[key]),
            known_attempts=count,
            eligible_attempts=total,
            coverage_ratio=_ratio(count, total),
        )

    def distribution(prefix, unit):
        count = row[prefix + "_count"]
        return dict(
            p50=_float(row[prefix + "_p50"]),
            p95=_float(row[prefix + "_p95"]),
            sample_count=count,
            eligible_attempts=total,
            coverage_ratio=_ratio(count, total),
            unit=unit,
            method="continuous_linear",
        )

    return dto.UsageMetrics(
        attempts=total,
        usage_records=row["usage_records"],
        known_usage_attempts=row["known_usage_attempts"],
        known_usage_coverage_ratio=_ratio(row["known_usage_attempts"], total),
        by_status=[
            dto.StatusCount(**item)
            for item in sorted(row["statuses"] or [], key=lambda item: item["status"])
        ],
        input_tokens=token("input_tokens_total"),
        output_tokens=token("output_tokens_total"),
        cache_read_tokens=token("cache_read_input_tokens"),
        cache_write_tokens=token("cache_write_input_tokens"),
        reasoning_tokens=token("reasoning_tokens"),
        cache=dto.CacheMetrics(
            observed_input_tokens=_int(row["cache_input"]),
            observed_read_tokens=_int(row["cache_read"]),
            known_attempts=row["cache_count"],
            hit_attempts=row["cache_hits"],
            eligible_attempts=total,
            inconsistent_attempts=row["cache_inconsistent"],
            coverage_ratio=_ratio(row["cache_count"], total),
            token_hit_ratio=_ratio(row["cache_read"], row["cache_input"]),
            request_hit_ratio=_ratio(row["cache_hits"], row["cache_count"]),
            known_input_token_coverage_ratio=_ratio(row["cache_input"], row["input_tokens_total"]),
        ),
        first_visible_ms=dto.Distribution(**distribution("visible", "ms")),
        request_output_rate=dto.OutputRate(
            **distribution("rate", "tokens_per_second"),
            weighted_tokens_per_second=_float(row["weighted_rate"]),
        ),
    )


def _aggregate_statement(user_id, filters, granularity):
    source = _attempts(user_id, filters).cte("attempt_facts")
    bucket = func.date_trunc(granularity, source.c.created_at, "UTC")
    totals = (
        select(
            bucket.label("bucket"),
            func.grouping(bucket).label("is_total"),
            *_metric_projection(source),
        )
        .group_by(func.grouping_sets(tuple_(), tuple_(bucket)))
        .cte("totals")
    )
    statuses = (
        select(
            bucket.label("bucket"),
            func.grouping(bucket).label("is_total"),
            source.c.status,
            func.count().label("count"),
        )
        .group_by(func.grouping_sets(tuple_(source.c.status), tuple_(bucket, source.c.status)))
        .cte("status_counts")
    )
    status_json = (
        select(
            statuses.c.bucket,
            statuses.c.is_total,
            func.jsonb_agg(
                func.jsonb_build_object("status", statuses.c.status, "count", statuses.c.count)
            ).label("statuses"),
        )
        .group_by(statuses.c.bucket, statuses.c.is_total)
        .cte("status_json")
    )
    return select(totals, status_json.c.statuses).outerjoin(
        status_json,
        and_(
            totals.c.is_total == status_json.c.is_total,
            totals.c.bucket.is_not_distinct_from(status_json.c.bucket),
        ),
    )


def _entity_counts(db, user_id, filters):
    conditions = _run_conditions(user_id, filters)
    runs = (
        select(literal("runs").label("kind"), Run.status, func.count().label("count"))
        .select_from(Run)
        .join(Workspace, Workspace.id == Run.workspace_id)
        .where(*conditions, Run.created_at >= filters.start_at, Run.created_at < filters.end_at)
        .group_by(Run.status)
    )
    operations = (
        select(literal("operations").label("kind"), Operation.status, func.count().label("count"))
        .select_from(Operation)
        .join(Run, Run.id == Operation.run_id)
        .join(Workspace, Workspace.id == Run.workspace_id)
        .where(
            *conditions,
            Operation.created_at >= filters.start_at,
            Operation.created_at < filters.end_at,
        )
        .group_by(Operation.status)
    )
    rows = list(db.execute(union_all(runs, operations)).mappings())
    return {
        kind: dto.EntityCounts(
            total=sum(row["count"] for row in rows if row["kind"] == kind),
            by_status=[
                dto.StatusCount(status=row["status"], count=row["count"])
                for row in sorted(rows, key=lambda row: row["status"])
                if row["kind"] == kind
            ],
        )
        for kind in ("runs", "operations")
    }


def _without_autoflush(function):
    @wraps(function)
    def read(db, *args, **kwargs):
        with db.no_autoflush:
            return function(db, *args, **kwargs)

    return read


@_without_autoflush
def summary(
    db: Session,
    user_id: str,
    workspace_id: str | None = None,
    window: dto.Window = "24h",
    model_connection_id: str | None = None,
) -> dto.AnalyticsSummary:
    filters = _filters(db, user_id, workspace_id, window, model_connection_id)
    granularity = "hour" if window == "24h" else "day"
    rows = list(db.execute(_aggregate_statement(user_id, filters, granularity)).mappings())
    total = next(row for row in rows if row["is_total"] == 1)
    span = timedelta(hours=1) if granularity == "hour" else timedelta(days=1)
    series = [
        dto.UsageTrendPoint(
            bucket_start=row["bucket"], bucket_end=row["bucket"] + span, metrics=_usage_metrics(row)
        )
        for row in sorted(
            (row for row in rows if row["is_total"] == 0), key=lambda row: row["bucket"]
        )
    ]
    counts = _entity_counts(db, user_id, filters)
    return dto.AnalyticsSummary(
        generated_at=now(),
        filters=filters,
        metrics=_usage_metrics(total),
        runs=counts["runs"],
        operations=counts["operations"],
        cost=_cost(),
        granularity=granularity,
        timezone="UTC",
        series=series,
        notes=[
            "分母为 model 步骤的实际 Attempt；含失败、取消与缺计量的尝试。",
            "重试分别计数；已知用量指至少一个标准 token 字段已知。",
            "缓存命中使用 input/read 共同已知且 read<=input 的子集。",
            "缓存写入独立，不算读取命中。",
            "输入 token 覆盖率仅相对于已知输入量。",
            "首可见时延不是首事件时延；分位数使用连续线性插值。",
            "少量分位数样本不能证明稳定性。",
            "输出速率含请求阶段开销及可能的推理 token，不是解码速度。",
            "时间按 Attempt 创建时间落桶；不补空桶。",
            "Run/Operation 按各自创建时间计数，状态为读取时状态。",
            "当前连接标签不等于历史配置；费用缺少价格或账单。",
        ],
    )


def _digest(user_id, workspace_id, window, model_connection_id):
    return hashlib.sha256(
        json.dumps(
            [user_id, workspace_id, window, model_connection_id], separators=(",", ":")
        ).encode()
    ).hexdigest()


def _cursor_read(cursor, digest):
    try:
        if not isinstance(cursor, str) or len(cursor) > 2048:
            raise ValueError
        data = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
        if not isinstance(data, dict) or any(
            not isinstance(data.get(key), str)
            for key in ("filter_digest", "end_at", "created_at", "attempt_id")
        ):
            raise ValueError
        if (
            set(data) != {"version", "filter_digest", "end_at", "created_at", "attempt_id"}
            or data["version"] != 1
            or data["filter_digest"] != digest
        ):
            raise ValueError
        end = datetime.fromisoformat(data["end_at"])
        created = datetime.fromisoformat(data["created_at"])
        if (
            end.tzinfo is None
            or created.tzinfo is None
            or not now() - timedelta(days=1) <= end <= now()
            or created >= end
        ):
            raise ValueError
        return end, created, str(UUID(data["attempt_id"]))
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise AppError(
            "analytics_cursor_invalid",
            "明细游标无效、已过期或过滤条件已改变，请从第一页重新读取。",
            422,
        ) from None


def _safe_label(value):
    return (
        value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value) else None
    )


def _call(row):
    counts = {key: _int(row[key]) for key in TOKEN_FIELDS}
    duration = _float(row["duration_ms"])
    rate = (
        counts["output_tokens_total"] * 1000 / duration
        if counts["output_tokens_total"] is not None and duration
        else None
    )
    code = _safe_label(row["error_code"])
    return dto.UsageCall(
        attempt_id=row["attempt_id"],
        usage_record_id=row["usage_record_id"],
        run_id=row["run_id"],
        step_id=row["step_id"],
        workspace_id=row["workspace_id"],
        workspace_name=row["workspace_name"],
        model_connection_id=row["model_connection_id"],
        model_name=row["model_name"],
        model_identifier=row["model_identifier"],
        provider_type=row["provider_type"],
        attempt_number=row["attempt_number"],
        status=row["status"],
        run_status=row["run_status"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        usage_source=row["usage_source"]
        if row["usage_source"] in {"provider", "local_engine"}
        else None,
        usage_known=any(value is not None for value in counts.values()),
        **counts,
        first_visible_ms=_float(row["first_visible_ms"]),
        duration_ms=duration,
        request_output_tokens_per_second=_float(rate),
        finish_reason=_safe_label(row["finish_reason"]),
        error=dto.CallError(
            code=code,
            message="本次调用已停止。"
            if code == "cancelled"
            else "调用发生错误；请在对应任务中核对诊断。",
        )
        if code
        else None,
        cost=_cost(),
    )


@_without_autoflush
def calls(
    db: Session,
    user_id: str,
    workspace_id: str | None = None,
    window: dto.Window = "24h",
    model_connection_id: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dto.AnalyticsCalls:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise AppError("analytics_limit_invalid", "每页数量必须为 1–100。", 422)
    digest = _digest(user_id, workspace_id, window, model_connection_id)
    end, created, attempt_id = _cursor_read(cursor, digest) if cursor else (None, None, None)
    filters = _filters(db, user_id, workspace_id, window, model_connection_id, end)
    query = _attempts(user_id, filters)
    if created is not None:
        query = query.where(
            or_(
                Attempt.created_at < created,
                and_(Attempt.created_at == created, Attempt.id < attempt_id),
            )
        )
    rows = list(
        db.execute(
            query.order_by(Attempt.created_at.desc(), Attempt.id.desc()).limit(limit + 1)
        ).mappings()
    )
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "version": 1,
                    "filter_digest": digest,
                    "end_at": filters.end_at.isoformat(),
                    "created_at": last["created_at"].isoformat(),
                    "attempt_id": last["attempt_id"],
                },
                separators=(",", ":"),
            ).encode()
        ).decode()
    return dto.AnalyticsCalls(
        generated_at=now(),
        filters=filters,
        items=[_call(row) for row in rows[:limit]],
        next_cursor=next_cursor,
    )


def hardware() -> dto.HardwareSnapshot:
    from workbench.modules.analytics.hardware import snapshot

    return snapshot()
