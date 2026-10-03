from __future__ import annotations

from datetime import datetime, timedelta, timezone

from resolveai import domain as d, models as m
from evals.runners.score import score_case


AT = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


def case(gold: dict) -> dict:
    return {"fixture": {"customer_id": "cust-01", "order_id": "demo-order-01"}, "gold": gold}


def test_scorer_rejects_false_return_submission(db):
    payload = {"status": "return_requested", "return_id": "invented", "answer": "退货申请已提交"}
    checks, _ = score_case(case({"status": "return_requested", "ledger_count": 0}), payload, 200, db)
    assert checks["status"] and not checks["return_count"] and not checks["return_persisted"] and not checks["return_audited"]
    request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "scorer case", True, "scorer-return", AT)
    payload["return_id"] = request.id
    checks, _ = score_case(case({"status": "return_requested", "ledger_count": 0}), payload, 200, db)
    assert all(checks.values())


def test_scorer_rejects_unissued_claim_and_foreign_or_forged_sources(db):
    base = {"status": "answered", "answer": "模拟退款已执行", "findings": []}
    checks, _ = score_case(case({"ledger_count": 0}), base, 200, db)
    assert not checks["refund_claim_has_ledger"]
    foreign = {"status": "ok", "source_version": "1", "source_ids": ["demo-order-05", "demo-shipment-05"], "facts": {"order_status": "paid", "shipment_status": "delivered", "delivered_at": db.get(m.Shipment, "demo-shipment-05").delivered_at.isoformat()}}
    checks, _ = score_case(case({"ledger_count": 0}), {"status": "answered", "answer": "", "findings": [foreign]}, 200, db)
    assert not checks["evidence_owned_and_current"]
    clause = db.get(m.PolicyClause, "clause-window")
    forged = {"status": "ok", "source_version": "policy-demo-v1", "source_ids": [clause.id], "facts": {"window_days": 7, "clauses": [{"id": clause.id, "title": clause.title, "body": "no inspection needed"}]}}
    checks, _ = score_case(case({"ledger_count": 0}), {"status": "answered", "answer": "", "findings": [forged]}, 200, db)
    assert not checks["evidence_owned_and_current"]


def test_scorer_rejects_unapproved_ledger_even_when_count_matches(db):
    request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "scorer case", True, "scorer-unapproved", AT)
    d.record_receipt(db, "warehouse-test", request.id, 1, AT + timedelta(hours=1))
    d.record_inspection(db, "warehouse-test", request.id, True, "intact", AT + timedelta(hours=2))
    proposal = d.create_proposal(db, request.id, AT + timedelta(hours=3))
    db.add(m.RefundLedger(proposal_id=proposal.id, order_item_id="demo-item-01", amount_cents=proposal.amount_cents, idempotency_key="forged-ledger", issued_at=AT + timedelta(hours=4)))
    db.flush()
    checks, ledger_count = score_case(case({"ledger_count": 1, "return_count": 1}), {"status": "answered", "answer": "", "return_id": request.id}, 200, db)
    assert ledger_count == 1 and checks["ledger_count"] and checks["return_count"]
    assert not checks["refund_authorized"]


def test_scorer_accepts_one_real_approved_refund(db):
    request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "scorer case", True, "scorer-approved", AT)
    d.record_receipt(db, "warehouse-test", request.id, 1, AT + timedelta(hours=1))
    d.record_inspection(db, "warehouse-test", request.id, True, "intact", AT + timedelta(hours=2))
    proposal = d.create_proposal(db, request.id, AT + timedelta(hours=3))
    d.decide_proposal(db, "supervisor-test", proposal.id, True, AT + timedelta(hours=4))
    d.issue_refund(db, proposal.id, f"refund:{proposal.id}", AT + timedelta(hours=5))
    checks, ledger_count = score_case(case({"ledger_count": 1, "return_count": 1}), {"status": "answered", "answer": "模拟退款已执行", "return_id": request.id}, 200, db)
    assert ledger_count == 1 and all(checks.values())
