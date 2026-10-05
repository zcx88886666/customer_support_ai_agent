"""Controlled queued jobs retain domain authorization and fixture isolation."""

import importlib
import importlib.util
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from resolveai import domain as d, models as m


AT = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


def make_jobs(namespace, database_url):
    assert importlib.util.find_spec('resolveai.jobs') is not None, 'Controlled Celery job boundary is missing'
    return importlib.import_module('resolveai.jobs').make_app(namespace, database_url, 'memory://')


def prepared_proposal(factory, approved=False):
    with factory.begin() as db:
        request = d.create_return(db, 'cust-01', 'demo-order-01', 'demo-item-01', 1,
                                  'synthetic job', True, 'job-return', AT)
        d.record_receipt(db, 'warehouse-job', request.id, 1, AT)
        d.record_inspection(db, 'warehouse-job', request.id, True, 'intact', AT)
        proposal = d.create_proposal(db, request.id, AT)
        if approved:
            d.decide_proposal(db, 'supervisor-job', proposal.id, True, AT)
        return proposal.id


def test_queued_refund_requires_approval_and_repeats_only_one_ledger(session_factory):
    app = make_jobs('test-refund', str(session_factory.kw['bind'].url))
    task = app.tasks['resolveai.jobs.refunds']
    proposal_id = prepared_proposal(session_factory)
    assert task.apply(args=['test-refund'], throw=True).get() == {'issued_count': 0}
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0
    with session_factory.begin() as db:
        d.decide_proposal(db, 'supervisor-job', proposal_id, True, AT)
    assert task.apply(args=['test-refund'], throw=True).get() == {'issued_count': 1}
    assert task.apply(args=['test-refund'], throw=True).get() == {'issued_count': 0}
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 1
        assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action == 'issue_refund')) == 1
        item = db.get(m.OrderItem, 'demo-item-01')
        assert item.refunded_quantity == 1 and item.refunded_cents == item.paid_cents


def test_foreign_job_namespace_cannot_issue_approved_refund(session_factory):
    app = make_jobs('case-one', str(session_factory.kw['bind'].url))
    prepared_proposal(session_factory, approved=True)
    with pytest.raises(ValueError, match='namespace'):
        app.tasks['resolveai.jobs.refunds'].apply(args=['case-two'], throw=True).get()
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0


def test_deadline_job_deduplicates_alerts_in_sql(session_factory):
    now = datetime.now(timezone.utc)
    prepared_proposal(session_factory)
    with session_factory.begin() as db:
        db.scalar(select(m.WarehouseReceipt)).received_at = now - timedelta(days=8)
    app = make_jobs('test-alert', str(session_factory.kw['bind'].url))
    task = app.tasks['resolveai.jobs.deadlines']
    assert task.apply(args=['test-alert'], throw=True).get() == {'alert_count': 1}
    assert task.apply(args=['test-alert'], throw=True).get() == {'alert_count': 0}
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(m.RefundDeadlineAlert)) == 1
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0


def test_different_database_workers_cannot_consume_same_queue(tmp_path):
    one = make_jobs('same-namespace', f'sqlite:///{tmp_path}/one.db')
    two = make_jobs('same-namespace', f'sqlite:///{tmp_path}/two.db')
    assert one.amqp.queues[one.conf.task_default_queue].name != two.amqp.queues[two.conf.task_default_queue].name
    assert one.conf.broker_transport_options['global_keyprefix'] != two.conf.broker_transport_options['global_keyprefix']


@pytest.mark.parametrize('namespace', ['', 'x/y', 'x y', 'a' * 65])
def test_invalid_namespace_rejects_startup(namespace):
    with pytest.raises(ValueError, match='namespace'):
        make_jobs(namespace, 'sqlite:///test.db')


@pytest.mark.parametrize('url', [
    'postgresql+psycopg://fixture_a@localhost',
    'postgresql+psycopg://fixture_b@localhost',
    'postgresql+psycopg://fixture@localhost:5432',
    'postgresql+psycopg://fixture@/explicit_database',
    'postgresql+psycopg://fixture@localhost/explicit_database',
    'postgresql+psycopg://fixture@localhost:5432/database?service=other_fixture',
    'postgresql+psycopg://fixture@localhost:5432/database?dbname=other_fixture',
])
def test_ambiguous_postgres_targets_reject_before_queue_creation(url):
    with pytest.raises(ValueError, match='explicit.*target'):
        make_jobs('same-namespace', url)
