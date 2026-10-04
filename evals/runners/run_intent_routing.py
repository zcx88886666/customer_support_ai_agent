"""Replay author-written intent labels against the mock or configured low-cost model."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from resolveai.agent import classify
from resolveai.models_config import ModelRegistry
from resolveai.prompts import PromptRegistry, ROOT

if __package__:
    from .run_paired_model import ProviderUsage, load_local_key
else:
    from run_paired_model import ProviderUsage, load_local_key


DATASET = ROOT / "evals/datasets/intent_routing_dev_v1.jsonl"


def load_cases() -> list[dict]:
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case["case_id"] for case in cases]
    if len(cases) != 30 or len(set(ids)) != 30:
        raise ValueError("Intent development set requires 30 unique cases")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    args = parser.parse_args()
    if args.mode == "live":
        load_local_key()
    else:
        os.environ["OPENROUTER_API_KEY"] = ""
    cases = load_cases()
    registry = PromptRegistry("release-v1")
    model = ModelRegistry().get("intent").model if args.mode == "live" else "deterministic-mock"
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-intent-" + args.mode + "-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    results = []
    for case in cases:
        started = time.perf_counter()
        with ProviderUsage() as usage:
            try:
                decision = classify(case["message"])
                route = decision.route
                intents = decision.intents
                status = "pass" if route in case.get("acceptable_routes", [case["route"]]) and set(intents) == set(case["intents"]) else "fail"
                error = None
            except Exception as exc:
                route, intents, status = None, [], "incomplete"
                error = type(exc).__name__
        results.append({"case_id": case["case_id"], "risk_tier": case["risk_tier"], "expected_route": case["route"], "actual_route": route, "expected_intents": case["intents"], "actual_intents": intents, "status": status, "error": error, "latency_ms": round((time.perf_counter() - started) * 1000, 2), "provider_usage": usage.summary()})
    counts = Counter(row["status"] for row in results)
    confusion = Counter(f"{row['expected_route']} -> {row['actual_route']}" for row in results)
    latencies = [row["latency_ms"] for row in results]
    summary = {"run_id": run_id, "suite": "intent_routing_dev_v1", "mode": args.mode, "model": model, "cases": len(results), "counts": dict(counts), "critical_failures": [row["case_id"] for row in results if row["risk_tier"] == "critical" and row["status"] != "pass"], "route_confusion": dict(sorted(confusion.items())), "median_latency_ms": round(statistics.median(latencies), 2), "provider_calls": sum(row["provider_usage"]["calls"] for row in results), "provider_reported_cost_usd": round(sum(row["provider_usage"]["reported_cost_usd"] for row in results), 8), "gate_pass": counts.get("pass", 0) == len(results)}
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "source": "author-written synthetic development labels", "prompt_release_id": "release-v1", "prompt_hashes": registry.manifest["prompts"], "model": model, "scorer_version": "intent-route-v1"}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = ["<html><meta charset='utf-8'><title>ResolveAI intent development report</title><body>", f"<h1>{html.escape(run_id)}</h1>", f"<p>{len(results)} author-written cases; {counts.get('pass', 0)} pass, {counts.get('fail', 0)} fail, {counts.get('incomplete', 0)} incomplete.</p>", "<table border='1'><tr><th>Case</th><th>Expected</th><th>Actual</th><th>Status</th></tr>"]
    for row in results:
        rows.append(f"<tr><td>{html.escape(row['case_id'])}</td><td>{html.escape(row['expected_route'])}</td><td>{html.escape(str(row['actual_route']))}</td><td>{html.escape(row['status'])}</td></tr>")
    rows.append("</table></body></html>")
    (folder / "report.html").write_text("\n".join(rows), encoding="utf-8")
    print(json.dumps({**summary, "report": str(folder)}, ensure_ascii=False))
    raise SystemExit(0 if summary["gate_pass"] else 1)


if __name__ == "__main__":
    main()
