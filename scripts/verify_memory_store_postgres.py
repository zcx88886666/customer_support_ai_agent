"""Exercise the authenticated PostgresStore memory route in an isolated DB."""

from __future__ import annotations

import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from resolveai import models as m
from resolveai.api import app
from resolveai.config import settings
from resolveai.db import SessionLocal, init_db
from resolveai.memory import _namespace, setup_long_term_store, upsert_preference


def main() -> None:
    assert "resolveai_memory_ab" in settings.database_url
    assert settings.long_term_memory_mode == "postgres_store"
    assert settings.auth_mode == "mock"
    init_db()
    suffix = uuid4().hex[:8]
    owner = "synthetic_memory_api_" + suffix
    stranger = "synthetic_memory_other_" + suffix
    with SessionLocal.begin() as db:
        for customer_id in (owner, stranger):
            db.add(m.Customer(id=customer_id, display_name="Synthetic memory test"))
            db.add(m.CustomerProfile(customer_id=customer_id, language="en", channel="web", memory_consent=False))

    def headers(customer_id: str) -> dict[str, str]:
        return {"X-Mock-Actor": customer_id, "X-Mock-Role": "customer"}

    checks = []
    with TestClient(app) as client:
        assert client.put("/profile/preferences/language", headers=headers(owner), json={"value": "English", "confirmed": True}).status_code == 403
        checks.append("unconsented_write_denied")
        assert client.post("/profile/memory-consent", headers=headers(owner), json={"consent": True}).status_code == 200
        assert client.put("/profile/preferences/language", headers=headers(owner), json={"value": "English", "confirmed": False}).status_code == 403
        assert client.put("/profile/preferences/language", headers=headers(owner), json={"value": "English", "confirmed": True}).status_code == 200
        assert client.get("/profile/preferences", headers=headers(stranger)).json()["preferences"] == {}
        assert client.put("/profile/preferences/language", headers=headers(owner), json={"value": "Spanish", "confirmed": True}).status_code == 200
        assert client.get("/profile/preferences", headers=headers(owner)).json()["preferences"] == {"language": "Spanish"}
        checks.append("owned_confirmed_correction")

        from langgraph.store.postgres import PostgresStore
        with SessionLocal.begin() as db:
            store = PostgresStore(db.connection().connection.driver_connection)
            store.put(_namespace(owner), "language", {"value": "Italian"}, index=False)
        assert client.get("/profile/preferences", headers=headers(owner)).status_code == 503
        setup_long_term_store()
        assert client.get("/profile/preferences", headers=headers(owner)).json()["preferences"] == {"language": "Spanish"}
        checks.append("drift_fails_closed_then_reconciles")

        try:
            with SessionLocal.begin() as db:
                upsert_preference(db, owner, "language", "French", True)
                raise RuntimeError("deliberate rollback")
        except RuntimeError:
            pass
        assert client.get("/profile/preferences", headers=headers(owner)).json()["preferences"] == {"language": "Spanish"}
        checks.append("same_transaction_rollback")

        assert client.delete("/profile/preferences/language", headers=headers(owner)).status_code == 200
        assert client.get("/profile/preferences", headers=headers(owner)).json()["preferences"] == {}
        assert client.put("/profile/preferences/language", headers=headers(owner), json={"value": "English", "confirmed": True}).status_code == 200
        assert client.post("/profile/memory-consent", headers=headers(owner), json={"consent": False}).status_code == 200
        assert client.get("/profile/preferences", headers=headers(owner)).json()["preferences"] == {}
        checks.append("delete_and_consent_revoke")

    with TestClient(app) as client:
        assert client.get("/profile/preferences", headers=headers(owner)).json()["preferences"] == {}
        assert client.get("/profile/preferences", headers=headers(stranger)).json()["preferences"] == {}
        checks.append("restart_and_cross_user_isolation")

    with SessionLocal.begin() as db:
        store = PostgresStore(db.connection().connection.driver_connection)
        assert not store.search(_namespace(owner), limit=20)
        assert not store.search(_namespace(stranger), limit=20)
        entries = db.scalars(select(m.MemoryEntry).where(m.MemoryEntry.customer_id == owner)).all()
        assert len(entries) == 1 and entries[0].revoked
        checks.append("no_retrievable_store_data")
    print(json.dumps({"database": "isolated_postgresql", "checks": checks, "passed": len(checks)}))


if __name__ == "__main__":
    main()
