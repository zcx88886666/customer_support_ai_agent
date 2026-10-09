"""Opt-in, bounded real-model replay of synthetic intent dialogues."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from uuid import uuid4


def run_cases(cases, run_id, seed_clock, stop_after_cost_usd, *, replay=None, meter_factory=None):
    if replay is None or meter_factory is None:
        from evals.runners.run_intent_dialogue import run_case
        from evals.runners.run_paired_model import ProviderUsage
        replay = replay or run_case
        meter_factory = meter_factory or ProviderUsage
    rows = []
    reported_cost = 0.0
    stop_reason = None
    for case in cases:
        if stop_reason:
            rows.append({"case_id": case["case_id"], "risk_tier": case["risk_tier"],
                         "status": "incomplete", "error": stop_reason})
            continue
        with meter_factory() as meter:
            try:
                row = replay(case, run_id, seed_clock)
            except Exception as exc:
                row = {"case_id": case["case_id"], "risk_tier": case["risk_tier"],
                       "status": "incomplete", "error": type(exc).__name__ + ": " + str(exc)}
        usage = meter.summary()
        row["provider_usage"] = usage
        rows.append(row)
        reported_cost += usage["reported_cost_usd"]
        if usage["cost_missing_calls"]:
            stop_reason = "provider_cost_unreported"
        elif reported_cost >= stop_after_cost_usd:
            stop_reason = "provider_cost_stop"
    return rows, stop_reason


def summarize(cases, rows, *, full_scope, stop_reason):
    counts = Counter(row["status"] for row in rows)
    usage_rows = [row["provider_usage"] for row in rows if "provider_usage" in row]
    usage = {"calls": sum(row["calls"] for row in usage_rows),
             "input_tokens": sum(row["input_tokens"] for row in usage_rows),
             "output_tokens": sum(row["output_tokens"] for row in usage_rows),
             "reported_cost_usd": round(sum(row["reported_cost_usd"] for row in usage_rows), 8),
             "cost_missing_calls": sum(row["cost_missing_calls"] for row in usage_rows),
             "http_errors": sum(row["http_errors"] for row in usage_rows)}
    return {"suite": "intent_dialogue_dev_v1_live_model", "scope": "full" if full_scope else "targeted",
            "unique_cases": len(cases), "replayed_cases": len(usage_rows), "counts": dict(counts),
            "provider_usage": usage, "stop_reason": stop_reason,
            "critical_failures": [row["case_id"] for row in rows if row["risk_tier"] == "critical" and row["status"] != "pass"],
            "development_pass": full_scope and len(rows) == len(cases) and counts.get("pass", 0) == len(cases)
                                and stop_reason is None and usage["cost_missing_calls"] == 0,
            "release_gate_pass": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", action="append", default=[], help="Replay only selected development case IDs")
    parser.add_argument("--stop-after-reported-cost-usd", type=float, default=0.05,
                        help="Stop before the next case once this provider-reported cost is reached; one case may cross it")
    args = parser.parse_args()
    if not math.isfinite(args.stop_after_reported_cost_usd) or args.stop_after_reported_cost_usd <= 0:
        parser.error("The provider cost stop must be finite and positive")
    if os.getenv("AUTH_MODE", "mock") != "mock":
        parser.error("This development runner requires AUTH_MODE=mock")
    if os.getenv("LANGFUSE_PUBLIC_KEY") or os.getenv("LANGFUSE_SECRET_KEY"):
        parser.error("Unset Langfuse keys for this local development replay")

    from evals.runners.run_intent_dialogue import DATASET, load_cases
    from evals.runners.run_paired_model import load_local_key
    from resolveai.models_config import ModelRegistry
    from resolveai.prompts import PromptRegistry, ROOT

    all_cases = load_cases()
    unknown = set(args.case_id) - {case["case_id"] for case in all_cases}
    if unknown:
        parser.error("Unknown case IDs: " + ", ".join(sorted(unknown)))
    full_scope = not args.case_id
    cases = [case for case in all_cases if full_scope or case["case_id"] in set(args.case_id)]
    load_local_key()
    release = os.getenv("PROMPT_RELEASE", "release-v1")
    prompts = PromptRegistry(release)
    models = ModelRegistry()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-dialogue-live-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    seed_clock = datetime.now(timezone.utc)
    rows, stop_reason = run_cases(cases, run_id, seed_clock, args.stop_after_reported_cost_usd)
    summary = {"run_id": run_id, **summarize(cases, rows, full_scope=full_scope, stop_reason=stop_reason)}
    manifest = {"run_id": run_id, "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
                "selected_case_ids": [case["case_id"] for case in cases], "seed_clock": seed_clock.isoformat(),
                "source": "author-written synthetic development labels", "prompt_release_id": release,
                "prompt_hashes": prompts.manifest["prompts"], "source_git_commit": prompts.manifest.get("source_git_commit"),
                "models_by_task": {name: task.model for name, task in models.tasks.items()},
                "resource_policy_sha256": models.resource_policy_sha256,
                "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                  ("apps/api/resolveai/agent.py", "evals/runners/run_intent_dialogue.py",
                                   "evals/runners/run_intent_dialogue_live.py", "evals/runners/run_paired_model.py")},
                "stop_after_reported_cost_usd": args.stop_after_reported_cost_usd,
                "scorer_version": "intent-dialogue-dev-v1", "auth_mode": "mock", "database": "isolated-sqlite-per-case"}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["<!doctype html><html lang='en'><meta charset='utf-8'><title>Live intent dialogue development report</title>",
             f"<h1>{html.escape(run_id)}</h1><p>Development only; {len(cases)} synthetic cases.</p>",
             "<table border='1'><tr><th>Case</th><th>Status</th><th>Checks or error</th></tr>"]
    for row in rows:
        lines.append("<tr><td>" + html.escape(row["case_id"]) + "</td><td>" + html.escape(row["status"])
                     + "</td><td>" + html.escape(json.dumps(row.get("checks", row.get("error")), ensure_ascii=False)) + "</td></tr>")
    lines.append("</table></html>")
    (folder / "report.html").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({**summary, "report": str(folder)}, ensure_ascii=False))
    raise SystemExit(0 if summary["development_pass"] or not full_scope and all(row["status"] == "pass" for row in rows)
                     and stop_reason is None else 1)


if __name__ == "__main__":
    main()
