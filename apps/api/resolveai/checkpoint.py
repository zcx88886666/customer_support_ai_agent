"""Parent graph checkpoint backend; enabled when PostgreSQL is the main store."""

from __future__ import annotations

from contextlib import contextmanager

from .config import settings
from .request_budget import BudgetExceeded, RequestBudget, budget_scope, current_budget, remaining_io_seconds


def setup_checkpointer():
    """Create LangGraph tables before request transactions can hold locks."""
    if not settings.database_url.startswith("postgresql"):
        return
    from langgraph.checkpoint.postgres import PostgresSaver
    uri = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with PostgresSaver.from_conn_string(uri) as saver:
        saver.setup()


@contextmanager
def parent_checkpointer():
    if not settings.database_url.startswith("postgresql"):
        yield None
        return
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import errors
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    from .database_io import BudgetLock, connect_with_deadline
    import math
    uri = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with budget_scope(current_budget() or RequestBudget()):
        parameters = conninfo_to_dict(uri)
        timeout_ms = max(1, math.ceil(remaining_io_seconds(86400) * 1000))
        options = parameters.get("options", "") + f" -c statement_timeout={timeout_ms}"
        connection = connect_with_deadline(uri, autocommit=True, prepare_threshold=0,
                                           row_factory=dict_row, options=options)
        try:
            saver = PostgresSaver(connection)
            saver.lock = BudgetLock()
            yield saver
        except errors.QueryCanceled:
            raise BudgetExceeded("checkpoint_database_deadline_expired") from None
        finally:
            # Unlike Connection.__exit__, close performs no commit/rollback I/O.
            connection.close()
