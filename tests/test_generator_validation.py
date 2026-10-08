"""Semantic fixture checks must catch inconsistent files even with matching hashes."""

import csv
import hashlib
import json
from datetime import datetime, timezone

import pytest

from data.generator.generate import generate
from data.generator.validate import validate


@pytest.mark.parametrize("mutation", ["in_transit_with_delivery", "delivered_event_time_disagrees"])
def test_validator_rejects_semantically_invalid_shipment_with_updated_file_hash(tmp_path, mutation):
    folder = tmp_path / mutation
    generate(folder, 25, 20260929, datetime(2026, 9, 29, tzinfo=timezone.utc))
    assert validate(folder)["violation_count"] == 0

    path = folder / "shipments.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        shipments = list(reader)
    if mutation == "in_transit_with_delivery":
        shipment = next(row for row in shipments if row["status"] == "in_transit")
        shipment["delivered_at"] = "2026-09-01T12:00:00+00:00"
    else:
        shipment = next(row for row in shipments if row["status"] == "delivered")
        shipment["delivered_at"] = "2026-09-01T12:00:00+00:00"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(shipments)
    report_path = folder / "data_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["sha256"]["shipments"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")

    result = validate(folder)
    assert result["file_hashes_match"]
    assert any(value.startswith("delivery mismatch") for value in result["violations"])


def test_validator_rejects_duplicate_return_even_when_report_count_and_hash_match(tmp_path):
    folder = tmp_path / "duplicate-return"
    generate(folder, 25, 20260929, datetime(2026, 9, 29, tzinfo=timezone.utc))
    path = folder / "return_requests.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        request = next(reader)
    with path.open("a", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=fields).writerow(request)
    report_path = folder / "data_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["counts"]["return_requests"] += 1
    report["sha256"]["return_requests"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")

    result = validate(folder)
    assert result["file_hashes_match"]
    assert any(value.startswith("duplicate return_requests") for value in result["violations"])


def test_validator_reports_missing_shipment_without_crashing(tmp_path):
    folder = tmp_path / "missing-shipment"
    generate(folder, 25, 20260929, datetime(2026, 9, 29, tzinfo=timezone.utc))
    path = folder / "shipments.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        shipments = list(reader)
    missing_order = shipments[-1]["order_id"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(shipments[:-1])
    report_path = folder / "data_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["counts"]["shipments"] -= 1
    report["sha256"]["shipments"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")

    result = validate(folder)
    assert result["file_hashes_match"]
    assert any(missing_order in value for value in result["violations"])
    assert json.loads(report_path.read_text(encoding="utf-8"))["quality_validation"] == result


def test_validator_rejects_extra_shipment_after_last_order(tmp_path):
    folder = tmp_path / "extra-shipment"
    generate(folder, 25, 20260929, datetime(2026, 9, 29, tzinfo=timezone.utc))
    path = folder / "shipments.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        last_shipment = list(reader)[-1]
    with path.open("a", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=fields).writerow(last_shipment)
    report_path = folder / "data_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["counts"]["shipments"] += 1
    report["sha256"]["shipments"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")

    result = validate(folder)
    assert result["file_hashes_match"]
    assert any(value.startswith("extra shipment") for value in result["violations"])


@pytest.mark.parametrize("name", ["customers", "products"])
def test_validator_rejects_duplicate_root_entity_with_matching_report(tmp_path, name):
    folder = tmp_path / name
    generate(folder, 25, 20260929, datetime(2026, 9, 29, tzinfo=timezone.utc))
    path = folder / f"{name}.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        duplicate = next(reader)
    with path.open("a", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=fields).writerow(duplicate)
    report_path = folder / "data_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["counts"][name] += 1
    report["sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")

    result = validate(folder)
    assert result["file_hashes_match"]
    assert any(value.startswith(f"duplicate {name}") for value in result["violations"])


def test_validator_reports_missing_product_referenced_by_return(tmp_path):
    folder = tmp_path / "missing-return-product"
    generate(folder, 25, 20260929, datetime(2026, 9, 29, tzinfo=timezone.utc))
    with (folder / "return_requests.csv").open(newline="", encoding="utf-8") as handle:
        request = next(csv.DictReader(handle))
    with (folder / "order_items.csv").open(newline="", encoding="utf-8") as handle:
        item = next(row for row in csv.DictReader(handle) if row["id"] == request["order_item_id"])

    path = folder / "products.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        products = [row for row in reader if row["id"] != item["product_id"]]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(products)
    report_path = folder / "data_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["counts"]["products"] = len(products)
    report["sha256"]["products"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")

    result = validate(folder)
    assert result["file_hashes_match"]
    assert any("return chain mismatch" in value for value in result["violations"])


def test_validator_rejects_duplicate_item_with_consistent_payment_and_allocation(tmp_path):
    folder = tmp_path / "duplicate-item"
    generate(folder, 25, 20260929, datetime(2026, 9, 29, tzinfo=timezone.utc))

    def read_csv(name):
        with (folder / f"{name}.csv").open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            return reader.fieldnames, list(reader)

    def write_csv(name, fields, values):
        path = folder / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(values)
        return path

    item_fields, items = read_csv("order_items")
    duplicate = dict(items[0])
    items.insert(2, duplicate)
    item_path = write_csv("order_items", item_fields, items)
    allocation_fields, allocations = read_csv("paid_allocations")
    allocations.insert(2, dict(allocations[0]))
    allocation_path = write_csv("paid_allocations", allocation_fields, allocations)
    payment_fields, payments = read_csv("payments")
    payments[0]["paid_cents"] = str(int(payments[0]["paid_cents"]) + int(duplicate["paid_cents"]))
    payment_path = write_csv("payments", payment_fields, payments)

    report_path = folder / "data_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    for name, path in (("order_items", item_path), ("paid_allocations", allocation_path), ("payments", payment_path)):
        report["sha256"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    report["counts"]["order_items"] += 1
    report["counts"]["paid_allocations"] += 1
    report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")

    result = validate(folder)
    assert result["file_hashes_match"]
    assert any("duplicate item" in value for value in result["violations"])


def test_validator_rejects_duplicate_event_id_with_updated_report(tmp_path):
    folder = tmp_path / "duplicate-event"
    generate(folder, 25, 20260929, datetime(2026, 9, 29, tzinfo=timezone.utc))
    path = folder / "shipment_events.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        events = list(reader)
    events[1]["id"] = events[0]["id"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(events)
    report_path = folder / "data_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["sha256"]["shipment_events"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")

    result = validate(folder)
    assert result["file_hashes_match"]
    assert any("duplicate shipment event" in value for value in result["violations"])


def test_validator_rejects_duplicate_conversation_id_with_updated_report(tmp_path):
    folder = tmp_path / "duplicate-message"
    generate(folder, 25, 20260929, datetime(2026, 9, 29, tzinfo=timezone.utc))
    path = folder / "conversation_messages.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        messages = list(reader)
    messages[1]["id"] = messages[0]["id"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(messages)
    report_path = folder / "data_quality_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["sha256"]["conversation_messages"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report) + "\n", encoding="utf-8")

    result = validate(folder)
    assert result["file_hashes_match"]
    assert any("duplicate conversation message" in value for value in result["violations"])
