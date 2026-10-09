"""Run fixed local development suites with no provider or Cloud credentials."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .prompts import ROOT


SUITES = {
    "smoke_demo": ("evals/runners/run_smoke.py", (), "gate_pass"),
    "core_business": ("evals/runners/run_core_business.py", (), "development_pass"),
    "intent_route": ("evals/runners/run_intent_routing.py", ("--mode", "mock"), "gate_pass"),
    "intent_dialogue": ("evals/runners/run_intent_dialogue.py", (), "development_pass"),
    "policy_rag": ("evals/runners/run_policy_runtime.py", ("--dialect", "sqlite"), "gate_pass"),
    "collaboration": ("evals/runners/run_collaboration.py", ("--mode", "mock"), "gate_pass"),
}


def run_development_eval(suite: str, report_root: Path, job_id: str) -> dict:
    if suite not in SUITES:
        raise ValueError("Unknown development evaluation suite")
    if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", job_id):
        raise ValueError("Invalid evaluation job name")
    report_root = Path(report_root)
    folder = report_root / job_id
    if folder.is_symlink():
        raise ValueError("Evaluation job output cannot be a symlink")
    summary_path = folder / "summary.json"
    if summary_path.exists():
        prior = json.loads(summary_path.read_text(encoding="utf-8"))
        if prior.get("suite") != suite:
            raise ValueError("Evaluation job name was reused for a different suite")
        if prior.get("status") == "pass":
            return prior
    folder.mkdir(parents=True, exist_ok=True)
    script, arguments, expected_flag = SUITES[suite]
    with TemporaryDirectory(prefix="resolveai-eval-job-") as temporary:
        env = {**os.environ,
               "AUTH_MODE": "mock", "OPENROUTER_API_KEY": "",
               "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "",
               "LANGFUSE_EXPORT_EVAL_TRACES": "0", "OTEL_EXPORTER_OTLP_ENDPOINT": "",
               "DATABASE_URL": f"sqlite:///{Path(temporary) / 'isolated.db'}",
               "PYTHONPATH": os.pathsep.join((str(ROOT / "apps/api"), str(ROOT / "packages"), str(ROOT)))}
        try:
            process = subprocess.run([sys.executable, script, *arguments], cwd=ROOT, env=env,
                                     text=True, capture_output=True, timeout=600, check=False)
            stdout, stderr, exit_code = process.stdout, process.stderr, process.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, stderr, exit_code = exc.stdout or "", exc.stderr or "", 124
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
    (folder / "runner.log").write_text(stdout + ("\nSTDERR:\n" + stderr if stderr else ""),
                                       encoding="utf-8")
    parsed = None
    try:
        parsed = json.loads(next(line for line in reversed(stdout.splitlines()) if line.strip()))
    except (ValueError, StopIteration):
        pass
    child_report = None
    if isinstance(parsed, dict) and isinstance(parsed.get("report"), str):
        child_path = Path(parsed["report"]).resolve()
        if child_path.is_relative_to(ROOT / "evals/reports") and child_path.is_dir():
            child_report = str(child_path.relative_to(ROOT))
    passed = bool(exit_code == 0 and isinstance(parsed, dict)
                  and parsed.get(expected_flag) is True and child_report)
    result = {"suite": suite, "status": "pass" if passed else "incomplete" if exit_code == 124 else "fail",
              "exit_code": exit_code, "child_report": child_report,
              "provider_calls": parsed.get("provider_calls", 0) if isinstance(parsed, dict) else None,
              "no_key": True, "job_id": job_id}
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
