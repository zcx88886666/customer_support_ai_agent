"""Sustained synthetic HTTP read/chat/write load with SQL terminal scoring."""

from __future__ import annotations

import hashlib
import html
import json
import os
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "evals/reports"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps/api"))
sys.path.insert(0, str(ROOT / "packages"))
from resolveai import domain as d, models as m
from resolveai.db import make_engine
from resolveai.seed import seed_demo
from scripts.verify_postgres_server_crash import DisposablePostgres, save_failure


def score_terminal(db, expected_completed: int) -> tuple[dict[str, bool], dict[str, int]]:
    def count(model):
        return db.scalar(select(func.count()).select_from(model))

    counts = {name: count(model) for name, model in (
        ("returns", m.ReturnRequest), ("receipts", m.WarehouseReceipt),
        ("inspections", m.Inspection), ("proposals", m.RefundProposal),
        ("approvals", m.Approval), ("ledgers", m.RefundLedger))}
    ledgers = db.scalars(select(m.RefundLedger)).all()
    amounts = {}
    quantities = {}
    approved, inspected, money, unique, ownership = True, True, True, True, True
    proposals_seen = set()
    for ledger in ledgers:
        proposal = db.get(m.RefundProposal, ledger.proposal_id)
        request = db.get(m.ReturnRequest, proposal.return_id) if proposal else None
        item = db.get(m.OrderItem, ledger.order_item_id)
        order = db.get(m.Order, request.order_id) if request else None
        allocation = db.get(m.PaidAllocation, ledger.order_item_id)
        payment = db.scalar(select(m.Payment).where(m.Payment.order_id == request.order_id)) if request else None
        receipt = db.scalar(select(m.WarehouseReceipt).where(m.WarehouseReceipt.return_id == request.id)) if request else None
        inspection = db.get(m.Inspection, proposal.inspection_id) if proposal else None
        approval = db.scalar(select(m.Approval).where(m.Approval.proposal_id == proposal.id, m.Approval.decision == "approved")) if proposal else None
        approved &= bool(proposal and request and approval and proposal.status == "issued" and request.status == "refund_issued"
                         and d.aware(approval.decided_at) <= d.aware(ledger.issued_at))
        inspected &= bool(receipt and inspection and inspection.passed and inspection.receipt_id == receipt.id)
        ownership &= bool(item and request and order and ledger.order_item_id == request.order_item_id
                          and item.order_id == request.order_id and order.customer_id == request.customer_id)
        money &= bool(item and request and proposal and allocation and payment
                      and ledger.amount_cents == proposal.amount_cents == item.paid_cents == allocation.paid_cents == payment.paid_cents
                      and payment.currency == "CNY" and payment.method == "synthetic_original")
        unique &= ledger.proposal_id not in proposals_seen
        proposals_seen.add(ledger.proposal_id)
        if item and request:
            amounts[item.id] = amounts.get(item.id, 0) + ledger.amount_cents
            quantities[item.id] = quantities.get(item.id, 0) + request.quantity
    balance = all(item.refunded_cents == amounts.get(item.id, 0) and
                  item.refunded_quantity == quantities.get(item.id, 0)
                  for item in db.scalars(select(m.OrderItem)).all())
    checks = {"at_least_one_completed_workflow": expected_completed > 0,
              "http_workflows_match_returns": counts["returns"] == expected_completed,
              "all_actions_reached_terminal_state": all(value == expected_completed for value in counts.values()),
              "ledger_requires_approved_proposal": approved,
              "ledger_requires_positive_inspection": inspected,
              "ledger_matches_return_ownership": ownership,
              "ledger_amount_matches_paid_item": money,
              "one_ledger_per_proposal": unique,
              "ledger_matches_item_balance": balance}
    return checks, counts


