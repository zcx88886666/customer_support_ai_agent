"""Run one no-key synthetic case and reconcile its essential Langfuse trace tree."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from resolveai.prompts import ROOT

if __package__:
    from .export_langfuse_run import export_run
else:
    from export_langfuse_run import export_run


LANGFUSE_KEYS = {"LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL"}
ESSENTIAL_SPANS = {"http.request", "fastapi.endpoint", "agent.request_resources", "specialist.policy", "specialist.order"}


def load_local_langfuse_env(path: Path) -> None:
    """Read only the three Langfuse settings from an ignored dotenv file."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in LANGFUSE_KEYS or os.environ.get(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        os.environ[key] = value


def hierarchy_result(folder: Path, expected_traces: int) -> dict:
    paths = sorted((folder / "traces").glob("*.json"))
    problems = []
    for path in paths:
        observations = json.loads(path.read_text(encoding="utf-8"))["observations"]
        ids = {row["id"] for row in observations}
        names = {row["name"] for row in observations}
        roots = [row for row in observations if not row.get("parent_observation_id")]
        if len(roots) != 1 or roots[0]["name"] not in ("POST /chat", "POST /chat/stream"):
            problems.append(path.stem + ":root")
        if any(row.get("parent_observation_id") and row["parent_observation_id"] not in ids for row in observations):
            problems.append(path.stem + ":missing_parent")
        if not ESSENTIAL_SPANS.issubset(names):
            problems.append(path.stem + ":missing_essential_spans")
    if len(paths) != expected_traces:
        problems.append("trace_file_count")
    return {"trace_files": len(paths), "connected": not problems, "problems": problems}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", choices=["smoke-03"], default="smoke-03")
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-essential-" + uuid4().hex[:6])
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--max-wait-seconds", type=int, default=90)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.run_id):
        parser.error("run-id must contain 1–64 letters, numbers, underscores, or hyphens")
    if not 0 <= args.max_wait_seconds <= 180:
        parser.error("max-wait-seconds must be between 0 and 180")
    load_local_langfuse_env(args.env_file)
    if not all(os.getenv(key) for key in LANGFUSE_KEYS):
        parser.error("Langfuse project URL and keys are required in the environment or ignored env file")

    os.environ.update({"AUTH_MODE": "mock", "OPENROUTER_API_KEY": "", "OTEL_EXPORTER_OTLP_ENDPOINT": "",
                       "PROMPT_RELEASE": "specialists-dev-v1", "LANGFUSE_SAMPLE_RATE": "1", "LANGFUSE_EXPORT_EVAL_TRACES": "1"})
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from evals.runners import run_smoke
    from resolveai import telemetry

    original_argv = sys.argv
    sys.argv = ["run_smoke", "--case-id", args.case_id, "--run-id", args.run_id]
    try:
        try:
            run_smoke.main()
        except SystemExit as exc:
            if exc.code != 0:
                return int(exc.code or 1)
    finally:
        sys.argv = original_argv
        if telemetry._langfuse is not None:
            telemetry._langfuse.flush()

    report_dir = ROOT / "evals/reports" / args.run_id
    deadline = time.monotonic() + args.max_wait_seconds
    manifest = {"status": "incomplete", "expected_traces": 2, "traces_linked_to_local_cases": 0,
                "matched_task_success_scores": 0, "observations": 0, "scores": 0}
    hierarchy = {"trace_files": 0, "connected": False, "problems": ["cloud_reconciliation_incomplete"]}
    last_error = None
    while time.monotonic() < deadline:
        try:
            manifest = export_run(args.run_id, refresh=True, deadline=deadline)
            hierarchy = hierarchy_result(ROOT / "observability/exports" / args.run_id, manifest["expected_traces"])
            if manifest["status"] == "complete" and hierarchy["connected"]:
                break
        except Exception as exc:
            last_error = type(exc).__name__
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(10, remaining))
    result = {"run_id": args.run_id, "case_id": args.case_id, "scope": "targeted", "provider_calls": 0,
              "cloud_status": manifest["status"], "expected_traces": manifest["expected_traces"],
              "traces_linked_to_local_cases": manifest["traces_linked_to_local_cases"],
              "matched_task_success_scores": manifest["matched_task_success_scores"],
              "observations": manifest["observations"], "scores": manifest["scores"], "hierarchy": hierarchy,
              "status": "pass" if manifest["status"] == "complete" and hierarchy["connected"] else "fail"}
    if last_error and result["status"] == "fail":
        result["last_cloud_error_type"] = last_error
    (report_dir / "langfuse_essential_check.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
