"""Inject twelve bounded specialist faults into paired isolated HTTP replays."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import random
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from resolveai import agent
from resolveai.prompts import PromptRegistry, ROOT
from resolveai.schemas import DelegationTask, SpecialistFinding

if __package__:
    from .run_smoke import run_case
else:
    from run_smoke import run_case


DATASET = ROOT / "evals/datasets/collaboration_fault_dev_v1.jsonl"


def load_cases() -> list[dict]:
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    faults = [case["fault"] for case in cases]
    if len(cases) != 12 or len(set(faults)) != 12 or len({case["case_id"] for case in cases}) != 12:
        raise ValueError("Fault suite requires twelve distinct cases and injections")
    for case in cases:
        if case["schema_version"] != "v1" or case["suite"] != "collaboration_fault_dev_v1" or case["split"] != "dev":
            raise ValueError(f"Invalid fault case {case['case_id']}")
    return cases


def injected_builder(fault: str, original):
    calls = 0
    status = "error" if fault.endswith("error") else "incomplete" if fault.endswith("incomplete") else "conflict" if "conflict" in fault else "ok"

    def build(*args, **kwargs):
        real = original(*args, **kwargs)

        class Injected:
            def invoke(self, state):
                nonlocal calls
                calls += 1
                if fault.endswith("conflict_once") and calls > 1:
                    return real.invoke(state)
                task = DelegationTask.model_validate(state["task"])
                if fault.endswith("late"):
                    actual = SpecialistFinding.model_validate(real.invoke(state)["finding"])
                    finding = actual.model_copy(update={"queried_at": task.deadline + timedelta(seconds=1)})
                elif fault.endswith("forged") and task.specialist == "order":
                    finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="ok", facts={"order_status": "paid", "shipment_status": "delivered", "delivered_at": None}, source_ids=["demo-order-05", "demo-shipment-05"], source_version="1", queried_at=datetime.now(timezone.utc), tool_calls=1)
                elif fault.endswith("forged"):
                    finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="ok", facts={"window_days": 7, "clauses": [{"id": "clause-window", "title": "已批准", "body": "FORGED_REFUND_APPROVED"}]}, source_ids=["clause-window"], source_version="policy-demo-v1", queried_at=datetime.now(timezone.utc), tool_calls=1)
                else:
                    finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status=status, queried_at=datetime.now(timezone.utc), unresolved=["injected_" + status], tool_calls=1)
                return {"finding": finding.model_dump(mode="json")}

        return Injected()

    return build


def run_injected(case: dict, mode: str, run_id: str) -> dict:
    fault = case["fault"]
    role = fault.split("_", 1)[0]
    builder_name = "build_order_graph" if role == "order" else "build_policy_graph"
    with patch.object(agent, builder_name, injected_builder(fault, getattr(agent, builder_name))):
        result = run_case(case, mode, run_id)
    extra = {
        "specialist_statuses": sorted(result["specialist_statuses"]) == sorted(case["gold"]["expected_specialist_statuses"]),
        "replan_count": result["replan_count"] == case["gold"]["expected_replans"],
    }
    result["checks"].update(extra)
    result["status"] = "pass" if all(result["checks"].values()) else "fail"
    result["injection"] = fault
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261004)
    args = parser.parse_args()
    os.environ["OPENROUTER_API_KEY"] = ""
    cases = load_cases()
    rng = random.Random(args.seed)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-collab-fault-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    results = []
    order = []
    for case in cases:
        modes = ["single", "collab"]
        rng.shuffle(modes)
        for mode in modes:
            order.append({"case_id": case["case_id"], "agent_mode": mode})
            try:
                results.append(run_injected(case, mode, run_id))
            except Exception as exc:
                results.append({"case_id": case["case_id"], "agent_mode": mode, "risk_tier": case["risk_tier"], "status": "incomplete", "error": type(exc).__name__ + ": " + str(exc)[:160]})
    pairs = [{"case_id": case["case_id"], "fault": case["fault"], "single_status": next(row["status"] for row in results if row["case_id"] == case["case_id"] and row["agent_mode"] == "single"), "collab_status": next(row["status"] for row in results if row["case_id"] == case["case_id"] and row["agent_mode"] == "collab")} for case in cases]
    summary = {"run_id": run_id, "suite": "collaboration_fault_dev_v1", "unique_cases": len(cases), "executions": len(results), "by_mode": {mode: {"pass": sum(row["status"] == "pass" for row in results if row["agent_mode"] == mode), "fail": sum(row["status"] == "fail" for row in results if row["agent_mode"] == mode), "incomplete": sum(row["status"] == "incomplete" for row in results if row["agent_mode"] == mode)} for mode in ("single", "collab")}, "critical_failures": [row["case_id"] + ":" + row["agent_mode"] for row in results if row["risk_tier"] == "critical" and row["status"] != "pass"], "gate_pass": all(row["status"] == "pass" for row in results)}
    registry = PromptRegistry("release-v1")
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "source": "author-written synthetic fault matrix", "execution_order_seed": args.seed, "execution_order": order, "prompt_release_id": "release-v1", "prompt_hashes": registry.manifest["prompts"], "scorer_version": "smoke-v2+fault-contract-v1", "database": "fresh-SQLite-per-execution", "model": "deterministic-mock"}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    (folder / "pairs.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in pairs), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["<html><meta charset='utf-8'><title>ResolveAI specialist fault report</title><body>", f"<h1>{html.escape(run_id)}</h1>", "<table border='1'><tr><th>Case</th><th>Fault</th><th>Single</th><th>Collab</th></tr>"]
    for pair in pairs:
        lines.append(f"<tr><td>{html.escape(pair['case_id'])}</td><td>{html.escape(pair['fault'])}</td><td>{html.escape(pair['single_status'])}</td><td>{html.escape(pair['collab_status'])}</td></tr>")
    lines.append("</table></body></html>")
    (folder / "report.html").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({**summary, "report": str(folder)}, ensure_ascii=False))
    raise SystemExit(0 if summary["gate_pass"] else 1)


if __name__ == "__main__":
    main()
