"""Opt-in, SELECT-only PostgreSQL probe of queued shared-session deadlines."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from resolveai.db import make_engine, session_read_lock
from resolveai.request_budget import BudgetLimits, RequestBudget, budget_scope


@pytest.mark.skipif(not os.getenv("DATABASE_DEADLINE_PG_URL"), reason="requires an isolated PostgreSQL connection")
def test_queued_shared_session_query_cancels_at_original_deadline_and_recovers():
    engine = make_engine(os.environ["DATABASE_DEADLINE_PG_URL"])
    first_started = threading.Event()
    try:
        with Session(engine) as db:
            db.execute(text("SELECT 1"))  # Warm connection outside the measured scope.
            lock = session_read_lock(db)
            budget = RequestBudget(BudgetLimits(timeout_seconds=0.8))
            started = time.monotonic()

            def query(first=False):
                with budget_scope(budget), lock:
                    if first:
                        first_started.set()
                    try:
                        db.execute(text("SELECT pg_sleep(0.6)"))
                        result = "completed"
                    except DBAPIError as error:
                        result = getattr(error.orig, "sqlstate", None)
                    return result, time.monotonic() - started

            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(query, True)
                assert first_started.wait(timeout=2)
                time.sleep(0.05)
                second = executor.submit(query)
                first_result, first_elapsed = first.result(timeout=3)
                second_result, second_elapsed = second.result(timeout=3)
            assert first_result == "completed"
            assert second_result == "57014"
            assert first_elapsed < second_elapsed < 1.1
            db.rollback()
            assert db.execute(text("SELECT 1")).scalar_one() == 1
            assert db.execute(text("SHOW statement_timeout")).scalar_one() == "0"
            db.rollback()
            print(f"shared-session: first={first_elapsed:.3f}s, cancelled={second_elapsed:.3f}s; rollback/reuse passed")
    finally:
        engine.dispose()
