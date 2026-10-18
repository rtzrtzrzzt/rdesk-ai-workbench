"""Quiescent local backup and isolated restore; never updates the source database."""

import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import time
from pathlib import Path
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from sqlalchemy import text

from workbench import __version__
from workbench.config import PROJECT_ROOT, get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import Base, now
from workbench.modules.maintenance import dto
from workbench.modules.maintenance.boundary import QuiescenceBoundary
from workbench.modules.maintenance.postgres import NativePostgres

CREDENTIALS_POLICY = "系统管理凭据与会话排除；用户正文保留，可能包含用户主动粘贴的敏感内容。"
CHUNK = 1024**2
MAX_FILES = 100_000


def _write(path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2, default=str).encode()
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _owner(db, user_id):
    with db.no_autoflush:
        owned = db.scalar(
            text("SELECT id FROM users WHERE id=:id AND owner_slot=1"), {"id": user_id}
        )
    if not owned:
        raise AppError(
            "maintenance_owner_required", "备份恢复仅允许当前所有者。", 403, "permission"
        )


def _root(kind):
    root = get_settings().data_dir.resolve() / "maintenance" / kind
    probe = get_settings().data_dir.absolute()
    if probe.is_symlink():
        raise AppError("maintenance_path_denied", "维护数据根不能是符号链接。", 403)
    for part in ("maintenance", kind):
        probe = probe / part
        if probe.is_symlink():
            raise AppError("maintenance_path_denied", "维护路径不能经过符号链接。", 403)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def _safe(root, relative):
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts or not value.parts:
        raise AppError("backup_reference_invalid", "备份含有越界文件引用。", 422, "policy")
    target = root
    for part in value.parts:
        target = target / part
        if target.is_symlink():
            raise AppError("backup_symlink_denied", "备份和恢复不跟随符号链接。", 422, "policy")
    if not target.resolve().is_relative_to(root.resolve()):
        raise AppError("backup_reference_invalid", "备份文件超出受管目录。", 422)
    return target


