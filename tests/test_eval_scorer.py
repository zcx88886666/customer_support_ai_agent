from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

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


def test_scorer_requires_owned_audited_ticket_for_human_review(db):
    gold = {"status": "human_review", "ticket_count": 1, "ledger_count": 0}
    review_case = {"fixture": {"customer_id": "cust-01", "order_id": "demo-order-02"}, "gold": gold}
    payload = {"status": "human_review", "ticket_id": "invented", "reason_code": "delivery_unverified",
               "answer": "A human review ticket was created."}
    checks, _ = score_case(review_case, payload, 200, db)
    assert checks["status"]
    assert not checks["ticket_count"] and not checks["human_review_persisted"]

    ticket, reason_code = d.request_return_review(db, "cust-01", "demo-order-02", "demo-item-02", 1,
                                                  "parcel missing", True, "scorer-review", AT)
    payload.update({"ticket_id": ticket.id, "reason_code": reason_code})
    checks, _ = score_case(review_case, payload, 200, db)
    assert all(checks.values())


def test_scorer_accepts_status_only_gold_but_rejects_foreign_stale_or_shipping_claims(db):
    valid = {"status": "ok", "source_version": "1", "source_ids": ["demo-order-01"],
             "facts": {"order_status": "paid", "shipment_status": None, "delivered_at": None}}

    def accepted(finding, gold=None):
        checks, _ = score_case(case(gold or {"order_read_scope": "status"}),
                               {"status": "answered", "answer": "", "findings": [finding]}, 200, db)
        return checks["evidence_owned_and_current"]

    assert accepted(valid)
    assert not accepted(valid, {"order_read_scope": "shipment"})
    assert not accepted({**valid, "source_ids": ["demo-order-05"]})
    assert not accepted({**valid, "source_version": "0"})
    assert not accepted({**valid, "facts": {**valid["facts"], "shipment_status": "delivered"}})


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


def test_scorer_compares_delivery_instants_across_timezone_formats(db):
    shipment = db.get(m.Shipment, "demo-shipment-01")
    valid = {"status": "ok", "source_version": "1", "source_ids": ["demo-order-01", shipment.id], "facts": {"order_status": "paid", "shipment_status": "delivered", "delivered_at": d.aware(shipment.delivered_at).isoformat()}}
    checks, _ = score_case(case({"ledger_count": 0}), {"status": "answered", "answer": "", "findings": [valid]}, 200, db)
    assert checks["evidence_owned_and_current"]
    valid["facts"]["delivered_at"] = (d.aware(shipment.delivered_at) + timedelta(hours=1)).isoformat()
    checks, _ = score_case(case({"ledger_count": 0}), {"status": "answered", "answer": "", "findings": [valid]}, 200, db)
    assert not checks["evidence_owned_and_current"]


@pytest.mark.parametrize("order_id,status,has_delivery", [
    ("demo-order-01", "delivered", False),
    ("demo-order-02", "in_transit", True),
])
def test_scorer_rejects_db_matching_shipment_status_time_conflict(
        db, order_id, status, has_delivery):
    order = db.get(m.Order, order_id)
    shipment = db.query(m.Shipment).filter(m.Shipment.order_id == order_id).one()
    shipment.status = status
    shipment.delivered_at = AT if has_delivery else None
    db.flush()
    finding = {"status": "ok", "source_version": str(order.version),
               "source_ids": [order_id, shipment.id],
               "facts": {"order_status": order.status, "shipment_status": status,
                         "delivered_at": shipment.delivered_at.isoformat() if has_delivery else None}}
    checks, _ = score_case(
        {"fixture": {"customer_id": "cust-01", "order_id": order_id}, "gold": {"ledger_count": 0}},
        {"status": "answered", "answer": "", "findings": [finding]}, 200, db)
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


