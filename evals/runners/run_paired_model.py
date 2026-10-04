"""Small real-model single/collaborative comparison on identical synthetic cases."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import random
import statistics
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from resolveai.models_config import ModelRegistry
from resolveai.prompts import PromptRegistry, ROOT
if __package__:
    from .run_smoke import DATASET, load_cases, run_case
else:
    from run_smoke import DATASET, load_cases, run_case


def load_local_key() -> None:
    if os.getenv("OPENROUTER_API_KEY"):
        return
    path = ROOT / ".env"
    if not path.is_file():
        raise RuntimeError("OPENROUTER_API_KEY is unavailable")
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == "OPENROUTER_API_KEY":
            value = value.strip().strip("\"'")
            if value:
                os.environ["OPENROUTER_API_KEY"] = value
                return
    raise RuntimeError("OPENROUTER_API_KEY is unavailable")


class ProviderUsage:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self.cost_missing = 0
        self.http_errors = 0
        self._original = None

    def __enter__(self):
        self._original = httpx.Client.post

        def capture(client, url, *args, **kwargs):
            response = self._original(client, url, *args, **kwargs)
            if str(url).startswith("https://openrouter.ai/api/v1/chat/completions"):
                with self.lock:
                    self.calls += 1
                    if response.status_code >= 400:
                        self.http_errors += 1
                    try:
                        usage = response.json().get("usage") or {}
                    except (ValueError, AttributeError):
                        usage = {}
                    if isinstance(usage.get("prompt_tokens"), int) and usage["prompt_tokens"] >= 0:
                        self.input_tokens += usage["prompt_tokens"]
                    if isinstance(usage.get("completion_tokens"), int) and usage["completion_tokens"] >= 0:
                        self.output_tokens += usage["completion_tokens"]
                    if isinstance(usage.get("cost"), (int, float)) and usage["cost"] >= 0:
                        self.cost_usd += usage["cost"]
                    else:
                        self.cost_missing += 1
            return response

        httpx.Client.post = capture
        return self

    def __exit__(self, *_exc):
        httpx.Client.post = self._original

    def summary(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "reported_cost_usd": round(self.cost_usd, 8), "cost_missing_calls": self.cost_missing, "http_errors": self.http_errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=("composite", "development"), default="composite")
    parser.add_argument("--seed", type=int, default=20261003)
    args = parser.parse_args()
    load_local_key()
    registry = PromptRegistry("release-v1")
    model_id = ModelRegistry().get("intent").model
    seed_clock = datetime.now(timezone.utc)
    cases = [case for case in load_cases() if "collaboration" in case.get("tags", []) or (args.scope == "development" and case["case_id"] in {"smoke-01", "smoke-02", "smoke-10", "smoke-11", "smoke-16", "smoke-17", "smoke-20", "smoke-24"})]
    assert len(cases) == (3 if args.scope == "composite" else 11)
    rng = random.Random(args.seed)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-paired-model-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    results = []
    execution_order = []
    for case in cases:
        modes = ["single", "collab"]
        rng.shuffle(modes)
        for mode in modes:
            execution_order.append({"case_id": case["case_id"], "agent_mode": mode})
            with ProviderUsage() as meter:
                try:
                    result = run_case(case, mode, run_id, seed_clock)
                except Exception as exc:
                    result = {"case_id": case["case_id"], "agent_mode": mode, "status": "incomplete", "error": type(exc).__name__ + ": " + str(exc)}
            result["provider_usage"] = meter.summary()
            results.append(result)
    pairs = []
    for case in cases:
        single = next(row for row in results if row["case_id"] == case["case_id"] and row["agent_mode"] == "single")
        collab = next(row for row in results if row["case_id"] == case["case_id"] and row["agent_mode"] == "collab")
        outcome = "tie" if single["status"] == collab["status"] == "pass" else "collab_win" if collab["status"] == "pass" else "single_win" if single["status"] == "pass" else "both_fail" if single["status"] == collab["status"] == "fail" else "both_incomplete"
        pairs.append({"case_id": case["case_id"], "single_status": single["status"], "collab_status": collab["status"], "outcome": outcome, "single_latency_ms": single.get("latency_ms"), "collab_latency_ms": collab.get("latency_ms")})
    by_mode = {}
    for mode in ("single", "collab"):
        selected = [row for row in results if row["agent_mode"] == mode]
        latencies = [row["latency_ms"] for row in selected if isinstance(row.get("latency_ms"), (int, float))]
        by_mode[mode] = {"pass": sum(row["status"] == "pass" for row in selected), "fail": sum(row["status"] == "fail" for row in selected), "incomplete": sum(row["status"] == "incomplete" for row in selected), "median_latency_ms": round(statistics.median(latencies), 2) if latencies else None, "provider_calls": sum(row["provider_usage"]["calls"] for row in selected), "input_tokens": sum(row["provider_usage"]["input_tokens"] for row in selected), "output_tokens": sum(row["provider_usage"]["output_tokens"] for row in selected), "reported_cost_usd": round(sum(row["provider_usage"]["reported_cost_usd"] for row in selected), 8), "cost_missing_calls": sum(row["provider_usage"]["cost_missing_calls"] for row in selected)}
    summary = {"run_id": run_id, "suite": "smoke_paired_model_" + args.scope, "unique_cases": len(cases), "executions": len(results), "model": model_id, "by_mode": by_mode, "paired_outcomes": {outcome: sum(pair["outcome"] == outcome for pair in pairs) for outcome in ("collab_win", "single_win", "tie", "both_fail", "both_incomplete")}, "gate_pass": all(row["status"] == "pass" for row in results)}
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    (folder / "pairs.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in pairs), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "selected_case_ids": [case["case_id"] for case in cases], "scope": args.scope, "execution_order_seed": args.seed, "execution_order": execution_order, "seed_clock": seed_clock.isoformat(), "prompt_release_id": "release-v1", "prompt_hashes": registry.manifest["prompts"], "model": model_id, "scorer_version": "smoke-v2", "auth_mode": "mock", "database": "isolated-sqlite-per-execution"}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = ["<html><meta charset='utf-8'><title>ResolveAI paired model report</title><body>", f"<h1>Run {html.escape(run_id)}</h1>", f"<p>{len(cases)} paired synthetic cases on {html.escape(model_id)}.</p>", "<table border='1'><tr><th>Case</th><th>Single</th><th>Collab</th><th>Outcome</th></tr>"]
    for pair in pairs:
        rows.append(f"<tr><td>{html.escape(pair['case_id'])}</td><td>{html.escape(pair['single_status'])}</td><td>{html.escape(pair['collab_status'])}</td><td>{html.escape(pair['outcome'])}</td></tr>")
    rows.append("</table></body></html>")
    (folder / "report.html").write_text("\n".join(rows), encoding="utf-8")
    print(json.dumps({**summary, "report": str(folder)}, ensure_ascii=False))
    raise SystemExit(0 if summary["gate_pass"] else 1)


if __name__ == "__main__":
    main()
