"""Read-only owner diagnostics. Every exported string has a bounded trusted source."""

import importlib.metadata
import io
import json
import platform
import re
import sys
import zipfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from workbench import __version__
from workbench.config import PROJECT_ROOT, get_settings
from workbench.contracts.errors import AppError
from workbench.modules.diagnostics import dto
from workbench.modules.diagnostics.sanitize import REASONS, sanitize_code
from workbench.modules.diagnostics.storage import measure

DEPENDENCIES = (
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
)
JOB_STATUSES = (
    "queued",
    "running",
    "waiting",
    "held",
    "finished",
    "cleanup",
    "cancelled",
    "failed",
    "waiting_children",
    "waiting_resource",
    "other",
)
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,4}[A-Za-z0-9.+-]{0,32}$")
REVISION = re.compile(r"^[0-9a-f]{12,40}$")
MAX_JSON = 512 * 1024
MAX_ZIP = 1024 * 1024


def _version(value):
    return (
        value if isinstance(value, str) and len(value) <= 80 and VERSION.fullmatch(value) else None
    )


def _runtime() -> dto.Runtime:
    dependencies = []
    for name in DEPENDENCIES:
        try:
            value = _version(importlib.metadata.version(name))
            dependencies.append(
                dto.Dependency(
                    name=name,
                    version=value,
                    status="observed" if value else "unknown",
                    reason_code="ok" if value else "invalid_metadata",
                )
            )
        except importlib.metadata.PackageNotFoundError:
            dependencies.append(
                dto.Dependency(name=name, status="unavailable", reason_code="not_installed")
            )
        except (OSError, ValueError):
            dependencies.append(
                dto.Dependency(name=name, status="unknown", reason_code="invalid_metadata")
            )
    architecture = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "i386": "x86",
        "i686": "x86",
    }.get(platform.machine().lower(), "other")
    implementation = platform.python_implementation()
    return dto.Runtime(
        app_version=_version(__version__),
        python_version=platform.python_version(),
        python_implementation=implementation if implementation in {"CPython", "PyPy"} else "other",
        platform=sys.platform if sys.platform in {"linux", "darwin", "win32"} else "other",
        architecture=architecture,
        dependencies=dependencies,
    )


def _owner_id(value) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise AppError(
            "diagnostics_owner_required", "诊断仅允许当前所有者。", 403, "permission"
        ) from None


def _query(connection, statement, params=None):
    try:
        # A missing optional table or timeout must not poison subsequent SELECTs.
        with connection.begin_nested():
            return connection.execute(text(statement), params or {}).mappings().all()
    except SQLAlchemyError:
        return None


