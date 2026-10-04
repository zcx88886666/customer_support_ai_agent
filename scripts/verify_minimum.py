"""Run the no-key ResolveAI v6 minimum development set and collect local evidence."""

from __future__ import annotations

import hashlib
import html
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from resolveai.prompts import ROOT


SUITES = (
    ("python_tests", [str(Path(sys.executable).with_name("pytest")), "-q"]),
    ("smoke_demo", [sys.executable, "evals/runners/run_smoke.py"]),
    ("core_business", [sys.executable, "evals/runners/run_core_business.py"]),
    ("intent_route", [sys.executable, "evals/runners/run_intent_routing.py", "--mode", "mock"]),
    ("intent_dialogue", [sys.executable, "evals/runners/run_intent_dialogue.py"]),
    ("policy_rag", [sys.executable, "evals/runners/run_policy_runtime.py", "--dialect", "sqlite"]),
    ("collaboration", [sys.executable, "evals/runners/run_collaboration.py", "--mode", "mock"]),
)


def evidence_passed(suite: str, result: dict, exit_code: int) -> bool:
    if exit_code != 0:
        return False
    if suite == "python_tests":
        return True
    if not result or result.get("critical_failures"):
        return False
    if suite == "smoke_demo":
        return result.get("unique_cases", 0) >= 25 and result.get("gate_pass") is True
    if suite == "core_business":
        return (result.get("unique_cases", 0) >= 30 and result.get("development_pass") is True
                and result.get("v6_minimum_cases_met") is True and result.get("release_gate_pass") is False)
    if suite == "intent_route":
        return result.get("cases", 0) >= 30 and result.get("gate_pass") is True
    if suite == "intent_dialogue":
        return result.get("unique_cases", 0) >= 12 and result.get("development_pass") is True
    if suite == "policy_rag":
        return (result.get("positive_cases", 0) >= 20 and result.get("hit_at_5", 0) >= 17
                and result.get("near_negative_cases", 0) >= 20
                and result.get("negative_abstentions") == result.get("near_negative_cases")
                and result.get("gate_pass") is True)
    if suite == "collaboration":
        return result.get("unique_cases", 0) >= 20 and result.get("gate_pass") is True
    return False


def main() -> None:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-minimum-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, "AUTH_MODE": "mock", "OPENROUTER_API_KEY": "", "LANGFUSE_PUBLIC_KEY": "",
           "LANGFUSE_SECRET_KEY": "", "OTEL_EXPORTER_OTLP_ENDPOINT": ""}
    rows = []
    for suite, command in SUITES:
        try:
            process = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=300)
            stdout, stderr, exit_code = process.stdout, process.stderr, process.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, exit_code = exc.stdout or "", exc.stderr or "", 124
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
        (folder / f"{suite}.log").write_text(stdout + ("\nSTDERR:\n" + stderr if stderr else ""), encoding="utf-8")
        parsed = None
        if suite != "python_tests":
            try:
                parsed = json.loads(next(line for line in reversed(stdout.splitlines()) if line.strip()))
            except (ValueError, StopIteration):
                pass
        passed = evidence_passed(suite, parsed or {}, exit_code)
        rows.append({"suite": suite, "status": "pass" if passed else "fail", "exit_code": exit_code,
                     "report": parsed.get("report") if parsed else None,
                     "run_id": parsed.get("run_id") if parsed else None,
                     "counts": parsed.get("counts") if parsed else None,
                     "log": f"{suite}.log"})
        print(f"{suite}: {'pass' if passed else 'fail'}", flush=True)
    development_pass = all(row["status"] == "pass" for row in rows)
    summary = {"run_id": run_id, "suite": "minimum_development_v1", "suite_count": len(rows),
               "pass": sum(row["status"] == "pass" for row in rows),
               "fail": sum(row["status"] == "fail" for row in rows),
               "minimum_development_pass": development_pass,
               "locked_release_pass": False, "review_status": "pending_independent_review"}
    source_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "source_git_commit": source_commit,
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "mode": "no-key-mock", "suite_commands": [{"suite": name, "argv": command} for name, command in SUITES]}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    body = ["<html><meta charset='utf-8'><title>ResolveAI minimum development verification</title><body>",
            f"<h1>{html.escape(run_id)}</h1>",
            f"<p>{summary['pass']}/{len(rows)} local suites passed. Independent review pending; locked release gate false.</p>",
            "<table border='1'><tr><th>Suite</th><th>Status</th><th>Exit</th><th>Report</th></tr>"]
    for row in rows:
        body.append(f"<tr><td>{html.escape(row['suite'])}</td><td>{html.escape(row['status'])}</td><td>{row['exit_code']}</td><td>{html.escape(str(row['report'] or ''))}</td></tr>")
    body.append("</table></body></html>")
    (folder / "report.html").write_text("\n".join(body), encoding="utf-8")
    print(json.dumps({**summary, "report": str(folder)}, ensure_ascii=False))
    raise SystemExit(0 if development_pass else 1)


if __name__ == "__main__":
    main()
