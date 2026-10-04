from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import example, given, settings, strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from resolveai import domain as d, models as m
from resolveai.db import Base, make_engine
from resolveai.seed import seed_demo


AT = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
ACTIONS = ("receipt", "inspect_pass", "inspect_fail", "proposal", "approve", "reject", "issue", "retry")


@settings(max_examples=min(5000, max(1, int(os.getenv("STATEFUL_REFUND_EXAMPLES", "100")))), deadline=None)
@example(quantity=1, actions=["receipt", "inspect_pass", "proposal", "approve", "issue", "retry"])
@example(quantity=2, actions=["receipt", "inspect_pass", "proposal", "reject", "issue"])
@example(quantity=3, actions=["receipt", "inspect_fail", "proposal", "approve", "issue"])
@given(quantity=st.integers(min_value=1, max_value=3), actions=st.lists(st.sampled_from(ACTIONS), min_size=0, max_size=18))
def test_random_refund_action_sequences_preserve_money_and_approval(quantity: int, actions: list[str]):
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory.begin() as db:
            seed_demo(db, AT)
            request = d.create_return(db, "cust-01", "demo-order-03", "demo-item-03", quantity, "stateful case", True, "stateful-return", AT)
            return_id = request.id
        for action in actions:
            try:
                with factory.begin() as db:
                    latest = db.scalar(select(m.RefundProposal).where(m.RefundProposal.return_id == return_id).order_by(m.RefundProposal.created_at.desc(), m.RefundProposal.id.desc()))
                    if action == "receipt":
                        d.record_receipt(db, "warehouse-test", return_id, quantity, AT + timedelta(hours=1))
                    elif action in ("inspect_pass", "inspect_fail"):
                        d.record_inspection(db, "warehouse-test", return_id, action == "inspect_pass", "checked", AT + timedelta(hours=2))
                    elif action == "proposal":
                        d.create_proposal(db, return_id, AT + timedelta(hours=3))
                    elif action in ("approve", "reject") and latest:
                        d.decide_proposal(db, "supervisor-test", latest.id, action == "approve", AT + timedelta(hours=4))
                    elif action in ("issue", "retry") and latest:
                        d.issue_refund(db, latest.id, f"refund:{latest.id}", AT + timedelta(hours=5))
            except d.DomainError:
                # Invalid ordering is expected; the transaction must not leave a partial write.
                pass
            with factory() as db:
                item = db.get(m.OrderItem, "demo-item-03")
                ledger = db.scalars(select(m.RefundLedger)).all()
                issue_audits = db.scalars(select(m.AuditEvent).where(m.AuditEvent.action == "issue_refund")).all()
                assert len(ledger) == len(issue_audits) <= 1
                assert item.refunded_quantity in (0, quantity)
                assert item.refunded_cents == sum(entry.amount_cents for entry in ledger)
                assert 0 <= item.refunded_cents <= item.paid_cents
                if ledger:
                    entry = ledger[0]
                    proposal = db.get(m.RefundProposal, entry.proposal_id)
                    approval = db.scalar(select(m.Approval).where(m.Approval.proposal_id == proposal.id))
                    inspection = db.get(m.Inspection, proposal.inspection_id)
                    receipt = db.get(m.WarehouseReceipt, inspection.receipt_id)
                    assert approval is not None and approval.decision == "approved"
                    assert inspection.passed and receipt.quantity == quantity
                    assert proposal.status == "issued"
                    assert entry.amount_cents == item.paid_cents * quantity // item.quantity
                else:
                    assert item.refunded_quantity == item.refunded_cents == 0
    finally:
        engine.dispose()


