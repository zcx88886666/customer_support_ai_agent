"""Prepare and check an isolated live refund-deadline worker run."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from resolveai import domain as d, models as m
from resolveai.db import SessionLocal, engine
from resolveai.seed import seed_demo


def prepare() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal.begin() as db:
        seed_demo(db, now - timedelta(days=9))
        assert db.scalar(select(func.count()).select_from(m.ReturnRequest)) == 0, "Use a fresh isolated database"
        for order_number, kind, age in ((1, "overdue", timedelta(days=8)), (3, "due_soon", timedelta(days=6, hours=12))):
            received_at = now - age
            request = d.create_return(db, "cust-01", f"demo-order-{order_number:02d}", f"demo-item-{order_number:02d}", 1, "deadline test", True, f"deadline-{kind}", received_at - timedelta(hours=1))
            d.record_receipt(db, "warehouse-test", request.id, 1, received_at)
    print(json.dumps({"prepared": ["overdue", "due_soon"], "clock_utc": now.isoformat()}))


def verify() -> None:
    with SessionLocal() as db:
        requests = {request.idempotency_key.removeprefix("deadline-"): request for request in db.scalars(select(m.ReturnRequest).where(m.ReturnRequest.idempotency_key.in_(("deadline-overdue", "deadline-due_soon"))))}
        assert set(requests) == {"overdue", "due_soon"}
        alerts = db.scalars(select(m.RefundDeadlineAlert)).all()
        assert {(alert.return_id, alert.kind) for alert in alerts} == {(requests[kind].id, kind) for kind in requests}
        events = db.scalars(select(m.AuditEvent).where(m.AuditEvent.action.like("refund_deadline_%"))).all()
        assert {(event.entity_id, event.action) for event in events} == {(requests[kind].id, f"refund_deadline_{kind}") for kind in requests}
        assert len(alerts) == len(events) == 2
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0
        print(json.dumps({"alerts": sorted(alert.kind for alert in alerts), "audit_events": len(events), "ledger_count": 0}))


def main() -> None:
    assert engine.dialect.name == "postgresql", "Use isolated PostgreSQL"
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "verify"))
    args = parser.parse_args()
    (prepare if args.action == "prepare" else verify)()


if __name__ == "__main__":
    main()
