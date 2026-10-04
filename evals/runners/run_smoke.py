"""Isolated HTTP replay and deterministic safety scoring for synthetic cases."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from resolveai.api import app
from resolveai.db import Base, get_db, make_engine
from resolveai.prompts import PromptRegistry, ROOT
from resolveai.seed import seed_demo
from resolveai.telemetry import score as cloud_score
if __package__:
    from .score import score_case
else:
    from score import score_case


DATASET = ROOT / "evals/datasets/smoke_demo.jsonl"


def load_cases() -> list[dict]:
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case["case_id"] for case in cases]
    if len(ids) != len(set(ids)) or len(cases) != 30:
        raise ValueError("smoke_demo requires 30 unique cases")
    for case in cases:
        if case["schema_version"] != "v1" or not case.get("gold") or not case.get("dialogue_script"):
            raise ValueError(f"Invalid case {case.get('case_id')}")
    return cases


def run_case(case: dict, mode: str, run_id: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="resolveai-eval-") as temp:
        engine = make_engine(f"sqlite:///{Path(temp) / 'case.db'}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as db:
            seed_demo(db, datetime.now(timezone.utc))

        def override_db():
            with factory() as db:
                yield db

        app.dependency_overrides[get_db] = override_db
        started = time.perf_counter()
        try:
            with TestClient(app) as client:
                result = None
                for turn in case["dialogue_script"]:
                    body = {**turn, "thread_id": case["case_id"], "agent_mode": mode}
                    if case["fixture"].get("order_id"):
                        body["order_id"] = case["fixture"]["order_id"]
                    result = client.post("/chat", json=body, headers={"x-mock-actor": case["fixture"]["customer_id"], "x-mock-role": "customer", "x-eval-run-id": run_id, "x-eval-case-id": case["case_id"]})
                assert result is not None
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            payload = result.json()
            with factory() as db:
                checks, ledger_count = score_case(case, payload, result.status_code, db)
            return {"case_id": case["case_id"], "suite": case["suite"], "split": case["split"], "risk_tier": case["risk_tier"], "agent_mode": mode, "status": "pass" if all(checks.values()) else "fail", "checks": checks, "http_status": result.status_code, "response_status": payload.get("status"), "error_code": payload.get("code"), "latency_ms": elapsed_ms, "specialist_count": len(payload.get("findings", [])), "ledger_count": ledger_count, "trace_id": result.headers.get("x-trace-id")}
        finally:
            app.dependency_overrides.clear()
            engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", default="release-v1")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    cases = load_cases()
    registry = PromptRegistry(args.release)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    results = []
    for case in cases:
        modes = ["single", "collab"] if "collaboration" in case.get("tags", []) else ["single"]
        for mode in modes:
            try:
                result = run_case(case, mode, run_id)
            except Exception as exc:
                result = {"case_id": case["case_id"], "suite": case["suite"], "split": case["split"], "risk_tier": case["risk_tier"], "agent_mode": mode, "status": "incomplete", "error": type(exc).__name__ + ": " + str(exc)}
            if result.get("trace_id") and result["status"] in ("pass", "fail"):
                result["cloud_score_submitted"] = cloud_score(result["trace_id"], "task_success", 1.0 if result["status"] == "pass" else 0.0)
            results.append(result)
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    counts = Counter(row["status"] for row in results)
    critical_failures = [row["case_id"] for row in results if row["risk_tier"] == "critical" and row["status"] != "pass"]
    telemetry = "not_configured" if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")) else "submitted_unreconciled" if all(row.get("cloud_score_submitted") for row in results if row.get("trace_id")) else "incomplete"
    summary = {"run_id": run_id, "counts": dict(counts), "unique_cases": len(cases), "executions": len(results), "critical_failures": critical_failures, "gate_pass": counts.get("fail", 0) == 0 and counts.get("incomplete", 0) == 0, "telemetry_sync": telemetry}
    (folder / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "prompt_release_id": args.release, "prompt_hashes": registry.manifest["prompts"], "source_git_commit": registry.manifest.get("source_git_commit"), "policy_bundle_id": "policy-demo-v1", "model": "deterministic-mock", "scorer_version": "smoke-v2", "seed": "demo-fixed-v1"}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = ["<html><meta charset='utf-8'><title>ResolveAI smoke report</title><body>", f"<h1>Run {html.escape(run_id)}</h1>", f"<p>{len(cases)} unique cases; {len(results)} executions; {counts.get('pass', 0)} pass, {counts.get('fail', 0)} fail, {counts.get('incomplete', 0)} incomplete.</p>", "<table border='1'><tr><th>Case</th><th>Split</th><th>Mode</th><th>Status</th><th>Checks</th></tr>"]
    for row in results:
        lines.append(f"<tr><td>{html.escape(row['case_id'])}</td><td>{html.escape(row['split'])}</td><td>{html.escape(row['agent_mode'])}</td><td>{html.escape(row['status'])}</td><td>{html.escape(json.dumps(row.get('checks', row.get('error')), ensure_ascii=False))}</td></tr>")
    lines.append("</table></body></html>")
    (folder / "report.html").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({**summary, "report": str(folder)}, ensure_ascii=False))
    raise SystemExit(0 if summary["gate_pass"] else 1)


if __name__ == "__main__":
    main()
