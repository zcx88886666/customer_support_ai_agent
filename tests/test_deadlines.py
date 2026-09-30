from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from resolveai import domain as d, models as m, worker


AT = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def test_refund_deadline_alerts_are_idempotent(session_factory, monkeypatch):
    with session_factory.begin() as db:
        request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "no longer needed", True, "deadline-case", AT)
        d.record_receipt(db, "warehouse-1", request.id, 1, AT)
        return_id = request.id
    monkeypatch.setattr(worker, "SessionLocal", session_factory)

    assert worker.alert_refund_deadlines_once(AT + timedelta(days=5)) == []
    assert worker.alert_refund_deadlines_once(AT + timedelta(days=6)) == [(return_id, "due_soon")]
    assert worker.alert_refund_deadlines_once(AT + timedelta(days=6, hours=1)) == []
    assert worker.alert_refund_deadlines_once(AT + timedelta(days=7)) == [(return_id, "overdue")]
    assert worker.alert_refund_deadlines_once(AT + timedelta(days=8)) == []
    with session_factory() as db:
        assert {alert.kind for alert in db.scalars(select(m.RefundDeadlineAlert)).all()} == {"due_soon", "overdue"}
        assert len(db.scalars(select(m.AuditEvent).where(m.AuditEvent.entity_id == return_id, m.AuditEvent.action.like("refund_deadline_%"))).all()) == 2
