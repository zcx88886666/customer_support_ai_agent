"""Read-only checks for a migrated ResolveAI PostgreSQL database."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    "customers", "orders", "order_items", "shipments", "shipment_events",
    "return_requests", "refund_proposals", "approvals", "refund_ledger",
)
CHECKS = {
    "orphan_orders": "SELECT count(*) FROM orders o LEFT JOIN customers c ON c.id = o.customer_id WHERE c.id IS NULL",
    "return_customer_mismatch": "SELECT count(*) FROM return_requests r JOIN orders o ON o.id = r.order_id WHERE r.customer_id <> o.customer_id",
    "paid_allocation_mismatch": "SELECT count(*) FROM order_items i LEFT JOIN paid_allocations a ON a.order_item_id = i.id WHERE a.order_item_id IS NULL OR a.paid_cents <> i.paid_cents",
    "over_refunded_items": "SELECT count(*) FROM order_items WHERE refunded_cents < 0 OR refunded_cents > paid_cents OR refunded_quantity < 0 OR refunded_quantity > quantity",
    "unapproved_refunds": "SELECT count(*) FROM refund_ledger l JOIN refund_proposals p ON p.id = l.proposal_id LEFT JOIN approvals a ON a.proposal_id = p.id WHERE a.decision IS DISTINCT FROM 'approved'",
    "refund_balance_exceeded": "SELECT count(*) FROM (SELECT r.order_id, sum(l.amount_cents) AS refunded FROM refund_ledger l JOIN refund_proposals p ON p.id = l.proposal_id JOIN return_requests r ON r.id = p.return_id GROUP BY r.order_id) x JOIN payments pay ON pay.order_id = x.order_id WHERE x.refunded > pay.paid_cents",
}


def expected_migration_head() -> str:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "infra/migrations"))
    return ScriptDirectory.from_config(config).get_current_head()


def verify(database_url: str, expected_orders: int | None = None) -> dict:
    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    if not url.startswith("postgresql://"):
        raise ValueError("DATABASE_URL must point to PostgreSQL")
    with psycopg.connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT current_setting('server_version'), current_database()")
            server_version, database = cursor.fetchone()
            cursor.execute("SELECT version_num FROM alembic_version")
            migration = cursor.fetchone()[0]
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            vector_row = cursor.fetchone()
            vector_version = vector_row[0] if vector_row else None
            if vector_version:
                cursor.execute("SELECT '[1,2,3]'::vector <-> '[2,2,3]'::vector")
                vector_distance = float(cursor.fetchone()[0])
            else:
                vector_distance = None
            counts = {}
            for table in TABLES:
                cursor.execute(f'SELECT count(*) FROM "{table}"')
                counts[table] = cursor.fetchone()[0]
            violations = {}
            for name, statement in CHECKS.items():
                cursor.execute(statement)
                violations[name] = cursor.fetchone()[0]
    head = expected_migration_head()
    passed = (
        migration == head
        and vector_version is not None
        and vector_distance == 1.0
        and all(value == 0 for value in violations.values())
        and (expected_orders is None or counts["orders"] == expected_orders)
    )
    return {
        "status": "pass" if passed else "fail",
        "server_version": server_version,
        "database": database,
        "migration": migration,
        "expected_migration": head,
        "pgvector_version": vector_version,
        "vector_distance_check": vector_distance,
        "counts": counts,
        "violations": violations,
        "expected_orders": expected_orders,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-orders", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(os.environ.get("DATABASE_URL", ""), args.expected_orders)
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(body, encoding="utf-8")
    print(body, end="")
    raise SystemExit(0 if result["status"] == "pass" else 1)
