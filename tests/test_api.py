from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from resolveai.api import app
from resolveai.db import get_db
from resolveai import models as m, worker


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


def test_http_return_to_approved_single_refund(session_factory, monkeypatch):
    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    customer = {"x-mock-actor": "cust-01", "x-mock-role": "customer"}
    other_customer = {"x-mock-actor": "cust-02", "x-mock-role": "customer"}
    warehouse = {"x-mock-actor": "warehouse-1", "x-mock-role": "warehouse"}
    supervisor = {"x-mock-actor": "supervisor-1", "x-mock-role": "supervisor"}
    body = {"order_id": "demo-order-01", "order_item_id": "demo-item-01", "quantity": 1, "reason": "no longer needed", "confirmed": True, "idempotency_key": "http-full-flow"}
    try:
        with TestClient(app) as client:
            denied = client.post("/returns", json=body, headers=other_customer)
            assert denied.status_code == 404
            created = client.post("/returns", json=body, headers=customer)
            assert created.status_code == 200
            return_id = created.json()["id"]
            assert client.post("/returns", json=body, headers=customer).json()["id"] == return_id
            assert client.get(f"/returns/{return_id}", headers=other_customer).status_code == 404
            receipt = client.post(f"/warehouse/returns/{return_id}/receipt", json={"quantity": 1}, headers=warehouse)
            inspection = client.post(f"/warehouse/returns/{return_id}/inspection", json={"passed": True, "note": "intact"}, headers=warehouse)
            proposal = client.post(f"/returns/{return_id}/proposal", headers=warehouse)
            assert receipt.status_code == inspection.status_code == proposal.status_code == 200
            proposal_id = proposal.json()["id"]
            assert worker.issue_approved_once() == []
            assert client.post(f"/supervisor/proposals/{proposal_id}/decision", json={"approve": True}, headers=customer).status_code == 403
            decision = client.post(f"/supervisor/proposals/{proposal_id}/decision", json={"approve": True}, headers=supervisor)
            assert decision.status_code == 200
            first = worker.issue_approved_once()
            assert len(first) == 1
            assert worker.issue_approved_once() == []
            assert client.get(f"/returns/{return_id}", headers=customer).json()["status"] == "refund_issued"
        with session_factory() as db:
            assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 1
            ledger = db.scalar(select(m.RefundLedger))
            assert ledger.amount_cents == db.get(m.OrderItem, "demo-item-01").paid_cents
            assert {event.action for event in db.scalars(select(m.AuditEvent)).all()} >= {"create_return", "record_receipt", "record_inspection", "create_proposal", "approved", "issue_refund"}
    finally:
        app.dependency_overrides.clear()
