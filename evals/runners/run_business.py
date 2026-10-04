"""Isolated cross-role HTTP and worker terminal-state development evaluation."""

from __future__ import annotations

import hashlib
import html
import json
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from resolveai import models as m, worker
from resolveai.api import app
from resolveai.config import settings
from resolveai.db import Base, get_db, make_engine
from resolveai.prompts import ROOT
from resolveai.seed import seed_demo
if __package__:
    from .score import _ledger_authorized
else:
    from score import _ledger_authorized


DATASET = ROOT / "evals/datasets/business_workflows_v2.jsonl"


def load_cases() -> list[dict]:
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case["case_id"] for case in cases]
    if len(cases) != 6 or len(ids) != len(set(ids)) or any(case.get("schema_version") != "v2" or case.get("split") != "dev" or not case.get("gold") for case in cases):
        raise ValueError("Invalid business workflow development dataset")
    return cases


def execute(client: TestClient | None, case: dict, run_id: str, *, post_request=None, issue_worker=None) -> tuple[dict, dict]:
    fixture = case["fixture"]
    customer = fixture["customer_id"]
    order_id = fixture["order_id"]
    item_id = fixture["item_id"]
    statuses: dict[str, int] = {}
    observations: dict = {"preapproval_issued": 0, "worker_replay_issued": 0}
    issue_worker = issue_worker or worker.issue_approved_once

    def post(name: str, path: str, role: str, actor: str, body: dict) -> dict:
        if post_request:
            status_code, payload = post_request(path, role, actor, body)
        else:
            response = client.post(path, json=body, headers={"x-mock-actor": actor, "x-mock-role": role, "x-eval-run-id": run_id, "x-eval-case-id": case["case_id"]})
            status_code, payload = response.status_code, response.json()
        statuses[name] = status_code
        return payload

    def create(name: str, reason: str, key: str, confirmed: bool = True) -> dict:
        return post(name, "/returns", "customer", customer, {"order_id": order_id, "order_item_id": item_id, "quantity": 1, "reason": reason, "confirmed": confirmed, "idempotency_key": key})

    if case["scenario"] == "unconfirmed_return":
        create("unconfirmed_return_refused", "synthetic return", case["case_id"], confirmed=False)
        return statuses, observations
    if case["scenario"] == "expired_window":
        create("expired_return_refused", "synthetic return", case["case_id"])
        return statuses, observations

    if case["scenario"] == "cross_customer_denial":
        create("foreign_return_refused", "foreign attempt", case["case_id"])
        return statuses, observations

    first = create("create_return", "synthetic return", case["case_id"])
    return_id = first["id"]
    observations["return_id"] = return_id
    if case["scenario"] == "approved_refund":
        retry = create("repeat_return", "synthetic return", case["case_id"])
        observations["same_return_on_retry"] = retry["id"] == return_id
    post("receipt", f"/warehouse/returns/{return_id}/receipt", "warehouse", "warehouse-test", {"quantity": 1})
    if case["scenario"] == "inspection_exception":
        post("inspection_fail", f"/warehouse/returns/{return_id}/inspection", "warehouse", "warehouse-test", {"passed": False, "note": "damaged"})
        post("proposal_refused", f"/returns/{return_id}/proposal", "warehouse", "warehouse-test", {})
        observations["worker_issued"] = len(issue_worker())
        return statuses, observations
    post("inspection", f"/warehouse/returns/{return_id}/inspection", "warehouse", "warehouse-test", {"passed": True, "note": "intact"})
    proposal = post("proposal", f"/returns/{return_id}/proposal", "warehouse", "warehouse-test", {})
    proposal_id = proposal["id"]
    observations["preapproval_issued"] = len(issue_worker())
    if case["scenario"] == "stale_proposal":
        create("second_return", "second unit", case["case_id"] + ":second")
        post("stale_approve_refused", f"/supervisor/proposals/{proposal_id}/decision", "supervisor", "supervisor-test", {"approve": True})
        proposal = post("new_proposal", f"/returns/{return_id}/proposal", "warehouse", "warehouse-test", {})
        observations["proposal_replaced"] = proposal["id"] != proposal_id
        proposal_id = proposal["id"]
    post("approve", f"/supervisor/proposals/{proposal_id}/decision", "supervisor", "supervisor-test", {"approve": True})
    observations["worker_issued"] = len(issue_worker())
    observations["worker_replay_issued"] = len(issue_worker())
    return statuses, observations


