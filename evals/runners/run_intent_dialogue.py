"""Isolated multi-turn HTTP evaluation of intent, slot, clarification, and handoff behavior."""

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

from resolveai import models as m
from resolveai.api import app
from resolveai.config import settings
from resolveai.db import Base, get_db, make_engine
from resolveai.prompts import PromptRegistry, ROOT
from resolveai.seed import seed_demo


DATASET = ROOT / "evals/datasets/intent_dialogue_dev_v1.jsonl"


def load_cases() -> list[dict]:
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case.get("case_id") for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("Intent dialogue cases must have unique IDs")
    for case in cases:
        if (case.get("schema_version") != "v1" or case.get("suite") != "intent_dialogue_dev_v1"
                or case.get("split") != "dev" or case.get("review", {}).get("status") != "pending"
                or not case.get("group_keys") or not case.get("dialogue_script")
                or len(case["dialogue_script"]) != len(case["gold"]["turns"])):
            raise ValueError(f"Invalid intent dialogue case {case.get('case_id')}")
    return cases


def score(case: dict, turns: list[dict], db) -> dict[str, bool]:
    checks = {"turn_count": len(turns) == len(case["gold"]["turns"])}
    for index, (actual, expected) in enumerate(zip(turns, case["gold"]["turns"])):
        for field in ("http_status", "status", "code", "route", "plan_revision", "return_count", "ledger_count", "ticket_count"):
            if field in expected:
                checks[f"turn_{index}_{field}"] = actual.get(field) == expected[field]
        if "intents" in expected:
            checks[f"turn_{index}_intents"] = set(actual.get("intents", [])) == set(expected["intents"])
        if "answer_contains" in expected:
            checks[f"turn_{index}_answer"] = all(fragment in actual.get("answer", "") for fragment in expected["answer_contains"])
        if "shipment_options" in expected:
            checks[f"turn_{index}_shipment_options"] = set(actual.get("shipment_options", [])) == set(expected["shipment_options"])
    returns = db.scalars(select(m.ReturnRequest)).all()
    ledgers = db.scalars(select(m.RefundLedger)).all()
    tickets = db.scalars(select(m.Ticket)).all()
    audits = Counter(event.action for event in db.scalars(select(m.AuditEvent)).all())
    final = case["gold"]["final"]
    checks.update({
        "final_return_count": len(returns) == final["return_count"],
        "final_ledger_count": len(ledgers) == final["ledger_count"],
        "final_ticket_count": len(tickets) == final["ticket_count"],
        "return_owner": all(request.customer_id == case["fixture"]["customer_id"] for request in returns),
        "ticket_owner": all(ticket.customer_id == case["fixture"]["customer_id"] for ticket in tickets),
        "audit_actions": dict(audits) == final["audit_actions"],
    })
    return checks


def run_case(case: dict, run_id: str, seed_clock: datetime) -> dict:
    with tempfile.TemporaryDirectory(prefix="resolveai-intent-dialogue-") as temp:
        engine = make_engine(f"sqlite:///{Path(temp) / 'case.db'}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as db:
            seed_demo(db, seed_clock)
            if case["fixture"].get("extra_shipment"):
                extra = case["fixture"]["extra_shipment"]
                db.add(m.Shipment(id=extra["id"], order_id=extra["order_id"], status="delivered",
                                  delivered_at=seed_clock - timedelta(days=1), version=1))

        def override_db():
            with factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        started = time.perf_counter()
        try:
            turns = []
            with TestClient(app) as client:
                for turn in case["dialogue_script"]:
                    response = client.post(
                        "/chat",
                        json={**turn, "thread_id": case["case_id"], "agent_mode": case["fixture"].get("agent_mode", "single")},
                        headers={"x-mock-actor": case["fixture"]["customer_id"], "x-mock-role": "customer",
                                 "x-eval-run-id": run_id, "x-eval-case-id": case["case_id"]},
                    )
                    payload = response.json()
                    with factory() as db:
                        counts = {"return_count": len(db.scalars(select(m.ReturnRequest)).all()),
                                  "ledger_count": len(db.scalars(select(m.RefundLedger)).all()),
                                  "ticket_count": len(db.scalars(select(m.Ticket)).all())}
                    turns.append({"http_status": response.status_code, "status": payload.get("status"),
                                  "code": payload.get("code"), "route": payload.get("route", {}).get("route"),
                                  "intents": payload.get("route", {}).get("intents", []),
                                  "plan_revision": payload.get("plan_revision"), "answer": payload.get("answer", ""),
                                  "shipment_options": payload.get("shipment_options", []),
                                  "trace_id": response.headers.get("x-trace-id"), **counts})
            with factory() as db:
                checks = score(case, turns, db)
            return {"case_id": case["case_id"], "split": case["split"], "risk_tier": case["risk_tier"],
                    "status": "pass" if all(checks.values()) else "fail", "checks": checks,
                    "turns": [{key: value for key, value in turn.items() if key != "answer"} for turn in turns],
                    "seed_clock": seed_clock.isoformat(),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
        finally:
            app.dependency_overrides.clear()
            engine.dispose()


def main() -> None:
    if settings.auth_mode != "mock":
        raise SystemExit("Intent dialogue development runner requires AUTH_MODE=mock")
    cases = load_cases()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-intent-dialogue-" + uuid4().hex[:6]
    seed_clock = datetime.now(timezone.utc)
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    results = []
    for case in cases:
        try:
            results.append(run_case(case, run_id, seed_clock))
        except Exception as exc:
            results.append({"case_id": case["case_id"], "split": case["split"], "risk_tier": case["risk_tier"],
                            "status": "incomplete", "error": type(exc).__name__ + ": " + str(exc)})
    counts = Counter(row["status"] for row in results)
    development_pass = counts.get("pass", 0) == len(cases)
    summary = {"run_id": run_id, "suite": "intent_dialogue_dev_v1", "unique_cases": len(cases),
               "executions": len(results), "counts": dict(counts),
               "critical_failures": [row["case_id"] for row in results if row["risk_tier"] == "critical" and row["status"] != "pass"],
               "development_pass": development_pass, "release_gate_pass": False}
    registry = PromptRegistry("release-v1")
    manifest = {"run_id": run_id, "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
                "seed_clock": seed_clock.isoformat(), "source": "author-written synthetic development labels",
                "prompt_release_id": "release-v1", "prompt_hashes": registry.manifest["prompts"],
                "source_git_commit": registry.manifest.get("source_git_commit"), "model": "deterministic-mock",
                "scorer_version": "intent-dialogue-dev-v1", "auth_mode": "mock", "database": "isolated-sqlite-per-case"}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = ["<html><meta charset='utf-8'><title>ResolveAI intent dialogue report</title><body>",
            f"<h1>Run {html.escape(run_id)}</h1>",
            f"<p>{len(cases)} development cases; {counts.get('pass', 0)} pass, {counts.get('fail', 0)} fail, {counts.get('incomplete', 0)} incomplete.</p>",
            "<table border='1'><tr><th>Case</th><th>Status</th><th>Checks</th></tr>"]
    for row in results:
        rows.append(f"<tr><td>{html.escape(row['case_id'])}</td><td>{html.escape(row['status'])}</td><td>{html.escape(json.dumps(row.get('checks', row.get('error')), ensure_ascii=False))}</td></tr>")
    rows.append("</table></body></html>")
    (folder / "report.html").write_text("\n".join(rows), encoding="utf-8")
    print(json.dumps({**summary, "report": str(folder)}, ensure_ascii=False))
    raise SystemExit(0 if development_pass else 1)


if __name__ == "__main__":
    main()
