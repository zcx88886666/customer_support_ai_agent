"""Small, separate synthetic order world for new review scenarios."""

from __future__ import annotations

from datetime import datetime, timedelta

from resolveai import models as m
from resolveai.seed import seed_demo


def seed_case_world(db, clock: datetime, fixture: dict) -> None:
    """Select only the named extra synthetic world for an isolated case."""
    seed_demo(db, clock)
    world = fixture.get("seed_world")
    if world is None:
        return
    if world != "independent_review_v1":
        raise ValueError("Unknown isolated evaluation seed world")
    seed_independent_review_world(db, clock)


def seed_independent_review_world(db, clock: datetime) -> None:
    if db.get(m.Customer, "review-cust-harbor"):
        return
    for customer_id in ("review-cust-harbor", "review-cust-ridge"):
        db.add(m.Customer(id=customer_id, display_name=f"Synthetic {customer_id}"))
        db.add(m.CustomerProfile(customer_id=customer_id))
    db.flush()
    contracts = (
        ("harbor-01", "review-cust-harbor", 2499, 1, True, False, 1, None),
        ("harbor-02", "review-cust-harbor", 3501, 2, True, False, 8, 10),
        ("ridge-01", "review-cust-ridge", 1099, 1, True, False, None, None),
        ("ridge-02", "review-cust-ridge", 4999, 1, False, True, 2, None),
    )
    for slug, customer_id, paid, quantity, returnable, special, delivered_age, favorable in contracts:
        order_id = f"review-order-{slug}"
        item_id = f"review-item-{slug}"
        product_id = f"review-product-{slug}"
        shipment_id = f"review-shipment-{slug}"
        delivered_at = clock - timedelta(days=delivered_age) if delivered_age is not None else None
        shipped_at = delivered_at - timedelta(days=1) if delivered_at else clock - timedelta(days=2)
        db.add(m.Product(id=product_id, seller_id="review-seller", title=f"Synthetic {slug}",
                         returnable=returnable, physical=True, special_notice_accepted=special))
        db.add(m.Order(id=order_id, customer_id=customer_id, seller_id="review-seller",
                       placed_at=clock - timedelta(days=16), status="paid", version=1,
                       currency="CNY", policy_bundle_id="policy-demo-v1",
                       favorable_window_days=favorable))
        db.flush()
        db.add(m.OrderItem(id=item_id, order_id=order_id, product_id=product_id,
                           quantity=quantity, paid_cents=paid))
        db.add(m.Payment(id=f"review-payment-{slug}", order_id=order_id, paid_cents=paid,
                         currency="CNY", method="synthetic_original"))
        db.flush()
        db.add(m.PaidAllocation(order_item_id=item_id, paid_cents=paid))
        db.add(m.Shipment(id=shipment_id, order_id=order_id,
                          status="delivered" if delivered_at else "in_transit",
                          delivered_at=delivered_at, version=1))
        db.flush()
        db.add(m.ShipmentEvent(id=f"review-event-{slug}-shipped", shipment_id=shipment_id,
                               status="shipped", occurred_at=shipped_at))
        if delivered_at:
            db.add(m.ShipmentEvent(id=f"review-event-{slug}-delivered", shipment_id=shipment_id,
                                   status="delivered", occurred_at=delivered_at))
    db.flush()
