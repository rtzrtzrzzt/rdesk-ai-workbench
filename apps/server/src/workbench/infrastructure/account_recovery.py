"""Read-only proof of stopped execution for native owner recovery."""

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from workbench.config import get_settings
from workbench.infrastructure.native_process import _identity
from workbench.infrastructure.vault.private import read_json
from workbench.modules.files.storage import storage_path
from workbench.modules.processes.native import read_state


def assert_resources_stopped(db: Session) -> None:
    # Run status is durable workflow state, not evidence of a live executor.
    # Keep held/queued/waiting runs intact; require the actual execution fences
    # and resource-close receipts instead. Unknown historical effects stay unknown.
    for key in (82139702, 82139704, 82139709):
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
            raise SystemExit("还有 Worker 持有执行所有权，请先停止，未修改账号。")
    busy = db.scalar(
        text("""SELECT
      (SELECT count(*) FROM worker_heartbeats WHERE last_seen_at>now()-interval '30 seconds') +
      (SELECT count(*) FROM jobs WHERE lease_until>now()) +
      (SELECT count(*) FROM command_executions WHERE NOT resources_closed) +
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
         OR (NOT revoked AND expires_at>now())) AS busy""")
    )
    if busy:
        raise SystemExit(
            "还有近期 Worker、活动租约或未确认关闭的命令/外部动作；请先完成安全停止，未修改账号。"
        )
    for output_path, nonce, identity, receipt, status in db.execute(
        text("SELECT output_path,nonce,native_identity,receipt,status FROM command_executions")
    ):
        output = storage_path(output_path)
        if identity is None and receipt is None and status == "cancelled":
            # Cancel-before-dispatch has no supervisor receipt. The durable
            # cancelled fence plus absence of a launch plan proves no dispatch.
            if not any(
                (output / name).exists() or (output / name).is_symlink()
                for name in ("plan.json", "launch.json", "state.json")
            ):
                continue
        state = read_state(output)
        if (
            state["nonce"] != nonce
            or state["supervisor_identity"] != identity
            or not state["owned_resources_closed"]
            or state["supervisor_observation"] != "gone"
        ):
            raise SystemExit("长命令原生关闭回执或进程退出未确认，未修改账号。")
    path = get_settings().data_dir / "runtime" / "automation.json"
    if path.exists() or path.is_symlink():
        # status()/terminate_owned() may signal a live companion. Recovery only
        # observes its strict identity; it never stops processes as a side effect.
        record = read_json(Path(path))
        if _identity(record)[0] != "gone":
            raise SystemExit("原生执行器退出尚未确认，请先执行安全停止，未修改账号。")
