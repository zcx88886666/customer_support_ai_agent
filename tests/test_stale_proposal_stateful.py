from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import example, given, settings, strategies as st
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from resolveai import domain as d, models as m, worker
from resolveai.db import Base, make_engine
from resolveai.seed import seed_demo


AT = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
ACTIONS = ("approve_old", "issue_old", "refresh", "approve_new", "issue_new", "worker")


@settings(max_examples=60, deadline=None)
@example(drift="order_version", preapproved=False,
         actions=["approve_old", "issue_old", "refresh", "approve_new", "worker", "issue_old"])
@example(drift="order_version", preapproved=True,
         actions=["worker", "issue_old", "refresh", "approve_new", "issue_new", "worker"])
@example(drift="plan_revision", preapproved=False,
         actions=["approve_old", "refresh", "approve_new", "issue_new", "issue_old"])
@example(drift="plan_revision", preapproved=True,
         actions=["worker", "refresh", "approve_new", "worker", "issue_old"])
@given(drift=st.sampled_from(("order_version", "plan_revision")),
       preapproved=st.booleans(),
       actions=st.lists(st.sampled_from(ACTIONS), min_size=0, max_size=12))
def test_stale_proposal_cannot_issue_and_fresh_approval_can_recover(
        drift: str, preapproved: bool, actions: list[str]):
    """A changed proposal fact invalidates old issuance, even after approval."""
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory.begin() as db:
            seed_demo(db, AT)
            request = d.create_return(db, "cust-01", "demo-order-03", "demo-item-03", 2,
                                      "stale proposal", True, "stale-return", AT)
            return_id = request.id
        with factory.begin() as db:
            d.record_receipt(db, "warehouse-test", return_id, 2, AT + timedelta(hours=1))
            d.record_inspection(db, "warehouse-test", return_id, True, "intact", AT + timedelta(hours=2))
            old = d.create_proposal(db, return_id, AT + timedelta(hours=3))
            old_id = old.id
            if preapproved:
                d.decide_proposal(db, "supervisor-test", old_id, True, AT + timedelta(hours=4))
        with factory.begin() as db:
            if drift == "order_version":
                db.get(m.Order, "demo-order-03").version += 1
            else:
                db.get(m.ReturnRequest, return_id).plan_revision += 1

        # The old proposal is unusable before any generated action runs.
        if not preapproved:
            with pytest.raises(d.DomainError) as denied:
                with factory.begin() as db:
                    d.decide_proposal(db, "supervisor-test", old_id, True, AT + timedelta(hours=5))
            assert denied.value.code == "stale_proposal"
        with pytest.raises(d.DomainError) as denied:
            with factory.begin() as db:
                d.issue_refund(db, old_id, f"refund:{old_id}", AT + timedelta(hours=6))
        assert denied.value.code == ("stale_proposal" if preapproved else "approval_required")

        new_id = None
        new_approved = False
        new_issued = False
        for action in ["worker", *actions]:
            if action == "worker":
                issued = worker.issue_approved_once(session_factory=factory)
                if new_approved and not new_issued:
                    assert len(issued) == 1
                    new_issued = True
                else:
                    assert issued == []
            elif action == "approve_old":
                if preapproved:
                    with factory.begin() as db:
                        assert d.decide_proposal(db, "supervisor-test", old_id, True,
                                                 AT + timedelta(hours=7)).proposal_id == old_id
                else:
                    with pytest.raises(d.DomainError, match="Proposal facts changed|Proposal not pending"):
                        with factory.begin() as db:
                            d.decide_proposal(db, "supervisor-test", old_id, True, AT + timedelta(hours=7))
            elif action == "issue_old":
                with pytest.raises(d.DomainError):
                    with factory.begin() as db:
                        d.issue_refund(db, old_id, f"refund:{old_id}", AT + timedelta(hours=8))
            elif action == "refresh":
                with factory.begin() as db:
                    refreshed = d.create_proposal(db, return_id, AT + timedelta(hours=9))
                    assert refreshed.id != old_id
                    if new_id is not None:
                        assert refreshed.id == new_id
                    new_id = refreshed.id
            elif action == "approve_new" and new_id is not None:
                with factory.begin() as db:
                    assert d.decide_proposal(db, "supervisor-test", new_id, True,
                                             AT + timedelta(hours=10)).proposal_id == new_id
                new_approved = True
            elif action == "issue_new" and new_id is not None:
                if new_approved:
                    with factory.begin() as db:
                        assert d.issue_refund(db, new_id, f"refund:{new_id}",
                                              AT + timedelta(hours=11)).proposal_id == new_id
                    new_issued = True
                else:
                    with pytest.raises(d.DomainError) as denied:
                        with factory.begin() as db:
                            d.issue_refund(db, new_id, f"refund:{new_id}", AT + timedelta(hours=11))
                    assert denied.value.code == "approval_required"

            with factory() as db:
                request = db.get(m.ReturnRequest, return_id)
                old = db.get(m.RefundProposal, old_id)
                proposals = db.scalars(select(m.RefundProposal).where(m.RefundProposal.return_id == return_id)).all()
                approvals = db.scalars(select(m.Approval)).all()
                ledgers = db.scalars(select(m.RefundLedger)).all()
                item = db.get(m.OrderItem, "demo-item-03")
                assert len(proposals) == (2 if new_id else 1)
                assert old.status == ("stale" if new_id else "approved" if preapproved else "pending")
                assert len(approvals) == int(preapproved) + int(new_approved)
                assert len(ledgers) == int(new_issued)
                assert all(row.proposal_id == new_id for row in ledgers)
                assert item.refunded_quantity == (2 if new_issued else 0)
                assert item.refunded_cents == (701 if new_issued else 0)
                assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                    m.AuditEvent.action == "issue_refund")) == int(new_issued)
                if new_id:
                    new = db.get(m.RefundProposal, new_id)
                    assert new.order_version == (3 if drift == "order_version" else 2)
                    assert new.plan_revision == (2 if drift == "plan_revision" else 1)
                    assert new.status == ("issued" if new_issued else "approved" if new_approved else "pending")
                assert request.status == ("refund_issued" if new_issued else "approved" if new_approved
                                          else "proposal_pending" if new_id else "approved" if preapproved
                                          else "proposal_pending")
    finally:
        engine.dispose()
