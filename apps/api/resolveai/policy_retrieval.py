"""Version-scoped lexical and character-gram retrieval with RRF fusion.

The local mock path uses deterministic character grams; a published bundle ID is
always selected by the service before retrieval. This is not a hosted embedding.
"""

from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m


SYNONYMS = {"我要退": "退货", "能退": "退货", "可以退": "退货", "没到": "未签收", "退款": "模拟退款", "七天": "七日"}


def terms(text: str) -> set[str]:
    normal = text.lower()
    for source, target in SYNONYMS.items():
        normal = normal.replace(source, target)
    chinese = re.findall(r"[\u4e00-\u9fff]", normal)
    grams = {"".join(chinese[i:i + 2]) for i in range(len(chinese) - 1)}
    words = set(re.findall(r"[a-z]{3,}", normal))
    return grams | words


def retrieve(db: Session, bundle_id: str, query: str, limit: int = 5) -> list[m.PolicyClause]:
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