def score(case: dict, statuses: dict, observations: dict, db) -> dict[str, bool]:
    gold = case["gold"]
    order_id = case["fixture"]["order_id"]
    order = db.get(m.Order, order_id)
    returns = db.scalars(select(m.ReturnRequest)).all()
    proposals = db.scalars(select(m.RefundProposal)).all()
    approvals = db.scalars(select(m.Approval)).all()
    ledgers = db.scalars(select(m.RefundLedger)).all()
    audits = db.scalars(select(m.AuditEvent)).all()
    checks = {
        "http_statuses": statuses == gold["http_statuses"],
        "return_count": len(returns) == gold["return_count"],
        "return_statuses": sorted(request.status for request in returns) == gold["return_statuses"],
        "proposal_statuses": sorted(proposal.status for proposal in proposals) == gold["proposal_statuses"],
        "approval_decisions": sorted(approval.decision for approval in approvals) == gold["approval_decisions"],
        "ledger_count": len(ledgers) == gold["ledger_count"],
        "order_version": order.version == gold["order_version"],
        "audit_actions": dict(Counter(event.action for event in audits)) == gold["audit_actions"],
        "return_owner": all(request.customer_id == case["fixture"]["customer_id"] and request.order_id == order_id for request in returns),
        "approval_before_refund": observations.get("preapproval_issued") == 0,
        "worker_replay": observations.get("worker_replay_issued") == 0,
        "refund_authorized": all(_ledger_authorized(db, ledger) for ledger in ledgers),
    }
    if case["scenario"] == "approved_refund":
        checks["return_idempotent"] = observations.get("same_return_on_retry") is True
    if case["scenario"] == "stale_proposal":
        checks["proposal_replaced"] = observations.get("proposal_replaced") is True
    if gold["return_count"] > 0:
        checks["return_id"] = any(request.id == observations.get("return_id") for request in returns)
    if ledgers:
        checks["worker_issued_once"] = observations.get("worker_issued") == 1
        checks["amount"] = all(ledger.amount_cents == db.get(m.OrderItem, ledger.order_item_id).paid_cents * db.get(m.ReturnRequest, db.get(m.RefundProposal, ledger.proposal_id).return_id).quantity // db.get(m.OrderItem, ledger.order_item_id).quantity for ledger in ledgers)
    else:
        checks["worker_issued_none"] = observations.get("worker_issued", 0) == 0
    return checks


def run_case(case: dict, run_id: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="resolveai-business-eval-") as temp:
        engine = make_engine(f"sqlite:///{Path(temp) / 'case.db'}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as db:
            seed_demo(db, datetime.now(timezone.utc))
        if case["scenario"] == "expired_window":
            with factory.begin() as db:
                shipment = db.scalar(select(m.Shipment).where(m.Shipment.order_id == case["fixture"]["order_id"]))
                shipment.delivered_at = datetime.now(timezone.utc) - timedelta(days=20)

        def override_db():
            with factory() as db:
                yield db

        original_worker_factory = worker.SessionLocal
        app.dependency_overrides[get_db] = override_db
        worker.SessionLocal = factory
        started = time.perf_counter()
        try:
            with TestClient(app) as client:
                statuses, observations = execute(client, case, run_id)
            with factory() as db:
                checks = score(case, statuses, observations, db)
            return {"case_id": case["case_id"], "split": case["split"], "risk_tier": case["risk_tier"], "status": "pass" if all(checks.values()) else "fail", "checks": checks, "http_statuses": statuses, "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
        finally:
            worker.SessionLocal = original_worker_factory
            app.dependency_overrides.clear()
            engine.dispose()


def main() -> None:
    if settings.auth_mode != "mock":
        raise SystemExit("Business development runner requires AUTH_MODE=mock")
    cases = load_cases()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-business-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    results = []
    for case in cases:
        try:
            results.append(run_case(case, run_id))
        except Exception as exc:
            results.append({"case_id": case["case_id"], "split": case["split"], "risk_tier": case["risk_tier"], "status": "incomplete", "error": type(exc).__name__ + ": " + str(exc)})
    counts = Counter(row["status"] for row in results)
    summary = {"run_id": run_id, "suite": "business_workflows_v2", "unique_cases": len(cases), "executions": len(results), "counts": dict(counts), "critical_failures": [row["case_id"] for row in results if row["risk_tier"] == "critical" and row["status"] != "pass"], "gate_pass": counts.get("pass") == len(cases)}
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "scorer_version": "business-v2", "auth_mode": "mock", "database": "isolated-sqlite-per-case", "model": "none", "seed": "demo-current-clock"}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = ["<html><meta charset='utf-8'><title>ResolveAI business workflow report</title><body>", f"<h1>Run {html.escape(run_id)}</h1>", f"<p>{len(cases)} cases; {counts.get('pass', 0)} pass, {counts.get('fail', 0)} fail, {counts.get('incomplete', 0)} incomplete.</p>", "<table border='1'><tr><th>Case</th><th>Status</th><th>Checks</th></tr>"]
    for row in results:
        rows.append(f"<tr><td>{html.escape(row['case_id'])}</td><td>{html.escape(row['status'])}</td><td>{html.escape(json.dumps(row.get('checks', row.get('error')), ensure_ascii=False))}</td></tr>")
    rows.append("</table></body></html>")
    (folder / "report.html").write_text("\n".join(rows), encoding="utf-8")
    print(json.dumps({**summary, "report": str(folder)}, ensure_ascii=False))
    raise SystemExit(0 if summary["gate_pass"] else 1)


if __name__ == "__main__":
    main()