def _database(db, user_id, sampled_at):
    bind = db.get_bind() if hasattr(db, "get_bind") else db
    engine = getattr(bind, "engine", bind)
    if getattr(getattr(engine, "dialect", None), "name", None) != "postgresql":
        raise AppError(
            "diagnostics_database_unavailable",
            "诊断需要可验证的PostgreSQL只读连接。",
            503,
            "dependency",
        )
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout='1500ms'"))
            connection.execute(text("SET LOCAL lock_timeout='250ms'"))
            if connection.scalar(text("SELECT current_setting('transaction_read_only')")) != "on":
                raise AppError(
                    "diagnostics_database_unavailable",
                    "无法建立实际只读诊断事务。",
                    503,
                    "dependency",
                )
            owned = connection.scalar(
                text("SELECT 1 FROM users WHERE id=:id AND owner_slot=1"), {"id": user_id}
            )
            if owned != 1:
                raise AppError(
                    "diagnostics_owner_required", "诊断仅允许当前所有者。", 403, "permission"
                )
            version = _query(
                connection,
                "SELECT current_setting('server_version_num') AS version, "
                "pg_database_size(current_database()) AS bytes",
            )
            schema = _query(
                connection, "SELECT version_num FROM alembic_version ORDER BY version_num LIMIT 17"
            )
            heartbeats = _query(
                connection,
                "SELECT queue,max(last_seen_at) AS latest,"
                "count(*) FILTER(WHERE last_seen_at >= :cutoff) AS recent "
                "FROM worker_heartbeats WHERE queue IN ('agent','files') GROUP BY queue",
                {"cutoff": sampled_at - timedelta(seconds=30)},
            )
            # The CASE prevents unknown historical values from becoming export strings.
            statuses = ",".join("'" + value + "'" for value in JOB_STATUSES)
            counts = _query(
                connection,
                "SELECT j.queue,CASE WHEN j.status IN ("
                + statuses
                + ") THEN j.status ELSE 'other' END AS status,"
                "count(*) AS count FROM jobs j JOIN runs r ON r.id=j.run_id "
                "WHERE r.user_id=:id AND j.queue IN ('agent','files') GROUP BY j.queue,2",
                {"id": user_id},
            )
            errors = []
            error_unavailable = False
            limit_reached = False
            for source, table, extra in [
                ("run_error", "runs", ""),
                ("attempt_error", "attempts", "JOIN runs r ON r.id=a.run_id"),
            ]:
                alias = "r" if table == "runs" else "a"
                rows = _query(
                    connection,
                    f"SELECT left({alias}.error->>'code',80) AS code FROM {table} {alias} {extra} "
                    f"WHERE r.user_id=:id AND {alias}.error IS NOT NULL AND "
                    f"{alias}.created_at>=:cutoff ORDER BY {alias}.created_at DESC LIMIT 200",
                    {
                        "id": user_id,
                        "cutoff": sampled_at - timedelta(hours=24),
                    },
                )
                if rows is None:
                    error_unavailable = True
                else:
                    limit_reached |= len(rows) == 200
                    errors.extend((source, sanitize_code(row["code"])) for row in rows)
            workspaces = _query(
                connection,
                "SELECT id FROM workspaces WHERE user_id=:id ORDER BY id LIMIT 257",
                {"id": user_id},
            )
            models = _query(
                connection,
                "SELECT count(*) AS count FROM model_connections WHERE user_id=:id",
                {"id": user_id},
            )
    except AppError:
        raise
    except (SQLAlchemyError, OSError):
        # No DSN, SQL parameters, DB name, driver details, usernames or exception repr.
        raise AppError(
            "diagnostics_database_unavailable",
            "无法读取所有者诊断事实；数据库异常正文已排除。",
            503,
            "dependency",
        ) from None
    # The owned read-only connection is closed before filesystem accounting/ZIP.
    pg_version = None
    byte_size = None
    if version:
        number = int(version[0]["version"])
        pg_version = f"{number // 10000}.{number % 10000}"
        byte_size = int(version[0]["bytes"])
    revisions = []
    valid_schema = schema is not None and len(schema) <= 16
    for row in (schema or [])[:16]:
        if isinstance(row["version_num"], str) and REVISION.fullmatch(row["version_num"]):
            revisions.append(row["version_num"])
        else:
            valid_schema = False
    queues = []
    hb = {row["queue"]: row for row in heartbeats or []}
    for name in ("agent", "files"):
        row = hb.get(name)
        latest = row["latest"] if row else None
        age = (sampled_at - latest).total_seconds() if latest else None
        skew = age is not None and age < -5
        age = None if skew else max(0, age) if age is not None else None
        queues.append(
            dto.Queue(
                queue=name,
                latest_heartbeat_at=None if skew else latest,
                heartbeat_age_seconds=age,
                recent_worker_count=int(row["recent"]) if row else None,
                heartbeat_status="recent"
                if age is not None and age <= 30
                else "stale"
                if age is not None
                else "unknown",
                counts={
                    row["status"]: int(row["count"]) for row in counts or [] if row["queue"] == name
                },
                status="observed" if heartbeats is not None and counts is not None else "partial",
                reason_code="clock_skew"
                if skew
                else "query_unavailable"
                if heartbeats is None or counts is None
                else "no_heartbeat"
                if latest is None
                else "stale"
                if age > 30
                else "ok",
            )
        )
    safe_workspaces = []
    for row in (workspaces or [])[:256]:
        try:
            safe_workspaces.append(str(UUID(row["id"])))
        except (ValueError, TypeError, AttributeError):
            pass
    frequencies = Counter(errors)
    return (
        dto.Database(
            status="observed" if version else "partial",
            postgres_version=pg_version,
            byte_size=byte_size,
            schema_revisions=revisions,
            schema_status="observed" if valid_schema and revisions else "unknown",
            reason_code="ok" if version else "query_unavailable",
        ),
        queues,
        dto.RecentErrors(
            status="partial" if error_unavailable or limit_reached else "observed",
            sampled_rows=len(errors),
            sample_limit_reached=limit_reached,
            counts=[
                dto.ErrorCount(source=source, code=code, count=count)
                for (source, code), count in sorted(frequencies.items())
            ],
            reason_code="query_unavailable"
            if error_unavailable
            else "scan_limit"
            if limit_reached
            else "ok",
        ),
        safe_workspaces,
        workspaces is None or len(workspaces) > 256,
        int(models[0]["count"]) if models else None,
    )


