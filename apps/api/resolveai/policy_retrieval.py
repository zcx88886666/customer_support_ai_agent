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


SYNONYMS = {
    "我要退": "退货", "能退": "退货", "可以退": "退货", "可退": "退货", "没到": "未签收", "退款": "模拟退款", "七天": "七日",
    "收到货": "签收", "寄回": "退回", "反悔": "无理由", "数字商品": "非实物", "虚拟产品": "非实物", "特别说明": "特殊商品",
    "退件": "退货", "少了一件": "数量", "损坏": "商品状态", "复核": "质检", "数量不对": "数量", "原支付方式": "原路退款",
    "钱何时回到": "模拟退款", "打款": "退款", "优惠后": "实付",
}
ENGLISH_SYNONYMS = (
    (r"\bdigital goods\b", "非实物"),
    (r"\bno-reason\b", "无理由"),
    (r"\breturns?\b|\breturned\b", "退货"),
    (r"\brefund(?:ed|s)?\b", "模拟退款"),
    (r"\bdeliver(?:y|ed)\b", "签收"),
    (r"\bproducts?\b|\bgoods\b", "商品"),
    (r"\bwarehouse\b", "仓库"),
    (r"\binspect(?:ed|ion)?\b", "质检"),
    (r"\bpaid\b", "实付"),
    (r"\bapproval\b|\bapproved\b", "主管批准"),
)
POLICY_SCOPE_CHINESE = ("退", "寄回", "签收", "质检", "仓库", "无理由", "七天", "七日", "批准", "打款", "实付", "收货后", "数量不对", "损坏", "原支付方式")
POLICY_SCOPE_ENGLISH = re.compile(r"\b(return(?:s|ed)?|refund(?:s|ed)?|deliver(?:y|ed)|warehouse|inspect(?:ion|ed)?|approval|approved)\b")
INDEX_VERSION = "local-gram-v2"
VECTOR_DIMENSIONS = 128


def in_policy_scope(query: str) -> bool:
    lower = query.lower()
    return any(word in lower for word in POLICY_SCOPE_CHINESE) or bool(POLICY_SCOPE_ENGLISH.search(lower))


def terms(text: str) -> set[str]:
    normal = text.lower()
    for source, target in SYNONYMS.items():
        normal = normal.replace(source, target)
    for pattern, target in ENGLISH_SYNONYMS:
        normal = re.sub(pattern, target, normal)
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
    if not in_policy_scope(query):
        return []
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
