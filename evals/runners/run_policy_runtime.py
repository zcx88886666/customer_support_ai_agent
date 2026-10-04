"""Score the published demo policy retriever on 20 relevant and 20 near-negative questions."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.orm import Session

from resolveai import models as m
from resolveai.db import Base, make_engine
from resolveai.policy_retrieval import INDEX_VERSION, index_bundle, retrieve
from resolveai.prompts import PromptRegistry, ROOT
from resolveai.seed import seed_demo


POSITIVES = ROOT / "evals/datasets/policy_retrieval_v1.jsonl"
NEGATIVES = ROOT / "evals/datasets/policy_retrieval_negatives_v1.jsonl"


def load_cases() -> list[dict]:
    positives = [json.loads(line) for line in POSITIVES.read_text(encoding="utf-8").splitlines() if line.strip()]
    negatives = [json.loads(line) for line in NEGATIVES.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = positives[:20] + negatives
    if len(positives) != 25 or len(negatives) != 20 or len({case["case_id"] for case in cases}) != 40:
        raise ValueError("Policy runtime development set needs 20 positive and 20 near-negative cases")
    return cases


def create_engine(dialect: str, run_id: str):
    if dialect == "sqlite":
        engine = make_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        return engine, "in-memory-SQLite"
    admin_url = os.getenv("POLICY_RUNTIME_PG_ADMIN_URL", "")
    parts = urlsplit(admin_url)
    if parts.scheme != "postgresql" or parts.path != "/postgres" or not parts.hostname:
        raise RuntimeError("POLICY_RUNTIME_PG_ADMIN_URL must connect to isolated PostgreSQL /postgres")
    name = "ra_policy_runtime_" + run_id[-6:]
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    db_url = urlunsplit(("postgresql+psycopg", parts.netloc, "/" + name, "", ""))
    env = {**os.environ, "DATABASE_URL": db_url, "OPENROUTER_API_KEY": "", "LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": "", "OTEL_EXPORTER_OTLP_ENDPOINT": ""}
    subprocess.run([str(Path(sys.executable).with_name("alembic")), "upgrade", "head"], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
    return make_engine(db_url), name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dialect", choices=("sqlite", "postgres"), default="sqlite")
    args = parser.parse_args()
    cases = load_cases()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-policy-runtime-" + args.dialect + "-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    engine, database = create_engine(args.dialect, run_id)
    try:
        with Session(engine) as db:
            seed_demo(db, datetime.now(timezone.utc))
            indexed = index_bundle(db, "policy-demo-v1")
            policy_hash = db.get(m.PolicyBundle, "policy-demo-v1").content_hash
            db.commit()
        rows = []
        with Session(engine) as db:
            for case in cases:
                started = time.perf_counter()
                hits = [clause.id for clause in retrieve(db, "policy-demo-v1", case["query"], limit=5)]
                expected = case["expected"]
                rows.append({"case_id": case["case_id"], "expected": expected, "retrieved": hits, "hit_at_1": hits[:1] == [expected] if expected else None, "hit_at_5": expected in hits if expected else None, "abstained": not hits if expected is None else None, "latency_ms": round((time.perf_counter() - started) * 1000, 2)})
        positives = [row for row in rows if row["expected"]]
        negatives = [row for row in rows if row["expected"] is None]
        summary = {"run_id": run_id, "suite": "policy_runtime_dev_v1", "database": database, "cases": len(rows), "positive_cases": len(positives), "near_negative_cases": len(negatives), "hit_at_1": sum(row["hit_at_1"] for row in positives), "hit_at_5": sum(row["hit_at_5"] for row in positives), "negative_abstentions": sum(row["abstained"] for row in negatives), "indexed_clauses": indexed, "provider_calls": 0, "gate_pass": sum(row["hit_at_5"] for row in positives) >= 17 and all(row["abstained"] for row in negatives)}
        prompt_release = PromptRegistry("release-v1")
        manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(), "positive_dataset_sha256": hashlib.sha256(POSITIVES.read_bytes()).hexdigest(), "negative_dataset_sha256": hashlib.sha256(NEGATIVES.read_bytes()).hexdigest(), "policy_bundle_id": "policy-demo-v1", "policy_content_hash": policy_hash, "database": database, "retriever": INDEX_VERSION, "prompt_release_id": "release-v1", "prompt_hashes": prompt_release.manifest["prompts"], "scorer_version": "policy-runtime-v1", "source": "author-written synthetic development queries"}
        (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        html_rows = ["<html><meta charset='utf-8'><title>Policy retrieval development report</title><body>", f"<h1>{html.escape(run_id)}</h1>", f"<p>Hit@5: {summary['hit_at_5']}/20; near-negative abstentions: {summary['negative_abstentions']}/20.</p>", "<table border='1'><tr><th>Case</th><th>Expected</th><th>Retrieved</th></tr>"]
        for row in rows:
            html_rows.append(f"<tr><td>{html.escape(row['case_id'])}</td><td>{html.escape(str(row['expected']))}</td><td>{html.escape(', '.join(row['retrieved']))}</td></tr>")
        html_rows.append("</table></body></html>")
        (folder / "report.html").write_text("\n".join(html_rows), encoding="utf-8")
        print(json.dumps({**summary, "report": str(folder)}))
        raise SystemExit(0 if summary["gate_pass"] else 1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
