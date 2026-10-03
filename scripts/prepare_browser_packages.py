"""Add a second synthetic package to owned demo order 02 in an isolated DB."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from resolveai import models as m
from resolveai.db import SessionLocal


def main() -> None:
    with SessionLocal.begin() as db:
        assert db.get(m.Shipment, "browser-package-02b") is None, "Use a fresh isolated database"
        order = db.get(m.Order, "demo-order-02")
        assert order is not None and order.customer_id == "cust-01"
        delivered = datetime.now(timezone.utc) - timedelta(days=1)
        db.add(m.Shipment(id="browser-package-02b", order_id=order.id, status="delivered", delivered_at=delivered, version=1))
        db.flush()
        db.add(m.ShipmentEvent(id="browser-package-02b-shipped", shipment_id="browser-package-02b", status="shipped", occurred_at=delivered - timedelta(days=2)))
        db.add(m.ShipmentEvent(id="browser-package-02b-delivered", shipment_id="browser-package-02b", status="delivered", occurred_at=delivered))
    print(json.dumps({"order_id": "demo-order-02", "new_shipment_id": "browser-package-02b"}))


if __name__ == "__main__":
    main()
