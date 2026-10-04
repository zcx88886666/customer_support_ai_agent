"""Replay business-v2 with real Keycloak JWTs, migrated PostgreSQL, and worker processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from resolveai import models as m
from resolveai.db import make_engine
from resolveai.prompts import ROOT
from resolveai.seed import seed_demo

if __package__:
    from . import run_business
else:
    import run_business


def database_url(admin_url: str, name: str, sqlalchemy: bool = False) -> str:
    parts = urlsplit(admin_url)
    scheme = "postgresql+psycopg" if sqlalchemy else "postgresql"
    return urlunsplit((scheme, parts.netloc, "/" + name, "", ""))


def process_env(db_url: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update({"DATABASE_URL": db_url, "AUTH_MODE": "oidc", "LONG_TERM_MEMORY_MODE": "postgres_store", "OPENROUTER_API_KEY": "", "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "", "OTEL_EXPORTER_OTLP_ENDPOINT": "", "OTEL_METRICS_EXPORTER": "none"})
    return env


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_healthy(port: int, process: subprocess.Popen) -> None:
    deadline = time.monotonic() + 45
    with httpx.Client(trust_env=False, timeout=2) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("isolated API exited during startup")
            try:
                if client.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise RuntimeError("isolated API health timed out")


def load_oidc_tokens() -> dict[str, str]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.verify_oidc import login
    accounts = json.loads((ROOT / ".local/demo-accounts.json").read_text(encoding="utf-8"))
    return {name: login(name, accounts[name]["password"]) for name in ("customer-one", "customer-two", "warehouse-demo", "supervisor-demo")}


def run_case(case: dict, run_id: str, index: int, admin_url: str, tokens: dict[str, str], report_dir: Path) -> dict:
    name = "ra_biz_oidc_" + run_id[:15].lower().replace("t", "_").replace("z", "") + "_" + run_id[-6:] + "_" + str(index)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    db_url = database_url(admin_url, name, sqlalchemy=True)
    env = process_env(db_url)
    subprocess.run([str(Path(sys.executable).with_name("alembic")), "upgrade", "head"], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
    engine = make_engine(db_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        with factory.begin() as db:
            seed_demo(db, datetime.now(timezone.utc))
        if case["scenario"] == "expired_window":
            with factory.begin() as db:
                shipment = db.scalar(select(m.Shipment).where(m.Shipment.order_id == case["fixture"]["order_id"]))
                shipment.delivered_at = datetime.now(timezone.utc) - timedelta(days=20)
        port = available_port()
        log_path = report_dir / f"{case['case_id']}.api.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "resolveai.api:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                wait_healthy(port, process)
                customer = "customer-one" if case["fixture"]["customer_id"] == "cust-01" else "customer-two"
                token_by_role = {"customer": tokens[customer], "warehouse": tokens["warehouse-demo"], "supervisor": tokens["supervisor-demo"]}
                started = time.perf_counter()
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=30) as client:
                    def post(path, role, _actor, body):
                        response = client.post(path, json=body, headers={"Authorization": "Bearer " + token_by_role[role], "x-eval-run-id": run_id, "x-eval-case-id": case["case_id"]})
                        return response.status_code, response.json()

                    def issue_worker():
                        code = "import json; from resolveai.worker import issue_approved_once; print(json.dumps(issue_approved_once()))"
                        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True, capture_output=True, text=True, timeout=30)
                        return json.loads(result.stdout.strip())

                    statuses, observations = run_business.execute(None, case, run_id, post_request=post, issue_worker=issue_worker)
                with factory() as db:
                    checks = run_business.score(case, statuses, observations, db)
                return {"case_id": case["case_id"], "database": name, "status": "pass" if all(checks.values()) else "fail", "checks": checks, "http_statuses": statuses, "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id", help="Run one development case for integration diagnosis")
    args = parser.parse_args()
    admin_url = os.environ.get("BUSINESS_PG_ADMIN_URL", "")
    parts = urlsplit(admin_url)
    if parts.scheme != "postgresql" or parts.path != "/postgres" or not parts.hostname:
        raise RuntimeError("BUSINESS_PG_ADMIN_URL must be a PostgreSQL admin connection to /postgres")
    cases = run_business.load_cases()
    if args.case_id:
        cases = [case for case in cases if case["case_id"] == args.case_id]
        if not cases:
            raise RuntimeError("Unknown business case ID")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-oidc-" + uuid4().hex[:6]
    report_dir = ROOT / "evals/reports" / run_id
    report_dir.mkdir(parents=True, exist_ok=False)
    tokens = load_oidc_tokens()
    results = []
    for index, case in enumerate(cases, start=1):
        try:
            result = run_case(case, run_id, index, admin_url, tokens, report_dir)
        except Exception as exc:
            result = {"case_id": case["case_id"], "status": "incomplete", "error": type(exc).__name__ + ": " + str(exc)[:180]}
        results.append(result)
        print(f"{case['case_id']}: {result['status']}", flush=True)
    summary = {"run_id": run_id, "suite": "business_workflows_v2_oidc_postgres", "cases": len(results), "pass": sum(row["status"] == "pass" for row in results), "fail": sum(row["status"] == "fail" for row in results), "incomplete": sum(row["status"] == "incomplete" for row in results), "gate_pass": all(row["status"] == "pass" for row in results), "report": str(report_dir)}
    (report_dir / "manifest.json").write_text(json.dumps({"run_id": run_id, "dataset_sha256": hashlib.sha256(run_business.DATASET.read_bytes()).hexdigest(), "selected_case_ids": [case["case_id"] for case in cases], "auth_mode": "real-Keycloak-OIDC-code-PKCE", "database": "fresh-migrated-PostgreSQL-per-case", "worker": "fresh-subprocess-per-step", "model": "none"}, indent=2) + "\n", encoding="utf-8")
    (report_dir / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    raise SystemExit(0 if summary["gate_pass"] else 1)


if __name__ == "__main__":
    main()
