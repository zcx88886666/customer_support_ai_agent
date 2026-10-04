"""Offline Chinese/English policy retrieval comparison with local multilingual vectors."""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from resolveai import models as m
from resolveai.db import Base, make_engine
from resolveai.policy_retrieval import retrieve
from resolveai.prompts import ROOT
from resolveai.seed import seed_demo

DATASET = ROOT / "evals/datasets/policy_retrieval_v1.jsonl"
EXPANSION = ROOT / "evals/datasets/policy_retrieval_expansion_v1.jsonl"
NEGATIVES = ROOT / "evals/datasets/policy_retrieval_negatives_v1.jsonl"
MODEL = os.environ.get("POLICY_EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
MODEL_DIMENSIONS = {"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384, "BAAI/bge-small-zh-v1.5": 512}


def cosine(left: list[float], right: list[float]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


def main() -> None:
    executable = os.environ.get("FASTEMBED_PYTHON")
    if not executable:
        raise RuntimeError("FASTEMBED_PYTHON must point to the optional FastEmbed environment")
    if MODEL not in MODEL_DIMENSIONS:
        raise RuntimeError("Policy embedding model is not allowlisted for this experiment")
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    extras = [json.loads(line) for line in EXPANSION.read_text(encoding="utf-8").splitlines() if line.strip()]
    negatives = [json.loads(line) for line in NEGATIVES.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(cases) == 25 and len(extras) == 36 and len(negatives) == 20 and all(case["expected"] is None for case in negatives)
    cases.extend({"case_id": "policy-extra-" + row["clause_id"], "query": row["query"], "expected": row["clause_id"]} for row in extras)
    cases.extend(negatives)
    assert len({case["case_id"] for case in cases}) == 81 and len({row["clause_id"] for row in extras}) == 36
    engine = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    with factory.begin() as db:
        seed_demo(db, datetime.now(timezone.utc))
        for row in extras:
            db.add(m.PolicyClause(id=row["clause_id"], bundle_id="policy-demo-v1", title=row["title"], body=row["body"]))
        db.flush()
        from resolveai.policy import fingerprint
        bundle = db.get(m.PolicyBundle, "policy-demo-v1")
        bundle.content_hash = fingerprint(bundle, db.scalars(select(m.PolicyClause).where(m.PolicyClause.bundle_id == bundle.id)).all())
    with factory() as db:
        clauses = db.scalars(select(m.PolicyClause).where(m.PolicyClause.bundle_id == "policy-demo-v1").order_by(m.PolicyClause.id)).all()
        assert len(clauses) == 40
        baseline = {case["case_id"]: [clause.id for clause in retrieve(db, "policy-demo-v1", case["query"], 40)] for case in cases}
        texts = [clause.title + " " + clause.body for clause in clauses] + [case["query"] for case in cases]
    engine.dispose()
    started = time.perf_counter()
    env = os.environ.copy()
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env["DO_NOT_TRACK"] = "1"
    process = subprocess.run([executable, str(Path(__file__).with_name("embed_local.py"))], input=json.dumps({"texts": texts}), capture_output=True, text=True, env=env, check=True, timeout=120)
    embedded = json.loads(process.stdout)
    embedding_ms = round((time.perf_counter() - started) * 1000, 2)
    vectors = embedded["vectors"]
    assert len(vectors) == len(texts) and embedded["dimensions"] == MODEL_DIMENSIONS[MODEL]
    rows = []
    for index, case in enumerate(cases):
        scored = sorted(((clauses[position].id, cosine(vectors[position], vectors[len(clauses) + index])) for position in range(len(clauses))), key=lambda row: (-row[1], row[0]))
        semantic_ids = [item[0] for item in scored]
        lexical_ids = baseline[case["case_id"]]
        lexical_ranks = {item: rank for rank, item in enumerate(lexical_ids, start=1)}
        hybrid_ids = sorted(semantic_ids, key=lambda item: (-(1 / (60 + semantic_ids.index(item) + 1) + (1 / (60 + lexical_ranks[item]) if item in lexical_ranks else 0)), item))
        expected = case["expected"]
        rows.append({"case_id": case["case_id"], "expected": expected, "baseline_top1": lexical_ids[0] if lexical_ids else None, "baseline_hit_at_3": expected in lexical_ids[:3] if expected else None, "baseline_hit_at_5": expected in lexical_ids[:5] if expected else None, "semantic_top1": scored[0][0], "semantic_hit_at_3": expected in semantic_ids[:3] if expected else None, "semantic_hit_at_5": expected in semantic_ids[:5] if expected else None, "hybrid_top1": hybrid_ids[0], "hybrid_hit_at_3": expected in hybrid_ids[:3] if expected else None, "hybrid_hit_at_5": expected in hybrid_ids[:5] if expected else None, "semantic_top1_similarity": round(scored[0][1], 4), "semantic_expected_similarity": round(next(value for key, value in scored if key == expected), 4) if expected else None})
    positives = [row for row in rows if row["expected"]]
    negatives = [row for row in rows if not row["expected"]]
    summary = {"suite": "policy_retrieval_expanded_v1", "clauses": len(clauses), "cases": len(rows), "positive_cases": len(positives), "negative_cases": len(negatives), "baseline_top1": sum(row["baseline_top1"] == row["expected"] for row in positives), "baseline_hit_at_3": sum(row["baseline_hit_at_3"] for row in positives), "baseline_hit_at_5": sum(row["baseline_hit_at_5"] for row in positives), "baseline_negative_abstentions": sum(row["baseline_top1"] is None for row in negatives), "semantic_top1": sum(row["semantic_top1"] == row["expected"] for row in positives), "semantic_hit_at_3": sum(row["semantic_hit_at_3"] for row in positives), "semantic_hit_at_5": sum(row["semantic_hit_at_5"] for row in positives), "hybrid_top1": sum(row["hybrid_top1"] == row["expected"] for row in positives), "hybrid_hit_at_3": sum(row["hybrid_hit_at_3"] for row in positives), "hybrid_hit_at_5": sum(row["hybrid_hit_at_5"] for row in positives), "semantic_positive_median_similarity": round(statistics.median(row["semantic_expected_similarity"] for row in positives), 4), "semantic_negative_top1_similarity": [row["semantic_top1_similarity"] for row in negatives], "semantic_positive_retained_at_0_3": sum(row["semantic_top1_similarity"] >= 0.3 for row in positives), "semantic_negative_abstentions_at_0_3": sum(row["semantic_top1_similarity"] < 0.3 for row in negatives), "batch_embedding_ms": embedding_ms, "model": MODEL, "dimensions": embedded["dimensions"], "provider_calls": 0}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-policy-embedding-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "manifest.json").write_text(json.dumps({"run_id": run_id, "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "expansion_sha256": hashlib.sha256(EXPANSION.read_bytes()).hexdigest(), "negatives_sha256": hashlib.sha256(NEGATIVES.read_bytes()).hexdigest(), "model": MODEL, "clauses": [clause.id for clause in clauses], "embedding": "local-FastEmbed", "database": "in-memory-SQLite-synthetic-demo"}, indent=2) + "\n", encoding="utf-8")
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_id": run_id, **summary, "report": str(folder)}))


if __name__ == "__main__":
    main()
