"""Checkpoint cancellation uses the established rollback/handoff path."""

from contextlib import contextmanager
from dataclasses import replace

import psycopg
import pytest

from resolveai import checkpoint, models as m
from resolveai.agent import run_chat
from resolveai.schemas import ChatInput
from resolveai.request_budget import BudgetExceeded, RequestBudget, budget_scope


def test_direct_checkpoint_query_cancellation_is_budget_exhaustion(monkeypatch):
    monkeypatch.setattr(checkpoint, 'settings', replace(checkpoint.settings, database_url='postgresql://synthetic@localhost/synthetic'))
    class Connection:
        def close(self):
            pass
    # The actual context manager must translate psycopg, not SQLAlchemy, errors.
    monkeypatch.setattr('resolveai.database_io.connect_with_deadline', lambda *a, **k: Connection())
    with budget_scope(RequestBudget()), pytest.raises(BudgetExceeded):
        with checkpoint.parent_checkpointer():
            raise psycopg.errors.QueryCanceled('synthetic checkpoint timeout')


def test_checkpoint_deadline_hands_off_without_business_mutations(db, monkeypatch):
    @contextmanager
    def expired():
        raise BudgetExceeded('checkpoint_deadline_expired')
        yield
    monkeypatch.setattr(checkpoint, 'parent_checkpointer', expired)
    result = run_chat(db, 'cust-01', ChatInput(thread_id='checkpoint-expired', message='包裹到了吗', order_id='demo-order-02'))
    assert result['status'] == 'handoff' and result['findings'] == []
    assert db.query(m.Ticket).count() == 1
    assert db.query(m.ReturnRequest).count() == db.query(m.RefundLedger).count() == 0
    assert db.get(m.ThreadState, 'checkpoint-expired').customer_id == 'cust-01'


def test_expired_transaction_is_discarded_without_another_network_rollback(db, monkeypatch):
    from resolveai import agent
    from types import SimpleNamespace
    original = agent._run_chat
    original_bind = db.get_bind
    interrupted = False
    network_bind_check = False
    def expire(session, *args, **kwargs):
        nonlocal interrupted, network_bind_check
        if not interrupted:
            interrupted = True
            session.add(m.Ticket(customer_id='cust-01', topic='uncommitted discarded work'))
            session.flush()
            network_bind_check = True
            raise BudgetExceeded('checkpoint_deadline_expired')
        return original(session, *args, **kwargs)
    def get_bind(*args, **kwargs):
        nonlocal network_bind_check
        if network_bind_check:
            network_bind_check = False
            return SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
        return original_bind(*args, **kwargs)
    def forbidden_rollback():
        pytest.fail('Expired transaction must close locally, without another network rollback')
    monkeypatch.setattr(agent, '_run_chat', expire)
    monkeypatch.setattr(db, 'get_bind', get_bind)
    monkeypatch.setattr(db, 'rollback', forbidden_rollback)
    result = run_chat(db, 'cust-01', ChatInput(thread_id='discard-expired', message='包裹到了吗'))
    assert result['status'] == 'handoff'
    assert db.query(m.Ticket).count() == 1
    assert db.query(m.Ticket).first().topic != 'uncommitted discarded work'


def test_local_in_memory_database_survives_resource_limit_handoff(monkeypatch):
    from datetime import datetime, timezone
    from sqlalchemy.orm import Session
    from resolveai.db import Base, make_engine
    from resolveai.seed import seed_demo
    @contextmanager
    def expired():
        raise BudgetExceeded('synthetic resource limit')
        yield
    monkeypatch.setattr(checkpoint, 'parent_checkpointer', expired)
    engine = make_engine('sqlite:///:memory:')
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            seed_demo(db, datetime.now(timezone.utc))
            db.commit()
            result = run_chat(db, 'cust-01', ChatInput(thread_id='memory-deadline', message='包裹到了吗', order_id='demo-order-02'))
            assert result['status'] == 'handoff' and db.query(m.Order).count() == 25
            assert db.query(m.Ticket).count() == 1
    finally:
        engine.dispose()
