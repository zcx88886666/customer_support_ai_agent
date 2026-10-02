"""Verify the real PostgreSQL policy index without committing test data."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from resolveai.db import SessionLocal
from resolveai.policy import create_draft, index_and_verify
from resolveai.policy_retrieval import postgres_hits, retrieve


def main() -> None:
    with SessionLocal() as db:
        assert db.bind.dialect.name == "postgresql", "PostgreSQL is required"
        try:
            revision = db.scalar(text("SELECT version_num FROM alembic_version"))
            assert revision == "0e7b4cb91a62", revision
            indexes = set(db.scalars(text("SELECT indexname FROM pg_indexes WHERE tablename='policy_clause_search'")))
            assert {"ix_policy_search_terms", "ix_policy_search_vector"} <= indexes
            count = db.scalar(text("SELECT count(*) FROM policy_clause_search WHERE bundle_id='policy-demo-v1'"))
            assert count == 4, count
            hits = postgres_hits(db, "policy-demo-v1", "七天无理由退货", 5)
            assert hits and hits[0].id == "clause-window", [hit.id for hit in hits]
            assert retrieve(db, "policy-demo-v1", "火星天气") == []

            bundle_id = "policy-search-check-" + uuid4().hex[:12]
            clause_id = bundle_id + ":window"
            create_draft(
                db, "support-check", bundle_id, 7,
                [{"id": clause_id, "title": "测试专用退货条件", "body": "测试专用商品签收后可以申请退货。"}],
                datetime.now(timezone.utc),
            )
            index_and_verify(db, "supervisor-check", bundle_id)
            assert [hit.id for hit in retrieve(db, bundle_id, "测试专用退货")] == [clause_id]
            assert all(hit.bundle_id == "policy-demo-v1" for hit in retrieve(db, "policy-demo-v1", "测试专用退货"))
            db.execute(text("UPDATE policy_clause_search SET index_version='stale' WHERE clause_id=:clause_id"), {"clause_id": clause_id})
            assert retrieve(db, bundle_id, "测试专用退货") == [], "Stale index must fail closed"
            print({"revision": revision, "demo_clauses": count, "indexes": sorted(indexes), "live_draft_index": "passed", "bundle_scope": "passed", "stale_index": "rejected"})
        finally:
            db.rollback()


if __name__ == "__main__":
    main()
