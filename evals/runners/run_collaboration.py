"""Paired single/collaborative replay of twenty synthetic composite customer cases."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import random
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from resolveai.models_config import ModelRegistry
from resolveai.prompts import PromptRegistry, ROOT

if __package__:
    from .run_paired_model import ProviderUsage, load_local_key
    from .run_smoke import run_case
else:
    from run_paired_model import ProviderUsage, load_local_key
    from run_smoke import run_case


DATASETS = {
    "composite": ROOT / "evals/datasets/collaboration_dev_v1.jsonl",
    "single_domain": ROOT / "evals/datasets/collaboration_single_dev_v1.jsonl",
}


def load_cases(dataset, suite: str) -> list[dict]:
    cases = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case["case_id"] for case in cases]
    if len(cases) != 20 or len(set(ids)) != 20:
        raise ValueError(f"Collaboration {suite} development set needs 20 unique cases")
    allowed_specialists = {("order", "policy")} if suite == "composite" else {("order",), ("policy",)}
    expected_suite = "collaboration_dev_v1" if suite == "composite" else "collaboration_single_dev_v1"
    for case in cases:
        specialists = tuple(sorted(case["gold"].get("specialists", [])))
        if case["schema_version"] != "v1" or case["split"] != "dev" or case["suite"] != expected_suite or specialists not in allowed_specialists:
            raise ValueError(f"Invalid collaboration case {case['case_id']}")
    return cases


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    return round(ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower), 2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--suite", choices=tuple(DATASETS), default="composite")
    parser.add_argument("--seed", type=int, default=20261004)
    args = parser.parse_args()
    if args.mode == "live":
        load_local_key()
    else:
        os.environ["OPENROUTER_API_KEY"] = ""
    dataset = DATASETS[args.suite]
    cases = load_cases(dataset, args.suite)
    rng = random.Random(args.seed)
    model = ModelRegistry().get("intent").model if args.mode == "live" else "deterministic-mock"
    release = PromptRegistry(os.getenv("PROMPT_RELEASE", "release-v1"))
    seed_clock = datetime.now(timezone.utc)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-collab-" + args.suite.replace("_", "-") + "-" + args.mode + "-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    results = []
    execution_order = []
    for case in cases:
        modes = ["single", "collab"]
        rng.shuffle(modes)
        for mode in modes:
            execution_order.append({"case_id": case["case_id"], "agent_mode": mode})
            with ProviderUsage() as usage:
                try:
                    result = run_case(case, mode, run_id, seed_clock)
                except Exception as exc:
                    result = {"case_id": case["case_id"], "suite": case["suite"], "split": case["split"], "risk_tier": case["risk_tier"], "agent_mode": mode, "status": "incomplete", "error": type(exc).__name__ + ": " + str(exc)[:160]}
            result["provider_usage"] = usage.summary()
            results.append(result)
    pairs = []
    for case in cases:
        single = next(row for row in results if row["case_id"] == case["case_id"] and row["agent_mode"] == "single")
        collab = next(row for row in results if row["case_id"] == case["case_id"] and row["agent_mode"] == "collab")
        if single["status"] == collab["status"] == "pass":
            outcome = "tie_pass"
        elif single["status"] == "pass":
            outcome = "single_win"
        elif collab["status"] == "pass":
            outcome = "collab_win"
        else:
            outcome = "both_not_pass"
        pairs.append({"case_id": case["case_id"], "family": case["group_keys"]["family"], "single_status": single["status"], "collab_status": collab["status"], "outcome": outcome, "single_latency_ms": single.get("latency_ms"), "collab_latency_ms": collab.get("latency_ms")})
    by_mode = {}
    for mode in ("single", "collab"):
        selected = [row for row in results if row["agent_mode"] == mode]
        latencies = [row["latency_ms"] for row in selected if isinstance(row.get("latency_ms"), (int, float))]
        by_mode[mode] = {"pass": sum(row["status"] == "pass" for row in selected), "fail": sum(row["status"] == "fail" for row in selected), "incomplete": sum(row["status"] == "incomplete" for row in selected), "handoffs": sum(row.get("response_status") == "handoff" for row in selected), "dispatch_errors": sum(row.get("checks", {}).get("specialists") is False for row in selected), "evidence_errors": sum(row.get("checks", {}).get("evidence_owned_and_current") is False for row in selected), "specialist_tool_calls": sum(row.get("specialist_tool_calls", 0) for row in selected), "latency_p50_ms": percentile(latencies, 0.5), "latency_p95_ms": percentile(latencies, 0.95), "provider_calls": sum(row["provider_usage"]["calls"] for row in selected), "input_tokens": sum(row["provider_usage"]["input_tokens"] for row in selected), "output_tokens": sum(row["provider_usage"]["output_tokens"] for row in selected), "reported_cost_usd": round(sum(row["provider_usage"]["reported_cost_usd"] for row in selected), 8), "cost_missing_calls": sum(row["provider_usage"]["cost_missing_calls"] for row in selected)}
    outcomes = Counter(pair["outcome"] for pair in pairs)
    family_by_case = {case["case_id"]: case["group_keys"]["family"] for case in cases}
    by_family = {}
    for family in sorted(set(family_by_case.values())):
        by_family[family] = {}
        for mode in ("single", "collab"):
            selected = [row for row in results if row["agent_mode"] == mode and family_by_case[row["case_id"]] == family]
            by_family[family][mode] = {"pass": sum(row["status"] == "pass" for row in selected), "total": len(selected)}
    summary = {"run_id": run_id, "suite": cases[0]["suite"], "mode": args.mode, "model": model, "unique_cases": len(cases), "executions": len(results), "by_mode": by_mode, "by_family": by_family, "paired_outcomes": dict(outcomes), "critical_failures": [row["case_id"] + ":" + row["agent_mode"] for row in results if row["risk_tier"] == "critical" and row["status"] != "pass"], "gate_pass": all(row["status"] == "pass" for row in results)}
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(), "source": "author-written synthetic development cases", "execution_order_seed": args.seed, "execution_order": execution_order, "seed_clock": seed_clock.isoformat(), "prompt_release_id": os.getenv("PROMPT_RELEASE", "release-v1"), "prompt_hashes": release.manifest["prompts"], "model": model, "scorer_version": "smoke-v2", "database": "fresh-SQLite-per-execution"}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    (folder / "pairs.jsonl").write_text("".join(json.dumps(pair, ensure_ascii=False) + "\n" for pair in pairs), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["<html><meta charset='utf-8'><title>ResolveAI collaboration development report</title><body>", f"<h1>{html.escape(run_id)}</h1>", f"<p>{len(cases)} paired author-written cases; {len(results)} isolated executions.</p>", "<table border='1'><tr><th>Case</th><th>Family</th><th>Single</th><th>Collab</th><th>Outcome</th></tr>"]
    for pair in pairs:
        lines.append(f"<tr><td>{html.escape(pair['case_id'])}</td><td>{html.escape(pair['family'])}</td><td>{html.escape(pair['single_status'])}</td><td>{html.escape(pair['collab_status'])}</td><td>{html.escape(pair['outcome'])}</td></tr>")
    lines.append("</table></body></html>")
    (folder / "report.html").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({**summary, "report": str(folder)}, ensure_ascii=False))
    raise SystemExit(0 if summary["gate_pass"] else 1)


if __name__ == "__main__":
    main()
