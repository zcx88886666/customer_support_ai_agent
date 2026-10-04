"""Development replay of Agent turns followed by committed cross-role business actions."""

from __future__ import annotations

import hashlib
import html
import json
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from resolveai import models as m, worker
from resolveai.api import app
from resolveai.config import settings
from resolveai.db import Base, get_db, make_engine
from resolveai.prompts import PromptRegistry, ROOT

if __package__:
    from . import run_business
else:
    import run_business


DATASET = ROOT / "evals/datasets/core_business_dev_v1.jsonl"


def load_cases() -> list[dict]:
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case.get("case_id") for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("Core business cases must have unique IDs")
    for case in cases:
        if (case.get("schema_version") != "v1" or case.get("suite") != "core_business_dev_v1"
                or case.get("split") != "dev" or not case.get("dialogue_script")
                or not case.get("group_keys") or not case.get("gold")
                or case.get("review", {}).get("status") != "pending"
                or case.get("terminal_scenario") != "approved_refund"):
            raise ValueError(f"Invalid development case {case.get('case_id')}")
    return cases


def score_chat_phase(case: dict, chat_rows: list[dict], chat_return_ids: list[str],
                     chat_ledger_count: int, observations: dict) -> dict[str, bool]:
    payload = chat_rows[-1]["payload"]
    return {
        "chat_http_status": all(row["http_status"] == 200 for row in chat_rows),
        "chat_status": payload.get("status") == case["gold"]["chat_status"],
        "chat_route": payload.get("route", {}).get("route") == case["gold"]["chat_route"],
        "chat_committed_return": len(chat_return_ids) == case["gold"]["chat_return_count"]
            and payload.get("return_id") in chat_return_ids
            and observations.get("return_id") == payload.get("return_id"),
        "chat_no_early_refund": chat_ledger_count == case["gold"]["chat_ledger_count"],
    }


def run_case(case: dict, run_id: str, seed_clock: datetime) -> dict:
    with tempfile.TemporaryDirectory(prefix="resolveai-core-business-") as temp:
        engine = make_engine(f"sqlite:///{Path(temp) / 'case.db'}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as db:
            run_business.seed_demo(db, seed_clock)

        def override_db():
            with factory() as db:
                yield db

        original_worker_factory = worker.SessionLocal
        app.dependency_overrides[get_db] = override_db
        worker.SessionLocal = factory
        started = time.perf_counter()
        try:
            with TestClient(app) as client:
                chat_rows = []
                for turn in case["dialogue_script"]:
                    response = client.post(
                        "/chat",
                        json={**turn, "thread_id": case["case_id"], "order_id": case["fixture"]["order_id"],
                              "idempotency_key": case["case_id"], "agent_mode": "single"},
                        headers={"x-mock-actor": case["fixture"]["customer_id"], "x-mock-role": "customer",
                                 "x-eval-run-id": run_id, "x-eval-case-id": case["case_id"]},
                    )
                    chat_rows.append({"http_status": response.status_code, "payload": response.json(),
                                      "trace_id": response.headers.get("x-trace-id")})
                with factory() as db:
                    chat_return_ids = [row.id for row in db.scalars(select(m.ReturnRequest)).all()]
                    chat_ledger_count = len(db.scalars(select(m.RefundLedger)).all())
                terminal_case = {**case, "scenario": case["terminal_scenario"]}
                statuses, observations = run_business.execute(client, terminal_case, run_id)
            with factory() as db:
                checks = run_business.score(terminal_case, statuses, observations, db)
            last_chat = chat_rows[-1]
            payload = last_chat["payload"]
            checks.update(score_chat_phase(case, chat_rows, chat_return_ids, chat_ledger_count, observations))
            return {"case_id": case["case_id"], "split": case["split"], "risk_tier": case["risk_tier"],
                    "status": "pass" if all(checks.values()) else "fail", "checks": checks,
                    "chat_status": payload.get("status"), "http_statuses": statuses,
                    "trace_ids": [row["trace_id"] for row in chat_rows],
                    "seed_clock": seed_clock.isoformat(),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
        finally:
            worker.SessionLocal = original_worker_factory
            app.dependency_overrides.clear()
            engine.dispose()


def main() -> None:
    if settings.auth_mode != "mock":
        raise SystemExit("Core business development runner requires AUTH_MODE=mock")
    cases = load_cases()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-core-" + uuid4().hex[:6]
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
    summary = {"run_id": run_id, "suite": "core_business_dev_v1", "unique_cases": len(cases),
               "executions": len(results), "counts": dict(counts),
               "critical_failures": [row["case_id"] for row in results if row["risk_tier"] == "critical" and row["status"] != "pass"],
               "development_pass": development_pass, "v6_minimum_cases_met": len(cases) >= 30,
               "release_gate_pass": False}
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "seed_clock": seed_clock.isoformat(),
                "scorer_version": "core-business-dev-v1", "auth_mode": "mock", "database": "isolated-sqlite-per-case",
                "model": "deterministic-mock", "prompt_release_id": "release-v1",
                "prompt_hashes": PromptRegistry("release-v1").manifest["prompts"]}
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = ["<html><meta charset='utf-8'><title>ResolveAI core business development report</title><body>",
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
