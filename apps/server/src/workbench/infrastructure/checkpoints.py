from contextlib import asynccontextmanager

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row

from workbench.contracts.errors import AppError
from workbench.modules.runs.ownership import current_owner


class FencedPostgresSaver(AsyncPostgresSaver):
    """Use the SDK's public write methods inside a transaction holding the current job.

    Checkpoints use a different connection from business events. Holding the job row
    during each checkpoint write prevents an expired worker publishing a later checkpoint.
    """

    def __init__(self, connection, *, dsn: str, **kwargs):
        super().__init__(connection, **kwargs)
        self.dsn = dsn

    @asynccontextmanager
    async def writer(self, config):
        owner = current_owner.get()
        if owner is None or config["configurable"]["thread_id"] != owner.run_id:
            raise AppError("lease_lost", "检查点缺少有效执行归属。", 409, "ownership")
        async with await psycopg.AsyncConnection.connect(
            self.dsn,
            autocommit=False,
            row_factory=dict_row,
            prepare_threshold=0,
        ) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT lease_owner, lease_epoch, status, lease_until > now() AS valid "
                    "FROM jobs WHERE run_id = %s FOR UPDATE",
                    (owner.run_id,),
                )
                row = await cursor.fetchone()
                if not row or (
                    row["lease_owner"] != owner.worker_id
                    or row["lease_epoch"] != owner.epoch
                    or row["status"] != "running"
                    or not row["valid"]
                ):
                    raise AppError("lease_lost", "检查点写入被过期租约阻止。", 409, "ownership")
            yield AsyncPostgresSaver(connection, serde=self.serde)

    async def aput(self, config, checkpoint, metadata, new_versions):
        async with self.writer(config) as saver:
            return await saver.aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        async with self.writer(config) as saver:
            await saver.aput_writes(config, writes, task_id, task_path)
