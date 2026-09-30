"""Transactional PostgreSQL COPY of a validated synthetic CSV world."""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
from pathlib import Path

import psycopg

from validate import validate


ORDER = (
    "customers", "customer_profiles", "products", "orders", "order_items",
    "payments", "paid_allocations", "shipments", "shipment_events",
    "return_requests", "warehouse_receipts", "inspections", "refund_proposals",
    "approvals", "refund_ledger", "tickets", "conversation_messages",
)


def import_world(folder: Path, database_url: str):
    quality = validate(folder)
    if quality["violation_count"]:
        raise RuntimeError("Data quality gate failed")
    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    if not url.startswith("postgresql://"):
        raise RuntimeError("COPY importer requires PostgreSQL")
    started = time.perf_counter()
    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM policy_bundles WHERE id = %s", ("policy-demo-v1",))
            if not cursor.fetchone():
                raise RuntimeError("Seed policy-demo-v1 before importing generated orders")
            for table in ORDER:
                path = folder / f"{table}.csv"
                with path.open(newline="", encoding="utf-8") as handle:
                    columns = next(csv.reader(handle))
                if not columns:
                    raise RuntimeError(f"No columns in {table}")
                quoted = ", ".join('"' + name.replace('"', '""') + '"' for name in columns)
                with cursor.copy(f'COPY "{table}" ({quoted}) FROM STDIN WITH (FORMAT CSV, HEADER true)') as copy:
                    with path.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            copy.write(chunk)
    report_path = folder / "import_report.json"
    report_path.write_text(json.dumps({"tables": list(ORDER), "rows": json.loads((folder / "data_quality_report.json").read_text())["counts"], "import_seconds": round(time.perf_counter() - started, 3)}, indent=2) + "\n", encoding="utf-8")
    return report_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL required")
    print(import_world(args.folder, url))