def test_approved_proposal_rejects_changed_order_and_amount(session_factory):
    with session_factory.begin() as db:
        request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "tamper case", True, "tamper-return", AT)
        d.record_receipt(db, "warehouse-test", request.id, 1, AT)
        d.record_inspection(db, "warehouse-test", request.id, True, "intact", AT)
        proposal = d.create_proposal(db, request.id, AT)
        d.decide_proposal(db, "supervisor-test", proposal.id, True, AT)
        proposal_id = proposal.id
    with session_factory.begin() as db:
        db.get(m.Order, "demo-order-01").version += 1
    with pytest.raises(d.DomainError, match="Proposal facts changed"):
        with session_factory.begin() as db:
            d.issue_refund(db, proposal_id, f"refund:{proposal_id}", AT)
    with session_factory.begin() as db:
        db.get(m.Order, "demo-order-01").version -= 1
        db.get(m.RefundProposal, proposal_id).amount_cents += 1
    with pytest.raises(d.DomainError, match="Refund amount changed"):
        with session_factory.begin() as db:
            d.issue_refund(db, proposal_id, f"refund:{proposal_id}", AT)
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0
        assert db.get(m.OrderItem, "demo-item-01").refunded_cents == 0


@settings(max_examples=30, deadline=None)
@given(parts=st.sampled_from(([3], [1, 2], [2, 1], [1, 1, 1])), replay=st.booleans())
def test_partitioned_returns_preserve_paid_allocation_and_ownership(parts: list[int], replay: bool):
    """Exercise cumulative rounding through committed returns, approvals, and ledgers."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory.begin() as db:
            seed_demo(db, AT)
        total_quantity = 0
        for index, quantity in enumerate(parts):
            key = f"partition-return-{index}"
            with pytest.raises(d.DomainError) as foreign:
                with factory.begin() as db:
                    d.create_return(db, "cust-02", "demo-order-03", "demo-item-03", quantity, "foreign", True, key, AT)
            assert foreign.value.code == "order_not_found"
            with factory.begin() as db:
                request = d.create_return(db, "cust-01", "demo-order-03", "demo-item-03", quantity, "partition", True, key, AT)
                return_id = request.id
            with pytest.raises(d.DomainError) as hijack:
                with factory.begin() as db:
                    d.create_return(db, "cust-02", "demo-order-03", "demo-item-03", quantity, "partition", True, key, AT)
            assert hijack.value.code == "return_not_found"
            if replay:
                with factory.begin() as db:
                    repeated = d.create_return(db, "cust-01", "demo-order-03", "demo-item-03", quantity, "partition", True, key, AT)
                    assert repeated.id == return_id
            with pytest.raises(d.DomainError) as overcommit:
                with factory.begin() as db:
                    d.create_return(db, "cust-01", "demo-order-03", "demo-item-03", 3, "overcommit", True, f"too-many-{index}", AT)
            assert overcommit.value.code == "quantity_already_requested"
            with factory.begin() as db:
                d.record_receipt(db, "warehouse-test", return_id, quantity, AT + timedelta(hours=1))
                d.record_inspection(db, "warehouse-test", return_id, True, "intact", AT + timedelta(hours=2))
                proposal = d.create_proposal(db, return_id, AT + timedelta(hours=3))
                d.decide_proposal(db, "supervisor-test", proposal.id, True, AT + timedelta(hours=4))
                proposal_id = proposal.id
            with factory.begin() as db:
                first = d.issue_refund(db, proposal_id, f"refund:{proposal_id}", AT + timedelta(hours=5))
                if replay:
                    second = d.issue_refund(db, proposal_id, f"refund:{proposal_id}", AT + timedelta(hours=5))
                    assert second.id == first.id
            total_quantity += quantity
            with factory() as db:
                item = db.get(m.OrderItem, "demo-item-03")
                ledgers = db.scalars(select(m.RefundLedger).where(m.RefundLedger.order_item_id == item.id)).all()
                audits = db.scalars(select(m.AuditEvent).where(m.AuditEvent.action == "issue_refund")).all()
                assert item.refunded_quantity == total_quantity
                assert item.refunded_cents == item.paid_cents * total_quantity // item.quantity
                assert sum(row.amount_cents for row in ledgers) == item.refunded_cents
                assert len(ledgers) == len(audits) == index + 1
        with factory() as db:
            item = db.get(m.OrderItem, "demo-item-03")
            assert item.refunded_quantity == item.quantity
            assert item.refunded_cents == item.paid_cents
    finally:
        engine.dispose()
