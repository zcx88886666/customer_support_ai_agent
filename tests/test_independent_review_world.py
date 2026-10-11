"""Independent review fixtures must carry their own causal order facts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from resolveai import models as m
from resolveai.db import Base, make_engine
from resolveai.seed import seed_demo
from evals.fixtures.independent_review_world import seed_independent_review_world
from evals.runners.run_smoke import run_case
from evals.runners.run_core_business import run_case as run_core_case


CLOCK = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def test_review_world_has_distinct_owned_orders_and_causal_payments():
    engine = make_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            seed_demo(db, CLOCK)
            seed_independent_review_world(db, CLOCK)
            seed_independent_review_world(db, CLOCK)
            db.commit()
        with Session(engine) as db:
            orders = db.scalars(select(m.Order).where(m.Order.id.like("review-order-%"))).all()
            assert len(orders) == 4
            assert {order.customer_id for order in orders} == {"review-cust-harbor", "review-cust-ridge"}
            for order in orders:
                item = db.scalar(select(m.OrderItem).where(m.OrderItem.order_id == order.id))
                allocation = db.get(m.PaidAllocation, item.id)
                payment = db.scalar(select(m.Payment).where(m.Payment.order_id == order.id))
                shipment = db.scalar(select(m.Shipment).where(m.Shipment.order_id == order.id))
                assert item.paid_cents == allocation.paid_cents == payment.paid_cents
                assert shipment.status == "in_transit" or shipment.delivered_at is not None
            assert db.get(m.Order, "review-order-harbor-01").customer_id != db.get(m.Order, "demo-order-01").customer_id
    finally:
        engine.dispose()


def test_smoke_replay_can_use_review_world_without_touching_demo_orders():
    case = {"schema_version": "v1", "case_id": "review-world-shipment-probe",
            "suite": "smoke_demo", "split": "dev", "risk_tier": "normal",
            "fixture": {"customer_id": "review-cust-harbor",
                        "order_id": "review-order-harbor-01",
                        "seed_world": "independent_review_v1"},
            "dialogue_script": [{"message": "这笔订单的物流状态是什么？"}],
            "gold": {"route": "knowledge", "specialists": ["order"],
                     "ledger_count": 0}}
    result = run_case(case, "single", "review-world-probe", CLOCK)
    assert result["http_status"] == 200
    assert result["status"] == "pass"


def test_special_notice_review_has_no_return_or_refund_in_terminal_replay():
    source = Path("evals/datasets/core_business_dev_v1.jsonl")
    original = next(json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()
                    if '"case_id":"core-chat-expired-window"' in line)
    case = {**original, "case_id": "review-ridge-product-exception-probe",
            "fixture": {"customer_id": "review-cust-ridge", "order_id": "review-order-ridge-02",
                        "item_id": "review-item-ridge-02", "reason": "not suitable",
                        "seed_world": "independent_review_v1"},
            "dialogue_script": [{"message": "我要退这件商品", "item_id": "review-item-ridge-02",
                                 "quantity": 1, "reason": "not suitable", "confirmed": True}],
            "terminal_scenario": "product_exception"}
    case["gold"] = {**original["gold"], "chat_reason_code": "product_exception",
                    "http_statuses": {"product_exception_refused": 202}}
    result = run_core_case(case, "review-world-probe", CLOCK)
    assert result["status"] == "pass"
    assert result["checks"]["chat_review_ticket"]
    assert result["checks"]["refund_authorized"]
