from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import example, given, settings, strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from resolveai import domain as d, models as m, worker
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


@settings(max_examples=100, deadline=None)
@example(requested=1, observed_delta=-1,
         actions=["mismatch", "mismatch", "match", "inspect_pass", "proposal", "approve", "issue"])
@example(requested=3, observed_delta=1,
         actions=["match", "mismatch", "inspect_pass", "proposal", "approve", "issue"])
@given(requested=st.integers(min_value=1, max_value=3),
       observed_delta=st.sampled_from((-1, 1)),
       actions=st.lists(st.sampled_from(("match", "mismatch", "inspect_pass", "inspect_fail",
                                         "proposal", "approve", "issue")), min_size=1, max_size=14))
def test_generated_receipt_exception_sequences_never_release_disputed_returns(
        requested: int, observed_delta: int, actions: list[str]):
    """A quantity dispute remains terminal despite later receipt/refund attempts."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory.begin() as db:
            seed_demo(db, AT)
            request = d.create_return(db, "cust-01", "demo-order-03", "demo-item-03",
                                      requested, "stateful receipt", True, "stateful-receipt", AT)
            return_id = request.id
        dispute_seen = False
        for action in actions:
            with factory() as db:
                before = db.get(m.ReturnRequest, return_id)
                expect_dispute = action == "mismatch" and before.status == "return_requested"
            handoff_id = None
            try:
                with factory.begin() as db:
                    proposal = db.scalar(select(m.RefundProposal).where(
                        m.RefundProposal.return_id == return_id))
                    if action in {"match", "mismatch"}:
                        observed = requested if action == "match" else requested + observed_delta
                        outcome = d.submit_warehouse_receipt(db, "warehouse-test", return_id, observed,
                                                             "count checked", AT + timedelta(hours=1),
                                                             AT + timedelta(hours=1))
                        if expect_dispute:
                            assert isinstance(outcome, m.Ticket)
                            assert outcome.return_id == return_id
                            assert outcome.topic == "warehouse receipt discrepancy"
                            handoff_id = outcome.id
                    elif action in {"inspect_pass", "inspect_fail"}:
                        d.record_inspection(db, "warehouse-test", return_id,
                                            action == "inspect_pass", "checked", AT + timedelta(hours=2))
                    elif action == "proposal":
                        d.create_proposal(db, return_id, AT + timedelta(hours=3))
                    elif action == "approve" and proposal:
                        d.decide_proposal(db, "supervisor-test", proposal.id, True,
                                          AT + timedelta(hours=4))
                    elif action == "issue" and proposal:
                        d.issue_refund(db, proposal.id, f"refund:{proposal.id}",
                                       AT + timedelta(hours=5))
            except d.DomainError:
                assert not expect_dispute, "A valid quantity mismatch must create a review ticket"
                pass
            with factory() as db:
                request = db.get(m.ReturnRequest, return_id)
                receipts = db.scalars(select(m.WarehouseReceipt).where(
                    m.WarehouseReceipt.return_id == return_id)).all()
                disputes = db.scalars(select(m.Ticket).where(
                    m.Ticket.return_id == return_id,
                    m.Ticket.topic == "warehouse receipt discrepancy")).all()
                ledgers = db.scalars(select(m.RefundLedger)).all()
                item = db.get(m.OrderItem, "demo-item-03")
                assert len(receipts) <= 1 and len(disputes) <= 1 and len(ledgers) <= 1
                assert item.refunded_cents == sum(ledger.amount_cents for ledger in ledgers)
                if expect_dispute:
                    assert len(disputes) == 1 and disputes[0].id == handoff_id
                    dispute_seen = True
                if dispute_seen:
                    assert len(disputes) == 1
                    assert request.status == "exception" and not receipts and not ledgers
                if disputes:
                    assert request.status == "exception" and not receipts and not ledgers
                    assert disputes[0].customer_id == request.customer_id
                    assert disputes[0].order_id == request.order_id
                    assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                        m.AuditEvent.action == "report_receipt_dispute")) == 1
                    assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                        m.AuditEvent.action == "create_ticket")) == 1
                if receipts:
                    assert receipts[0].quantity == requested and not disputes
                if ledgers:
                    assert not disputes and receipts and request.status == "refund_issued"
    finally:
        engine.dispose()


@settings(max_examples=60, deadline=None)
@example(denial="inspection_failed", actions=["proposal", "inspect_pass", "receipt", "worker"])
@example(denial="supervisor_rejected", actions=["issue", "approve", "reject", "proposal", "worker"])
@given(
    denial=st.sampled_from(("inspection_failed", "supervisor_rejected")),
    actions=st.lists(st.sampled_from(("receipt", "inspect_pass", "inspect_fail", "proposal",
                                      "approve", "reject", "issue", "worker")),
                     min_size=0, max_size=15),
)
def test_terminal_denials_never_issue_refund_after_retries(denial: str, actions: list[str]):
    """A committed failed inspection or rejection cannot be undone by later actions."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory.begin() as db:
            seed_demo(db, AT)
            request = d.create_return(db, "cust-01", "demo-order-03", "demo-item-03", 2,
                                      "terminal denial", True, "terminal-denial", AT)
            return_id = request.id
        with factory.begin() as db:
            d.record_receipt(db, "warehouse-test", return_id, 2, AT + timedelta(hours=1))
        with factory.begin() as db:
            inspection = d.record_inspection(db, "warehouse-test", return_id,
                                             denial != "inspection_failed", "checked", AT + timedelta(hours=2))
            inspection_id = inspection.id
        proposal_id = None
        if denial == "inspection_failed":
            with pytest.raises(d.DomainError, match="Passing inspection required"):
                with factory.begin() as db:
                    d.create_proposal(db, return_id, AT + timedelta(hours=3))
        else:
            with factory.begin() as db:
                proposal = d.create_proposal(db, return_id, AT + timedelta(hours=3))
                proposal_id = proposal.id
            with factory.begin() as db:
                d.decide_proposal(db, "supervisor-test", proposal_id, False, AT + timedelta(hours=4))
            with pytest.raises(d.DomainError, match="Valid supervisor approval required"):
                with factory.begin() as db:
                    d.issue_refund(db, proposal_id, f"refund:{proposal_id}", AT + timedelta(hours=5))

        for action in ["worker", *actions]:
            if action == "worker":
                assert worker.issue_approved_once(session_factory=factory) == []
            else:
                try:
                    with factory.begin() as db:
                        if action == "receipt":
                            d.record_receipt(db, "warehouse-test", return_id, 2, AT + timedelta(hours=1))
                        elif action in ("inspect_pass", "inspect_fail"):
                            d.record_inspection(db, "warehouse-test", return_id,
                                                action == "inspect_pass", "checked", AT + timedelta(hours=2))
                        elif action == "proposal":
                            d.create_proposal(db, return_id, AT + timedelta(hours=3))
                        elif action in ("approve", "reject") and proposal_id:
                            d.decide_proposal(db, "supervisor-test", proposal_id,
                                              action == "approve", AT + timedelta(hours=4))
                        elif action == "issue" and proposal_id:
                            d.issue_refund(db, proposal_id, f"refund:{proposal_id}",
                                           AT + timedelta(hours=5))
                except d.DomainError:
                    # Conflicting or out-of-order calls must roll back completely.
                    pass
            with factory() as db:
                request = db.get(m.ReturnRequest, return_id)
                item = db.get(m.OrderItem, "demo-item-03")
                receipt = db.scalar(select(m.WarehouseReceipt).where(m.WarehouseReceipt.return_id == return_id))
                inspections = db.scalars(select(m.Inspection).where(m.Inspection.receipt_id == receipt.id)).all()
                proposals = db.scalars(select(m.RefundProposal).where(m.RefundProposal.return_id == return_id)).all()
                approvals = db.scalars(select(m.Approval)).all()
                tickets = db.scalars(select(m.Ticket).where(m.Ticket.return_id == return_id)).all()
                assert len(inspections) == 1 and inspections[0].id == inspection_id
                assert len(db.scalars(select(m.RefundLedger)).all()) == 0
                assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                    m.AuditEvent.action == "issue_refund")) == 0
                assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                    m.AuditEvent.action == "record_receipt")) == 1
                assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                    m.AuditEvent.action == "record_inspection")) == 1
                assert item.refunded_quantity == item.refunded_cents == 0
                if denial == "inspection_failed":
                    assert request.status == "exception" and not inspections[0].passed
                    assert not proposals and not approvals
                    assert len(tickets) == 1 and tickets[0].topic == "return inspection exception"
                    assert tickets[0].customer_id == request.customer_id
                    assert tickets[0].order_id == request.order_id
                    assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                        m.AuditEvent.action == "create_ticket")) == 1
                    assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                        m.AuditEvent.action == "create_proposal")) == 0
                else:
                    assert request.status == "rejected" and inspections[0].passed
                    assert len(proposals) == 1 and proposals[0].id == proposal_id
                    assert proposals[0].status == "rejected"
                    assert len(approvals) == 1 and approvals[0].proposal_id == proposal_id
                    assert approvals[0].decision == "rejected" and not tickets
                    assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                        m.AuditEvent.action == "rejected")) == 1
                    assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                        m.AuditEvent.action == "create_proposal")) == 1
    finally:
        engine.dispose()