def _model_references(count) -> dto.ModelReferences:
    # Exactly one application-owned JSON reference. Do not touch model_path,
    # verification trees, weight files, or a configured external PLE directory.
    path = PROJECT_ROOT / "runtime/model/deployment.json"
    value = {}
    present = False
    try:
        import os
        import stat

        from workbench.modules.diagnostics.storage import _open_absolute

        parent = _open_absolute(path.parent)
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(descriptor)
                if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 65536:
                    present = True
                    raw = os.read(descriptor, 65537)
                    candidate = json.loads(raw)
                    if isinstance(candidate, dict):
                        value = candidate
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
    except (OSError, ValueError, AttributeError, RecursionError):
        pass
    declared = value.get("asset_bytes_previously_verified")
    return dto.ModelReferences(
        connection_count=count,
        managed_manifest_present=present,
        previously_verified_asset_bytes=declared
        if type(declared) is int and 0 <= declared < 2**63
        else None,
        engine="sglang" if value.get("engine") == "sglang" else "unknown",
        engine_version=_version(value.get("engine_version")),
    )


def snapshot(db, user_id) -> dto.DiagnosticsSnapshot:
    """Owner-check in an owned read-only transaction; caller's Session is untouched.

    Accept a SQLAlchemy Session or Engine. To avoid holding the HTTP authentication
    transaction during stat/ZIP, close that Session first and pass its Engine.
    """
    user_id = _owner_id(user_id)
    sampled_at = datetime.now(UTC)
    database, queues, errors, workspaces, limited, models = _database(db, user_id, sampled_at)
    return dto.DiagnosticsSnapshot(
        generated_at=sampled_at,
        runtime=_runtime(),
        database=database,
        queues=queues,
        recent_errors=errors,
        storage=measure(
            get_settings().data_dir.absolute(), user_id, workspaces, database.byte_size, limited
        ),
        models=_model_references(models),
    )


def export(db, user_id) -> bytes:
    """Return a default-redacted ZIP of exactly two self-generated JSON members."""
    value = snapshot(db, user_id)
    raw = json.dumps(
        value.model_dump(mode="json"), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    if len(raw) > MAX_JSON:
        raise AppError("diagnostics_budget", "结构化诊断超过导出预算。", 422, "budget")
    manifest = json.dumps(
        {
            "schema_version": 1,
            "redaction": "structural_allowlist_v1",
            "generated_at": value.generated_at.isoformat(),
            "members": [{"name": "snapshot.json", "byte_size": len(raw)}],
            "reason_dictionary": dict(REASONS),
            "excluded": [
                "raw_logs",
                "prompts_messages",
                "user_files",
                "screenshots",
                "markdown_bodies",
                "paths_endpoints",
                "argv_environment",
                "usernames_addresses",
                "credentials",
                "host_identifiers",
                "model_weights",
                "external_workspace_files",
            ],
            "automatic_upload": False,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, content in [("manifest.json", manifest), ("snapshot.json", raw)]:
            item = zipfile.ZipInfo(name)
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100600 << 16
            archive.writestr(item, content)
    if output.tell() > MAX_ZIP:
        raise AppError("diagnostics_budget", "结构化诊断ZIP超过导出预算。", 422, "budget")
    return output.getvalue()
