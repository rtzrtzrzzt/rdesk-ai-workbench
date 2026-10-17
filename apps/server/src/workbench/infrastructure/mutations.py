"""A database-scoped read/write gate for cross-storage maintenance windows."""

import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.infrastructure.database import engine, now
from workbench.modules.maintenance.boundary import GATE_KEY, QuiescenceBoundary


def abort_marker() -> Path:
    directory = get_settings().ensure_data_dir() / "maintenance"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory / "abort-active-maintenance"


def request_abort() -> None:
    abort_marker().write_text("stop requested", encoding="utf-8")


@contextmanager
def write_gate(*, interrupt_maintenance=False):
    with engine().connect() as connection:
        until = time.monotonic() + (10 if interrupt_maintenance else 0)
        while not connection.scalar(
            text("SELECT pg_try_advisory_lock_shared(:key)"), {"key": GATE_KEY}
        ):
            connection.rollback()
            if interrupt_maintenance:
                request_abort()
            if time.monotonic() >= until:
                raise AppError(
                    "maintenance_active",
                    "本地维护正在保持数据静止，请稍后重试。停止请求已优先通知维护中断。"
                    if interrupt_maintenance
                    else "本地维护正在保持数据静止，暂不接受新写入。",
                    409,
                    "resource",
                )
            time.sleep(0.05)
        connection.commit()
        try:
            yield
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock_shared(:key)"), {"key": GATE_KEY})
            connection.commit()


@contextmanager
def quiescence():
    with engine().connect() as connection:
        if not connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": GATE_KEY}):
            raise AppError(
                "maintenance_busy",
                "当前还有写入或活动任务，请等待安全停点后再创建备份。",
                409,
                "resource",
            )
        pid, database = connection.execute(
            text("SELECT pg_backend_pid(), current_database()")
        ).one()
        connection.commit()
        marker = abort_marker()
        marker.unlink(missing_ok=True)

        def check():
            if marker.exists():
                raise AppError(
                    "maintenance_aborted", "本轮维护已停止，未发布为完整备份。", 409, "cancelled"
                )

        boundary = QuiescenceBoundary(
            str(uuid4()), now(), database, get_settings().data_dir.resolve(), pid, connection, check
        )
        try:
            boundary.assert_held()
            yield boundary
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": GATE_KEY})
            connection.commit()
            marker.unlink(missing_ok=True)
