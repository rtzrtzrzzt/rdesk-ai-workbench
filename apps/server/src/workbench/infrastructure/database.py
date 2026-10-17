from collections.abc import Generator
from datetime import UTC, datetime
from functools import lru_cache
from uuid import uuid4

from sqlalchemy import DateTime, Engine, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from workbench.config import get_settings
from workbench.contracts.errors import AppError


class Base(DeclarativeBase):
    pass


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Entity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    version: Mapped[int] = mapped_column(default=1)


@lru_cache
def engine() -> Engine:
    dsn = get_settings().database_dsn()
    if not dsn:
        raise AppError("database_not_configured", "请先准备本地数据库。", 503, "dependency")
    if dsn.startswith("postgresql://"):
        dsn = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
    if not dsn.startswith("postgresql+psycopg://"):
        raise AppError("invalid_database", "工作台需要 PostgreSQL 数据库。", 503, "dependency")
    return create_engine(
        dsn,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        connect_args={"connect_timeout": 3},
        hide_parameters=True,
    )


def session() -> Generator[Session, None, None]:
    with sessionmaker(bind=engine(), expire_on_commit=False)() as db:
        yield db


def database_ready() -> bool:
    try:
        with engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        # Health responses deliberately never expose connection strings or driver errors.
        return False
