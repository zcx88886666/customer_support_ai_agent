from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import models as m
from .db import SessionLocal, init_db


DEMO_CLOCK = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def seed_demo(db, clock: datetime = DEMO_CLOCK):
    if db.get(m.Customer, "cust-01"):
        return
    for customer_id in ("cust-01", "cust-02"):
        db.add(m.Customer(id=customer_id, display_name=f"Synthetic {customer_id}"))
        db.add(m.CustomerProfile(customer_id=customer_id))
    db.flush()
    bundle = m.PolicyBundle(id="policy-demo-v1", status="active", effective_from=clock - timedelta(days=365), window_days=7, content_hash="pending", active=True)
    db.add(bundle)
    db.flush()
    clauses = [
        ("window", "七日无理由退货申请期", "已签收合格实物商品，自签收次日起七个自然日内可申请退货。"),
        ("exceptions", "例外商品", "非实物及已明确告知并确认的特殊商品不适用无理由退货。"),
        ("inspection", "仓库质检", "实际收到退货后核对数量与商品状态；异常转人工。"),
        ("refund", "模拟退款", "仓库质检通过并经主管批准后，按实付分摊额模拟原路退款。"),
    ]
    clause_rows = []
    for slug, title, body in clauses:
        clause = m.PolicyClause(id=f"clause-{slug}", bundle_id="policy-demo-v1", title=title, body=body)
        clause_rows.append(clause)
        db.add(clause)
    from .policy import fingerprint
    bundle.content_hash = fingerprint(bundle, clause_rows)
    for index in range(1, 26):
        customer_id = "cust-02" if index % 5 == 0 else "cust-01"
        order_id = f"demo-order-{index:02d}"
        item_id = f"demo-item-{index:02d}"
        product_id = f"demo-product-{index:02d}"
        paid = 1001 + index * 17
        db.add(m.Product(id=product_id, seller_id="demo-seller", title=f"Synthetic product {index}", returnable=index not in (7, 17), physical=True, special_notice_accepted=index in (7, 17)))
        db.add(m.Order(id=order_id, customer_id=customer_id, seller_id="demo-seller", placed_at=clock - timedelta(days=12 + index), status="paid", version=1, currency="CNY", policy_bundle_id="policy-demo-v1", favorable_window_days=10 if index == 11 else None))
        db.flush()
        db.add(m.OrderItem(id=item_id, order_id=order_id, product_id=product_id, quantity=3 if index == 3 else 1, paid_cents=paid))
        db.add(m.Payment(id=f"demo-payment-{index:02d}", order_id=order_id, paid_cents=paid, currency="CNY", method="synthetic_original"))
        db.flush()
        db.add(m.PaidAllocation(order_item_id=item_id, paid_cents=paid))
        status = "in_transit" if index in (2, 12, 22) else "delivered"
        age_days = 7 if index in (4, 14) else 8 if index in (8, 18) else 2
        delivered_at = None if status != "delivered" else clock - timedelta(days=age_days)
        shipment_id = f"demo-shipment-{index:02d}"
        db.add(m.Shipment(id=shipment_id, order_id=order_id, status=status, delivered_at=delivered_at, version=1))
        db.flush()
        db.add(m.ShipmentEvent(id=f"demo-event-{index:02d}-1", shipment_id=shipment_id, status="shipped", occurred_at=clock - timedelta(days=9)))
        if delivered_at:
            db.add(m.ShipmentEvent(id=f"demo-event-{index:02d}-2", shipment_id=shipment_id, status="delivered", occurred_at=delivered_at))
    db.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clock", default=DEMO_CLOCK.isoformat())
    args = parser.parse_args()
    init_db()
    with SessionLocal.begin() as db:
        seed_demo(db, datetime.fromisoformat(args.clock))
    print("seeded 25 demo orders")


if __name__ == "__main__":
    main()
