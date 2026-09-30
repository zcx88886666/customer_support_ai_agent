from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from resolveai import domain as d, models as m


AT = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def test_ownership_and_seven_day_boundary(db):
    with pytest.raises(d.DomainError) as exc:
        d.owned_order(db, "cust-02", "demo-order-01")
    assert exc.value.status == 404
    assert d.eligibility(db, "cust-01", "demo-order-04", "demo-item-04", 1, AT)["eligible"]
    assert not d.eligibility(db, "cust-01", "demo-order-08", "demo-item-08", 1, AT)["eligible"]
    assert d.eligibility(db, "cust-01", "demo-order-11", "demo-item-11", 1, AT)["eligible"]
    assert not d.eligibility(db, "cust-01", "demo-order-02", "demo-item-02", 1, AT)["eligible"]
    assert not d.eligibility(db, "cust-01", "demo-order-07", "demo-item-07", 1, AT)["eligible"]


def test_approval_before_refund_and_idempotency(db):
    request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "no longer needed", True, "return-1", AT)
    assert d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "no longer needed", True, "return-1", AT).id == request.id
    with pytest.raises(d.DomainError) as foreign_retry:
        d.create_return(db, "cust-02", "demo-order-01", "demo-item-01", 1, "no longer needed", True, "return-1", AT)
    assert foreign_retry.value.status == 404
    with pytest.raises(d.DomainError) as missing_confirmation:
        d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "no longer needed", False, "return-1", AT)
    assert missing_confirmation.value.code == "confirmation_required"
    with pytest.raises(d.DomainError):
        d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "different", True, "return-1", AT)
    receipt = d.record_receipt(db, "warehouse-1", request.id, 1, AT + timedelta(hours=1))
    inspection = d.record_inspection(db, "warehouse-1", request.id, True, "intact", AT + timedelta(hours=2))
    proposal = d.create_proposal(db, request.id, AT + timedelta(hours=2))
    assert proposal.inspection_id == inspection.id
    with pytest.raises(d.DomainError) as exc:
        d.issue_refund(db, proposal.id, "refund-1", AT)
    assert exc.value.code == "approval_required"
    assert db.scalars(select(m.RefundLedger)).all() == []
    d.decide_proposal(db, "supervisor-1", proposal.id, True, AT + timedelta(hours=3))
    ledger = d.issue_refund(db, proposal.id, "refund-1", AT + timedelta(hours=4))
    assert d.issue_refund(db, proposal.id, "refund-1", AT + timedelta(hours=5)).id == ledger.id
    assert len(db.scalars(select(m.RefundLedger)).all()) == 1
    assert ledger.amount_cents == db.get(m.OrderItem, "demo-item-01").paid_cents
    assert any(e.action == "issue_refund" for e in db.scalars(select(m.AuditEvent)).all())


def test_partial_refund_rounding_and_rejection(db):
    item = db.get(m.OrderItem, "demo-item-03")
    first = d.expected_refund(item, 1)
    item.refunded_cents = first
    item.refunded_quantity = 1
    second = d.expected_refund(item, 1)
    item.refunded_cents += second
    item.refunded_quantity += 1
    third = d.expected_refund(item, 1)
    assert first + second + third == item.paid_cents
    with pytest.raises(d.DomainError):
        d.expected_refund(item, 2)


def test_stale_proposal_cannot_be_approved(db):
    request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "no longer needed", True, "return-2", AT)
    d.record_receipt(db, "warehouse-1", request.id, 1, AT)
    d.record_inspection(db, "warehouse-1", request.id, True, "intact", AT)
    proposal = d.create_proposal(db, request.id, AT)
    db.get(m.Order, request.order_id).version += 1
    with pytest.raises(d.DomainError) as exc:
        d.decide_proposal(db, "supervisor-1", proposal.id, True, AT)
    assert exc.value.code == "stale_proposal"
    assert db.scalars(select(m.RefundLedger)).all() == []
    refreshed = d.create_proposal(db, request.id, AT + timedelta(seconds=1))
    assert refreshed.id != proposal.id
    assert proposal.status == "stale"
    assert refreshed.order_version == db.get(m.Order, request.order_id).version
