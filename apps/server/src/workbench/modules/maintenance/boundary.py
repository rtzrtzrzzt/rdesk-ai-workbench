"""Internal proof of an application-wide quiescence gate, never accepted from HTTP JSON."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from workbench.contracts.errors import AppError

GATE_KEY = 82139705


@dataclass(frozen=True)
class QuiescenceBoundary:
    boundary_id: str
    acquired_at: datetime
    source_database: str
    data_dir: Path
    backend_pid: int
    connection: Connection = field(repr=False, compare=False)
    gate_check: Callable[[], None] = field(repr=False, compare=False)

    def assert_held(self) -> None:
        self.gate_check()
        try:
            state = self.connection.execute(
                text("""
                SELECT pg_backend_pid(), current_database(), EXISTS (
                    SELECT 1 FROM pg_locks
                    WHERE locktype='advisory' AND pid=pg_backend_pid()
                      AND classid=0 AND objid=:key AND objsubid=1
                      AND mode='ExclusiveLock' AND granted
                )
            """),
                {"key": GATE_KEY},
            ).one()
        except Exception:
            raise AppError(
                "backup_boundary_lost", "备份静止边界连接失效，本轮备份不能完成。", 409, "cancelled"
            ) from None
        if state[0] != self.backend_pid or state[1] != self.source_database or not state[2]:
            raise AppError(
                "backup_boundary_lost", "备份未持有对应数据库与进程的独占静止锁。", 409, "cancelled"
            )
