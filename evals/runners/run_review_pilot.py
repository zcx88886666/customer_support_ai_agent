"""Replay separate authored scenario families as no-key development evidence."""

from __future__ import annotations

import hashlib
import html
import json
import os
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DATASETS = {
    "smoke_demo": "evals/datasets/review_pilot_smoke_v1.jsonl",
    "core_business": "evals/datasets/review_pilot_core_v1.jsonl",
    "intent_route": "evals/datasets/review_pilot_intent_v1.jsonl",
    "intent_dialogue": "evals/datasets/review_pilot_dialogue_v1.jsonl",
    "policy_rag_positive": "evals/datasets/review_pilot_policy_positive_v1.jsonl",
    "policy_rag_negative": "evals/datasets/review_pilot_policy_negative_v1.jsonl",
    "collaboration": "evals/datasets/review_pilot_collaboration_v1.jsonl",
}
SOURCE_SUITES = {
    "smoke_demo": "smoke_demo", "core_business": "core_business_dev_v1",
    "intent_route": "intent_routing_dev_v1", "intent_dialogue": "intent_dialogue_dev_v1",
    "policy_rag_positive": "policy_retrieval_dev_v1",
    "policy_rag_negative": "policy_retrieval_dev_v1",
    "collaboration": "collaboration_dev_v1",
}


def load_cases() -> list[tuple[str, dict]]:
    cases = []
    seen = set()
    for suite, relative_path in DATASETS.items():
        path = ROOT / relative_path
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not rows:
            raise ValueError(f"Empty review pilot source: {relative_path}")
        for case in rows:
            case_id = case.get("case_id")
            if (not isinstance(case_id, str) or not case_id or case_id in seen
                    or case.get("schema_version") != "v1" or case.get("split") != "dev"
                    or case.get("suite") != SOURCE_SUITES[suite]
                    or case.get("review", {}).get("status") != "pending"
                    or not isinstance(case.get("group_keys"), dict) or not case["group_keys"]
                    or case.get("source", {}).get("kind") != "synthetic"):
                raise ValueError(f"Invalid review pilot case: {case_id}")
            seen.add(case_id)
            cases.append((suite, case))
    return cases


def run_pilot(report_root: Path | None = None) -> dict:
    """Write a local report; all external model and Cloud credentials are cleared."""
    from resolveai.telemetry import cloud_export_configured

    if cloud_export_configured():
        raise RuntimeError("Review pilot requires a fresh process without a configured Cloud exporter")
    report_root = Path(report_root or ROOT / "evals/reports")
    cases = load_cases()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-review-pilot-" + uuid4().hex[:6]
    folder = report_root / run_id
    folder.mkdir(parents=True, exist_ok=False)
    seed_clock = datetime.now(timezone.utc)
    rows = []
    with patch.dict(os.environ, {"AUTH_MODE": "mock", "OPENROUTER_API_KEY": "",
                                 "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "",
                                 "LANGFUSE_EXPORT_EVAL_TRACES": "0",
                                 "OTEL_EXPORTER_OTLP_ENDPOINT": ""}):
        from evals.runners.run_paired_model import ProviderUsage
        from sqlalchemy.orm import Session

        from resolveai.agent import classify
        from resolveai.db import Base, make_engine
        from resolveai.policy_retrieval import index_bundle, retrieve
        from resolveai.prompts import PromptRegistry
        from resolveai.seed import seed_demo
        from evals.runners import run_core_business, run_intent_dialogue, run_smoke

        policy_engine = make_engine("sqlite:///:memory:")
        Base.metadata.create_all(policy_engine)
        stack = ExitStack()
        try:
            usage = stack.enter_context(ProviderUsage())
            with Session(policy_engine) as db:
                seed_demo(db, seed_clock)
                index_bundle(db, "policy-demo-v1")
                db.commit()
            for suite, case in cases:
                try:
                    if suite == "smoke_demo":
                        result = run_smoke.run_case(case, "single", run_id, seed_clock)
                    elif suite == "core_business":
                        result = run_core_business.run_case(case, run_id, seed_clock)
                    elif suite == "intent_route":
                        decision = classify(case["message"])
                        checks = {"route": decision.route == case["route"],
                                  "intents": set(decision.intents) == set(case["intents"])}
                        result = {"status": "pass" if all(checks.values()) else "fail",
                                  "checks": checks, "actual_route": decision.route,
                                  "actual_intents": decision.intents}
                    elif suite == "intent_dialogue":
                        result = run_intent_dialogue.run_case(case, run_id, seed_clock)
                    elif suite in {"policy_rag_positive", "policy_rag_negative"}:
                        with Session(policy_engine) as db:
                            hits = [clause.id for clause in retrieve(db, "policy-demo-v1", case["query"], limit=5)]
                        expected = case["expected"]
                        check = expected in hits if expected else not hits
                        result = {"status": "pass" if check else "fail",
                                  "checks": {"retrieval": check}, "retrieved": hits}
                    else:
                        single = run_smoke.run_case(case, "single", run_id, seed_clock)
                        collab = run_smoke.run_case(case, "collab", run_id, seed_clock)
                        result = {"status": "pass" if single["status"] == collab["status"] == "pass" else "fail",
                                  "checks": {"single": single["status"] == "pass",
                                             "collab": collab["status"] == "pass"},
                                  "single": single, "collab": collab}
                    rows.append({"case_id": case["case_id"], "suite": suite,
                                 "risk_tier": case["risk_tier"], **result})
                except Exception as exc:
                    rows.append({"case_id": case["case_id"], "suite": suite,
                                 "risk_tier": case["risk_tier"], "status": "incomplete",
                                 "error": type(exc).__name__ + ": " + str(exc)})
            prompt = PromptRegistry(os.getenv("PROMPT_RELEASE", "release-v1"))
            prompt_hashes = prompt.manifest["prompts"]
        finally:
            stack.close()
            policy_engine.dispose()

    counts = Counter(row["status"] for row in rows)
    summary = {"run_id": run_id, "cases": len(rows), "counts": dict(counts),
               "critical_failures": [row["case_id"] for row in rows
                                     if row["risk_tier"] == "critical" and row["status"] != "pass"],
               "provider_calls": usage.calls, "langfuse_enabled": False,
               "development_pass": counts.get("pass", 0) == len(rows) and usage.calls == 0,
               "release_gate_pass": False}
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "split": "dev", "seed_clock": seed_clock.isoformat(),
                "seed_world": "independent_review_v1",
                "source_hashes": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                                  for path in (*DATASETS.values(), "evals/fixtures/independent_review_world.py",
                                               "apps/api/resolveai/seed.py")},
                "model": "deterministic-mock", "prompt_hashes": prompt_hashes,
                "review_status": "pending", "release_gate_pass": False}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "report.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Review pilot</title><h1>Review pilot</h1><pre>"
        + html.escape(json.dumps(summary, ensure_ascii=False, indent=2)) + "</pre>", encoding="utf-8")
    return summary


def main() -> None:
    summary = run_pilot()
    print(json.dumps({**summary, "report": str(ROOT / "evals/reports" / summary["run_id"])}, ensure_ascii=False))
    raise SystemExit(0 if summary["development_pass"] else 1)


if __name__ == "__main__":
    main()
