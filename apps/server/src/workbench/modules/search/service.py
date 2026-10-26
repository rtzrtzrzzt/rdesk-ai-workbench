"""Search orchestration. Caller owns database transactions; no fallback provider or retries."""

import asyncio
import hashlib
import json
import re
from datetime import timedelta

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import now
from workbench.modules import workspace_service
from workbench.modules.search import dto
from workbench.modules.search.domain import SearchConnection
from workbench.modules.search.http import (
    MAX_TEXT,
    TextExtractor,
    bounded_body,
    client,
    normalize_url,
    plain,
    public_document,
    status_error,
)

CHECK_TTL = timedelta(minutes=5)


def _row(db: Session, user_id: str, workspace_id: str, scope: dto.Scope | None = None):
    workspace_service.get(db, user_id, workspace_id)
    rows = list(db.scalars(select(SearchConnection).where(SearchConnection.user_id == user_id)))
    user = next((row for row in rows if row.scope == "user" and row.workspace_id is None), None)
    workspace = next(
        (row for row in rows if row.scope == "workspace" and row.workspace_id == workspace_id), None
    )
    return user if scope == "user" else workspace if scope == "workspace" else workspace or user


def _view(row: SearchConnection) -> dto.SearchConnectionView:
    return dto.SearchConnectionView(
        id=row.id,
        provider_type="searxng",
        scope=row.scope,
        workspace_id=row.workspace_id,
        name=row.name,
        endpoint=row.endpoint,
        enabled=row.enabled,
        engines=row.engines,
        language=row.language,
        region=row.region,
        max_results=row.max_results,
        timeout_seconds=row.timeout_seconds,
        version=row.version,
        status=row.status,
        checked_at=row.checked_at,
        last_error=dto.SearchError.model_validate(row.last_error) if row.last_error else None,
    )


def get_connection(
    db: Session, user_id: str, workspace_id: str, scope: dto.Scope | None = None
) -> dto.SearchConnectionView | None:
    row = _row(db, user_id, workspace_id, scope)
    return _view(row) if row else None


def _save_connection(
    db: Session, user_id: str, workspace_id: str, command: dto.SearchConnectionSave
) -> dto.SearchConnectionView:
    workspace_service.get(db, user_id, workspace_id)
    endpoint = _endpoint(command.endpoint)
    key = f"user:{user_id}" if command.scope == "user" else f"workspace:{workspace_id}"
    # Serialize creation as well as updates; a missing row cannot be row-locked.
    lock_key = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    row = db.scalar(
        select(SearchConnection).where(SearchConnection.scope_key == key).with_for_update()
    )
    if row is not None and row.user_id != user_id:
        raise AppError("search_connection_not_found", "搜索连接不存在或不可访问。", 404)
    if (row is None and command.expected_version is not None) or (
        row is not None and row.version != command.expected_version
    ):
        raise AppError(
            "search_version_conflict", "搜索连接已修改，请重新读取后保存。", 409, "conflict"
        )
    if row is None:
        row = SearchConnection(
            user_id=user_id,
            workspace_id=workspace_id if command.scope == "workspace" else None,
            scope=command.scope,
            scope_key=key,
        )
        db.add(row)
    else:
        row.version += 1
    for name in (
        "name",
        "enabled",
        "engines",
        "language",
        "region",
        "max_results",
        "timeout_seconds",
    ):
        setattr(row, name, getattr(command, name))
    row.endpoint = endpoint
    row.status, row.checked_at, row.last_error = "unchecked", None, None
    db.flush()
    return _view(row)


def _endpoint(value: str) -> str:
    url = normalize_url(value.strip(), public=False)
    if url.query:
        raise AppError("search_endpoint_invalid", "搜索连接基础 URL 不应含查询参数。", 422)
    return str(url).rstrip("/")


