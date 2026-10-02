"""Version-scoped hybrid retrieval with a deterministic SQLite mock path.

The local mock path uses deterministic character grams; a published bundle ID is
always selected by the service before retrieval. This is not a hosted embedding.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from sqlalchemy import func, select, text as sql_text
from sqlalchemy.orm import Session

from . import models as m


SYNONYMS = {"我要退": "退货", "能退": "退货", "可以退": "退货", "没到": "未签收", "退款": "模拟退款", "七天": "七日"}
INDEX_VERSION = "local-gram-v1"
VECTOR_DIMENSIONS = 128


def terms(text: str) -> set[str]:
    normal = text.lower()
    for source, target in SYNONYMS.items():
        normal = normal.replace(source, target)
    chinese = re.findall(r"[\u4e00-\u9fff]", normal)
    grams = {"".join(chinese[i:i + 2]) for i in range(len(chinese) - 1)}
    words = set(re.findall(r"[a-z]{3,}", normal))
    return grams | words


def gram_vector(value: str) -> str:
    """Stable local 128-dimensional character-gram sketch, with no model cost."""
    counts = Counter(int.from_bytes(hashlib.sha256(term.encode()).digest()[:4], "big") % VECTOR_DIMENSIONS for term in terms(value))
    magnitude = math.sqrt(sum(count * count for count in counts.values())) or 1
    return "[" + ",".join(f"{counts.get(index, 0) / magnitude:.8f}" for index in range(VECTOR_DIMENSIONS)) + "]"


def index_bundle(db: Session, bundle_id: str) -> int:
    if db.bind.dialect.name != "postgresql":
        return 0
    bundle = db.get(m.PolicyBundle, bundle_id)
    if bundle is None or bundle.status not in {"active", "verified", "indexed", "superseded"}:
        return 0
    clauses = db.scalars(select(m.PolicyClause).where(m.PolicyClause.bundle_id == bundle_id)).all()
    for clause in clauses:
        joined = " ".join(sorted(terms(clause.title + " " + clause.body)))
        db.execute(sql_text("""INSERT INTO policy_clause_search
            (clause_id, bundle_id, content_hash, index_version, search_terms, gram_vector)
            VALUES (:clause_id, :bundle_id, :content_hash, :index_version,
                    to_tsvector('simple', :search_terms), CAST(:gram_vector AS vector))
            ON CONFLICT (clause_id) DO UPDATE SET
                content_hash = EXCLUDED.content_hash,
                index_version = EXCLUDED.index_version,
                search_terms = EXCLUDED.search_terms,
                gram_vector = EXCLUDED.gram_vector"""), {"clause_id": clause.id, "bundle_id": bundle.id, "content_hash": bundle.content_hash, "index_version": INDEX_VERSION, "search_terms": joined, "gram_vector": gram_vector(clause.title + " " + clause.body)})
    return len(clauses)


def index_current(db: Session, bundle_id: str) -> bool:
    if db.bind.dialect.name != "postgresql":
        return True
    bundle = db.get(m.PolicyBundle, bundle_id)
    if not bundle:
        return False
    total = db.scalar(select(func.count()).select_from(m.PolicyClause).where(m.PolicyClause.bundle_id == bundle_id))
    indexed = db.scalar(sql_text("SELECT count(*) FROM policy_clause_search WHERE bundle_id=:bundle_id AND content_hash=:digest AND index_version=:version"), {"bundle_id": bundle_id, "digest": bundle.content_hash, "version": INDEX_VERSION})
    return bool(total and indexed == total)


def postgres_hits(db: Session, bundle_id: str, query: str, limit: int) -> list[m.PolicyClause]:
    bundle = db.get(m.PolicyBundle, bundle_id)
    if not bundle:
        return []
    query_terms = terms(query)
    if not query_terms:
        return []
    if not index_current(db, bundle_id):
        return []
    refs = db.execute(sql_text("""WITH lexical_candidates AS (
            SELECT clause_id, ts_rank_cd(search_terms, to_tsquery('simple', :query_terms)) AS score
            FROM policy_clause_search
            WHERE bundle_id=:bundle_id AND content_hash=:digest AND index_version=:version
              AND search_terms @@ to_tsquery('simple', :query_terms)
            ORDER BY score DESC, clause_id ASC LIMIT :candidate_limit
        ), vector_candidates AS (
            SELECT clause_id, gram_vector <=> CAST(:query_vector AS vector) AS distance
            FROM policy_clause_search
            WHERE bundle_id=:bundle_id AND content_hash=:digest AND index_version=:version
            ORDER BY distance ASC, clause_id ASC LIMIT :candidate_limit
        ), ranked AS (
            SELECT clause_id, row_number() OVER (ORDER BY score DESC, clause_id) AS position FROM lexical_candidates
            UNION ALL
            SELECT clause_id, row_number() OVER (ORDER BY distance ASC, clause_id) AS position FROM vector_candidates
        )
        SELECT clause_id FROM ranked GROUP BY clause_id
        ORDER BY sum(1.0 / (60 + position)) DESC, clause_id ASC
        LIMIT :candidate_limit"""), {"bundle_id": bundle_id, "digest": bundle.content_hash, "version": INDEX_VERSION, "query_terms": " | ".join(sorted(query_terms)), "query_vector": gram_vector(query), "candidate_limit": max(20, limit * 4)}).scalars().all()
    if not refs:
        return []
    clauses = {clause.id: clause for clause in db.scalars(select(m.PolicyClause).where(m.PolicyClause.bundle_id == bundle_id, m.PolicyClause.id.in_(refs))).all()}
    return [clauses[ref] for ref in refs if ref in clauses and query_terms & terms(clauses[ref].title + " " + clauses[ref].body)][:limit]


def retrieve(db: Session, bundle_id: str, query: str, limit: int = 5) -> list[m.PolicyClause]:
    if db.bind.dialect.name == "postgresql":
        return postgres_hits(db, bundle_id, query, limit)
    clauses = db.scalars(select(m.PolicyClause).where(m.PolicyClause.bundle_id == bundle_id)).all()
    query_terms = terms(query)
    if not query_terms:
        return []
    scored = []
    for clause in clauses:
        title_terms = terms(clause.title)
        body_terms = terms(clause.body)
        lexical = 3 * len(query_terms & title_terms) + len(query_terms & body_terms)
        similarity = len(query_terms & (title_terms | body_terms)) / max(1, len(query_terms | title_terms | body_terms))
        if lexical:
            scored.append((clause, lexical, similarity))
    lexical_rank = {clause.id: i for i, (clause, _, _) in enumerate(sorted(scored, key=lambda row: (-row[1], row[0].id)), 1)}
    gram_rank = {clause.id: i for i, (clause, _, _) in enumerate(sorted(scored, key=lambda row: (-row[2], row[0].id)), 1)}
    ranked = sorted((clause for clause, _, _ in scored), key=lambda clause: (-(1 / (60 + lexical_rank[clause.id]) + 1 / (60 + gram_rank[clause.id])), clause.id))
    return ranked[:limit]
