"""Bounded PostgresStore application write-load on a fresh synthetic database."""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[2]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999))], 2)


def run_stage(factory, customer_ids: list[str], rounds: int) -> dict:
    from resolveai.memory import list_preferences, upsert_preference

    barrier = Barrier(len(customer_ids))

    def work(customer_id: str) -> list[float]:
        latencies = []
        barrier.wait(timeout=30)
        for index in range(rounds):
            value = "English" if index % 2 == 0 else "中文"
            started = time.perf_counter()
            with factory.begin() as db:
                upsert_preference(db, customer_id, "language", value, True)
            latencies.append((time.perf_counter() - started) * 1000)
            with factory() as db:
                assert list_preferences(db, customer_id) == {"language": value}
        return latencies

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(customer_ids)) as pool:
        results = list(pool.map(work, customer_ids))
    elapsed = time.perf_counter() - started
    latencies = [value for row in results for value in row]
    return {"workers": len(customer_ids), "writes": len(latencies), "reads": len(latencies), "elapsed_s": round(elapsed, 3), "write_per_second": round(len(latencies) / elapsed, 2), "write_median_ms": round(statistics.median(latencies), 2), "write_p95_ms": percentile(latencies, 0.95), "write_max_ms": round(max(latencies), 2), "failed_checks": 0}


def run_contention_stage(factory, customer_id: str, workers: int = 10, rounds: int = 20) -> dict:
    from resolveai.memory import list_preferences, upsert_preference
    from resolveai import models as m

    barrier = Barrier(workers)

    def work(worker: int) -> list[float]:
        latencies = []
        barrier.wait(timeout=30)
        for index in range(rounds):
            value = "English" if (worker + index) % 2 == 0 else "中文"
            started = time.perf_counter()
            with factory.begin() as db:
                upsert_preference(db, customer_id, "language", value, True)
            latencies.append((time.perf_counter() - started) * 1000)
        return latencies

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, range(workers)))
    elapsed = time.perf_counter() - started
    with factory() as db:
        entries = db.query(m.MemoryEntry).filter(m.MemoryEntry.customer_id == customer_id).all()
        assert len(entries) == 1 and not entries[0].revoked
        assert list_preferences(db, customer_id) == {"language": entries[0].value}
    latencies = [value for row in results for value in row]
    return {"workers": workers, "writes": len(latencies), "elapsed_s": round(elapsed, 3), "write_per_second": round(len(latencies) / elapsed, 2), "write_median_ms": round(statistics.median(latencies), 2), "write_p95_ms": percentile(latencies, 0.95), "write_max_ms": round(max(latencies), 2), "sql_store_consistent": True, "failed_checks": 0}


def main() -> None:
    admin_url = os.environ.get("MEMORY_LOAD_PG_ADMIN_URL", "")
    parts = urlsplit(admin_url)
    if parts.scheme != "postgresql" or parts.path != "/postgres" or not parts.hostname:
        raise RuntimeError("MEMORY_LOAD_PG_ADMIN_URL must connect to a separate PostgreSQL /postgres database")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-memory-load-" + uuid4().hex[:6]
    database_name = "ra_memory_load_" + uuid4().hex[:12]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    db_url = urlunsplit(("postgresql+psycopg", parts.netloc, "/" + database_name, "", ""))
    environment = os.environ.copy()
    environment.update({"DATABASE_URL": db_url, "LONG_TERM_MEMORY_MODE": "postgres_store", "AUTH_MODE": "mock", "OPENROUTER_API_KEY": "", "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "", "OTEL_EXPORTER_OTLP_ENDPOINT": ""})
    subprocess.run([str(Path(sys.executable).with_name("alembic")), "upgrade", "head"], cwd=ROOT, env=environment, check=True, stdout=subprocess.DEVNULL)
    os.environ.update(environment)

    from sqlalchemy import select
    from resolveai import models as m
    from resolveai.db import SessionLocal, engine
    from resolveai.memory import _namespace, list_preferences, set_consent, setup_long_term_store

    report_dir = ROOT / "evals/reports" / run_id
    report_dir.mkdir(parents=True, exist_ok=False)
    stages = []
    setup_long_term_store()
    try:
        all_customers = []
        for workers in (1, 5, 10):
            customer_ids = [f"synthetic_mem_load_{workers}_{index}_{run_id[-6:]}" for index in range(workers)]
            all_customers.extend(customer_ids)
            with SessionLocal.begin() as db:
                for customer_id in customer_ids:
                    db.add(m.Customer(id=customer_id, display_name="Synthetic write-load customer"))
                    db.add(m.CustomerProfile(customer_id=customer_id, language="zh-CN", channel="web", memory_consent=True))
            stages.append(run_stage(SessionLocal, customer_ids, 20))
        contended_customer = "synthetic_mem_contention_" + run_id[-6:]
        all_customers.append(contended_customer)
        with SessionLocal.begin() as db:
            db.add(m.Customer(id=contended_customer, display_name="Synthetic contended customer"))
            db.add(m.CustomerProfile(customer_id=contended_customer, language="zh-CN", channel="web", memory_consent=True))
        contention = run_contention_stage(SessionLocal, contended_customer)
        with SessionLocal.begin() as db:
            for customer_id in all_customers:
                set_consent(db, customer_id, False)
        with SessionLocal() as db:
            assert all(list_preferences(db, customer_id) == {} for customer_id in all_customers)
            entries = db.scalars(select(m.MemoryEntry).where(m.MemoryEntry.customer_id.in_(all_customers))).all()
            assert len(entries) == len(all_customers) and all(entry.revoked and entry.value == "" for entry in entries)
            from langgraph.store.postgres import PostgresStore
            store = PostgresStore(db.connection().connection.driver_connection)
            assert all(not store.search(_namespace(customer_id), limit=5) for customer_id in all_customers)
        result = {"run_id": run_id, "database": database_name, "stages": stages, "same_customer_contention": contention, "customers": len(all_customers), "revoked_rows": len(entries), "store_namespaces_empty": True, "provider_calls": 0, "report": str(report_dir)}
        (report_dir / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
