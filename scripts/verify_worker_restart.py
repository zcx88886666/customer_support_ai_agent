"""Prepare and inspect a synthetic refund across worker container restarts."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from resolveai import domain as d, models as m
from resolveai.db import SessionLocal, engine
from resolveai.seed import seed_demo


def prepare() -> None:
    at = datetime.now(timezone.utc) - timedelta(hours=4)
    with SessionLocal.begin() as db:
        seed_demo(db, at)
        assert db.scalar(select(func.count()).select_from(m.ReturnRequest)) == 0, "Use a fresh isolated database"
        request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "restart replay", True, "restart-replay-return", at)
        d.record_receipt(db, "warehouse-test", request.id, 1, at + timedelta(hours=1))
        d.record_inspection(db, "warehouse-test", request.id, True, "intact", at + timedelta(hours=2))
        proposal = d.create_proposal(db, request.id, at + timedelta(hours=2))
        d.decide_proposal(db, "supervisor-test", proposal.id, True, at + timedelta(hours=3))
    print(json.dumps({"prepared": True, "proposal_status": "approved", "ledger_count": 0}))


def verify() -> None:
    with SessionLocal() as db:
        request = db.scalar(select(m.ReturnRequest).where(m.ReturnRequest.idempotency_key == "restart-replay-return"))
        assert request is not None and request.status == "refund_issued"
        proposal = db.scalar(select(m.RefundProposal).where(m.RefundProposal.return_id == request.id))
        assert proposal is not None and proposal.status == "issued"
        ledgers = db.scalars(select(m.RefundLedger).where(m.RefundLedger.proposal_id == proposal.id)).all()
        events = db.scalars(select(m.AuditEvent).where(m.AuditEvent.entity_id == proposal.id, m.AuditEvent.action == "issue_refund")).all()
        assert len(ledgers) == len(events) == 1
        item = db.get(m.OrderItem, "demo-item-01")
        order = db.get(m.Order, "demo-order-01")
        assert ledgers[0].amount_cents == item.refunded_cents == item.paid_cents
        assert item.refunded_quantity == 1 and order.version == 3
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 1
        print(json.dumps({"ledger_count": 1, "issue_audit_count": 1, "amount_cents": ledgers[0].amount_cents, "order_version": order.version}))


def main() -> None:
    assert engine.dialect.name == "postgresql", "Use isolated PostgreSQL"
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "verify"))
    args = parser.parse_args()
    (prepare if args.action == "prepare" else verify)()


if __name__ == "__main__":
    main()
