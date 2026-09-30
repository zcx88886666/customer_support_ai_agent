from __future__ import annotations

from fastapi.testclient import TestClient

from resolveai.api import app
from resolveai.db import get_db


def test_api_object_authorization(session_factory):
    def override_db():
        with session_factory() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            owned = client.get("/orders/demo-order-01", headers={"x-mock-actor": "cust-01", "x-mock-role": "customer"})
            other = client.get("/orders/demo-order-05", headers={"x-mock-actor": "cust-01", "x-mock-role": "customer"})
            denied = client.post("/supervisor/proposals/fake/decision", json={"approve": True}, headers={"x-mock-actor": "cust-01", "x-mock-role": "customer"})
        assert owned.status_code == 200
        assert other.status_code == 404
        assert denied.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_support_only_sees_assigned_ticket(session_factory):
    def override_db():
        with session_factory() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            created = client.post("/chat", json={"thread_id": "ticket-thread", "message": "我要人工客服"}, headers={"x-mock-actor": "cust-01", "x-mock-role": "customer"})
            ticket_id = created.json()["ticket_id"]
            unassigned = client.get("/tickets", headers={"x-mock-actor": "support-a", "x-mock-role": "support"})
            assert unassigned.json() == []
            assigned = client.post(f"/supervisor/tickets/{ticket_id}/assign", json={"support_actor_id": "support-a"}, headers={"x-mock-actor": "supervisor-a", "x-mock-role": "supervisor"})
            visible = client.get("/tickets", headers={"x-mock-actor": "support-a", "x-mock-role": "support"})
            other = client.get("/tickets", headers={"x-mock-actor": "support-b", "x-mock-role": "support"})
        assert assigned.status_code == 200
        assert visible.json()[0]["id"] == ticket_id
        assert other.json() == []
    finally:
        app.dependency_overrides.clear()
