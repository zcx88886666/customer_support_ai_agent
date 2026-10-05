"""Queued deadline batches advance past existing alerts instead of rescanning."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event, func, select

from resolveai import domain as d, models as m, worker


AT = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


def receipts(factory):
    ids = []
    with factory.begin() as db:
        for number in (1, 3, 6, 9, 11, 13, 16):
            key = str(number).zfill(2)
            request = d.create_return(db, 'cust-01', 'demo-order-' + key, 'demo-item-' + key,
                                      1, 'synthetic batch', True, 'batch-' + key, AT)
            d.record_receipt(db, 'warehouse-batch', request.id, 1, AT)
            ids.append(request.id)
    return ids


def test_small_batches_advance_past_already_alerted_receipts(session_factory):
    ids = receipts(session_factory)
    batches = [worker.alert_refund_deadlines_once(AT + timedelta(days=8), session_factory=session_factory, batch_size=2)
               for _ in range(5)]
    assert [len(batch) for batch in batches] == [2, 2, 2, 1, 0]
    assert {return_id for batch in batches for return_id, kind in batch if kind == 'overdue'} == set(ids)
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(m.RefundDeadlineAlert)) == 7
        assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action == 'refund_deadline_overdue')) == 7
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0
    # A large alerted history must not trigger per-return verification again.
    selects = []
    engine = session_factory.kw['bind']
    def observed(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith('SELECT'):
            selects.append(statement)
    event.listen(engine, 'after_cursor_execute', observed)
    try:
        assert worker.alert_refund_deadlines_once(AT + timedelta(days=9), session_factory=session_factory, batch_size=2) == []
    finally:
        event.remove(engine, 'after_cursor_execute', observed)
    assert len(selects) <= 2


def test_prior_due_soon_alert_does_not_suppress_overdue_batch(session_factory):
    ids = receipts(session_factory)
    for _ in range(4):
        worker.alert_refund_deadlines_once(AT + timedelta(days=6), session_factory=session_factory, batch_size=2)
    for _ in range(4):
        worker.alert_refund_deadlines_once(AT + timedelta(days=7), session_factory=session_factory, batch_size=2)
    with session_factory() as db:
        alerts = db.scalars(select(m.RefundDeadlineAlert)).all()
        assert len(alerts) == 14
        assert {(a.return_id, a.kind) for a in alerts} == {(rid, kind) for rid in ids for kind in ('due_soon', 'overdue')}


@pytest.mark.parametrize('received_at,zone', [
    (AT, timezone(timedelta(hours=-7))),
    (AT, timezone(timedelta(hours=8))),
    # The six-day interval crosses the November 1 DST fallback.
    (datetime(2026, 10, 27, 12, tzinfo=timezone.utc), ZoneInfo('America/Los_Angeles')),
])
@pytest.mark.parametrize('batch_size', [None, 100])
def test_equivalent_instants_preserve_deadline_boundaries(session_factory, received_at, zone, batch_size):
    with session_factory.begin() as db:
        request = d.create_return(db, 'cust-01', 'demo-order-01', 'demo-item-01',
                                  1, 'timezone boundary', True, 'timezone-return', AT)
        d.record_receipt(db, 'warehouse-timezone', request.id, 1, received_at)
        return_id = request.id
    for age, kind in ((6, 'due_soon'), (7, 'overdue')):
        boundary = received_at + timedelta(days=age)
        before = (boundary - timedelta(microseconds=1)).astimezone(zone)
        assert worker.alert_refund_deadlines_once(before, session_factory=session_factory, batch_size=batch_size) == []
        assert worker.alert_refund_deadlines_once(boundary.astimezone(zone), session_factory=session_factory,
                                                 batch_size=batch_size) == [(return_id, kind)]
        assert worker.alert_refund_deadlines_once(boundary, session_factory=session_factory, batch_size=batch_size) == []
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(m.RefundDeadlineAlert)) == 2
        assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
            m.AuditEvent.action.in_(['refund_deadline_due_soon', 'refund_deadline_overdue']))) == 2
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0


@pytest.mark.parametrize('limit', [0, -1, 1001, True, 1.5])
def test_invalid_batch_limits_reject_before_sql(session_factory, limit):
    with pytest.raises(ValueError, match='batch'):
        worker.alert_refund_deadlines_once(AT, session_factory=session_factory, batch_size=limit)


def test_job_probe_fixtures_keep_returns_before_receipts(session_factory):
    from scripts import verify_celery_jobs as probe
    assert hasattr(probe, 'seed_probe_state'), 'Probe fixture builder must preserve historical chronology'
    probe.seed_probe_state(session_factory, AT)
    with session_factory() as db:
        receipts = db.execute(select(m.ReturnRequest.created_at, m.WarehouseReceipt.received_at)
                              .join(m.WarehouseReceipt, m.WarehouseReceipt.return_id == m.ReturnRequest.id)).all()
        assert len(receipts) == 4 and all(created <= received for created, received in receipts)
        for shipment in db.scalars(select(m.Shipment).where(m.Shipment.order_id.in_(['demo-order-03', 'demo-order-04']))).all():
            events = db.scalars(select(m.ShipmentEvent).where(m.ShipmentEvent.shipment_id == shipment.id)).all()
            shipped = next(event for event in events if event.status == 'shipped')
            delivered = next(event for event in events if event.status == 'delivered')
            assert shipped.occurred_at <= delivered.occurred_at == shipment.delivered_at
