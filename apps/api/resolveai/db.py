from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str | None = None):
    database_url = url or settings.database_url
    kwargs = {"connect_args": {"check_same_thread": False}} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, pool_pre_ping=True, **kwargs)
    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
    return engine


engine = make_engine()
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def init_db(engine_override=None):
    from . import models  # noqa: F401
    Base.metadata.create_all(engine_override or engine)


def get_db():
    with SessionLocal() as session:
        yield session
