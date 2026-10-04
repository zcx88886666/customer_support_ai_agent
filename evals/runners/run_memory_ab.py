"""Offline PostgresStore/Mem0 comparison on fixed synthetic two-session stories.

Run the controller with the project venv and set MEM0_PYTHON to a separate venv
containing mem0ai==2.2.1, fastembed==0.8.1, psycopg[binary,pool], pgvector.
The controller and workers use only MEMORY_AB_DATABASE_URL, an isolated DB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "evals/datasets/memory_stories_v1.jsonl"


def stories() -> list[dict]:
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines()]
    assert len(cases) == 40 and len({case["customer_id"] for case in cases}) == 40
    return cases


def load_local_key() -> None:
    if os.getenv("OPENROUTER_API_KEY"):
        return
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "OPENROUTER_API_KEY" and value.strip():
                os.environ["OPENROUTER_API_KEY"] = value.strip().strip("\"'")
                return
    raise RuntimeError("OPENROUTER_API_KEY unavailable in environment or ignored .env")


def mem0_config(db_url: str, run_id: str, report_dir: Path) -> dict:
    return {
        "vector_store": {"provider": "pgvector", "config": {"connection_string": db_url, "collection_name": f"mem0_{run_id.replace('-', '_')}", "embedding_model_dims": 384}},
        "embedder": {"provider": "fastembed", "config": {"model": "sentence-transformers/all-MiniLM-L6-v2", "embedding_dims": 384}},
        "llm": {"provider": "openai", "config": {"model": "openai/gpt-4o-mini-2024-07-18", "temperature": 0, "max_tokens": 200}},
        "history_db_path": str(report_dir / "mem0_history.db"),
    }


def pg_namespace(run_id: str, customer_id: str) -> tuple[str, ...]:
    return ("memory_ab", run_id, customer_id, "preferences")


def postgres_worker(cases: list[dict], db_url: str, run_id: str) -> dict:
    from langgraph.store.postgres import PostgresStore

    timings = {}
    with PostgresStore.from_conn_string(db_url) as store:
        store.setup()
        for case in cases:
            start = time.perf_counter()
            ns = pg_namespace(run_id, case["customer_id"])
            if case["sessions"][0]["confirmed"] and case["sessions"][0]["consent"]:
                store.put(ns, case["key"], {"value": case["initial"]}, index=False)
            if case["action"] == "correct":
                store.put(ns, case["key"], {"value": case["replacement"]}, index=False)
            elif case["action"] == "revoke":
                store.delete(ns, case["key"])
            timings[case["case_id"]] = round((time.perf_counter() - start) * 1000, 2)
    with PostgresStore.from_conn_string(db_url) as store:
        return score(cases, "postgres_store", timings, lambda case: [item.value["value"] for item in store.search(pg_namespace(run_id, case["customer_id"]), limit=20)], lambda case: [item.value["value"] for item in store.search(pg_namespace(run_id, case["customer_id"]), limit=3)], lambda case: [item.value["value"] for item in store.search(pg_namespace(run_id, "shadow_" + case["customer_id"]), limit=3)], None)


def mem0_worker(cases: list[dict], db_url: str, run_id: str, report_dir: Path) -> dict:
    os.environ["MEM0_TELEMETRY"] = "false"
    os.environ["MEM0_DIR"] = str(report_dir / "mem0_config")
    os.environ.setdefault("HF_HOME", str(ROOT / ".local/huggingface"))
    load_local_key()
    from openai.resources.chat.completions import Completions
    from mem0 import Memory

    config = mem0_config(db_url, run_id, report_dir)
    provider = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "reported_cost_usd": 0.0, "cost_missing_calls": 0}
    original_create = Completions.create

    def metered_create(client, *args, **kwargs):
        response = original_create(client, *args, **kwargs)
        provider["calls"] += 1
        usage = response.usage
        if usage:
            provider["input_tokens"] += usage.prompt_tokens or 0
            provider["output_tokens"] += usage.completion_tokens or 0
            extra = getattr(usage, "model_extra", None) or {}
            cost = extra.get("cost")
            if isinstance(cost, (float, int)):
                provider["reported_cost_usd"] += cost
            else:
                provider["cost_missing_calls"] += 1
        else:
            provider["cost_missing_calls"] += 1
        return response

    Completions.create = metered_create
    memory = Memory.from_config(config)
    timings = {}
    errors = {}
    for case in cases:
        start = time.perf_counter()
        user_id = case["customer_id"]
        try:
            if case["sessions"][0]["confirmed"] and case["sessions"][0]["consent"]:
                memory.add(case["sessions"][0]["utterance"], user_id=user_id)
            if case["action"] == "correct":
                memory.add(case["sessions"][1]["utterance"], user_id=user_id)
            elif case["action"] == "revoke":
                memory.delete_all(user_id=user_id)
        except Exception as exc:
            errors[case["case_id"]] = type(exc).__name__ + ": " + str(exc)[:160]
        timings[case["case_id"]] = round((time.perf_counter() - start) * 1000, 2)
        if len(timings) % 10 == 0:
            print(f"mem0 progress {len(timings)}/{len(cases)}", flush=True)
    del memory
    memory = Memory.from_config(config)

    def all_values(case):
        rows = memory.get_all(filters={"user_id": case["customer_id"]}, top_k=20)["results"]
        return [(row.get("memory", ""), row.get("user_id")) for row in rows]

    def search_values(case):
        rows = memory.search(case["query"], filters={"user_id": case["customer_id"]}, top_k=3, threshold=0.0)["results"]
        return [(row.get("memory", ""), row.get("user_id")) for row in rows]

    def shadow_values(case):
        rows = memory.search(case["query"], filters={"user_id": "shadow_" + case["customer_id"]}, top_k=3, threshold=0.0)["results"]
        return [(row.get("memory", ""), row.get("user_id")) for row in rows]

    result = score(cases, "mem0_oss", timings, all_values, search_values, shadow_values, errors)
    result["provider_usage"] = {**provider, "reported_cost_usd": round(provider["reported_cost_usd"], 8)}
    with sqlite3.connect(config["history_db_path"]) as history:
        for case in cases:
            if case["action"] != "revoke":
                continue
            residual = history.execute("SELECT COUNT(*) FROM messages WHERE session_scope LIKE ?", ("%" + case["customer_id"] + "%",)).fetchone()[0]
            row = next(row for row in result["results"] if row["case_id"] == case["case_id"])
            row["history_residual_rows"] = residual
            if residual and row["status"] == "pass":
                row["status"] = "fail"
                result["pass"] -= 1
                result["fail"] += 1
    result["revoked_history_residual_cases"] = sum(row.get("history_residual_rows", 0) > 0 for row in result["results"])
    Completions.create = original_create
    return result


def score(cases, backend, timings, all_fn, search_fn, shadow_fn, errors):
    results = []
    for case in cases:
        case_id = case["case_id"]
        if errors and case_id in errors:
            results.append({"case_id": case_id, "action": case["action"], "status": "incomplete", "error": errors[case_id], "write_latency_ms": timings[case_id]})
            continue
        try:
            all_rows = all_fn(case)
            found_rows = search_fn(case)
            shadow_rows = shadow_fn(case)
            all_text = [row[0] if isinstance(row, tuple) else row for row in all_rows]
            found_text = [row[0] if isinstance(row, tuple) else row for row in found_rows]
            owner_ok = all(row[1] == case["customer_id"] for row in all_rows + found_rows if isinstance(row, tuple))
            isolation_ok = owner_ok and not shadow_rows
            expected = case["expected"]
            recalled = bool(expected and any(expected.lower() in value.lower() for value in found_text))
            old_removed = case["action"] != "correct" or not any(case["initial"].lower() in value.lower() for value in all_text)
            empty_after_revoke = case["action"] not in {"revoke", "no_consent"} or not all_rows and not found_rows
            status = "pass" if isolation_ok and old_removed and empty_after_revoke and (recalled if expected else True) else "fail"
            results.append({"case_id": case_id, "action": case["action"], "status": status, "recall_at_3": recalled if expected else None, "old_removed": old_removed, "empty_after_revoke": empty_after_revoke, "isolation_ok": isolation_ok, "stored_count": len(all_rows), "search_count": len(found_rows), "write_latency_ms": timings[case_id]})
        except Exception as exc:
            results.append({"case_id": case_id, "action": case["action"], "status": "incomplete", "error": type(exc).__name__ + ": " + str(exc)[:160], "write_latency_ms": timings[case_id]})
    eligible = [row for row in results if row.get("recall_at_3") is not None]
    latencies = [row["write_latency_ms"] for row in results]
    return {"backend": backend, "cases": len(results), "pass": sum(row["status"] == "pass" for row in results), "fail": sum(row["status"] == "fail" for row in results), "incomplete": sum(row["status"] == "incomplete" for row in results), "recall_at_3": {"passed": sum(row["recall_at_3"] is True for row in eligible), "total": len(eligible)}, "median_write_latency_ms": round(statistics.median(latencies), 2), "p95_write_latency_ms": round(sorted(latencies)[int(0.95 * (len(latencies) - 1))], 2), "provider_usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "reported_cost_usd": 0.0, "cost_missing_calls": 0}, "results": results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["postgres", "mem0"])
    parser.add_argument("--run-id")
    parser.add_argument("--report-dir", type=Path)
    args = parser.parse_args()
    db_url = os.environ.get("MEMORY_AB_DATABASE_URL")
    if not db_url or "resolveai_memory_ab" not in db_url:
        raise RuntimeError("MEMORY_AB_DATABASE_URL must point to an isolated resolveai_memory_ab database")
    cases = stories()
    if args.backend:
        report_dir = args.report_dir.resolve()
        result = postgres_worker(cases, db_url, args.run_id) if args.backend == "postgres" else mem0_worker(cases, db_url, args.run_id, report_dir)
        (report_dir / f"{args.backend}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in result.items() if key != "results"}))
        return
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_memory_" + uuid4().hex[:6]
    report_dir = ROOT / "evals/reports" / run_id
    report_dir.mkdir(parents=True, exist_ok=False)
    (report_dir / "manifest.json").write_text(json.dumps({"run_id": run_id, "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "postgres": "PostgresStore", "mem0": "mem0ai==2.2.1", "model": "openai/gpt-4o-mini-2024-07-18", "embedding": "sentence-transformers/all-MiniLM-L6-v2", "database": "isolated PostgreSQL; distinct namespaces and Mem0 collection"}, indent=2) + "\n", encoding="utf-8")
    for backend, executable in (("postgres", sys.executable), ("mem0", os.environ.get("MEM0_PYTHON"))):
        if not executable:
            print("MEM0_PYTHON not set; Mem0 result incomplete")
            continue
        env = os.environ.copy()
        env["MEM0_TELEMETRY"] = "false"
        if backend == "mem0":
            load_local_key()
            env["OPENROUTER_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
        print(f"running {backend} on 40 stories", flush=True)
        proc = subprocess.run([executable, str(Path(__file__).resolve()), "--backend", backend, "--run-id", run_id, "--report-dir", str(report_dir)], env=env, cwd=report_dir, check=False)
        if proc.returncode:
            print(f"{backend} worker exited {proc.returncode}", flush=True)
    summary = {"run_id": run_id, "report": str(report_dir), "backends": {}}
    for backend in ("postgres", "mem0"):
        path = report_dir / f"{backend}.json"
        summary["backends"][backend] = {key: value for key, value in json.loads(path.read_text()).items() if key != "results"} if path.exists() else {"status": "incomplete"}
        item = summary["backends"][backend]
        item["safety_gate_pass"] = item.get("pass") == 40 and item.get("fail") == 0 and item.get("incomplete") == 0
    summary["comparison_complete"] = all((report_dir / f"{backend}.json").exists() for backend in ("postgres", "mem0"))
    (report_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    if not summary["comparison_complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
