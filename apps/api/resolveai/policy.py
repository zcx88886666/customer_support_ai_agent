from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import domain as d, models as m


def fingerprint(bundle: m.PolicyBundle, clauses: list[m.PolicyClause]) -> str:
    document = {"window_days": bundle.window_days, "effective_from": d.aware(bundle.effective_from).isoformat(), "clauses": [{"id": c.id, "title": c.title, "body": c.body} for c in sorted(clauses, key=lambda c: c.id)]}
    return hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def create_draft(db: Session, actor: str, bundle_id: str, window_days: int, clauses: list[dict], effective_from: datetime) -> m.PolicyBundle:
    if db.get(m.PolicyBundle, bundle_id):
        raise d.DomainError("bundle_exists", "Policy bundle ID exists")
    if window_days < 7 or window_days > 30 or not clauses:
        raise d.DomainError("invalid_policy", "Policy window or clauses invalid", 422)
    if len({clause["id"] for clause in clauses}) != len(clauses):
        raise d.DomainError("duplicate_clause", "Duplicate clause ID", 422)
    bundle = m.PolicyBundle(id=bundle_id, status="draft", effective_from=d.aware(effective_from), window_days=window_days, content_hash="pending", active=False)
    db.add(bundle)
    db.flush()
    values = []
    for clause in clauses:
        if not clause["id"].startswith(bundle_id + ":") or len(clause["body"].strip()) < 10:
            raise d.DomainError("invalid_clause", "Clause identifier or body invalid", 422)
        if any(marker in clause["body"].lower() for marker in ("ignore previous", "system prompt", "忽略以上指令")):
            raise d.DomainError("policy_injection", "Policy content contains instructions", 422)
        value = m.PolicyClause(id=clause["id"], bundle_id=bundle_id, title=clause["title"], body=clause["body"])
        values.append(value)
        db.add(value)
    bundle.content_hash = fingerprint(bundle, values)
    d.audit(db, actor, "create_policy_draft", "policy_bundle", bundle_id, bundle=bundle_id)
    return bundle


def index_and_verify(db: Session, actor: str, bundle_id: str) -> m.PolicyBundle:
    bundle = db.get(m.PolicyBundle, bundle_id)
    if not bundle or bundle.status != "draft":
        raise d.DomainError("invalid_policy_state", "Draft policy required")
    clauses = db.scalars(select(m.PolicyClause).where(m.PolicyClause.bundle_id == bundle_id)).all()
    if fingerprint(bundle, clauses) != bundle.content_hash or not any("退货" in c.body for c in clauses):
        raise d.DomainError("policy_validation_failed", "Policy hash or required evidence invalid")
    bundle.status = "indexed"
    db.flush()
    # Rule boundary behavior is covered by domain tests; only verified local
    # bundles may be activated. This stage checks the indexed content hash.
    bundle.status = "verified"
    d.audit(db, actor, "verify_policy", "policy_bundle", bundle_id, bundle=bundle_id, details={"content_hash": bundle.content_hash})
    return bundle


def activate(db: Session, actor: str, bundle_id: str) -> m.PolicyBundle:
    bundle = db.get(m.PolicyBundle, bundle_id)
    if not bundle or bundle.status != "verified":
        raise d.DomainError("policy_not_verified", "Verified policy required")
    clauses = db.scalars(select(m.PolicyClause).where(m.PolicyClause.bundle_id == bundle_id)).all()
    if fingerprint(bundle, clauses) != bundle.content_hash:
        raise d.DomainError("policy_hash_mismatch", "Policy changed after verification")
    for old in db.scalars(select(m.PolicyBundle).where(m.PolicyBundle.active.is_(True)).with_for_update()).all():
        old.active = False
        old.status = "superseded"
    bundle.active = True
    bundle.status = "active"
    d.audit(db, actor, "activate_policy", "policy_bundle", bundle_id, bundle=bundle_id, details={"content_hash": bundle.content_hash})
    return bundle
