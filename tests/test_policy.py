from __future__ import annotations

from datetime import datetime, timezone

import pytest

from resolveai import domain as d, models as m
from resolveai.policy import activate, create_draft, index_and_verify
from resolveai.policy_retrieval import retrieve


def test_policy_bundle_hash_and_atomic_activation(db):
    bundle = create_draft(db, "support-1", "policy-v2", 7, [{"id": "policy-v2:window", "title": "退货", "body": "合格商品签收次日起可申请退货。"}], datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert not bundle.active
    index_and_verify(db, "supervisor-1", bundle.id)
    activate(db, "supervisor-1", bundle.id)
    assert d.active_policy(db).id == bundle.id
    assert db.get(m.PolicyBundle, "policy-demo-v1").status == "superseded"


def test_policy_drift_rejected(db):
    bundle = create_draft(db, "support-1", "policy-v3", 7, [{"id": "policy-v3:window", "title": "退货", "body": "合格商品签收次日起可申请退货。"}], datetime(2026, 10, 1, tzinfo=timezone.utc))
    db.get(m.PolicyClause, "policy-v3:window").body = "changed content after draft"
    with pytest.raises(d.DomainError) as exc:
        index_and_verify(db, "supervisor-1", bundle.id)
    assert exc.value.code == "policy_validation_failed"


def test_retrieval_stays_in_bundle_and_reports_no_match(db):
    hits = retrieve(db, "policy-demo-v1", "七天无理由退货", 5)
    assert hits and hits[0].id == "clause-window"
    assert all(hit.bundle_id == "policy-demo-v1" for hit in hits)
    assert retrieve(db, "policy-demo-v1", "火星天气", 5) == []
