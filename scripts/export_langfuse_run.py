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
        for attempt in range(5):
            try:
                response = fetch(cursor)
                break
            except Exception as exc:
                status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
                if status != 429 or attempt == 4:
                    raise
                time.sleep(15 * (attempt + 1))
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
    expected = {row["trace_id"]: row for row in results if row.get("trace_id")}
    trace_ids = sorted(expected)
    missing_local_trace_rows = [row["case_id"] + ":" + row["agent_mode"] for row in results if not row.get("trace_id")]
    output = ROOT / "observability/exports" / run_id
    traces = output / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    lf = Langfuse(public_key=os.environ["LANGFUSE_PUBLIC_KEY"], secret_key=os.environ["LANGFUSE_SECRET_KEY"], base_url=os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"), tracing_enabled=False)
    found = 0
    observations = 0
    scores = 0
    matched_scores = 0
    linked_traces = 0
    missing_score_trace_ids = []
    missing_case_links = []
    for trace_id in trace_ids:
        target = traces / f"{trace_id}.json"
        saved = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        if saved is None or not saved["observations"] or not saved["scores"]:
            obs = []
            for group in pages(lambda cursor: lf.api.observations.get_many(trace_id=trace_id, fields="core,basic,metadata", limit=100, cursor=cursor)):
                obs.extend({"id": row.id, "trace_id": row.trace_id, "parent_observation_id": row.parent_observation_id, "name": row.name, "type": row.type, "start_time": row.start_time.isoformat(), "run_id": (row.metadata or {}).get("attributes.run_id"), "case_id": (row.metadata or {}).get("attributes.case_id")} for row in group)
            score_rows = []
            for group in pages(lambda cursor: lf.api.scores_v3.get_many_v3(trace_id=trace_id, fields="subject", limit=100, cursor=cursor)):
                score_rows.extend({"id": row.id, "name": row.name, "value": row.value, "data_type": row.data_type} for row in group)
            saved = {"trace_id": trace_id, "observations": obs, "scores": score_rows}
            target.write_text(json.dumps(saved, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        found += bool(saved["observations"])
        observations += len(saved["observations"])
        scores += len(saved["scores"])
        row = expected[trace_id]
        if any(obs.get("run_id") == run_id and obs.get("case_id") == row["case_id"] for obs in saved["observations"]):
            linked_traces += 1
        else:
            missing_case_links.append(trace_id)
        value = 1.0 if row["status"] == "pass" else 0.0 if row["status"] == "fail" else None
        if value is not None:
            if any(score["name"] == "task_success" and score["value"] == value for score in saved["scores"]):
                matched_scores += 1
            else:
                missing_score_trace_ids.append(trace_id)
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(traces.glob("*.json"))}
    missing_trace_ids = [trace_id for trace_id in trace_ids if not json.loads((traces / f"{trace_id}.json").read_text(encoding="utf-8"))["observations"]]
    complete = not (missing_local_trace_rows or missing_trace_ids or missing_case_links or missing_score_trace_ids)
    manifest = {"run_id": run_id, "base_url": os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"), "expected_traces": len(results), "traces_with_observations": found, "traces_linked_to_local_cases": linked_traces, "matched_task_success_scores": matched_scores, "observations": observations, "scores": scores, "missing_local_trace_rows": missing_local_trace_rows, "missing_trace_ids": missing_trace_ids, "missing_case_links": missing_case_links, "missing_score_trace_ids": missing_score_trace_ids, "sha256": hashes, "status": "complete" if complete else "incomplete"}
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