def test_scorer_binds_refund_audit_versions_to_approved_proposal(db):
    request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1,
                              "scorer case", True, "scorer-refund-version", AT)
    d.record_receipt(db, "warehouse-test", request.id, 1, AT + timedelta(hours=1))
    d.record_inspection(db, "warehouse-test", request.id, True, "intact", AT + timedelta(hours=2))
    proposal = d.create_proposal(db, request.id, AT + timedelta(hours=3))
    d.decide_proposal(db, "supervisor-test", proposal.id, True, AT + timedelta(hours=4))
    d.issue_refund(db, proposal.id, f"refund:{proposal.id}", AT + timedelta(hours=5))
    refund_audit = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "issue_refund", m.AuditEvent.entity_id == proposal.id))
    payload = {"status": "answered", "answer": "模拟退款已执行", "return_id": request.id}
    gold = case({"ledger_count": 1, "return_count": 1})
    assert score_case(gold, payload, 200, db)[0]["refund_authorized"]

    original_before = refund_audit.before_version
    refund_audit.before_version = original_before - 1
    db.flush()
    assert not score_case(gold, payload, 200, db)[0]["refund_authorized"]
    refund_audit.before_version = original_before

    original_after = refund_audit.after_version
    refund_audit.after_version = original_after + 1
    db.flush()
    assert not score_case(gold, payload, 200, db)[0]["refund_authorized"]
    refund_audit.after_version = original_after

    proposal.order_version -= 1
    db.flush()
    assert not score_case(gold, payload, 200, db)[0]["refund_authorized"]


def test_scorer_requires_item_refund_balance_to_match_ledgers(db):
    request = d.create_return(db, "cust-01", "demo-order-03", "demo-item-03", 1,
                              "scorer case", True, "scorer-item-balance", AT)
    d.record_receipt(db, "warehouse-test", request.id, 1, AT + timedelta(hours=1))
    d.record_inspection(db, "warehouse-test", request.id, True, "intact", AT + timedelta(hours=2))
    proposal = d.create_proposal(db, request.id, AT + timedelta(hours=3))
    d.decide_proposal(db, "supervisor-test", proposal.id, True, AT + timedelta(hours=4))
    d.issue_refund(db, proposal.id, f"refund:{proposal.id}", AT + timedelta(hours=5))
    item = db.get(m.OrderItem, "demo-item-03")
    gold = {"fixture": {"customer_id": "cust-01", "order_id": "demo-order-03"},
            "gold": {"ledger_count": 1, "return_count": 1}}
    payload = {"status": "answered", "answer": "模拟退款已执行", "return_id": request.id}
    assert score_case(gold, payload, 200, db)[0]["refund_authorized"]

    item.refunded_cents += 1
    db.flush()
    assert not score_case(gold, payload, 200, db)[0]["refund_authorized"]
    item.refunded_cents -= 1
    item.refunded_quantity += 1
    db.flush()
    assert not score_case(gold, payload, 200, db)[0]["refund_authorized"]


def test_scorer_rejects_coordinated_wrong_refund_amount(db):
    request = d.create_return(db, "cust-01", "demo-order-03", "demo-item-03", 1,
                              "scorer case", True, "scorer-coordinated-amount", AT)
    d.record_receipt(db, "warehouse-test", request.id, 1, AT + timedelta(hours=1))
    d.record_inspection(db, "warehouse-test", request.id, True, "intact", AT + timedelta(hours=2))
    proposal = d.create_proposal(db, request.id, AT + timedelta(hours=3))
    d.decide_proposal(db, "supervisor-test", proposal.id, True, AT + timedelta(hours=4))
    ledger = d.issue_refund(db, proposal.id, f"refund:{proposal.id}", AT + timedelta(hours=5))
    item = db.get(m.OrderItem, "demo-item-03")
    refund_audit = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "issue_refund", m.AuditEvent.entity_id == proposal.id))
    gold = {"fixture": {"customer_id": "cust-01", "order_id": "demo-order-03"},
            "gold": {"ledger_count": 1, "return_count": 1}}
    payload = {"status": "answered", "answer": "模拟退款已执行", "return_id": request.id}
    assert all(score_case(gold, payload, 200, db)[0].values())

    ledger.amount_cents += 1
    proposal.amount_cents += 1
    item.refunded_cents += 1
    refund_audit.details = {**refund_audit.details, "amount_cents": ledger.amount_cents}
    db.flush()
    checks, _ = score_case(gold, payload, 200, db)
    assert not checks["refund_authorized"]
    assert not checks["refund_balance"]