async def engine_catalog(endpoint: str, timeout_seconds: int = 20) -> dto.SearchEngineCatalog:
    """Read this instance's authorized engine names, not a bundled/global list."""
    endpoint = _endpoint(endpoint)
    base = endpoint[:-7] if endpoint.endswith("/search") else endpoint
    try:
        async with asyncio.timeout(timeout_seconds):
            async with client(timeout_seconds) as http:
                async with http.stream(
                    "GET", base + "/config", headers={"Accept": "application/json"}
                ) as response:
                    status_error(response, search=True)
                    raw = json.loads(await bounded_body(response))
    except (TimeoutError, httpx.TimeoutException):
        raise AppError(
            "search_catalog_timeout", "读取引擎目录超时；已有选择已保留。", 504, "transient", True
        ) from None
    except httpx.HTTPError:
        raise AppError(
            "search_catalog_network_error", "无法连接引擎目录；请检查端点。", 502, "transient", True
        ) from None
    except ValueError:
        raise AppError(
            "search_catalog_protocol_error",
            "搜索服务未返回有效的引擎目录 JSON。",
            502,
            "dependency",
        ) from None
    if not isinstance(raw, dict) or not isinstance(raw.get("engines"), list):
        raise AppError(
            "search_catalog_protocol_error", "搜索服务响应缺少 engines 目录。", 502, "dependency"
        )
    engines, names = [], set()
    # A malformed/duplicate directory must never authorize an accidental default fallback.
    for item in raw["engines"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not re.fullmatch(r"[A-Za-z0-9 _.-]{1,80}", item["name"])
            or item["name"] in names
            or not isinstance(item.get("enabled"), bool)
            or not isinstance(item.get("categories"), list)
            or any(not isinstance(category, str) for category in item["categories"])
        ):
            raise AppError(
                "search_catalog_protocol_error", "搜索服务引擎目录格式不完整。", 502, "dependency"
            )
        names.add(item["name"])
        engines.append(
            dto.SearchEngine(
                name=item["name"], categories=item["categories"], default_enabled=item["enabled"]
            )
        )
    if not engines:
        raise AppError(
            "search_catalog_empty", "此搜索服务没有公开可选择的引擎。", 502, "dependency"
        )
    return dto.SearchEngineCatalog(
        endpoint=endpoint, fetched_at=now(), engines=sorted(engines, key=lambda item: item.name)
    )


def _validate_engines(engines: list[str], catalog: dto.SearchEngineCatalog) -> None:
    available = {engine.name for engine in catalog.engines}
    unknown = [name for name in engines if name not in available]
    if unknown:
        raise AppError(
            "search_engines_unknown",
            "所选引擎不在此服务的当前目录中，请重新选择：" + "、".join(unknown),
            422,
            "dependency",
        )


def _catalog_target(db, user_id, workspace_id, command):
    try:
        workspace_service.get(db, user_id, workspace_id)
        if command.endpoint is not None:
            return _endpoint(command.endpoint), command.timeout_seconds
        row = _row(db, user_id, workspace_id, command.scope)
        if row is None:
            raise AppError("search_not_configured", "请先填写 SearXNG 端点再读取引擎目录。", 409)
        return row.endpoint, row.timeout_seconds
    finally:
        db.rollback()


async def get_engine_catalog(db, user_id, workspace_id, command):
    endpoint, timeout = await _db_call(_catalog_target, db, user_id, workspace_id, command)
    return await engine_catalog(endpoint, timeout)


async def save_connection(
    db: Session,
    user_id: str,
    workspace_id: str,
    command: dto.SearchConnectionSave,
    *,
    before_save=None,
) -> dto.SearchConnectionView:
    # Check authorization before contacting a user-supplied endpoint; release the read transaction.
    await _db_call(
        _catalog_target,
        db,
        user_id,
        workspace_id,
        dto.SearchEngineCatalogQuery(
            endpoint=command.endpoint, timeout_seconds=command.timeout_seconds
        ),
    )
    if command.engines:
        _validate_engines(
            command.engines, await engine_catalog(command.endpoint, command.timeout_seconds)
        )

    def publish():
        if before_save:
            before_save(db)
        return _save_connection(db, user_id, workspace_id, command)

    return await _db_call(publish)


def _capability(row: SearchConnection | dto.SearchConnectionView | None) -> dto.SearchCapability:
    error = None
    status = "unavailable"
    if row is None:
        return dto.SearchCapability(status="not_configured", connection=None, error=None)
    if not row.enabled:
        error = dto.SearchError(
            code="search_disabled", message="搜索连接已禁用；不会发起网络请求。", retryable=False
        )
    elif row.status == "ready" and row.checked_at and now() - row.checked_at <= CHECK_TTL:
        status = "ready"
    elif row.last_error:
        error = dto.SearchError.model_validate(row.last_error)
    else:
        error = dto.SearchError(
            code="search_check_required",
            message="连接尚未检查或检查已过期，请执行真实检查。",
            retryable=False,
        )
    return dto.SearchCapability(
        status=status,
        connection=row if isinstance(row, dto.SearchConnectionView) else _view(row),
        error=error,
    )


def get_capability(db: Session, user_id: str, workspace_id: str) -> dto.SearchCapability:
    return _capability(_row(db, user_id, workspace_id))


