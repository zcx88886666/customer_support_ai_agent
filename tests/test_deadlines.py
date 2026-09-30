from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from fastapi.testclient import TestClient

from resolveai import domain as d, models as m, worker
from resolveai.api import app
from resolveai.db import get_db


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

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            assert client.get("/supervisor/refund-deadlines", headers={"x-mock-actor": "cust-01", "x-mock-role": "customer"}).status_code == 403
            result = client.get("/supervisor/refund-deadlines", headers={"x-mock-actor": "supervisor-1", "x-mock-role": "supervisor"})
        assert result.status_code == 200
        assert [(row["return_id"], row["kind"]) for row in result.json()] == [(return_id, "overdue")]
    finally:
        app.dependency_overrides.clear()
