"""Injectable real-PG protocol-test store, deliberately separate from production ORM.

The caller supplies authenticated connections. Creating the isolated test schema
is explicit; importing this module never connects or mutates a database.
"""

import json
import re
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict

from psycopg import AsyncConnection, sql
from psycopg.types.json import Jsonb

from workbench.modules.mcp.domain import ExchangeRequest, Reservation
from workbench.modules.mcp.dto import MCPOutcome


class AcceptancePostgresStore:
    def __init__(
        self,
        connect: Callable[[], AbstractAsyncContextManager[AsyncConnection]],
        test_schema: str,
    ):
        if not re.fullmatch(r"mcp_acceptance_[a-f0-9]{8,32}", test_schema):
            raise ValueError("Use a fresh, explicitly named mcp_acceptance_<hex> schema")
        self.connect = connect
        self.test_schema = test_schema
        self.table = sql.Identifier(test_schema, "tool_exchanges")

    async def install(self) -> None:
        async with self.connect() as connection:
            await connection.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.test_schema))
            )
            await connection.execute(
                sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    id text PRIMARY KEY, operation_id text NOT NULL,
                    attempt_id text NOT NULL, sequence integer NOT NULL,
                    request jsonb NOT NULL, may_have_been_sent boolean NOT NULL DEFAULT false,
                    outcome jsonb, UNIQUE(operation_id, attempt_id, sequence)
                )
            """).format(self.table)
            )
            await connection.commit()

    async def reserve(self, request: ExchangeRequest) -> Reservation:
        exchange_id = uuid.uuid4().hex
        payload = json.loads(json.dumps(asdict(request), default=lambda x: x.isoformat()))
        async with self.connect() as connection:
            cursor = await connection.execute(
                sql.SQL("""
                INSERT INTO {} (id, operation_id, attempt_id, sequence, request)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id
            """).format(self.table),
                (
                    exchange_id,
                    request.operation_id,
                    request.attempt_id,
                    request.sequence,
                    Jsonb(payload),
                ),
            )
            inserted = await cursor.fetchone()
            if inserted:
                result = Reservation(exchange_id, True)
            else:
                cursor = await connection.execute(
                    sql.SQL("""
                    SELECT id, outcome FROM {}
                    WHERE operation_id=%s AND attempt_id=%s AND sequence=%s
                """).format(self.table),
                    (request.operation_id, request.attempt_id, request.sequence),
                )
                row = await cursor.fetchone()
                result = Reservation(
                    row[0], False, MCPOutcome.model_validate(row[1]) if row[1] else None
                )
            await connection.commit()
            return result

    async def mark_dispatched(self, exchange_id: str) -> None:
        async with self.connect() as connection:
            cursor = await connection.execute(
                sql.SQL("""
                UPDATE {} SET may_have_been_sent=true
                WHERE id=%s AND NOT may_have_been_sent AND outcome IS NULL
            """).format(self.table),
                (exchange_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("Exchange does not have unique unsent ownership")
            await connection.commit()

    async def finish(self, exchange_id: str, outcome: MCPOutcome) -> None:
        async with self.connect() as connection:
            cursor = await connection.execute(
                sql.SQL("""
                UPDATE {} SET outcome=%s WHERE id=%s
            """).format(self.table),
                (Jsonb(outcome.model_dump(mode="json")), exchange_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Exchange reservation is missing")
            await connection.commit()