def _copy(source, target, check, *, expected_size=None, expected_hash=None):
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    digest = hashlib.sha256()
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    count = 0
    with os.fdopen(fd, "rb") as input_file:
        before = os.fstat(input_file.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise AppError("backup_file_type", "备份只接受普通非硬链接文件。", 422)
        if expected_size is not None and before.st_size != expected_size:
            raise AppError("backup_reference_changed", "引用文件大小已改变。", 409, "conflict")
        with target.open("xb") as output:
            os.chmod(target, 0o600)
            while True:
                check()
                chunk = input_file.read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                count += len(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        after = os.fstat(input_file.fileno())
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) or (expected_hash is not None and digest.hexdigest() != expected_hash):
        raise AppError(
            "backup_reference_changed", "文件在备份期间改变或版本不匹配。", 409, "conflict"
        )
    return count, digest.hexdigest()


def _hash(path, check):
    digest = hashlib.sha256()
    count = 0
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        while True:
            check()
            chunk = stream.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            count += len(chunk)
    return count, digest.hexdigest()


def _check_factory(abort_check, timeout, boundary=None):
    deadline = time.monotonic() + timeout

    def check():
        if abort_check() or time.monotonic() >= deadline:
            raise AppError(
                "maintenance_aborted",
                "本轮维护已停止或超时；未替换已有完整备份。",
                409,
                "cancelled",
            )
        if boundary is not None:
            boundary.assert_held()

    return check


def _facts(pg, user_id, *, require_quiescent=True):
    from workbench.composition import tables  # noqa: F401

    with pg.connect(pg.source_database) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        connection.row_factory = dict_row

        def rows(query):
            return connection.execute(query).fetchall()

        tables = [
            r["tablename"]
            for r in rows(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )
        ]
        allowed = set(Base.metadata.tables) | {
            "alembic_version",
            "checkpoint_migrations",
            "checkpoints",
            "checkpoint_blobs",
            "checkpoint_writes",
        }
        if set(tables) - allowed:
            raise AppError(
                "backup_schema_unsupported",
                "数据库包含尚未审核的表，不能保证凭据排除范围。",
                422,
                "capability",
            )
        sensitive = {
            (r["table_name"], r["column_name"])
            for r in rows(
                "SELECT table_name,column_name FROM information_schema.columns WHERE "
                "table_schema='public' AND column_name ~* "
                "'(secret|password|token_digest|encrypted|api_key|session)'"
            )
        }
        if sensitive - {
            ("auth_sessions", "token_digest"),
            ("model_connections", "encrypted_api_key"),
            ("model_revisions", "encrypted_api_key"),
            ("mcp_connections", "encrypted_secrets"),
            ("desktop_writer_leases", "session_id"),  # A reviewed FK, not a bearer credential.
            ("automation_actions", "session_id"),
            ("account_command_receipts", "session_id"),  # Revoked auth-session audit FK only.
        }:
            raise AppError(
                "backup_secret_schema_unsupported",
                "检测到未审核的系统凭据字段。",
                422,
                "capability",
            )
        busy = rows("""SELECT
          (SELECT count(*) FROM attempts WHERE status IN ('running','created')) +
          (SELECT count(*) FROM jobs WHERE status='running' OR lease_until>now()) +
          (SELECT count(*) FROM command_executions WHERE NOT resources_closed OR
            (receipt->>'status' IS NOT NULL AND log_artifacts='{}'::jsonb)) +
          (SELECT count(*) FROM operations WHERE status='running'
            OR (effect_status='possibly_submitted' AND status<>'outcome_unknown')) +
          (SELECT count(*) FROM tool_exchanges WHERE
             (dispatched AND (outcome IS NULL OR outcome='null'::jsonb)) OR
             (outcome#>>'{reconciliation,transport_cleanup,state}' IS NOT NULL
               AND outcome#>>'{reconciliation,transport_cleanup,state}'<>'closed') OR
             (outcome#>>'{continuation,transport_lease_id}' IS NOT NULL
               AND COALESCE(outcome#>>'{reconciliation,transport_cleanup,state}',
                 'unknown')<>'closed')) +
          (SELECT count(*) FROM automation_sessions WHERE
             status NOT IN ('stopped','taken_over','expired') OR
             CASE kind
               WHEN 'browser' THEN COALESCE(native_view->>'owned_resources_closed','false')<>'true'
               WHEN 'desktop' THEN
                 COALESCE(native_view->>'owned_inputs_released','false')<>'true' OR
                 COALESCE(native_view->>'pending_native_call','true')<>'false'
               ELSE true END) +
          (SELECT count(*) FROM automation_actions WHERE status='in_progress') +
          (SELECT count(*) FROM desktop_writer_leases WHERE session_id IS NOT NULL
             OR (NOT revoked AND expires_at>now())) +
          (SELECT count(*) FROM memory_write_journal
            WHERE status IN ('prepared','published','outcome_unknown')) +
          (SELECT count(*) FROM artifacts WHERE status<>'available') +
          (SELECT count(*) FROM uploads WHERE status='publishing') AS busy""")[0]["busy"]
        if busy and require_quiescent:
            raise AppError(
                "backup_not_quiescent",
                "仍有活动执行、无回执交换、未确认关闭的资源或文件发布，不能一致备份。",
                409,
                "busy",
            )
        extensions = rows(
            "SELECT extname AS name,extversion AS version FROM pg_extension ORDER BY extname"
        )
        if any(item["name"] != "plpgsql" for item in extensions):
            raise AppError(
                "backup_extension_unsupported",
                "当前隔离恢复仅验证了内置 plpgsql 扩展。",
                422,
                "capability",
            )
        return dict(
            tables=tables,
            extensions=extensions,
            schema=[
                r["version_num"]
                for r in rows("SELECT version_num FROM alembic_version ORDER BY version_num")
            ],
            checkpoint=[r["v"] for r in rows("SELECT v FROM checkpoint_migrations ORDER BY v")],
            graph=[
                r["graph_version"]
                for r in rows("SELECT DISTINCT graph_version FROM runs ORDER BY graph_version")
            ],
            major=int(
                connection.execute("SHOW server_version_num").fetchone()["server_version_num"]
            )
            // 10000,
            users=rows("SELECT id FROM users"),
            workspaces=rows("SELECT id,name,root_path FROM workspaces ORDER BY id"),
            artifacts=rows(
                "SELECT id,workspace_id,storage_path,byte_size,content_hash FROM "
                "artifacts ORDER BY id"
            ),
            documents=rows(
                "SELECT id,scope_id,storage_path,status,content_hash FROM "
                "memory_documents ORDER BY id"
            ),
            proposals=rows("SELECT storage_path,status,content_hash FROM memory_proposals"),
            memory_revisions=rows(
                "SELECT storage_path,content_hash FROM memory_revisions WHERE status='available'"
            ),
            journal_documents=rows("SELECT document_id,status FROM memory_write_journal"),
            scopes=rows(
                "SELECT id,user_id,workspace_id,scope,generation,index_hash FROM "
                "memory_scopes ORDER BY id"
            ),
            tombstones=rows(
                "SELECT document_id,generation,forgotten_version,journal_id FROM "
                "memory_tombstones ORDER BY id"
            ),
            uploads=rows("SELECT id,workspace_id,chunks FROM uploads WHERE chunks<>'{}'::jsonb"),
            models=rows(
                "SELECT id,version,provider_type,model_identifier FROM "
                "model_connections ORDER BY id"
            ),
            revisions=rows(
                "SELECT id,connection_id,connection_version,configuration FROM "
                "model_revisions ORDER BY id"
            ),
            reconciliation_required=rows(
                "SELECT 'operations' AS source_table,id AS source_id,id AS operation_id,"
                "run_id,status,effect_status FROM operations WHERE status='outcome_unknown' "
                "OR effect_status='needs_reconciliation' UNION ALL "
                "SELECT 'tool_exchanges',e.id,e.operation_id,o.run_id,e.status,NULL "
                "FROM tool_exchanges e JOIN operations o ON o.id=e.operation_id "
                "WHERE e.status IN ('outcome_unknown','awaiting_input') "
                "UNION ALL SELECT 'automation_actions',a.id,NULL,s.run_id,a.status,NULL "
                "FROM automation_actions a JOIN automation_sessions s ON s.id=a.session_id "
                "WHERE a.status='outcome_unknown' "
                "ORDER BY source_table,source_id"
            ),
        )


MCP_CONTROL_KEYS = frozenset(
    {
        "request_state",
        "requestState",
        "continuation",
        "input_requests",
        "inputRequests",
        "input_responses",
        "inputResponses",
        "transport_lease_id",
        "transport_cleanup",
        "session_id",
        "sessionId",
        "callback_url",
        "callbackUrl",
        "authorization",
        "access_token",
        "refresh_token",
        "bearer_token",
        "encrypted_secrets",
        "_meta",
    }
)


def _without_mcp_control(value):
    """Remove host/protocol recovery capabilities, retain actual business content.

    Arbitrary user text and tool business results are not an automatic secret
    detector. Their contents remain covered by the explicit credentials policy.
    """
    if isinstance(value, dict):
        return {
            key: item
            if key in {"content", "structured_content", "structuredContent", "arguments"}
            else _without_mcp_control(item)
            for key, item in value.items()
            if key not in MCP_CONTROL_KEYS
        }
    if isinstance(value, list):
        return [_without_mcp_control(item) for item in value]
    return value


def _sanitize_mcp(connection, *, restoring):
    for identifier, configuration in connection.execute(
        "SELECT id,configuration FROM mcp_connections"
    ).fetchall():
        value = _without_mcp_control(configuration)
        # argv may contain inline credentials; do not pretend a flag-name
        # heuristic can establish that an arbitrary executable's args are safe.
        value.update(enabled=False, args=[], env_secret_refs={}, bearer_secret_ref=None)
        connection.execute(
            "UPDATE mcp_connections SET encrypted_secrets='{}'::jsonb,"
            "execution_authorized=false,configuration=%s WHERE id=%s",
            (Jsonb(value), identifier),
        )
    for identifier, snapshot in connection.execute(
        "SELECT id,snapshot FROM mcp_snapshots"
    ).fetchall():
        value = _without_mcp_control(snapshot)
        value.update(
            server_info=None, server_capabilities={}, negotiation={}, expires_at=now().isoformat()
        )
        connection.execute(
            "UPDATE mcp_snapshots SET snapshot=%s WHERE id=%s", (Jsonb(value), identifier)
        )
    for identifier, status, request, outcome in connection.execute(
        "SELECT id,status,request,outcome FROM tool_exchanges"
    ).fetchall():
        original = outcome or {}
        continuation = original.get("continuation") or {}
        marker = original.get("reconciliation", {}).get("transport_cleanup")
        value = _without_mcp_control(outcome) if outcome is not None else None
        if value is not None:
            reconciliation = value.setdefault("reconciliation", {})
            old = reconciliation.get("backup", {})
            reconciliation["backup"] = {
                **old,
                "source_exchange_status": old.get("source_exchange_status", status),
                "continuation_removed": bool(continuation)
                or old.get("continuation_removed", False),
                "remote_replay_allowed": False,
            }
            if continuation:
                reconciliation["backup"].update(
                    source_last_exchange_id=continuation.get("last_exchange_id"),
                    source_round=continuation.get("round"),
                )
            if marker:
                reconciliation["transport_cleanup"] = {
                    "state": marker.get("state", "unknown"),
                    "source_state": marker.get("source_state", marker.get("state", "unknown")),
                    "restored_resource": False,
                }
            if restoring and status == "awaiting_input":
                status = value["status"] = "outcome_unknown"
                value.update(
                    code="RESTORED_CONTINUATION_REMOVED",
                    message="Original continuation is excluded; reconcile without replay",
                )
        connection.execute(
            "UPDATE tool_exchanges SET request=%s,outcome=%s,status=%s WHERE id=%s",
            (
                Jsonb(_without_mcp_control(request)),
                Jsonb(value) if value is not None else None,
                status,
                identifier,
            ),
        )
    if restoring:
        connection.execute(
            "UPDATE operations SET status='outcome_unknown',effect_status='needs_reconciliation' "
            "WHERE tool_name='mcp_call' AND status='waiting_input'"
        )
        connection.execute(
            "UPDATE questions SET status='invalidated' WHERE interaction_key LIKE 'mcp:%%' "
            "AND status='pending'"
        )


AUTOMATION_CONTROL_KEYS = MCP_CONTROL_KEYS | {
    "native_id",
    "runtime_nonce",
    "nonce",
    "instance_id",
    "lease_id",
    "grant_id",
    "target_window_id",
    "target_window_pid",
    "window_id",
    "window_pid",
    "target_ref",
    "ref",
    "last_observation",
    "cookies",
    "headers",
    "token",
    "secret",
}


def _automation_history(value):
    """Keep observed text/geometry/artifact IDs, discard old-host execution handles."""
    if isinstance(value, dict):
        return {
            key: _automation_history(item)
            for key, item in value.items()
            if key not in AUTOMATION_CONTROL_KEYS
            and key != "path"
            and not key.endswith(("_path", "_dir", "_directory"))
        }
    if isinstance(value, list):
        return [_automation_history(item) for item in value]
    return value


def _sanitize_automation(connection):
    for identifier, configuration in connection.execute(
        "SELECT id,configuration FROM automation_policies"
    ).fetchall():
        value = dict(configuration)
        value.update(
            browser_enabled=False,
            desktop_capture=False,
            desktop_control=False,
            remote_model_revisions=[],
            target_window_id=None,
            target_window_pid=None,
            manual_takeover_ack=False,
        )
        connection.execute(
            "UPDATE automation_policies SET configuration=%s,grant_expires_at=NULL WHERE id=%s",
            (Jsonb(value), identifier),
        )
    for identifier, command, view, observation, error in connection.execute(
        "SELECT id,command,native_view,observation,error FROM automation_sessions"
    ).fetchall():
        historical_view = _automation_history(view)
        historical_view["backup_native_identity_removed"] = True
        connection.execute(
            "UPDATE automation_sessions SET native_id=NULL,runtime_nonce='',command=%s,"
            "native_view=%s,observation=%s,error=%s,expires_at=now()-interval '1 second' "
            "WHERE id=%s",
            (
                Jsonb(_automation_history(command)),
                Jsonb(historical_view),
                Jsonb(_automation_history(observation)) if observation is not None else None,
                Jsonb(_automation_history(error)) if error is not None else None,
                identifier,
            ),
        )
    for identifier, result in connection.execute(
        "SELECT id,result FROM automation_actions WHERE result IS NOT NULL"
    ).fetchall():
        connection.execute(
            "UPDATE automation_actions SET result=%s WHERE id=%s",
            (Jsonb(_automation_history(result)), identifier),
        )
    connection.execute(
        "UPDATE desktop_writer_leases SET session_id=NULL,lease_id='',revoked=true,"
        "epoch=epoch+1,expires_at=now()-interval '1 second'"
    )


def _sanitize_commands(connection):
    # Final log artifacts are the portable history. Old-host process receipts
    # must never become signal capabilities after moving or restoring data.
    receipt_fields = {
        "status",
        "exit_code",
        "stdout_bytes",
        "stderr_bytes",
        "stdout_observed_bytes",
        "stderr_observed_bytes",
        "truncated",
        "started_at",
        "finished_at",
        "owned_resources_closed",
    }
    for identifier, receipt, error in connection.execute(
        "SELECT id,receipt,error FROM command_executions"
    ).fetchall():
        historical = (
            {key: value for key, value in receipt.items() if key in receipt_fields}
            if isinstance(receipt, dict)
            else None
        )
        connection.execute(
            "UPDATE command_executions SET native_identity=NULL,nonce='',stop_requested=true,"
            "receipt=%s,error=%s WHERE id=%s",
            (
                Jsonb(historical) if historical else None,
                Jsonb(_automation_history(error)) if error else None,
                identifier,
            ),
        )


def _sanitize(pg, database, *, restored_root=None):
    with pg.connect(database) as connection:
        connection.execute("DELETE FROM auth_sessions")
        connection.execute("DELETE FROM login_throttles")
        connection.execute("UPDATE account_command_receipts SET request_digest=NULL")
        connection.execute(
            "UPDATE model_connections SET "
            "encrypted_api_key=NULL,allow_remote=false,service_status='unchecked',checked_at=NULL,capabilities='{}'::jsonb"
        )
        connection.execute(
            "UPDATE model_revisions SET "
            "encrypted_api_key=NULL,configuration=(configuration "
            "- 'encrypted_api_key' - 'api_key') || "
            '\'{"credential_configured":false,"allow_remote":false,"service_status":"unchecked","capabilities":{}}\'::jsonb'
        )
        connection.execute(
            "UPDATE search_connections SET "
            "enabled=false,status='unchecked',checked_at=NULL,last_error=NULL"
        )
        connection.execute("UPDATE client_requests SET response=NULL,expired=true")
        connection.execute("DELETE FROM worker_heartbeats")
        _sanitize_mcp(connection, restoring=restored_root is not None)
        _sanitize_automation(connection)
        _sanitize_commands(connection)
        if restored_root is not None:
            connection.execute("UPDATE threads SET queue_paused=true")
            connection.execute("UPDATE queued_inputs SET status='paused' WHERE status='queued'")
            held = connection.execute(
                "UPDATE runs SET "
                "status='held',error=jsonb_build_object('code','restore_review_required','message','恢复的任务需要评估，未自动继续。')"
                " WHERE status NOT IN ('succeeded','failed','cancelled') RETURNING id"
            ).fetchall()
            connection.execute(
                "UPDATE jobs SET "
                "lease_owner=NULL,lease_until=NULL,lease_epoch=lease_epoch+1,"
                "completion_status=CASE WHEN run_id IN (SELECT id FROM runs WHERE status='held') "
                "THEN NULL ELSE completion_status END,status=CASE"
                " WHEN run_id IN (SELECT id FROM runs WHERE status='held') THEN "
                "'held' ELSE status END"
            )
            connection.execute(
                "UPDATE approvals SET status='pending',approved=NULL WHERE run_id IN "
                "(SELECT id FROM runs WHERE status='held')"
            )
            for (identifier,) in connection.execute("SELECT id FROM workspaces").fetchall():
                connection.execute(
                    "UPDATE workspaces SET root_path=%s WHERE id=%s",
                    (str(restored_root / "workspace-mapping-required" / identifier), identifier),
                )
            owners = connection.execute("SELECT id FROM users WHERE owner_slot=1").fetchall()
            if len(owners) != 1:
                raise AppError("restore_owner_invalid", "备份没有唯一原所有者，拒绝恢复登录。", 422)
        else:
            held = []
    with pg.connect(database) as connection:
        secrets = connection.execute(
            "SELECT (SELECT count(*) FROM auth_sessions)+(SELECT count(*) FROM "
            "model_connections WHERE encrypted_api_key IS NOT NULL)+(SELECT "
            "count(*) FROM model_revisions WHERE encrypted_api_key IS NOT NULL)"
            "+(SELECT count(*) FROM mcp_connections WHERE encrypted_secrets<>'{}'::jsonb "
            "OR execution_authorized OR COALESCE((configuration->>'enabled')::boolean,false))"
        ).fetchone()[0]
        if secrets:
            raise AppError("backup_secret_verification", "系统凭据净化校验未通过。", 500)
        executable = connection.execute("""SELECT
          (SELECT count(*) FROM automation_policies WHERE grant_expires_at IS NOT NULL
            OR configuration->>'browser_enabled'='true'
            OR configuration->>'desktop_capture'='true'
            OR configuration->>'desktop_control'='true'
            OR configuration->'remote_model_revisions'<>'[]'::jsonb
            OR configuration->>'target_window_id' IS NOT NULL
            OR configuration->>'target_window_pid' IS NOT NULL) +
          (SELECT count(*) FROM automation_sessions WHERE native_id IS NOT NULL
            OR runtime_nonce<>'' OR expires_at>=now()) +
          (SELECT count(*) FROM desktop_writer_leases WHERE session_id IS NOT NULL
            OR lease_id<>'' OR NOT revoked OR expires_at>=now()) +
          (SELECT count(*) FROM command_executions WHERE native_identity IS NOT NULL
            OR nonce<>'' OR NOT resources_closed) +
          (SELECT count(*) FROM tool_exchanges WHERE
            request->'request_payload' ?| ARRAY['request_state','requestState',
              'continuation','input_responses','inputResponses'] OR
            outcome ? 'continuation' OR outcome->'raw_response' ?|
              ARRAY['requestState','inputRequests','_meta'] OR
            outcome#>'{reconciliation,transport_cleanup}' ?|
              ARRAY['lease_id','owner_instance','owner_pid']) AS remaining""").fetchone()[0]
        if executable:
            raise AppError(
                "backup_capability_verification", "恢复可执行授权或继续材料净化校验未通过。", 500
            )
    return len(held)


def _references(facts, root):
    entries = {}

    def add(path, category, size=None, digest=None):
        source = _safe(root, path)
        if not source.is_file():
            raise AppError(
                "backup_reference_missing", "数据库引用的必要文件缺失，不能标记完整备份。", 409
            )
        entries[path] = (category, size, digest)

    for item in facts["artifacts"]:
        prefix = f"workspaces/{item['workspace_id']}/artifacts/{item['id']}/"
        if not item["storage_path"].startswith(prefix):
            raise AppError("backup_reference_invalid", "Artifact 引用不符合受管存储范围。", 422)
        add(item["storage_path"], "artifact", item["byte_size"], item["content_hash"])
    for item in facts["documents"]:
        if item["status"] == "active":
            add(item["storage_path"], "memory")
    for item in facts["proposals"]:
        if item["status"] == "pending":
            add(item["storage_path"], "memory", digest=item["content_hash"])
    for item in facts["memory_revisions"]:
        add(item["storage_path"], "memory", digest=item["content_hash"])
    for scope in facts["scopes"]:
        relative = (
            f"users/{scope['user_id']}/memory"
            if scope["scope"] == "user"
            else f"workspaces/{scope['workspace_id']}/memory"
        )
        index = relative + "/MEMORY.md"
        if scope["index_hash"] is not None:
            add(index, "memory")
    roots = []
    for user in facts["users"]:
        rules = f"users/{user['id']}/AGENTS.md"
        if _safe(root, rules).exists():
            add(rules, "rules")
        roots.append(f"users/{user['id']}/memory")
    roots += [f"workspaces/{item['id']}/memory" for item in facts["workspaces"]]
    for relative in roots:
        directory = _safe(root, relative)
        if not directory.exists():
            continue
        for parent, dirs, names in os.walk(directory, followlinks=False):
            for name in dirs:
                if (Path(parent) / name).is_symlink():
                    raise AppError("backup_symlink_denied", "MD 目录含符号链接。", 422)
            for name in names:
                if name.endswith(".md"):
                    path = (Path(parent) / name).relative_to(root).as_posix()
                    if path not in entries:
                        add(path, "memory")
    for upload in facts["uploads"]:
        for offset, chunk in upload["chunks"].items():
            if not offset.isdigit():
                raise AppError("backup_upload_invalid", "上传块索引无效。", 422)
            add(
                f"workspaces/{upload['workspace_id']}/uploads/{upload['id']}/{offset}.chunk",
                "upload",
                chunk["size"],
                chunk["hash"],
            )
    if len(entries) > MAX_FILES:
        raise AppError("backup_file_budget", "引用文件超过十万项预算。", 422, "resource")
    return entries


def _rebuild_memory_cache(pg, database, facts, staging, manifest, check):
    from workbench.modules.memory import files as memory_files

    scopes = {row["id"]: row for row in facts["scopes"]}
    copied = {row.path.removeprefix("data/"): row for row in manifest.files}
    blocked = {
        row["document_id"]
        for row in facts["journal_documents"]
        if row["status"] in {"prepared", "published", "outcome_unknown", "conflict"}
    }
    forgotten = {row["document_id"] for row in facts["tombstones"]}
    with pg.connect(database) as connection:
        for document in manifest.memory_documents:
            check()
            if document.status != "active":
                continue
            if document.id in blocked or document.id in forgotten:
                raise AppError(
                    "backup_memory_conflict", "记忆仍有冲突或遗忘屏障，不能重建为 active。", 409
                )
            scope = scopes[document.scope_id]
            prefix = (
                f"users/{scope['user_id']}/memory/topics/"
                if scope["scope"] == "user"
                else f"workspaces/{scope['workspace_id']}/memory/topics/"
            )
            if document.path != prefix + document.id + ".md":
                raise AppError("backup_memory_identity", "MD 路径与文档作用域身份不一致。", 409)
            raw = _safe(staging, "data/" + document.path).read_bytes()
            if len(raw) > memory_files.MAX_BYTES or b"\0" in raw:
                raise AppError("backup_memory_metadata", "MD 正文不符合受限 UTF-8 格式。", 422)
            metadata, _ = memory_files.decode(raw, document.id)
            if metadata["scope"] != scope["scope"] or metadata["generation"] > scope["generation"]:
                raise AppError(
                    "backup_memory_identity", "MD 元信息改变了 scope 或超前于有效世代。", 409
                )
            actual = copied[document.path].sha256
            document.actual_version = actual
            connection.execute(
                "UPDATE memory_documents SET content_hash=%s,title=%s,source_refs=%s::jsonb "
                "WHERE id=%s AND status='active'",
                (actual, metadata["title"], json.dumps(metadata["source_refs"]), document.id),
            )
        for identifier, scope in scopes.items():
            relative = (
                f"users/{scope['user_id']}/memory/MEMORY.md"
                if scope["scope"] == "user"
                else f"workspaces/{scope['workspace_id']}/memory/MEMORY.md"
            )
            if relative in copied:
                connection.execute(
                    "UPDATE memory_scopes SET index_hash=%s WHERE id=%s",
                    (copied[relative].sha256, identifier),
                )
    manifest.notes.append(
        "仅在 scratch 按已验证 MD 主数据重建索引；source recorded_version 与实际版本分别记录。"
    )


def _model_assets():
    result = []
    directory = PROJECT_ROOT / "models/.verification"
    if directory.is_dir():
        for file in sorted(directory.glob("*/verification.json"))[:100]:
            if file.stat().st_size > 1024**2:
                continue
            item = json.loads(file.read_text())
            if all(
                isinstance(item.get(key), str) for key in ("repo_id", "revision", "model_directory")
            ):
                result.append(
                    dto.ModelAssetReference(
                        repository=item["repo_id"],
                        revision=item["revision"],
                        path_reference=item["model_directory"],
                        verification_reference=file.relative_to(PROJECT_ROOT).as_posix(),
                        previously_verified=item.get("complete") is True,
                        declared_bytes=item.get("total_bytes"),
                        weights_included=False,
                    )
                )
    return result


def _view(manifest):
    return dto.BackupView(**{key: getattr(manifest, key) for key in dto.BackupView.model_fields})


def _load(directory):
    path = _safe(directory, "manifest.json")
    if path.stat().st_size > 32 * 1024**2:
        raise AppError("backup_manifest_budget", "备份清单超过读取预算。", 422)
    try:
        return dto.BackupManifest.model_validate_json(path.read_bytes())
    except ValueError:
        raise AppError("backup_manifest_invalid", "备份清单格式不受支持。", 422) from None


def inspect_backup(db, user_id, backup_id) -> dto.BackupManifest:
    _owner(db, user_id)
    identifier = str(UUID(str(backup_id)))
    directory = _root("backups") / identifier
    if directory.is_symlink() or not directory.is_dir():
        raise AppError("backup_not_found", "完整备份不存在。", 404)
    manifest = _load(directory)
    if manifest.source_user_id != user_id:
        raise AppError("backup_not_found", "备份不存在或不可访问。", 404)
    marker = _safe(directory, "COMPLETE")
    if (
        manifest.status != "complete"
        or not marker.is_file()
        or marker.read_text().strip()
        != hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
    ):
        raise AppError("backup_incomplete", "备份未完整发布。", 409)
    return manifest


def list_backups(db, user_id) -> list[dto.BackupView]:
    _owner(db, user_id)
    result = []
    for directory in _root("backups").iterdir():
        if (
            directory.is_dir()
            and not directory.is_symlink()
            and (directory / "manifest.json").is_file()
        ):
            manifest = _load(directory)
            if manifest.source_user_id == user_id:
                result.append(_view(manifest))
    return sorted(result, key=lambda item: item.created_at, reverse=True)


def create_backup(
    db, user_id, command: dto.BackupCreate, boundary: QuiescenceBoundary, *, abort_check
) -> dto.BackupView:
    _owner(db, user_id)
    if not isinstance(boundary, QuiescenceBoundary):
        raise AppError("backup_boundary_required", "必须持有真实静止边界对象。", 409)
    pg = NativePostgres(db.get_bind())
    root = get_settings().data_dir.resolve()
    if boundary.source_database != pg.source_database or boundary.data_dir.resolve() != root:
        raise AppError("backup_boundary_mismatch", "备份边界不属于当前数据源。", 409)
    check = _check_factory(abort_check, command.timeout_seconds, boundary)
    check()
    identifier = str(command.request_id)
    destination = _root("backups") / identifier
    digest = hashlib.sha256(
        json.dumps(command.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    if destination.exists():
        manifest = inspect_backup(db, user_id, identifier)
        if manifest.request_digest != digest:
            raise AppError("idempotency_conflict", "备份请求标识已用于其他参数。", 409)
        return _view(manifest)
    staging = destination.with_name(".incomplete-" + identifier)
    if staging.exists():
        raise AppError(
            "backup_incomplete_exists", "该请求已有不完整备份，请查看状态并明确创建新请求。", 409
        )
    facts = _facts(pg, user_id)
    check()
    if facts["major"] != 16:
        raise AppError("backup_pg_version", "当前仅验证 PostgreSQL16。", 422, "capability")
    staging.mkdir(mode=0o700)
    manifest = dto.BackupManifest(
        format_version=1,
        backup_id=identifier,
        request_id=identifier,
        request_digest=digest,
        source_user_id=user_id,
        label=command.label,
        status="incomplete",
        created_at=now(),
        completed_at=None,
        boundary_id=boundary.boundary_id,
        boundary_acquired_at=boundary.acquired_at,
        app_version=__version__,
        schema_revisions=facts["schema"],
        checkpoint_migrations=facts["checkpoint"],
        checkpoint_package_version=importlib.metadata.version("langgraph-checkpoint-postgres"),
        graph_versions=facts["graph"],
        database_major_version=facts["major"],
        dump_client_version=pg.client_version,
        extensions=facts["extensions"],
        tables=facts["tables"],
        workspaces=[
            dict(
                id=row["id"],
                name=row["name"],
                external_root_path=row["root_path"],
                external_files_included=False,
                remapping_required=True,
            )
            for row in facts["workspaces"]
        ],
        memory_documents=[
            dict(
                id=row["id"],
                scope_id=row["scope_id"],
                path=row["storage_path"],
                status=row["status"],
                recorded_version=row["content_hash"],
                actual_version=None,
            )
            for row in facts["documents"]
        ],
        memory_scopes=[
            dict(id=row["id"], scope=row["scope"], generation=row["generation"])
            for row in facts["scopes"]
        ],
        forgetting_barriers=facts["tombstones"],
        model_references=[
            dict(
                connection_id=row["id"],
                revision_id=None,
                connection_version=row["version"],
                provider_type=row["provider_type"],
                model_identifier=row["model_identifier"],
                weights_included=False,
            )
            for row in facts["models"]
        ]
        + [
            dict(
                connection_id=row["connection_id"],
                revision_id=row["id"],
                connection_version=row["connection_version"],
                provider_type=row["configuration"].get("provider_type", "unknown"),
                model_identifier=row["configuration"].get("model_identifier", "unknown"),
                weights_included=False,
            )
            for row in facts["revisions"]
        ],
        model_assets=_model_assets(),
        files=[],
        file_count=0,
        total_bytes=0,
        credentials_policy=CREDENTIALS_POLICY,
        excluded=[
            "vault.key/keyring/database passwords/provider credentials/login "
            "password hashes/auth sessions",
            "runtime/logs/.env/cache/staging",
            "external workspace original files",
            "model weights and PLE backing/compiler cache",
        ],
        notes=[
            "需要用户另行保管模型 revision 对应权重；未复制132GB模型。",
            "MD journal历史可能含已遗忘旧正文；tombstone/generation完整保留，禁止自动复活旧候选。",
            "应用gate不冻结外部编辑器；检测到文件变化即失败，不宣称OS快照。",
            "此备份未加密，权限为本机0700/0600；同设备备份不覆盖设备损坏。",
            "未知/待核对业务事实列于reconciliation_required，未被标记成功；"
            "MCP凭据、启动argv和远程继续材料排除，恢复必须重新配置与授权。",
        ],
        reconciliation_required=facts["reconciliation_required"],
    )
    _write(staging / "manifest.json", manifest.model_dump(mode="json"))
    scratch = None
    try:
        entries = _references(facts, root)
        check()
        scratch = pg.create_database("workbench_backup_")
        pg.clone_source(scratch, check)
        check()
        _sanitize(pg, scratch)
        check()
        for relative, (category, size, sha) in sorted(entries.items()):
            check()
            source = _safe(root, relative)
            count, actual = _copy(
                source,
                _safe(staging, "data/" + relative),
                check,
                expected_size=size,
                expected_hash=sha,
            )
            manifest.files.append(
                dto.BackupFile(
                    path="data/" + relative, byte_size=count, sha256=actual, category=category
                )
            )
        # Recheck all source files before publishing the completion marker.
        for item in manifest.files:
            if item.category != "database" and _hash(
                _safe(root, item.path.removeprefix("data/")), check
            ) != (item.byte_size, item.sha256):
                raise AppError(
                    "backup_reference_changed", "完成前发现外部文件改变。", 409, "conflict"
                )
        _rebuild_memory_cache(pg, scratch, facts, staging, manifest, check)
        dump = staging / "database.dump"
        pg.dump(scratch, dump, check)
        pg.validate_archive(dump, check)
        size, dump_hash = _hash(dump, check)
        manifest.files.append(
            dto.BackupFile(
                path="database.dump", byte_size=size, sha256=dump_hash, category="database"
            )
        )
        pg.drop_database(scratch)
        scratch = None
        manifest.status = "complete"
        manifest.completed_at = now()
        manifest.file_count = len(manifest.files)
        manifest.total_bytes = sum(item.byte_size for item in manifest.files)
        _write(staging / "manifest.json", manifest.model_dump(mode="json"))
        check()
        marker = hashlib.sha256((staging / "manifest.json").read_bytes()).hexdigest()
        with (staging / "COMPLETE").open("x") as stream:
            os.chmod(staging / "COMPLETE", 0o600)
            stream.write(marker)
            stream.flush()
            os.fsync(stream.fileno())
        check()
        os.rename(staging, destination)
        return _view(manifest)
    except BaseException as error:
        manifest.status = (
            "aborted" if isinstance(error, AppError) and error.kind == "cancelled" else "failed"
        )
        manifest.completed_at = now()
        manifest.files = []
        manifest.file_count = 0
        manifest.total_bytes = 0
        for path in staging.iterdir():
            if path.name != "manifest.json":
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        _write(staging / "manifest.json", manifest.model_dump(mode="json"))
        if isinstance(error, AppError):
            raise
        raise AppError("backup_failed", "本轮备份失败，未替换任何已有完整备份。", 500) from None
    finally:
        if scratch is not None:
            pg.drop_database(scratch)


def restore_backup(db, user_id, command: dto.RestoreCreate, *, abort_check) -> dto.RestoreView:
    manifest = inspect_backup(db, user_id, command.backup_id)
    pg = NativePostgres(db.get_bind())
    facts = _facts(pg, user_id, require_quiescent=False)
    if (
        manifest.app_version != __version__
        or manifest.schema_revisions != facts["schema"]
        or manifest.checkpoint_migrations != facts["checkpoint"]
        or manifest.checkpoint_package_version
        != importlib.metadata.version("langgraph-checkpoint-postgres")
        or manifest.database_major_version != facts["major"]
    ):
        raise AppError(
            "restore_version_mismatch",
            "应用/schema/checkpoint/PG版本不兼容；不会自动迁移或加载。",
            422,
            "capability",
        )
    check = _check_factory(abort_check, command.timeout_seconds)
    source = _root("backups") / str(command.backup_id)
    identifier = str(command.request_id)
    target = _root("restores") / identifier
    if target.exists():
        result_file = _safe(target, "restore.json")
        if not target.is_symlink() and result_file.is_file():
            previous = dto.RestoreView.model_validate_json(result_file.read_bytes())
            if previous.backup_id != str(command.backup_id) or previous.request_id != identifier:
                raise AppError("idempotency_conflict", "恢复请求标识已用于其他备份。", 409)
            if not previous.database_name.startswith("workbench_restore_"):
                raise AppError("restore_target_invalid", "恢复结果库名不符合隔离范围。", 409)
            return previous
        raise AppError("restore_target_exists", "恢复请求目录已存在且未完成，拒绝覆盖。", 409)
    target.mkdir(mode=0o700)
    data = target / "data"
    data.mkdir(mode=0o700)
    database = None
    try:
        seen = set()
        if sum(item.category == "database" for item in manifest.files) != 1:
            raise AppError("backup_manifest_invalid", "备份必须包含一个数据库归档。", 422)
        for item in manifest.files:
            check()
            if item.path in seen:
                raise AppError("backup_manifest_invalid", "备份清单含重复文件。", 422)
            seen.add(item.path)
            file = _safe(source, item.path)
            if _hash(file, check) != (item.byte_size, item.sha256):
                raise AppError("backup_integrity_failed", "备份文件完整性校验失败。", 422)
            if item.category == "database":
                if item.path != "database.dump":
                    raise AppError("backup_manifest_invalid", "数据库归档路径无效。", 422)
            else:
                if not item.path.startswith(("data/users/", "data/workspaces/")):
                    raise AppError(
                        "backup_manifest_invalid", "恢复文件不在允许的受管数据范围。", 422
                    )
                _copy(
                    file,
                    _safe(data, item.path.removeprefix("data/")),
                    check,
                    expected_size=item.byte_size,
                    expected_hash=item.sha256,
                )
        pg.validate_archive(source / "database.dump", check)
        database = pg.create_database("workbench_restore_")
        pg.restore(database, source / "database.dump", check)
        check()
        held = _sanitize(
            pg,
            database,
            restored_root=data,
        )
        check()
        _verify_restored(pg, database, data, manifest, check)
        # Connection credentials are local 0600 state only, never included in the backup.
        engine = getattr(db.get_bind(), "engine", db.get_bind())
        connection_url = engine.url.set(database=database).render_as_string(hide_password=False)
        connection_file = target / "database.json"
        _write(connection_file, {"database_url": connection_url})
        result = dto.RestoreView(
            restore_id=identifier,
            request_id=identifier,
            backup_id=str(command.backup_id),
            status="isolated",
            created_at=now(),
            database_name=database,
            data_dir=str(data),
            connection_file=str(connection_file),
            held_runs=held,
            sessions_restored=False,
            credentials_restored=False,
            remote_enabled=False,
            search_enabled=False,
            user_interface_verified=False,
            notes=[
                "只创建隔离副本，未切换当前实例、未启动Worker或模型。",
                "原所有者使用本次新口令登录；外部workspace路径、API和搜索需重新授权。",
                "非terminal任务与Job全部held，必须对账与评估后另行恢复。",
                "恢复目录没有旧vault/keyring，不能解开原凭据；系统凭据已从归档排除。",
            ],
        )
        _write(target / "restore.json", result.model_dump(mode="json"))
        check()
        return result
    except BaseException as error:
        if database is not None:
            pg.drop_database(database)
        shutil.rmtree(target)
        if isinstance(error, AppError):
            raise
        raise AppError(
            "restore_failed", "隔离恢复失败，本轮新建库/目录已清理；当前实例未变更。", 500
        ) from None


def _verify_restored(pg, database, data, manifest, check):
    versions = {
        item.path.removeprefix("data/"): item
        for item in manifest.files
        if item.category != "database"
    }
    with pg.connect(database) as connection:
        for path, size, digest in connection.execute(
            "SELECT storage_path,byte_size,content_hash FROM artifacts WHERE status='available'"
        ):
            check()
            item = versions.get(path)
            if item is None or (item.byte_size, item.sha256) != (size, digest):
                raise AppError("restore_reference_missing", "恢复库Artifact引用不闭合。", 422)
        for path, digest in connection.execute(
            "SELECT storage_path,content_hash FROM memory_documents WHERE status='active'"
        ):
            check()
            item = versions.get(path)
            if item is None or item.sha256 != digest:
                raise AppError("restore_reference_missing", "恢复库MD引用不闭合。", 422)
        for path, digest in connection.execute(
            "SELECT storage_path,content_hash FROM memory_revisions WHERE status='available'"
        ):
            check()
            item = versions.get(path)
            if item is None or item.sha256 != digest:
                raise AppError("restore_reference_missing", "恢复库记忆历史引用不闭合。", 422)
        scopes = connection.execute(
            "SELECT id,scope,generation FROM memory_scopes ORDER BY id"
        ).fetchall()
        if [dict(id=row[0], scope=row[1], generation=row[2]) for row in scopes] != [
            item.model_dump() for item in manifest.memory_scopes
        ]:
            raise AppError("restore_memory_barrier", "记忆世代核对失败。", 422)
        barriers = connection.execute(
            "SELECT document_id,generation,forgotten_version,journal_id FROM "
            "memory_tombstones ORDER BY id"
        ).fetchall()
        if [
            dict(document_id=row[0], generation=row[1], forgotten_version=row[2], journal_id=row[3])
            for row in barriers
        ] != [item.model_dump() for item in manifest.forgetting_barriers]:
            raise AppError("restore_memory_barrier", "遗忘屏障核对失败。", 422)
        unsafe = connection.execute(
            "SELECT (SELECT count(*) FROM runs WHERE status NOT IN "
            "('succeeded','failed','cancelled','held'))+(SELECT count(*) FROM "
            "jobs WHERE lease_owner IS NOT NULL OR lease_until IS NOT "
            "NULL)+(SELECT count(*) FROM search_connections WHERE "
            "enabled)+(SELECT count(*) FROM model_connections WHERE allow_remote)"
        ).fetchone()[0]
        if unsafe:
            raise AppError(
                "restore_safety_failed", "恢复安全停点检查未通过，拒绝发布隔离副本。", 500
            )
