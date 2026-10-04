"""Confirmed customer preferences with correction, revocation and ownership."""

from __future__ import annotations

import re
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import domain as d, models as m
from .config import settings

ALLOWED_KEYS = {"language", "channel", "communication_style"}
FORBIDDEN = re.compile(r"\b\d{11,19}\b|@|(?:地址|银行卡|身份证|password|token)", re.IGNORECASE)


def _allowed_preference(key: str, value: str) -> bool:
    return key in ALLOWED_KEYS and bool(value.strip()) and len(value) <= 120 and not FORBIDDEN.search(value)


def _namespace(customer_id: str) -> tuple[str, ...]:
    return ("resolveai", "customer", customer_id, "preferences")


def _locked_profile(db: Session, customer_id: str) -> m.CustomerProfile | None:
    return db.scalar(select(m.CustomerProfile).where(m.CustomerProfile.customer_id == customer_id).with_for_update())


class LongTermMemory(Protocol):
    def list(self, customer_id: str) -> dict[str, str]: ...
    def put(self, customer_id: str, key: str, value: str) -> None: ...
    def delete(self, customer_id: str, key: str) -> None: ...
    def clear(self, customer_id: str) -> None: ...


class _PostgresLongTermMemory:
    def __init__(self, connection):
        from langgraph.store.postgres import PostgresStore
        self.store = PostgresStore(connection)

    def list(self, customer_id: str) -> dict[str, str]:
        return {item.key: item.value["value"] for item in self.store.search(_namespace(customer_id), limit=20)}

    def put(self, customer_id: str, key: str, value: str) -> None:
        self.store.put(_namespace(customer_id), key, {"value": value}, index=False)

    def delete(self, customer_id: str, key: str) -> None:
        self.store.delete(_namespace(customer_id), key)

    def clear(self, customer_id: str) -> None:
        for key in self.list(customer_id):
            self.delete(customer_id, key)

    def customer_ids(self) -> set[str]:
        result = set()
        offset = 0
        while True:
            page = self.store.list_namespaces(prefix=("resolveai", "customer"), max_depth=4, limit=500, offset=offset)
            result.update(namespace[2] for namespace in page if len(namespace) == 4 and namespace[3] == "preferences")
            if len(page) < 500:
                return result
            offset += len(page)


def _store(db: Session) -> LongTermMemory | None:
    if settings.long_term_memory_mode == "off":
        return None
    if settings.long_term_memory_mode != "postgres_store" or db.bind.dialect.name != "postgresql":
        raise RuntimeError("LONG_TERM_MEMORY_MODE requires postgres_store on PostgreSQL")
    return _PostgresLongTermMemory(db.connection().connection.driver_connection)


def setup_long_term_store() -> None:
    """Prepare PostgresStore and reconcile the structured profile before traffic."""
    if settings.long_term_memory_mode == "off":
        return
    if settings.long_term_memory_mode != "postgres_store" or not settings.database_url.startswith("postgresql+psycopg://"):
        raise RuntimeError("LONG_TERM_MEMORY_MODE requires postgres_store on PostgreSQL")
    from langgraph.store.postgres import PostgresStore
    from .db import SessionLocal

    url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with PostgresStore.from_conn_string(url) as store:
        store.setup()
    with SessionLocal.begin() as db:
        store = _store(db)
        customers = set(db.scalars(select(m.MemoryEntry.customer_id).distinct()).all()) | store.customer_ids()
        for customer_id in customers:
            profile = db.get(m.CustomerProfile, customer_id)
            active = {entry.key: entry.value for entry in db.scalars(select(m.MemoryEntry).where(m.MemoryEntry.customer_id == customer_id, m.MemoryEntry.revoked.is_(False))).all() if _allowed_preference(entry.key, entry.value)} if profile and profile.memory_consent else {}
            existing = store.list(customer_id)
            for key, value in existing.items():
                if key not in active or value != active[key]:
                    store.delete(customer_id, key)
            for key, value in active.items():
                if existing.get(key) != value:
                    store.put(customer_id, key, value)


def list_preferences(db: Session, customer_id: str) -> dict[str, str]:
    profile = db.get(m.CustomerProfile, customer_id)
    if not profile or not profile.memory_consent:
        return {}
    authoritative = {entry.key: entry.value for entry in db.scalars(select(m.MemoryEntry).where(m.MemoryEntry.customer_id == customer_id, m.MemoryEntry.revoked.is_(False))).all()}
    if any(not _allowed_preference(key, value) for key, value in authoritative.items()):
        raise d.DomainError("memory_content_rejected", "Stored preference content is invalid", 503)
    store = _store(db)
    if store is None:
        return authoritative
    cached = store.list(customer_id)
    if cached != authoritative:
        raise d.DomainError("memory_store_conflict", "Preference store is out of sync", 503)
    return cached


def upsert_preference(db: Session, customer_id: str, key: str, value: str, confirmed: bool) -> m.MemoryEntry:
    profile = _locked_profile(db, customer_id)
    if not profile or not profile.memory_consent or not confirmed:
        raise d.DomainError("memory_consent_required", "Explicit preference confirmation and consent required", 403)
    if not _allowed_preference(key, value):
        raise d.DomainError("memory_content_rejected", "Preference content not allowed", 422)
    entry = db.scalar(select(m.MemoryEntry).where(m.MemoryEntry.customer_id == customer_id, m.MemoryEntry.key == key))
    if entry:
        entry.value = value.strip()
        entry.revoked = False
    else:
        entry = m.MemoryEntry(customer_id=customer_id, key=key, value=value.strip(), revoked=False)
        db.add(entry)
    db.flush()
    store = _store(db)
    if store is not None:
        store.put(customer_id, key, entry.value)
    d.audit(db, customer_id, "correct_preference", "memory", entry.id, details={"key": key})
    return entry


def delete_preference(db: Session, customer_id: str, key: str):
    _locked_profile(db, customer_id)
    entry = db.scalar(select(m.MemoryEntry).where(m.MemoryEntry.customer_id == customer_id, m.MemoryEntry.key == key))
    if entry:
        entry.revoked = True
        entry.value = ""
        d.audit(db, customer_id, "revoke_preference", "memory", entry.id, details={"key": key})
    store = _store(db)
    if store is not None:
        store.delete(customer_id, key)


def set_consent(db: Session, customer_id: str, consent: bool):
    profile = _locked_profile(db, customer_id)
    if not profile:
        raise d.DomainError("profile_not_found", "Profile unavailable", 404)
    profile.memory_consent = consent
    if not consent:
        store = _store(db)
        for entry in db.scalars(select(m.MemoryEntry).where(m.MemoryEntry.customer_id == customer_id)).all():
            entry.revoked = True
            entry.value = ""
        if store is not None:
            store.clear(customer_id)
    d.audit(db, customer_id, "memory_consent", "profile", customer_id, details={"consent": consent})
