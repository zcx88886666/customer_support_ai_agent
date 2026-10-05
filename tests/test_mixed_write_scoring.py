"""Load success requires committed money and approval facts, not HTTP claims."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from resolveai import domain as d, models as m
from resolveai.db import Base, make_engine
from resolveai.seed import seed_demo
from evals.runners.run_mixed_write_load import score_terminal, seed_load_orders


def seeded_session():
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with factory.begin() as db:
        seed_demo(db, now)
    return engine, factory, now


def test_forged_ledger_without_approval_fails_terminal_gate():
    engine, factory, now = seeded_session()
    try:
        with factory.begin() as db:
            request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1,
                                      "synthetic load", True, "load-return-01", now)
            receipt = d.record_receipt(db, "warehouse-load", request.id, 1, now)
            d.record_inspection(db, "warehouse-load", request.id, True, "good", now)
            proposal = d.create_proposal(db, request.id, now)
            db.add(m.RefundLedger(proposal_id=proposal.id, order_item_id="demo-item-01",
                                  amount_cents=proposal.amount_cents, idempotency_key="forged", issued_at=now))
        with factory() as db:
            checks, _ = score_terminal(db, expected_completed=1)
            assert not checks["ledger_requires_approved_proposal"]
    finally:
        engine.dispose()


def test_wrong_item_balance_fails_terminal_gate():
    engine, factory, now = seeded_session()
    try:
        with factory.begin() as db:
            request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1,
                                      "synthetic load", True, "load-return-01", now)
            d.record_receipt(db, "warehouse-load", request.id, 1, now)
            d.record_inspection(db, "warehouse-load", request.id, True, "good", now)
            proposal = d.create_proposal(db, request.id, now)
            d.decide_proposal(db, "supervisor-load", proposal.id, True, now)
            d.issue_refund(db, proposal.id, "refund:" + proposal.id, now)
        with factory() as db:
            checks, _ = score_terminal(db, expected_completed=1)
            assert all(checks.values())
        with factory.begin() as db:
            db.get(m.OrderItem, "demo-item-01").refunded_cents += 1
        with factory() as db:
            checks, _ = score_terminal(db, expected_completed=1)
            assert not checks["ledger_matches_item_balance"]
    finally:
        engine.dispose()


def test_load_seed_respects_foreign_keys_and_paid_allocation():
    engine, factory, now = seeded_session()
    try:
        seed_load_orders(factory, 2, now)
        with factory() as db:
            item = db.get(m.OrderItem, "load-item-00000")
            assert db.get(m.PaidAllocation, item.id).paid_cents == item.paid_cents
            assert d.eligibility(db, "load-customer", item.order_id, item.id, 1, now)["eligible"]
    finally:
        engine.dispose()


def test_swapped_equal_price_ledger_items_fail_ownership_gate():
    engine, factory, now = seeded_session()
    try:
        seed_load_orders(factory, 102, now)
        with factory.begin() as db:
            for index in (0, 101):
                key = str(index).zfill(5)
                request = d.create_return(db, "load-customer", "load-order-" + key, "load-item-" + key,
                                          1, "synthetic load", True, "load-return-" + key, now)
                d.record_receipt(db, "warehouse-load", request.id, 1, now)
                d.record_inspection(db, "warehouse-load", request.id, True, "good", now)
                proposal = d.create_proposal(db, request.id, now)
                d.decide_proposal(db, "supervisor-load", proposal.id, True, now)
                d.issue_refund(db, proposal.id, "refund:" + proposal.id, now)
        with factory() as db:
            assert all(score_terminal(db, expected_completed=2)[0].values())
        with factory.begin() as db:
            ledgers = db.scalars(select(m.RefundLedger).order_by(m.RefundLedger.order_item_id)).all()
            ledgers[0].order_item_id, ledgers[1].order_item_id = ledgers[1].order_item_id, ledgers[0].order_item_id
        with factory() as db:
            checks, _ = score_terminal(db, expected_completed=2)
            assert not all(checks.values())
    finally:
        engine.dispose()


def test_k6_cleanup_timeout_still_cleans_postgres_and_reports(monkeypatch, tmp_path):
    import subprocess
    from evals.runners import run_mixed_write_load as runner

    cleaned = []

    class FakeServer:
        def __init__(self, run_id, report_dir):
            self.report_dir = report_dir
            self.name, self.volume = "owned-test", "owned-test-data"
            self.container_id = self.image_id = ""

        def create(self):
            raise RuntimeError("synthetic setup failure")

        def cleanup(self, success):
            cleaned.append(success)
            return {"container": self.name, "volume": self.volume, "removed": False}

    monkeypatch.setattr(runner, "REPORT_ROOT", tmp_path)
    monkeypatch.setattr(runner, "DisposablePostgres", FakeServer)
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "test-commit")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("docker inspect", 10)))
    assert runner.main() == 1
    assert cleaned == [False]
    assert len(list(tmp_path.glob("*/summary.json"))) == 1
