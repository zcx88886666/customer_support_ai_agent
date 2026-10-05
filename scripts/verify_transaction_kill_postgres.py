"""Kill disposable clients after SQL flush, before commit, and verify recovery."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import selectors
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]


def child(stage: str) -> None:
    from resolveai import domain as d
    from resolveai.db import SessionLocal

    with SessionLocal.begin() as db:
        db.execute(text("SELECT set_config('application_name', :name, true)"),
                   {"name": os.environ["TRANSACTION_KILL_APP_NAME"]})
        if stage == "return":
            d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1,
                            "synthetic crash recovery", True, "transaction-kill-return", datetime.now(timezone.utc))
        else:
            proposal_id = os.environ["TRANSACTION_KILL_PROPOSAL_ID"]
            d.issue_refund(db, proposal_id, f"refund:{proposal_id}", datetime.now(timezone.utc))
        db.flush()
        print(json.dumps({"stage": stage, "backend_pid": db.scalar(text("SELECT pg_backend_pid()")),
                          "transaction_id": db.scalar(text("SELECT txid_current()")), "flushed": True}), flush=True)
        # The parent kills this process only after observing its uncommitted
        # transaction in PostgreSQL. No production crash hook is installed.
        while True:
            signal.pause()


def read_marker(process: subprocess.Popen, timeout: float = 20) -> dict:
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout):
            raise RuntimeError("Disposable transaction process did not reach flush marker")
        line = process.stdout.readline()
    if not line:
        raise RuntimeError("Disposable transaction process exited before flush marker")
    return json.loads(line)


def run_case(stage: str, admin_url: str, run_id: str, report_dir: Path, crash_server=None) -> dict:
    from resolveai import domain as d, models as m
    from resolveai.db import make_engine
    from resolveai.seed import seed_demo

    database = "ra_txkill_" + uuid4().hex[:16]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    parts = urlsplit(admin_url)
    database_url = urlunsplit(("postgresql+psycopg", parts.netloc, "/" + database, "", ""))
    env = {**os.environ, "DATABASE_URL": database_url, "AUTH_MODE": "mock",
           "LONG_TERM_MEMORY_MODE": "off", "OPENROUTER_API_KEY": "",
           "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "",
           "OTEL_EXPORTER_OTLP_ENDPOINT": "", "OTEL_METRICS_EXPORTER": "none",
           "TRANSACTION_KILL_APP_NAME": "resolveai_txkill_" + run_id[-6:] + "_" + stage}
    engine = make_engine(database_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    process = None
    checks = {}
    try:
        with (report_dir / f"{stage}.migration.log").open("w") as log:
            subprocess.run([str(Path(sys.executable).with_name("alembic")), "upgrade", "head"],
                           cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
        now = datetime.now(timezone.utc)
        with factory.begin() as db:
            seed_demo(db, now)
            if stage == "refund":
                request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1,
                                          "synthetic crash recovery", True, "transaction-kill-return", now)
                d.record_receipt(db, "warehouse-test", request.id, 1, now)
                d.record_inspection(db, "warehouse-test", request.id, True, "intact", now)
                proposal = d.create_proposal(db, request.id, now)
                d.decide_proposal(db, "supervisor-test", proposal.id, True, now)
                env["TRANSACTION_KILL_PROPOSAL_ID"] = proposal.id
        with (report_dir / f"{stage}.process.log").open("w") as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child-stage", stage],
                                       cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=log, text=True)
            marker = read_marker(process)
            checks["child_reached_flush_before_commit"] = marker["stage"] == stage and marker["flushed"] is True
            with factory() as db:
                activity = db.execute(text("SELECT state, backend_xid IS NOT NULL AS open_transaction "
                                           "FROM pg_stat_activity WHERE pid = :pid AND application_name = :name"),
                                      {"pid": marker["backend_pid"], "name": env["TRANSACTION_KILL_APP_NAME"]}).mappings().one()
                checks["postgres_observed_open_transaction"] = activity["state"] == "idle in transaction" and activity["open_transaction"]
                checks["uncommitted_ledger_invisible"] = db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0
                checks["uncommitted_action_audit_invisible"] = db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                    m.AuditEvent.action == ("create_return" if stage == "return" else "issue_refund"))) == 0
            if crash_server is not None:
                if not all(checks.values()):
                    raise RuntimeError("Crash preconditions were not observed")
                # Close observer connections before the actual server crash;
                # the child still holds its flushed, uncommitted transaction.
                engine.dispose()
                checks.update(crash_server(stage))
            process.kill()
            process.wait(timeout=10)
            checks["child_killed_by_sigkill"] = process.returncode == -signal.SIGKILL
            deadline = time.monotonic() + 10
            while True:
                with factory() as db:
                    alive = db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE pid = :pid AND application_name = :name"),
                                      {"pid": marker["backend_pid"], "name": env["TRANSACTION_KILL_APP_NAME"]})
                if alive == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("PostgreSQL did not close killed client's transaction")
                time.sleep(0.05)
            checks["killed_transaction_disconnected"] = True
        with factory() as db:
            order = db.get(m.Order, "demo-order-01")
            item = db.get(m.OrderItem, "demo-item-01")
            checks["rollback_preserved_order_version"] = order.version == (1 if stage == "return" else 2)
            checks["rollback_preserved_paid_balance"] = item.refunded_cents == item.refunded_quantity == 0
            checks["rollback_left_no_ledger"] = db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0
            if stage == "return":
                checks["rollback_left_no_return_or_audit"] = db.scalar(select(func.count()).select_from(m.ReturnRequest)) == 0 and db.scalar(
                    select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action == "create_return")) == 0
            else:
                checks["rollback_preserved_approval"] = db.get(m.RefundProposal, env["TRANSACTION_KILL_PROPOSAL_ID"]).status == "approved" and db.scalar(
                    select(func.count()).select_from(m.Approval).where(m.Approval.decision == "approved")) == 1
                checks["rollback_left_no_issue_audit"] = db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action == "issue_refund")) == 0
        if stage == "return":
            code = ("import json; from datetime import datetime, timezone; from resolveai.db import SessionLocal; "
                    "from resolveai.domain import create_return; "
                    "db=SessionLocal(); "
                    "r=create_return(db,'cust-01','demo-order-01','demo-item-01',1,'synthetic crash recovery',True,'transaction-kill-return',datetime.now(timezone.utc)); "
                    "db.commit(); print(json.dumps({'return_id':r.id})); db.close()")
        else:
            code = "import json; from resolveai.worker import issue_approved_once; print(json.dumps({'issued':issue_approved_once()}))"
        recovery = []
        for attempt in range(2):
            with (report_dir / f"{stage}.recovery-{attempt + 1}.log").open("w") as log:
                result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                        stderr=log, text=True, check=True, timeout=30)
                log.write(result.stdout)
                recovery.append(json.loads(result.stdout))
        with factory() as db:
            order = db.get(m.Order, "demo-order-01")
            item = db.get(m.OrderItem, "demo-item-01")
            checks["recovery_has_one_return"] = db.scalar(select(func.count()).select_from(m.ReturnRequest)) == 1
            if stage == "return":
                checks["recovery_return_is_idempotent"] = recovery[0]["return_id"] == recovery[1]["return_id"]
                checks["recovery_has_one_create_audit"] = db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action == "create_return")) == 1
                checks["recovery_has_no_refund"] = db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0 and order.version == 2
            else:
                ledger = db.scalars(select(m.RefundLedger)).one()
                checks["recovery_worker_is_idempotent"] = recovery[0]["issued"] == [ledger.id] and recovery[1]["issued"] == []
                checks["recovery_has_one_issue_audit"] = db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action == "issue_refund")) == 1
                checks["recovery_refund_amount_and_quantity"] = ledger.amount_cents == item.refunded_cents == item.paid_cents and item.refunded_quantity == 1 and order.version == 3
        return {"case_id": "transaction-kill-" + stage, "database": database, "status": "pass" if all(checks.values()) else "fail", "checks": checks}
    except Exception as exc:
        # Keep stack locations for troubleshooting without copying connection
        # strings or environment values from exception messages into reports.
        failure = {"error_type": type(exc).__name__, "stack": [
            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)]}
        diagnostics = getattr(exc, "diagnostics", None)
        if isinstance(diagnostics, dict):
            failure["diagnostics"] = diagnostics
        (report_dir / f"{stage}.error.json").write_text(json.dumps(failure, indent=2) + "\n")
        return {"case_id": "transaction-kill-" + stage, "database": database, "status": "incomplete",
                "checks": checks, "error_type": type(exc).__name__}
    finally:
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if process and process.stdout:
            process.stdout.close()
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child-stage", choices=("return", "refund"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child_stage:
        child(args.child_stage)
        return 0
    admin_url = os.environ.get("TRANSACTION_KILL_PG_ADMIN_URL", "")
    parts = urlsplit(admin_url)
    if parts.scheme != "postgresql" or parts.path != "/postgres" or not parts.hostname:
        raise RuntimeError("TRANSACTION_KILL_PG_ADMIN_URL must be a PostgreSQL admin connection to /postgres")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-txkill-" + uuid4().hex[:6]
    report_dir = ROOT / "evals/reports" / run_id
    report_dir.mkdir(parents=True)
    manifest = {"run_id": run_id, "suite": "postgres-transaction-kill-v1", "split": "dev", "synthetic": True,
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "case_count": 2}
    (report_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rows = [run_case(stage, admin_url, run_id, report_dir) for stage in ("return", "refund")]
    (report_dir / "case_results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    summary = {"run_id": run_id, "cases": len(rows), "passed": sum(row["status"] == "pass" for row in rows),
               "incomplete": sum(row["status"] == "incomplete" for row in rows),
               "failed_checks": sum(not value for row in rows for value in row["checks"].values()), "locked_release_ready": False}
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (report_dir / "report.html").write_text("<!doctype html><meta charset='utf-8'><title>Transaction kill verification</title>"
                                           "<h1>PostgreSQL transaction kill verification</h1><pre>" + html.escape(json.dumps({"summary": summary, "cases": rows}, indent=2)) + "</pre>")
    print(json.dumps({**summary, "report_dir": str(report_dir), "databases": [row["database"] for row in rows]}))
    return 0 if summary["passed"] == 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
