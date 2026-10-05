"""Real HTTP/OIDC/PostgreSQL fault checks using disposable local processes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import psycopg
from psycopg import sql
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from evals.runners.run_business_oidc_postgres import available_port, database_url, process_env, wait_healthy
from resolveai import models as m
from resolveai.db import make_engine
from resolveai.prompts import ROOT, PromptRegistry
from resolveai.seed import seed_demo
from resolveai.policy_retrieval import index_bundle
from scripts.verify_oidc import login


def child(kind: str, port: int, scenario: str):
    import uvicorn

    if kind == "api":
        from resolveai import openrouter
        from resolveai.api import app
        openrouter.CHAT_COMPLETIONS_URL = os.environ["TRANSPORT_TEST_PROVIDER_URL"]
    else:
        from services.commerce_mcp import server
        original = server.track_shipment
        calls = 0

        async def track_shipment(order_id: str) -> list[dict]:
            nonlocal calls
            calls += 1
            call_id = calls
            if scenario == "mcp_slow" and call_id == 1:
                print(json.dumps({"event": "slow_tool_started", "call": call_id}), flush=True)
                try:
                    await asyncio.sleep(12)
                except asyncio.CancelledError:
                    print(json.dumps({"event": "slow_tool_cancelled", "call": call_id}), flush=True)
                    raise
                print(json.dumps({"event": "slow_tool_completed", "call": call_id}), flush=True)
            rows = original(order_id)
            if scenario == "mcp_contradiction" and call_id == 1:
                rows = [{**row, "status": "delivered", "delivered_at": "2026-10-04T00:00:00+00:00"} for row in rows]
                print(json.dumps({"event": "contradictory_snapshot_sent", "call": call_id}), flush=True)
            return rows

        server.mcp.remove_tool("track_shipment")
        server.mcp.add_tool(track_shipment, name="track_shipment", description="Test-only shipment fault wrapper")
        app = server.app
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


class LocalProvider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), ProviderHandler)
        self.trickle = True
        self.events = []
        self.lock = threading.Lock()


class ProviderHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        schema = request["response_format"]["json_schema"]["name"]
        if schema == "RouteDecision":
            content = {"route": "knowledge", "intents": ["shipment_tracking"], "uncertainty": None}
        elif schema == "OrderToolPlan":
            content = {"tools": ["get_order", "track_shipment"]}
        elif schema == "PolicyRetrievalPlan":
            content = {"queries": ["退货条件", "未签收配送异常"]}
        else:
            content = {"selected_evidence": [], "unresolved_conditions": []}
        payload = json.dumps({"choices": [{"message": {"content": json.dumps(content)}}],
                              "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.00003}}).encode()
        with self.server.lock:
            trickle = self.server.trickle
            self.server.events.append({"event": "provider_started", "trickle": trickle, "schema": schema})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            for index in range(0, len(payload), 2 if trickle else len(payload)):
                self.wfile.write(payload[index:index + (2 if trickle else len(payload))])
                self.wfile.flush()
                if trickle:
                    time.sleep(0.04)
        except (BrokenPipeError, ConnectionResetError):
            with self.server.lock:
                self.server.events.append({"event": "provider_peer_disconnected", "trickle": trickle})


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_case(scenario: str, mode: str, admin_url: str, token: str, folder: Path) -> dict:
    case_id = scenario + "-" + mode
    name = "ra_transport_" + uuid4().hex[:16]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    db_url = database_url(admin_url, name, sqlalchemy=True)
    env = process_env(db_url)
    provider = LocalProvider()
    if scenario != "llm_trickle":
        provider.trickle = False
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()
    env.update({"LONG_TERM_MEMORY_MODE": "off", "TRANSPORT_TEST_PROVIDER_URL": f"http://127.0.0.1:{provider.server_port}/chat",
                "MCP_RESOURCE_URL": "http://localhost:8001/mcp", "AGENT_REQUEST_TIMEOUT_SECONDS": "0.8" if scenario in {"llm_trickle", "sql_lock"} else "25",
                "AGENT_MAX_LLM_CALLS": "10", "AGENT_MAX_TOKENS": "16000", "AGENT_MAX_COST_USD": "0.02",
                "OPENROUTER_API_KEY": "" if scenario == "sql_lock" else "synthetic-local-key"})
    engine = make_engine(db_url)
    factory = sessionmaker(engine, expire_on_commit=False)
    processes = []
    lock_connection = None
    checks = {}
    observations = {}
    try:
        with (folder / f"{case_id}.migration.log").open("w") as log:
            subprocess.run([str(Path(sys.executable).with_name("alembic")), "upgrade", "head"], cwd=ROOT,
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=30)
        with factory.begin() as db:
            seed_demo(db, datetime.now(timezone.utc))
            index_bundle(db, "policy-demo-v1")
        api_port, mcp_port = available_port(), available_port()
        env["COMMERCE_MCP_URL"] = f"http://127.0.0.1:{mcp_port}/mcp"
        with (folder / f"{case_id}.mcp.log").open("w") as mcp_log, (folder / f"{case_id}.api.log").open("w") as api_log:
            for kind, port, log in (("mcp", mcp_port, mcp_log), ("api", api_port, api_log)):
                process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child", kind,
                                            "--port", str(port), "--scenario", scenario], cwd=ROOT, env=env,
                                           stdout=log, stderr=subprocess.STDOUT)
                processes.append(process)
            wait_healthy(api_port, processes[-1])
            headers = {"Authorization": "Bearer " + token}
            body = {"thread_id": case_id, "message": "查包裹物流" if scenario == "llm_trickle" else "包裹没到能退吗",
                    "order_id": "demo-order-02", "agent_mode": mode}
            if scenario == "sql_lock":
                body.update({"message": "我要退货", "order_id": "demo-order-01", "item_id": "demo-item-01",
                             "quantity": 1, "reason": "synthetic lock recovery", "confirmed": True,
                             "idempotency_key": case_id + "-return"})
                lock_connection = psycopg.connect(database_url(admin_url, name))
                lock_connection.execute("SELECT id FROM orders WHERE id = 'demo-order-01' FOR UPDATE")
            with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=30, trust_env=False) as client:
                started = time.monotonic()
                response = client.post("/chat", json=body, headers=headers)
                elapsed = time.monotonic() - started
                response.raise_for_status()
                first = response.json()
                observations["first_response"] = first
                observations["first_elapsed_seconds"] = round(elapsed, 4)
                checks["fault_http_200"] = response.status_code == 200
                checks["fault_response_bounded"] = elapsed < (5 if scenario == "sql_lock" else 3 if scenario == "llm_trickle" else 11)
                if scenario == "sql_lock":
                    checks["sql_timeout_handoff"] = first["status"] == "handoff" and first["resource_usage"]["exhausted_reason"] == "database_deadline_expired" and bool(first.get("ticket_id"))
                    with factory() as db:
                        checks["locked_request_rolled_back"] = db.scalar(select(func.count()).select_from(m.ReturnRequest)) == 0 and db.get(m.Order, "demo-order-01").version == 1
                    lock_connection.rollback()
                    lock_connection.close()
                    lock_connection = None
                elif scenario == "llm_trickle":
                    checks["model_timeout_handoff"] = first["status"] == "handoff" and first["findings"] == [] and bool(first.get("ticket_id"))
                    checks["attempt_accounted_with_unknown_usage"] = first["resource_usage"]["llm_attempts"] == 1 and first["resource_usage"]["unknown_usage_calls"] == 1 and first["resource_usage"]["pending_calls"] == 0
                    with provider.lock:
                        provider.trickle = False
                else:
                    checks["verified_policy_only"] = first["status"] == "answered" and any(finding["source_version"] == "policy-demo-v1" for finding in first["findings"]) and all(not finding["facts"] or "shipment_status" not in finding["facts"] for finding in first["findings"])
                    checks["acknowledged_missing_evidence"] = "部分证据未核实" in first["answer"]
                    checks["no_contradictory_delivery_claim"] = "delivered" not in first["answer"]
                recovered_response = client.post("/chat", json=body, headers=headers)
                recovered_response.raise_for_status()
                recovered = recovered_response.json()
                observations["recovery_response"] = recovered
                if scenario == "sql_lock":
                    replay = client.post("/chat", json=body, headers=headers)
                    replay.raise_for_status()
                    checks["fresh_turn_return_retry_idempotent"] = recovered["status"] == replay.json()["status"] == "return_requested" and recovered["return_id"] == replay.json()["return_id"]
                else:
                    checks["fresh_turn_recovered_owned_shipment"] = recovered["status"] == "answered" and any(
                        finding["source_ids"] == ["demo-order-02", "demo-shipment-02"] and finding["facts"]["shipment_status"] == "in_transit"
                        for finding in recovered["findings"])
                checks["fresh_turn_budget_reset"] = recovered["resource_usage"]["exhausted_reason"] is None and recovered["resource_usage"]["pending_calls"] == 0
            if scenario == "mcp_slow":
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    contents = (folder / f"{case_id}.mcp.log").read_text()
                    if '"event": "slow_tool_completed"' in contents or '"event": "slow_tool_cancelled"' in contents:
                        break
                    time.sleep(0.1)
                checks["late_tool_finished_or_cancelled"] = '"event": "slow_tool_completed"' in contents or '"event": "slow_tool_cancelled"' in contents
            with factory() as db:
                checks["no_unauthorized_business_writes"] = db.scalar(select(func.count()).select_from(m.ReturnRequest)) == (1 if scenario == "sql_lock" else 0) and db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0
                if scenario == "sql_lock":
                    checks["one_recovered_return_audit_and_version"] = db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action == "create_return")) == 1 and db.get(m.Order, "demo-order-01").version == 2
                state = db.get(m.ThreadState, case_id).state
                checks["late_result_did_not_replace_current_thread"] = state["status"] == recovered["status"] and state["task_id"] and state["plan_revision"] == recovered["plan_revision"]
            mcp_log.flush()
            if scenario in {"mcp_slow", "mcp_contradiction"} and "order_tool_plan" in PromptRegistry(env.get("PROMPT_RELEASE", "release-v1")).manifest["prompts"]:
                with provider.lock:
                    schemas = {event.get("schema") for event in provider.events}
                checks["both_specialist_planners_executed"] = {"OrderToolPlan", "PolicyRetrievalPlan"}.issubset(schemas)
            checks["mcp_transport_no_internal_errors"] = "ERROR" not in (folder / f"{case_id}.mcp.log").read_text()
            return {"case_id": case_id, "database": name, "status": "pass" if all(checks.values()) else "fail", "checks": checks, **observations}
    except Exception as exc:
        return {"case_id": case_id, "database": name, "status": "incomplete", "error_type": type(exc).__name__, "checks": checks, **observations}
    finally:
        if lock_connection is not None:
            lock_connection.rollback()
            lock_connection.close()
        for process in reversed(processes):
            stop(process)
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=2)
        with provider.lock:
            (folder / f"{case_id}.provider-events.json").write_text(json.dumps(provider.events, indent=2) + "\n")
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", choices=("api", "mcp"), help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--scenario", choices=("llm_trickle", "mcp_slow", "mcp_contradiction", "sql_lock"))
    args = parser.parse_args()
    if args.child:
        child(args.child, args.port, args.scenario)
        return 0
    admin_url = os.environ.get("SPECIALIST_TRANSPORT_PG_ADMIN_URL", "")
    from urllib.parse import urlsplit
    parts = urlsplit(admin_url)
    if parts.scheme != "postgresql" or parts.path != "/postgres" or not parts.hostname:
        raise ValueError("SPECIALIST_TRANSPORT_PG_ADMIN_URL must target /postgres")
    accounts = json.loads((ROOT / ".local/demo-accounts.json").read_text())
    token = login("customer-one", accounts["customer-one"]["password"])
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-transport-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True)
    manifest = {"run_id": run_id, "suite": "specialist-transport-v2", "split": "dev", "synthetic": True,
                "model": "local HTTP stub; no provider credit", "authentication": "real Keycloak code+PKCE",
                "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    release = PromptRegistry(os.getenv("PROMPT_RELEASE", "release-v1"))
    manifest.update({"prompt_release_id": release.release_id, "prompt_hashes": release.manifest["prompts"]})
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    scenarios = [args.scenario] if args.scenario else ["llm_trickle", "mcp_slow", "mcp_contradiction", "sql_lock"]
    rows = []
    for scenario in scenarios:
        for mode in ("single", "collab"):
            row = run_case(scenario, mode, admin_url, token, folder)
            rows.append(row)
            with (folder / "case_results.jsonl").open("a") as output:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps({"case_id": row["case_id"], "status": row["status"]}), flush=True)
    summary = {"run_id": run_id, "cases": len(rows), "passed": sum(row["status"] == "pass" for row in rows),
               "incomplete": sum(row["status"] == "incomplete" for row in rows), "failed_checks": sum(not value for row in rows for value in row["checks"].values()),
               "locked_release_ready": False, "report_dir": str(folder)}
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (folder / "report.html").write_text("<!doctype html><meta charset='utf-8'><title>Specialist transport checks</title><pre>" + html.escape(json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2)) + "</pre>")
    print(json.dumps(summary))
    return 0 if summary["passed"] == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
