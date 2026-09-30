"""Confirmed customer preferences with correction, revocation and ownership."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import domain as d, models as m

ALLOWED_KEYS = {"language", "channel", "communication_style"}
FORBIDDEN = re.compile(r"\b\d{11,19}\b|@|(?:地址|银行卡|身份证|password|token)", re.IGNORECASE)


def list_preferences(db: Session, customer_id: str) -> dict[str, str]:
    return {entry.key: entry.value for entry in db.scalars(select(m.MemoryEntry).where(m.MemoryEntry.customer_id == customer_id, m.MemoryEntry.revoked.is_(False))).all()}


def upsert_preference(db: Session, customer_id: str, key: str, value: str, confirmed: bool) -> m.MemoryEntry:
    profile = db.get(m.CustomerProfile, customer_id)
    if not profile or not profile.memory_consent or not confirmed:
        raise d.DomainError("memory_consent_required", "Explicit preference confirmation and consent required", 403)
    if key not in ALLOWED_KEYS or not value.strip() or len(value) > 120 or FORBIDDEN.search(value):
        raise d.DomainError("memory_content_rejected", "Preference content not allowed", 422)
    entry = db.scalar(select(m.MemoryEntry).where(m.MemoryEntry.customer_id == customer_id, m.MemoryEntry.key == key))
    if entry:
        entry.value = value.strip()
        entry.revoked = False
    else:
        entry = m.MemoryEntry(customer_id=customer_id, key=key, value=value.strip(), revoked=False)
        db.add(entry)
    db.flush()
    d.audit(db, customer_id, "correct_preference", "memory", entry.id, details={"key": key})
    return entry


def delete_preference(db: Session, customer_id: str, key: str):
    entry = db.scalar(select(m.MemoryEntry).where(m.MemoryEntry.customer_id == customer_id, m.MemoryEntry.key == key))
    if entry:
        entry.revoked = True
        d.audit(db, customer_id, "revoke_preference", "memory", entry.id, details={"key": key})


def set_consent(db: Session, customer_id: str, consent: bool):
    profile = db.get(m.CustomerProfile, customer_id)
    if not profile:
        raise d.DomainError("profile_not_found", "Profile unavailable", 404)
    profile.memory_consent = consent
    if not consent:
        for entry in db.scalars(select(m.MemoryEntry).where(m.MemoryEntry.customer_id == customer_id)).all():
            entry.revoked = True
    d.audit(db, customer_id, "memory_consent", "profile", customer_id, details={"consent": consent})