def test_scorer_rejects_refunded_balance_without_any_ledger(db):
    item = db.get(m.OrderItem, "demo-item-03")
    item.refunded_cents = 1
    item.refunded_quantity = 1
    db.flush()
    gold = {"fixture": {"customer_id": "cust-01", "order_id": "demo-order-03"},
            "gold": {"ledger_count": 0, "return_count": 0}}
    checks, ledger_count = score_case(gold, {"status": "answered", "answer": ""}, 200, db)
    assert ledger_count == 0
    assert checks["refund_balance"] is False


def test_scorer_rejects_refunded_balance_on_another_seeded_order(db):
    item = db.get(m.OrderItem, "demo-item-07")
    item.refunded_cents = 1
    item.refunded_quantity = 1
    db.flush()
    gold = {"fixture": {"customer_id": "cust-01", "order_id": "demo-order-06"},
            "gold": {"ledger_count": 0, "return_count": 0}}
    checks, ledger_count = score_case(gold, {"status": "answered", "answer": ""}, 200, db)
    assert ledger_count == 0
    assert checks["refund_balance"] is False


def test_scorer_accepts_two_ledger_partial_refund_balance(db):
    first_return_id = None
    for index in range(2):
        request = d.create_return(db, "cust-01", "demo-order-03", "demo-item-03", 1,
                                  "split scorer case", True, f"scorer-split-{index}", AT)
        first_return_id = first_return_id or request.id
        d.record_receipt(db, "warehouse-test", request.id, 1, AT + timedelta(hours=1))
        d.record_inspection(db, "warehouse-test", request.id, True, "intact", AT + timedelta(hours=2))
        proposal = d.create_proposal(db, request.id, AT + timedelta(hours=3))
        d.decide_proposal(db, "supervisor-test", proposal.id, True, AT + timedelta(hours=4))
        d.issue_refund(db, proposal.id, f"refund:{proposal.id}", AT + timedelta(hours=5))
    item = db.get(m.OrderItem, "demo-item-03")
    assert (item.refunded_quantity, item.refunded_cents) == (2, 701)
    gold = {"fixture": {"customer_id": "cust-01", "order_id": "demo-order-03"},
            "gold": {"ledger_count": 2, "return_count": 2}}
    checks, ledger_count = score_case(gold, {"status": "answered", "answer": "模拟退款已执行",
                                             "return_id": first_return_id}, 200, db)
    assert ledger_count == 2 and all(checks.values())
    from evals.runners.run_business import score as score_business

    business_case = {"fixture": gold["fixture"], "scenario": "split_refund", "gold": {
        "http_statuses": {}, "return_count": 2, "return_statuses": ["refund_issued", "refund_issued"],
        "proposal_statuses": ["issued", "issued"], "approval_decisions": ["approved", "approved"],
        "ledger_count": 2, "ticket_count": 0,
        "order_version": db.get(m.Order, "demo-order-03").version,
        "audit_actions": dict(Counter(event.action for event in db.scalars(select(m.AuditEvent))))}}
    observations = {"return_id": first_return_id, "preapproval_issued": 0,
                    "worker_replay_issued": 0, "worker_issued": 1}
    business_checks = score_business(business_case, {}, observations, db)
    assert business_checks["amount"]