def _enabled(db, user_id, workspace_id) -> SearchConnection:
    row = _row(db, user_id, workspace_id)
    if row is None:
        raise AppError("search_not_configured", "请先配置搜索连接并明确启用。", 409, "dependency")
    if not row.enabled:
        raise AppError("search_disabled", "搜索连接已禁用；不会发起网络请求。", 409, "permission")
    return row


def _record(db: Session, row: SearchConnection, error: AppError | None):
    # Ignore an obsolete network response if configuration changed during IO.
    values = {
        "status": "unavailable" if error else "ready",
        "checked_at": now(),
        "last_error": {"code": error.code, "message": error.message, "retryable": error.retryable}
        if error
        else None,
    }
    changed = db.execute(
        update(SearchConnection)
        .where(SearchConnection.id == row.id, SearchConnection.version == row.version)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if not changed.rowcount:
        raise AppError(
            "search_version_conflict", "连接在检查期间已修改，请重新检查。", 409, "conflict"
        )
    for key, value in values.items():
        setattr(row, key, value)
    db.flush()


async def _db_call(function, *args):
    # Cancellation must not close the caller's Session while its DB thread is active.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


def _snapshot(db, user_id, workspace_id, require_enabled):
    try:
        row = (
            _enabled(db, user_id, workspace_id)
            if require_enabled
            else _row(db, user_id, workspace_id)
        )
        return _view(row) if row else None
    finally:
        # Only the detached DTO crosses network IO. Callers must not pass pending writes.
        db.rollback()


def _current(db, user_id, workspace_id, snapshot):
    # Serialize the final effective-priority check against either scope's configuration write.
    for key in (f"user:{user_id}", f"workspace:{workspace_id}"):
        lock_key = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    row = _enabled(db, user_id, workspace_id)
    if row.id != snapshot.id or row.version != snapshot.version:
        raise AppError(
            "search_version_conflict",
            "网络请求期间有效搜索配置已改变，请重新操作。",
            409,
            "conflict",
        )
    return row


def _finish(db, user_id, workspace_id, snapshot, error):
    row = _current(db, user_id, workspace_id, snapshot)
    _record(db, row, error)
    return _capability(row)


async def check_connection(db: Session, user_id: str, workspace_id: str) -> dto.SearchCapability:
    snapshot = await _db_call(_snapshot, db, user_id, workspace_id, False)
    if snapshot is None or not snapshot.enabled:
        return _capability(snapshot)
    error = None
    try:
        # A /config response alone does not prove the JSON search path works.
        await query_connection(snapshot, dto.SearchQuery(query="SearXNG", max_results=1))
    except AppError as failure:
        error = failure
    return await _db_call(_finish, db, user_id, workspace_id, snapshot, error)


async def _query_connection(
    connection: dto.SearchConnectionView, command: dto.SearchQuery
) -> dto.SearchResult:
    """Adapter entry for a previously authorized immutable connection snapshot."""
    if not connection.enabled:
        raise AppError("search_disabled", "搜索连接已禁用。", 409, "permission")
    limit = command.max_results or connection.max_results
    if limit > connection.max_results:
        raise AppError("search_result_limit", "请求结果数超过用户配置上限。", 422)
    language = connection.language
    if connection.region and "-" not in language:
        language += "-" + connection.region
    params = {"q": command.query, "format": "json", "language": language, "pageno": "1"}
    if connection.engines:
        params["engines"] = ",".join(connection.engines)
    if command.time_range:
        params["time_range"] = command.time_range
    endpoint = connection.endpoint.rstrip("/")
    if not endpoint.endswith("/search"):
        endpoint += "/search"
    try:
        async with asyncio.timeout(connection.timeout_seconds):
            async with client(connection.timeout_seconds) as http:
                async with http.stream(
                    "GET", endpoint, params=params, headers={"Accept": "application/json"}
                ) as response:
                    status_error(response, search=True)
                    body = await bounded_body(response)
        raw = json.loads(body)
    except (TimeoutError, httpx.TimeoutException):
        raise AppError(
            "search_timeout", "搜索超时；未自动重试或切换付费 API。", 504, "transient", True
        ) from None
    except httpx.HTTPError:
        raise AppError(
            "search_network_error",
            "搜索连接失败（含 TLS 校验失败）；未自动重试或换源。",
            502,
            "transient",
            True,
        ) from None
    except ValueError:
        raise AppError(
            "search_protocol_error",
            "搜索连接失败或未返回有效 JSON；请核对本机服务及 JSON 配置。",
            502,
            "dependency",
        ) from None
    if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
        raise AppError(
            "search_protocol_error", "搜索服务响应缺少 results 列表。", 502, "dependency"
        )
    if not raw["results"] and raw.get("unresponsive_engines"):
        raise AppError(
            "search_engines_unavailable",
            "未取得结果且搜索引擎报告失败；不能确认搜索能力可用。",
            503,
            "transient",
            True,
        )
    fetched_at = now()
    results, seen = [], set()
    warnings = (
        ["部分搜索引擎不可用；当前仅展示其余实际结果。"] if raw.get("unresponsive_engines") else []
    )
    truncated = False
    budget = 40_000
    for item in raw["results"][:200]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        try:
            url = normalize_url(item["url"], public=True)
        except AppError:
            continue
        if command.domains and not any(
            url.host == domain or url.host.endswith("." + domain) for domain in command.domains
        ):
            continue
        if str(url) in seen:
            continue
        title = plain(item.get("title", "") if isinstance(item.get("title"), str) else "", 300)
        snippet = plain(
            item.get("content", "") if isinstance(item.get("content"), str) else "", 1200
        )
        engines = item.get(
            "engines", [item["engine"]] if isinstance(item.get("engine"), str) else []
        )
        engines = (
            [value[:80] for value in engines[:10] if isinstance(value, str)]
            if isinstance(engines, list)
            else []
        )
        cost = len(str(url)) + len(title) + len(snippet) + sum(map(len, engines)) + 100
        if len(results) >= limit or cost > budget:
            truncated = True
            break
        results.append(
            dto.SearchSource(
                url=str(url), title=title, snippet=snippet, fetched_at=fetched_at, engines=engines
            )
        )
        budget -= cost
        seen.add(str(url))
    truncated |= len(raw["results"]) > 200
    if command.domains:
        warnings.append("域名筛选在返回结果上严格应用；本次只读取搜索服务第一页。")
    return dto.SearchResult(
        connection_id=connection.id,
        query=command.query,
        fetched_at=fetched_at,
        results=results,
        warnings=warnings,
        truncated=truncated,
    )


async def query_connection(
    connection: dto.SearchConnectionView, command: dto.SearchQuery
) -> dto.SearchResult:
    """Validate explicit engine selection on every execution, including after instance changes."""
    if not connection.enabled:
        raise AppError("search_disabled", "搜索连接已禁用。", 409, "permission")
    try:
        async with asyncio.timeout(connection.timeout_seconds):
            if connection.engines:
                _validate_engines(
                    connection.engines,
                    await engine_catalog(connection.endpoint, connection.timeout_seconds),
                )
            return await _query_connection(connection, command)
    except TimeoutError:
        raise AppError("search_timeout", "搜索超时；未自动重试。", 504, "transient", True) from None


async def search(
    db: Session, user_id: str, workspace_id: str, command: dto.SearchQuery
) -> dto.SearchResult:
    snapshot = await _db_call(_snapshot, db, user_id, workspace_id, True)
    result = await query_connection(snapshot, command)
    await _db_call(_finish, db, user_id, workspace_id, snapshot, None)
    return result


async def fetch_page(
    db: Session, user_id: str, workspace_id: str, command: dto.FetchPage
) -> dto.WebPage:
    snapshot = await _db_call(_snapshot, db, user_id, workspace_id, True)
    url, raw_type, body, http_status = await public_document(
        command.url, min(snapshot.timeout_seconds, 30)
    )
    await _db_call(_current, db, user_id, workspace_id, snapshot)
    charset = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", raw_type, re.I)
    try:
        source = body.decode(charset[1] if charset else "utf-8", errors="replace")
    except LookupError:
        source = body.decode("utf-8", errors="replace")
    content_type = raw_type.split(";", 1)[0].strip().lower()
    if content_type == "text/plain":
        text, title, truncated, extraction = (
            source[:MAX_TEXT],
            "",
            len(source) > MAX_TEXT,
            "plain_text",
        )
    else:
        parser = TextExtractor()
        parser.feed(source)
        parser.close()
        text, title, truncated, extraction = (
            parser.text(),
            parser.title.strip(),
            parser.truncated,
            "html_text",
        )
    if not text.strip():
        raise AppError(
            "web_no_readable_text",
            "页面没有可读取的静态正文；可能需要 JavaScript，当前未启用浏览器抓取。",
            422,
            "dependency",
        )
    return dto.WebPage(
        requested_url=command.url,
        url=str(url),
        title=title,
        snippet=" ".join(text.split())[:300],
        text=text,
        fetched_at=now(),
        content_type=content_type,
        http_status=http_status,
        truncated=truncated,
        extraction=extraction,
    )
