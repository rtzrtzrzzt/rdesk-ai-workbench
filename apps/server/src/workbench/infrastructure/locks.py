"""Session locks for operations that span several short database transactions."""

import hashlib
from contextlib import contextmanager

from sqlalchemy import text

from workbench.infrastructure.database import engine


@contextmanager
def advisory_connection(resource: str):
    key = int.from_bytes(hashlib.sha256(resource.encode()).digest()[:8], signed=True)
    with engine().connect() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
        connection.commit()
        try:
            yield connection
        finally:
            connection.rollback()
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            connection.commit()