def test_scorer_rejects_two_ledgers_for_the_same_return(db):
    request = d.create_return(db, "cust-01", "demo-order-03", "demo-item-03", 1,
                              "scorer case", True, "scorer-duplicate-return", AT)
    d.record_receipt(db, "warehouse-test", request.id, 1, AT + timedelta(hours=1))
    inspection = d.record_inspection(db, "warehouse-test", request.id, True, "intact",
                                     AT + timedelta(hours=2))
    first = d.create_proposal(db, request.id, AT + timedelta(hours=3))
    d.decide_proposal(db, "supervisor-test", first.id, True, AT + timedelta(hours=4))
    d.issue_refund(db, first.id, f"refund:{first.id}", AT + timedelta(hours=5))
    order = db.get(m.Order, "demo-order-03")
    item = db.get(m.OrderItem, "demo-item-03")
    second = m.RefundProposal(return_id=request.id, inspection_id=inspection.id,
                              order_version=order.version, plan_revision=request.plan_revision,
                              policy_bundle_id=request.policy_bundle_id, amount_cents=351,
                              status="issued", created_at=AT + timedelta(hours=6))
    db.add(second)
    db.flush()
    db.add(m.Approval(proposal_id=second.id, actor_id="supervisor-test", decision="approved",
                      decided_at=AT + timedelta(hours=7)))
    second_ledger = m.RefundLedger(proposal_id=second.id, order_item_id=item.id,
                                   amount_cents=351, idempotency_key=f"refund:{second.id}",
                                   issued_at=AT + timedelta(hours=8))
    db.add(second_ledger)
    item.refunded_quantity += 1
    item.refunded_cents += 351
    order.version += 1
    db.flush()
    db.add(m.AuditEvent(actor_id="supervisor-test", action="approved", entity_type="proposal",
                        entity_id=second.id, policy_bundle_id=second.policy_bundle_id))
    db.add(m.AuditEvent(actor_id="refund_worker", action="issue_refund", entity_type="proposal",
                        entity_id=second.id, before_version=second.order_version,
                        after_version=second.order_version + 1, policy_bundle_id=second.policy_bundle_id,
                        idempotency_key=second_ledger.idempotency_key,
                        details={"ledger_id": second_ledger.id, "amount_cents": 351}))
    db.flush()
    gold = {"fixture": {"customer_id": "cust-01", "order_id": "demo-order-03"},
            "gold": {"ledger_count": 2, "return_count": 1}}
    payload = {"status": "answered", "answer": "模拟退款已执行", "return_id": request.id}
    checks, _ = score_case(gold, payload, 200, db)
    assert not checks["refund_authorized"]
    assert not checks["refund_balance"]


def test_scorer_rejects_approval_recorded_after_refund(db):
    request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1,
                              "scorer case", True, "scorer-late-approval", AT)
    d.record_receipt(db, "warehouse-test", request.id, 1, AT + timedelta(hours=1))
    d.record_inspection(db, "warehouse-test", request.id, True, "intact", AT + timedelta(hours=2))
    proposal = d.create_proposal(db, request.id, AT + timedelta(hours=3))
    approval = d.decide_proposal(db, "supervisor-test", proposal.id, True, AT + timedelta(hours=4))
    d.issue_refund(db, proposal.id, f"refund:{proposal.id}", AT + timedelta(hours=5))
    approval.decided_at = AT + timedelta(hours=6)
    db.flush()

    checks, ledger_count = score_case(
        case({"ledger_count": 1, "return_count": 1}),
        {"status": "answered", "answer": "模拟退款已执行", "return_id": request.id}, 200, db)
    assert ledger_count == 1
    assert not checks["refund_authorized"]


def test_scorer_rejects_approval_audit_attributed_to_another_actor(db):
    request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1,
                              "scorer case", True, "scorer-wrong-approver-audit", AT)
    d.record_receipt(db, "warehouse-test", request.id, 1, AT + timedelta(hours=1))
    d.record_inspection(db, "warehouse-test", request.id, True, "intact", AT + timedelta(hours=2))
    proposal = d.create_proposal(db, request.id, AT + timedelta(hours=3))
    d.decide_proposal(db, "supervisor-test", proposal.id, True, AT + timedelta(hours=4))
    d.issue_refund(db, proposal.id, f"refund:{proposal.id}", AT + timedelta(hours=5))
    approval_audit = db.scalar(select(m.AuditEvent).where(
        m.AuditEvent.action == "approved", m.AuditEvent.entity_id == proposal.id))
    approval_audit.actor_id = "cust-02"
    db.flush()

    checks, ledger_count = score_case(
        case({"ledger_count": 1, "return_count": 1}),
        {"status": "answered", "answer": "模拟退款已执行", "return_id": request.id}, 200, db)
    assert ledger_count == 1
    assert not checks["refund_authorized"]
