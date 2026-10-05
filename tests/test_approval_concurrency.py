"""Approval checkpoint reads must not mix facts across a worker commit."""

import os
from datetime import datetime, timezone
from threading import Event, Thread
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from resolveai import domain as d, models as m
from resolveai.approval_checkpoint import _status
from resolveai.db import Base, make_engine
from resolveai.seed import seed_demo


@pytest.mark.skipif(not os.environ.get("APPROVAL_CONCURRENCY_PG_ADMIN_URL"), reason="requires isolated PostgreSQL concurrency database")
def test_worker_commit_during_approval_read_cannot_make_valid_approval_stale():
    admin_url = os.environ["APPROVAL_CONCURRENCY_PG_ADMIN_URL"]
    parts = urlsplit(admin_url)
    assert parts.scheme == "postgresql" and parts.path == "/postgres"
    name = "ra_approval_race_" + uuid4().hex[:16]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    uri = urlunsplit(("postgresql+psycopg", parts.netloc, "/" + name, "", ""))
    engine = make_engine(uri)
    factory = sessionmaker(engine, expire_on_commit=False)
    done = Event()
    worker_errors = []
    worker = None
    try:
        Base.metadata.create_all(engine)
        now = datetime.now(timezone.utc)
        with factory.begin() as db:
            seed_demo(db, now)
            request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1,
                                      "synthetic concurrency", True, "approval-race", now)
            d.record_receipt(db, "warehouse-race", request.id, 1, now)
            d.record_inspection(db, "warehouse-race", request.id, True, "intact", now)
            proposal = d.create_proposal(db, request.id, now)
            d.decide_proposal(db, "supervisor-race", proposal.id, True, now)
            proposal_id = proposal.id

        def issue():
            try:
                with factory.begin() as db:
                    d.issue_refund(db, proposal_id, "refund:" + proposal_id, now)
            except Exception as exc:
                worker_errors.append(type(exc).__name__)
            finally:
                done.set()

        armed = True

        def after_query(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal armed, worker
            if armed and statement.lstrip().startswith("SELECT refund_proposals."):
                armed = False
                worker = Thread(target=issue)
                worker.start()
                # Before the fix, issuance finishes between the proposal and
                # order reads. With the order lock, the worker waits until the
                # observer releases its transaction instead.
                done.wait(timeout=0.5)

        event.listen(engine, "after_cursor_execute", after_query)
        with factory() as db:
            status, approval_id = _status(db, proposal_id)
            db.rollback()
        assert done.wait(timeout=10)
        worker.join(timeout=1)
        assert not worker_errors
        assert status in {"approved", "issued"} and approval_id
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 1
    finally:
        if worker and worker.is_alive():
            worker.join(timeout=10)
        engine.dispose()
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
