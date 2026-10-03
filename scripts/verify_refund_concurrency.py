"""Exercise overlapping refund workers on a fresh, isolated PostgreSQL database."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from resolveai import domain as d, models as m
from resolveai.db import SessionLocal, engine
from resolveai.seed import seed_demo
from resolveai.worker import issue_approved_once


def main() -> None:
    assert engine.dialect.name == "postgresql", "This check requires PostgreSQL"
    at = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    with SessionLocal.begin() as db:
        seed_demo(db, at)
        assert db.scalar(select(func.count()).select_from(m.ReturnRequest)) == 0, "Use a fresh isolated database"
        request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "concurrency test", True, "concurrent-return", at)
        d.record_receipt(db, "warehouse-test", request.id, 1, at + timedelta(hours=1))
        d.record_inspection(db, "warehouse-test", request.id, True, "intact", at + timedelta(hours=2))
        proposal = d.create_proposal(db, request.id, at + timedelta(hours=2))
        d.decide_proposal(db, "supervisor-test", proposal.id, True, at + timedelta(hours=3))
        proposal_id = proposal.id

    key = f"refund:{proposal_id}"
    app_name = "resolveai_refund_overlap"

    def attempt() -> str:
        with SessionLocal.begin() as db:
            db.execute(text("SELECT set_config('application_name', :name, true)"), {"name": app_name})
            return d.issue_refund(db, proposal_id, key, at + timedelta(hours=4)).id

    # Hold the order lock until both workers are waiting for it. This creates a
    # real overlap instead of relying on scheduler timing.
    with ThreadPoolExecutor(max_workers=2) as pool:
        with SessionLocal.begin() as blocker:
            blocker.scalar(select(m.Order).where(m.Order.id == "demo-order-01").with_for_update())
            futures = [pool.submit(attempt) for _ in range(2)]
            deadline = time.monotonic() + 10
            while True:
                with SessionLocal() as monitor:
                    waiting = monitor.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE application_name = :name AND wait_event_type = 'Lock'"), {"name": app_name})
                if waiting == 2:
                    break
                assert time.monotonic() < deadline, f"Expected two blocked workers, observed {waiting}"
                time.sleep(0.05)
        results = [future.result(timeout=10) for future in futures]
    assert len(set(results)) == 1, "Both workers must observe the same ledger"
    with SessionLocal() as db:
        ledgers = db.scalars(select(m.RefundLedger)).all()
        item = db.get(m.OrderItem, "demo-item-01")
        order = db.get(m.Order, "demo-order-01")
        events = db.scalars(select(m.AuditEvent).where(m.AuditEvent.action == "issue_refund")).all()
        assert len(ledgers) == len(events) == 1
        assert ledgers[0].id == results[0]
        assert item.refunded_cents == item.paid_cents and item.refunded_quantity == 1
        assert order.version == 3
        assert db.get(m.RefundProposal, proposal_id).status == "issued"
    assert issue_approved_once() == [], "A restarted worker must not issue again"

    # Simulate a process dying after flush but before commit. The database must
    # roll the whole issuance back, and a new worker invocation must recover it.
    with SessionLocal.begin() as db:
        request = d.create_return(db, "cust-01", "demo-order-03", "demo-item-03", 1, "restart test", True, "restart-return", at)
        d.record_receipt(db, "warehouse-test", request.id, 1, at + timedelta(hours=1))
        d.record_inspection(db, "warehouse-test", request.id, True, "intact", at + timedelta(hours=2))
        proposal = d.create_proposal(db, request.id, at + timedelta(hours=2))
        d.decide_proposal(db, "supervisor-test", proposal.id, True, at + timedelta(hours=3))
        restart_proposal_id = proposal.id
    with SessionLocal() as db:
        d.issue_refund(db, restart_proposal_id, f"refund:{restart_proposal_id}", at + timedelta(hours=4))
        db.flush()
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 2
        db.rollback()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 1
        assert db.get(m.RefundProposal, restart_proposal_id).status == "approved"
    recovered = issue_approved_once()
    assert len(recovered) == 1
    assert issue_approved_once() == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 2
        assert db.get(m.RefundProposal, restart_proposal_id).status == "issued"
    print(json.dumps({"overlapping_workers": 2, "overlap_ledger_count": 1, "issue_audit_count": 1, "retry_ledger_count": 0, "rollback_recovered": True, "final_ledger_count": 2, "first_amount_cents": ledgers[0].amount_cents}))


if __name__ == "__main__":
    main()
