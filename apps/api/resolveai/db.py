from __future__ import annotations

import math
import threading

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def session_read_lock(session):
    """Serialize a shared Session's local reads before computing SQL deadlines."""
    return session.info.setdefault("resolveai_read_lock", threading.RLock())


def make_engine(url: str | None = None):
    database_url = url or settings.database_url
    kwargs = {"connect_args": {"check_same_thread": False}} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, pool_pre_ping=True, **kwargs)
    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
    elif database_url.startswith("postgresql"):
        @event.listens_for(engine, "before_cursor_execute")
        def bound_request_statement(_connection, cursor, _statement, _parameters, _context, _executemany):
            from .request_budget import current_budget, remaining_io_seconds
            if current_budget() is not None:
                # SET LOCAL ends with this transaction; pooled connections do
                # not retain a previous customer's statement deadline.
                timeout_ms = max(1, math.ceil(remaining_io_seconds(86400) * 1000))
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", (str(timeout_ms),))
    return engine


engine = make_engine()
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db(engine_override=None):
    from . import models  # noqa: F401
    Base.metadata.create_all(engine_override or engine)


def get_db():
    with SessionLocal() as session:
        yield session
