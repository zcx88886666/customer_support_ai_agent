"""Deterministic, bounded-memory synthetic world generator.

Writes CSV files for PostgreSQL COPY. The generated facts are synthetic; profile
sizes are targets until a run and its report are measured on a specific machine.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import time
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROFILES = {"demo": 25, "realistic": 100_000, "scale": 1_000_000}
HEADERS = {
    "customers": ["id", "display_name"],
    "customer_profiles": ["customer_id", "language", "channel", "memory_consent"],
    "products": ["id", "seller_id", "title", "returnable", "physical", "special_notice_accepted"],
    "orders": ["id", "customer_id", "seller_id", "placed_at", "status", "version", "currency", "policy_bundle_id", "favorable_window_days"],
    "order_items": ["id", "order_id", "product_id", "quantity", "paid_cents", "refunded_cents", "refunded_quantity"],
    "payments": ["id", "order_id", "paid_cents", "method", "currency"],
    "paid_allocations": ["order_item_id", "paid_cents"],
    "shipments": ["id", "order_id", "status", "delivered_at", "version"],
    "shipment_events": ["id", "shipment_id", "status", "occurred_at"],
    "return_requests": ["id", "order_id", "customer_id", "order_item_id", "quantity", "reason", "status", "created_at", "idempotency_key", "plan_revision", "policy_bundle_id"],
    "warehouse_receipts": ["id", "return_id", "quantity", "received_at", "actor_id"],
    "inspections": ["id", "receipt_id", "passed", "note", "inspected_at"],
    "refund_proposals": ["id", "return_id", "inspection_id", "order_version", "plan_revision", "policy_bundle_id", "amount_cents", "status", "created_at"],
    "approvals": ["id", "proposal_id", "actor_id", "decision", "decided_at"],
    "refund_ledger": ["id", "proposal_id", "order_item_id", "amount_cents", "currency", "idempotency_key", "issued_at"],
    "tickets": ["id", "customer_id", "order_id", "topic", "status", "support_actor_id"],
    "conversation_messages": ["id", "ticket_id", "actor_type", "body", "created_at"],
}


def generate(output: Path, count: int, seed: int, clock: datetime):
    output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(seed)
    began = time.perf_counter()
    customer_count = max(2, count // 6)
    product_count = max(25, count // 40)
    counts = {name: 0 for name in HEADERS}
    with ExitStack() as stack:
        writers = {}
        for name, headers in HEADERS.items():
            handle = stack.enter_context((output / f"{name}.csv").open("w", newline="", encoding="utf-8"))
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            writers[name] = writer

        def write(name: str, row: dict):
            writers[name].writerow(row)
            counts[name] += 1

        for i in range(customer_count):
            write("customers", {"id": f"gen-customer-{i:08d}", "display_name": f"Synthetic customer {i}"})
            write("customer_profiles", {"customer_id": f"gen-customer-{i:08d}", "language": "zh-CN", "channel": "web", "memory_consent": "false"})
        for i in range(product_count):
            write("products", {"id": f"gen-product-{i:07d}", "seller_id": f"seller-{i % 19:02d}", "title": f"Synthetic product {i}", "returnable": "true" if i % 23 else "false", "physical": "true", "special_notice_accepted": "true" if i % 23 == 0 else "false"})
        for i in range(count):
            order_id = f"gen-order-{i:09d}"
            customer_id = f"gen-customer-{i % customer_count:08d}"
            placed = clock - timedelta(days=30 + i % 330, hours=i % 24)
            seller = f"seller-{i % 19:02d}"
            in_transit = i % 29 == 0
            product_index = ((i // 19) % max(1, product_count // 19)) * 19 + i % 19
            completed_return = i % 40 == 1 and not in_transit and product_index % 23 != 0
            write("orders", {"id": order_id, "customer_id": customer_id, "seller_id": seller, "placed_at": placed.isoformat(), "status": "paid", "version": 3 if completed_return else 1, "currency": "CNY", "policy_bundle_id": "policy-demo-v1", "favorable_window_days": ""})
            items = 2 if i % 4 == 0 else 1
            paid_total = 0
            return_amount = 0
            first_item_id = f"gen-item-{i:09d}-0"
            for j in range(items):
                item_id = f"gen-item-{i:09d}-{j}"
                paid = 500 + rng.randrange(1, 30000)
                paid_total += paid
                quantity = 1 + i % 3
                if j == 0 and completed_return:
                    return_amount = paid // quantity
                write("order_items", {"id": item_id, "order_id": order_id, "product_id": f"gen-product-{(product_index + j) % product_count:07d}", "quantity": quantity, "paid_cents": paid, "refunded_cents": return_amount if j == 0 and completed_return else 0, "refunded_quantity": 1 if j == 0 and completed_return else 0})
                write("paid_allocations", {"order_item_id": item_id, "paid_cents": paid})
            write("payments", {"id": f"gen-payment-{i:09d}", "order_id": order_id, "paid_cents": paid_total, "method": "synthetic_original", "currency": "CNY"})
            shipment_id = f"gen-shipment-{i:09d}"
            delivered = placed + timedelta(days=2 + i % 7)
            write("shipments", {"id": shipment_id, "order_id": order_id, "status": "in_transit" if in_transit else "delivered", "delivered_at": "" if in_transit else delivered.isoformat(), "version": 1})
            journey = delivered - placed
            for step, (status_step, fraction) in enumerate((("packed", 0.05), ("shipped", 0.1), ("hub_departed", 0.25), ("hub_arrived", 0.5), ("out_for_delivery", 0.75))):
                write("shipment_events", {"id": f"gen-event-{i:09d}-{step}", "shipment_id": shipment_id, "status": status_step, "occurred_at": (placed + journey * fraction).isoformat()})
            if not in_transit:
                write("shipment_events", {"id": f"gen-event-{i:09d}-5", "shipment_id": shipment_id, "status": "delivered", "occurred_at": delivered.isoformat()})
            if completed_return:
                return_id = f"gen-return-{i:09d}"
                receipt_id = f"gen-receipt-{i:09d}"
                inspection_id = f"gen-inspection-{i:09d}"
                proposal_id = f"gen-proposal-{i:09d}"
                requested_at = delivered + timedelta(days=1)
                received_at = requested_at + timedelta(days=3)
                inspected_at = received_at + timedelta(hours=1)
                proposed_at = inspected_at + timedelta(hours=1)
                decided_at = proposed_at + timedelta(hours=1)
                issued_at = decided_at + timedelta(hours=1)
                write("return_requests", {"id": return_id, "order_id": order_id, "customer_id": customer_id, "order_item_id": first_item_id, "quantity": 1, "reason": "synthetic preference change", "status": "refund_issued", "created_at": requested_at.isoformat(), "idempotency_key": f"gen-return-key-{i:09d}", "plan_revision": 1, "policy_bundle_id": "policy-demo-v1"})
                write("warehouse_receipts", {"id": receipt_id, "return_id": return_id, "quantity": 1, "received_at": received_at.isoformat(), "actor_id": "synthetic-warehouse"})
                write("inspections", {"id": inspection_id, "receipt_id": receipt_id, "passed": "true", "note": "intact", "inspected_at": inspected_at.isoformat()})
                write("refund_proposals", {"id": proposal_id, "return_id": return_id, "inspection_id": inspection_id, "order_version": 2, "plan_revision": 1, "policy_bundle_id": "policy-demo-v1", "amount_cents": return_amount, "status": "issued", "created_at": proposed_at.isoformat()})
                write("approvals", {"id": f"gen-approval-{i:09d}", "proposal_id": proposal_id, "actor_id": "synthetic-supervisor", "decision": "approved", "decided_at": decided_at.isoformat()})
                write("refund_ledger", {"id": f"gen-ledger-{i:09d}", "proposal_id": proposal_id, "order_item_id": first_item_id, "amount_cents": return_amount, "currency": "CNY", "idempotency_key": f"gen-refund-key-{i:09d}", "issued_at": issued_at.isoformat()})
            if i % 15 == 0:
                ticket_id = f"gen-ticket-{i:09d}"
                write("tickets", {"id": ticket_id, "customer_id": customer_id, "order_id": order_id, "topic": "synthetic delivery inquiry", "status": "closed", "support_actor_id": "synthetic-support"})
                write("conversation_messages", {"id": f"gen-message-{i:09d}-0", "ticket_id": ticket_id, "actor_type": "customer", "body": "Where is my synthetic parcel?", "created_at": (placed + timedelta(hours=1)).isoformat()})
                write("conversation_messages", {"id": f"gen-message-{i:09d}-1", "ticket_id": ticket_id, "actor_type": "support", "body": "We checked the synthetic shipment status.", "created_at": (placed + timedelta(hours=2)).isoformat()})
    checksums = {}
    for name in HEADERS:
        digest = hashlib.sha256()
        with (output / f"{name}.csv").open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        checksums[name] = digest.hexdigest()
    report = {"provenance": "synthetic", "seed": seed, "clock": clock.isoformat(), "orders_requested": count, "counts": counts, "sha256": checksums, "generation_seconds": round(time.perf_counter() - began, 3), "quality_validation": "not_run", "assumptions": ["Distribution parameters are synthetic assumptions; no external calibration was used."]}
    (output / "data_quality_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=PROFILES, default="demo")
    parser.add_argument("--count", type=int)
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--clock", default="2026-09-29T12:00:00+00:00")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    count = args.count or PROFILES[args.profile]
    if count < 1:
        raise SystemExit("count must be positive")
    print(json.dumps(generate(args.output, count, args.seed, datetime.fromisoformat(args.clock))))


if __name__ == "__main__":
    main()
