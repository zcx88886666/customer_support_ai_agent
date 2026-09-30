"""Independent streaming checks for generated CSV relationships and money."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime
from itertools import groupby
from pathlib import Path


def rows(path: Path):
    with path.open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def validate(folder: Path) -> dict:
    report_path = folder / "data_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    violations = []
    customers = {row["id"] for row in rows(folder / "customers.csv")}
    profiles = {row["customer_id"] for row in rows(folder / "customer_profiles.csv")}
    products = {row["id"]: row for row in rows(folder / "products.csv")}
    if customers != profiles:
        violations.append("customer profile mismatch")
    returns = {row["order_id"]: row for row in rows(folder / "return_requests.csv")}
    receipts = {row["return_id"]: row for row in rows(folder / "warehouse_receipts.csv")}
    inspections = {row["receipt_id"]: row for row in rows(folder / "inspections.csv")}
    proposals = {row["return_id"]: row for row in rows(folder / "refund_proposals.csv")}
    approvals = {row["proposal_id"]: row for row in rows(folder / "approvals.csv")}
    ledgers = {row["proposal_id"]: row for row in rows(folder / "refund_ledger.csv")}
    tickets = {row["order_id"]: row for row in rows(folder / "tickets.csv")}
    messages = {key: list(group) for key, group in groupby(rows(folder / "conversation_messages.csv"), key=lambda row: row["ticket_id"])}
    order_rows = rows(folder / "orders.csv")
    payment_rows = rows(folder / "payments.csv")
    shipment_rows = rows(folder / "shipments.csv")
    item_groups = groupby(rows(folder / "order_items.csv"), key=lambda row: row["order_id"])
    allocation_rows = rows(folder / "paid_allocations.csv")
    event_groups = groupby(rows(folder / "shipment_events.csv"), key=lambda row: row["shipment_id"])
    checked = 0
    for order in order_rows:
        checked += 1
        order_id = order["id"]
        if order["customer_id"] not in customers or order["currency"] != "CNY" or int(order["version"]) < 1:
            violations.append(f"invalid order {order_id}")
        group_id, items_iter = next(item_groups)
        items = list(items_iter)
        if group_id != order_id:
            violations.append(f"item group mismatch {order_id}")
        paid_total = 0
        for item in items:
            allocation = next(allocation_rows)
            if item["product_id"] not in products or allocation["order_item_id"] != item["id"] or int(allocation["paid_cents"]) != int(item["paid_cents"]):
                violations.append(f"allocation mismatch {item['id']}")
            if int(item["quantity"]) < 1 or int(item["refunded_cents"]) > int(item["paid_cents"]):
                violations.append(f"invalid item {item['id']}")
            paid_total += int(item["paid_cents"])
        payment = next(payment_rows)
        if payment["order_id"] != order_id or payment["currency"] != "CNY" or int(payment["paid_cents"]) != paid_total:
            violations.append(f"payment mismatch {order_id}")
        shipment = next(shipment_rows)
        if shipment["order_id"] != order_id:
            violations.append(f"shipment mismatch {order_id}")
        event_id, events_iter = next(event_groups)
        events = list(events_iter)
        if event_id != shipment["id"]:
            violations.append(f"event mismatch {order_id}")
        times = [datetime.fromisoformat(event["occurred_at"]) for event in events]
        if times != sorted(times) or times[0] < datetime.fromisoformat(order["placed_at"]):
            violations.append(f"event time mismatch {order_id}")
        if shipment["status"] == "delivered" and (not shipment["delivered_at"] or not any(event["status"] == "delivered" for event in events)):
            violations.append(f"delivery mismatch {order_id}")
        request = returns.pop(order_id, None)
        if request:
            receipt = receipts.pop(request["id"], None)
            inspection = inspections.pop(receipt["id"], None) if receipt else None
            proposal = proposals.pop(request["id"], None)
            approval = approvals.pop(proposal["id"], None) if proposal else None
            ledger = ledgers.pop(proposal["id"], None) if proposal else None
            first = items[0]
            expected = int(first["paid_cents"]) // int(first["quantity"])
            timeline = [request.get("created_at"), receipt.get("received_at") if receipt else None, inspection.get("inspected_at") if inspection else None, proposal.get("created_at") if proposal else None, approval.get("decided_at") if approval else None, ledger.get("issued_at") if ledger else None]
            if (not all(timeline) or [datetime.fromisoformat(value) for value in timeline] != sorted(datetime.fromisoformat(value) for value in timeline)
                    or request["customer_id"] != order["customer_id"] or request["order_item_id"] != first["id"]
                    or order["version"] != "3" or shipment["status"] != "delivered"
                    or products[first["product_id"]]["seller_id"] != order["seller_id"]
                    or products[first["product_id"]]["returnable"] != "true"
                    or receipt["quantity"] != request["quantity"] or inspection["passed"] != "true"
                    or proposal["order_version"] != "2" or proposal["inspection_id"] != inspection["id"]
                    or approval["decision"] != "approved" or ledger["amount_cents"] != str(expected)
                    or proposal["amount_cents"] != str(expected) or first["refunded_cents"] != str(expected)
                    or first["refunded_quantity"] != "1"):
                violations.append(f"return chain mismatch {order_id}")
        elif any(int(item["refunded_cents"]) or int(item["refunded_quantity"]) for item in items):
            violations.append(f"unbacked refund {order_id}")
        ticket = tickets.pop(order_id, None)
        if ticket:
            conversation = messages.pop(ticket["id"], [])
            if ticket["customer_id"] != order["customer_id"] or len(conversation) != 2 or [entry["actor_type"] for entry in conversation] != ["customer", "support"]:
                violations.append(f"ticket mismatch {order_id}")
        if len(violations) >= 100:
            break
    if any((returns, receipts, inspections, proposals, approvals, ledgers, tickets, messages)):
        violations.append("orphan after-sales or conversation records")
    def file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    hashes_match = all(file_hash(folder / f"{name}.csv") == digest for name, digest in report["sha256"].items())
    if not hashes_match:
        violations.append("file hash mismatch")
    if checked != report["counts"]["orders"]:
        violations.append("order count mismatch")
    summary = {"orders_checked": checked, "violation_count": len(violations), "violations": violations[:100], "file_hashes_match": hashes_match}
    report["quality_validation"] = summary
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    result = validate(args.folder)
    print(json.dumps(result))
    raise SystemExit(0 if result["violation_count"] == 0 else 1)
