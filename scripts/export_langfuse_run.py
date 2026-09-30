"""Resume-safe, redacted Langfuse trace/score export for one local run."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from resolveai.prompts import ROOT


def pages(fetch):
    cursor = None
    for _ in range(100):
        response = fetch(cursor)
        yield response.data
        next_cursor = response.meta.cursor
        if not next_cursor or next_cursor == cursor:
            return
        cursor = next_cursor
        time.sleep(0.15)
    raise RuntimeError("Cloud pagination exceeded 100 pages")


def export_run(run_id: str) -> dict:
    if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
        raise RuntimeError("Langfuse keys are not configured")
    from langfuse import Langfuse

    local = ROOT / "evals/reports" / run_id / "case_results.jsonl"
    results = [json.loads(line) for line in local.read_text(encoding="utf-8").splitlines()]
    trace_ids = sorted({row["trace_id"] for row in results if row.get("trace_id")})
    output = ROOT / "observability/exports" / run_id
    traces = output / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    lf = Langfuse(public_key=os.environ["LANGFUSE_PUBLIC_KEY"], secret_key=os.environ["LANGFUSE_SECRET_KEY"], base_url=os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"), tracing_enabled=False)
    found = 0
    observations = 0
    scores = 0
    for trace_id in trace_ids:
        target = traces / f"{trace_id}.json"
        if not target.exists():
            obs = []
            for group in pages(lambda cursor: lf.api.observations.get_many(trace_id=trace_id, fields="core", limit=100, cursor=cursor)):
                obs.extend({"id": row.id, "trace_id": row.trace_id, "parent_observation_id": row.parent_observation_id, "name": row.name, "type": row.type, "start_time": row.start_time.isoformat()} for row in group)
            score_rows = []
            for group in pages(lambda cursor: lf.api.scores_v3.get_many_v3(trace_id=trace_id, fields="subject", limit=100, cursor=cursor)):
                score_rows.extend({"id": row.id, "name": row.name, "value": row.value, "data_type": row.data_type} for row in group)
            target.write_text(json.dumps({"trace_id": trace_id, "observations": obs, "scores": score_rows}, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        saved = json.loads(target.read_text(encoding="utf-8"))
        found += bool(saved["observations"])
        observations += len(saved["observations"])
        scores += len(saved["scores"])
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(traces.glob("*.json"))}
    manifest = {"run_id": run_id, "base_url": os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"), "expected_traces": len(trace_ids), "traces_with_observations": found, "observations": observations, "scores": scores, "missing_trace_ids": [trace_id for trace_id in trace_ids if not json.loads((traces / f"{trace_id}.json").read_text(encoding="utf-8"))["observations"]], "sha256": hashes, "status": "complete" if found == len(trace_ids) else "incomplete"}
    (output / "export_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args()
    try:
        result = export_run(args.run_id)
    except Exception as exc:
        result = {"run_id": args.run_id, "status": "incomplete", "error": str(exc)}
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "complete" else 1)