def seed_load_orders(factory, count: int, now: datetime) -> None:
    with factory.begin() as db:
        seed_demo(db, now)
        db.add(m.Customer(id="load-customer", display_name="Synthetic load customer"))
        db.add(m.CustomerProfile(customer_id="load-customer"))
        db.flush()
        for index in range(count):
            key = str(index).zfill(5)
            order_id, item_id, product_id, shipment_id = (prefix + key for prefix in (
                "load-order-", "load-item-", "load-product-", "load-shipment-"))
            amount = 1000 + index % 101
            db.add(m.Product(id=product_id, seller_id="load-seller", title="Synthetic item " + key,
                             returnable=True, physical=True, special_notice_accepted=False))
            db.add(m.Order(id=order_id, customer_id="load-customer", seller_id="load-seller",
                           placed_at=now - timedelta(days=5), status="paid", version=1, currency="CNY",
                           policy_bundle_id="policy-demo-v1"))
            db.flush()
            db.add(m.OrderItem(id=item_id, order_id=order_id, product_id=product_id, quantity=1, paid_cents=amount))
            db.add(m.Payment(id="load-payment-" + key, order_id=order_id, paid_cents=amount,
                             method="synthetic_original", currency="CNY"))
            db.flush()
            db.add(m.PaidAllocation(order_item_id=item_id, paid_cents=amount))
            db.add(m.Shipment(id=shipment_id, order_id=order_id, status="delivered",
                              delivered_at=now - timedelta(days=2), version=1))
            db.flush()
            db.add(m.ShipmentEvent(id="load-event-" + key, shipment_id=shipment_id,
                                   status="delivered", occurred_at=now - timedelta(days=2)))


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def wait_api(url: str, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Temporary isolated API exited before health check")
        try:
            if httpx.get(url + "/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Temporary isolated API did not become healthy")


def main() -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-mixed-write-" + uuid4().hex[:6]
    report_dir = REPORT_ROOT / run_id
    report_dir.mkdir(parents=True)
    server_dir = report_dir / "postgres"
    server_dir.mkdir()
    server = DisposablePostgres(run_id, server_dir)
    database = "ra_mixed_" + uuid4().hex[:16]
    vus, duration, orders = 10, 60, 1000
    api = worker = None
    checks, counts, error_type = {}, {}, None
    load_summary = {}
    env = None
    k6_image_id = ""
    k6_name = "ra_k6_" + uuid4().hex[:16]
    sample_stop = Event()
    sampler = None
    samples = []
    try:
        server.create()
        with psycopg.connect(server.admin_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        db_url = server.admin_url.replace("/postgres", "/" + database)
        app_db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
        env = {**os.environ, "DATABASE_URL": app_db_url, "AUTH_MODE": "mock", "LONG_TERM_MEMORY_MODE": "off",
               "OPENROUTER_API_KEY": "", "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "",
               "OTEL_EXPORTER_OTLP_ENDPOINT": "", "OTEL_METRICS_EXPORTER": "none", "PROMPT_RELEASE": "specialists-dev-v1",
               "PYTHONPATH": str(ROOT / "apps/api") + ":" + str(ROOT / "packages") + ":" + str(ROOT)}
        with (report_dir / "migration.log").open("w") as log:
            subprocess.run([str(Path(sys.executable).with_name("alembic")), "upgrade", "head"],
                           cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        engine = make_engine(app_db_url)
        factory = sessionmaker(engine, expire_on_commit=False)
        seed_load_orders(factory, orders, datetime.now(timezone.utc))
        engine.dispose()
        port = free_port()
        url = f"http://127.0.0.1:{port}"
        with (report_dir / "api.log").open("w") as log:
            api = subprocess.Popen([sys.executable, "-m", "uvicorn", "resolveai.api:app", "--host", "127.0.0.1",
                                    "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        wait_api(url, api)
        worker_code = ("import time; from resolveai.worker import issue_approved_once; "
                       "from pathlib import Path; flag=Path('" + str(report_dir / "worker.stop") + "'); "
                       "\nwhile not flag.exists():\n issue_approved_once(); time.sleep(.5)\n")
        with (report_dir / "worker.log").open("w") as log:
            worker = subprocess.Popen([sys.executable, "-c", worker_code], cwd=ROOT, env=env,
                                      stdout=log, stderr=subprocess.STDOUT)
        load_script = ROOT / "evals/load/mixed_write.js"
        k6_image_id = subprocess.check_output(["docker", "image", "inspect", "grafana/k6:0.56.0",
                                               "--format", "{{.Id}}"], text=True).strip()
        def sample_resources():
            started = time.monotonic()
            while not sample_stop.wait(10):
                try:
                    observation = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", server.container_id],
                                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5)
                    if observation.returncode == 0:
                        samples.append({"elapsed_seconds": round(time.monotonic() - started, 2),
                                        "postgres": json.loads(observation.stdout)})
                except Exception as exc:
                    samples.append({"error_type": type(exc).__name__})
        sampler = Thread(target=sample_resources, daemon=True)
        sampler.start()
        load_started = time.monotonic()
        with (report_dir / "k6.log").open("w") as log:
            result = subprocess.run(["docker", "run", "--rm", "--pull", "never", "--network", "host",
                                     "--name", k6_name, "--label", "resolveai.verifier=mixed-write-load",
                                     "--label", "resolveai.run_id=" + run_id,
                                     "--user", f"{os.getuid()}:{os.getgid()}",
                                     "--volume", str(load_script) + ":/scripts/mixed_write.js:ro",
                                     "--volume", str(report_dir) + ":/reports:rw",
                                     "--env", "TARGET_URL=" + url, "--env", "LOAD_ORDERS=" + str(orders),
                                     "--env", "LOAD_VUS=" + str(vus), "--env", "LOAD_DURATION=" + str(duration) + "s",
                                     k6_image_id, "run", "--quiet", "--summary-export=/reports/k6-summary.json",
                                     "/scripts/mixed_write.js"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=duration + 60)
        elapsed_seconds = round(time.monotonic() - load_started, 3)
        sample_stop.set()
        sampler.join(timeout=6)
        checks["k6_completed"] = result.returncode == 0
        load_summary = json.loads((report_dir / "k6-summary.json").read_text())
        metrics = load_summary["metrics"]
        def values(name):
            entry = metrics.get(name, {})
            return entry.get("values", entry)
        completed = int(values("load_completed_workflows").get("count", 0))
        errors = float(values("load_http_errors").get("rate", values("load_http_errors").get("value", 1)))
        checks["zero_http_errors"] = errors == 0
        checks["write_orders_not_exhausted"] = values("load_exhausted_orders").get("count", 1) == 0
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise RuntimeError("Temporary refund worker exited during load")
            with factory() as db:
                issued = db.scalar(select(func.count()).select_from(m.RefundLedger))
            if issued >= completed:
                break
            time.sleep(0.2)
        (report_dir / "worker.stop").touch()
        worker.wait(timeout=15)
        checks["worker_exited_cleanly"] = worker.returncode == 0
        with factory() as db:
            terminal_checks, counts = score_terminal(db, completed)
        checks.update(terminal_checks)
        load_summary = {"completed_workflows": completed, "http_error_rate": errors,
                        "http_requests": values("http_reqs").get("count", 0),
                        "http_p50_ms": values("http_req_duration").get("med"),
                        "http_p95_ms": values("http_req_duration").get("p(95)"),
                        "workflow_p95_ms": values("load_workflow_ms").get("p(95)"),
                        "vus": vus, "duration_seconds": duration, "elapsed_seconds_including_grace": elapsed_seconds,
                        "http_requests_per_second": values("http_reqs").get("rate"), "orders_prepared": orders}
    except Exception as exc:
        error_type = type(exc).__name__
        save_failure(report_dir, exc)
    finally:
        sample_stop.set()
        if sampler:
            sampler.join(timeout=6)
        if api and api.poll() is None:
            api.terminate()
            try:
                api.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api.kill()
                api.wait(timeout=5)
        if worker and worker.poll() is None:
            (report_dir / "worker.stop").touch()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
                error_type = "WorkerCleanupTimeout"
        # --rm normally removes k6. On a timeout, verify its generated name,
        # run label and immutable image before removing the owned container.
        try:
            inspection = subprocess.run(["docker", "inspect", k6_name], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True, timeout=10)
            if inspection.returncode == 0:
                info = json.loads(inspection.stdout)[0]
                actual = info.get("Config", {}).get("Labels") or {}
                if (info.get("Name") == "/" + k6_name and info.get("Image") == k6_image_id
                        and actual.get("resolveai.verifier") == "mixed-write-load" and actual.get("resolveai.run_id") == run_id):
                    subprocess.run(["docker", "rm", "--force", info["Id"]], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=10, check=True)
                else:
                    error_type = "K6OwnershipMismatch"
        except Exception as exc:
            error_type = type(exc).__name__
            save_failure(report_dir, exc)
        try:
            cleanup = server.cleanup(success=bool(checks and all(checks.values()) and error_type is None))
        except Exception as exc:
            error_type = type(exc).__name__
            save_failure(server_dir, exc)
            cleanup = {"container": server.name, "volume": server.volume, "removed": False}
    manifest = {"run_id": run_id, "suite": "mixed-write-load-v1", "split": "dev", "synthetic": True,
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "script_hashes": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (
                    ROOT / "evals/load/mixed_write.js", Path(__file__))}, "database": database,
                "postgres_container_id": server.container_id, "postgres_image_id": server.image_id,
                "k6_image_id": k6_image_id,
                "machine": {"platform": platform.platform(), "logical_cpus": os.cpu_count(),
                            "memory_kib": int(next(line for line in Path('/proc/meminfo').read_text().splitlines()
                                                   if line.startswith('MemTotal:')).split()[1])},
                "cleanup": cleanup, "locked_release_ready": False}
    (report_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (report_dir / "resource-samples.json").write_text(json.dumps(samples, indent=2) + "\n")
    row = {"case_id": "mixed-write-load", "status": "pass" if checks and all(checks.values()) and not error_type else "incomplete",
           "checks": checks, "counts": counts, "measurements": load_summary, "error_type": error_type}
    (report_dir / "case_results.jsonl").write_text(json.dumps(row) + "\n")
    summary = {"run_id": run_id, "cases": 1, "passed": int(row["status"] == "pass"),
               "incomplete": int(row["status"] != "pass"), "failed_checks": sum(not value for value in checks.values()),
               "measurements": load_summary, "cleanup": cleanup, "locked_release_ready": False}
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (report_dir / "report.html").write_text("<!doctype html><meta charset='utf-8'><title>Mixed write load</title>"
                                          "<h1>Isolated mixed write load</h1><pre>" +
                                          html.escape(json.dumps({"summary": summary, "case": row}, indent=2)) + "</pre>")
    print(json.dumps({**summary, "report_dir": str(report_dir)}))
    return 0 if row["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
