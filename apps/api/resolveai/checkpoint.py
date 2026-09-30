"""Parent graph checkpoint backend; enabled when PostgreSQL is the main store."""

from __future__ import annotations

from contextlib import contextmanager

from .config import settings


@contextmanager
def parent_checkpointer():
    if not settings.database_url.startswith("postgresql"):
        yield None
        return
    from langgraph.checkpoint.postgres import PostgresSaver
    uri = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with PostgresSaver.from_conn_string(uri) as saver:
        saver.setup()
        yield saver
