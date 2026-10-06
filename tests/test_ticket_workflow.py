from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from resolveai import domain as d
from resolveai import models as m
from resolveai.api import app
from resolveai.auth import Principal, principal
from resolveai.db import get_db


CUSTOMER = {"x-mock-actor": "cust-01", "x-mock-role": "customer"}
FOREIGN_CUSTOMER = {"x-mock-actor": "cust-02", "x-mock-role": "customer"}
SUPPORT = {"x-mock-actor": "support-a", "x-mock-role": "support"}
FOREIGN_SUPPORT = {"x-mock-actor": "support-b", "x-mock-role": "support"}
SUPERVISOR = {"x-mock-actor": "supervisor-a", "x-mock-role": "supervisor"}
WAREHOUSE = {"x-mock-actor": "warehouse-a", "x-mock-role": "warehouse"}


def test_ticket_messages_and_resolution_preserve_ownership_and_refund_gate(session_factory):
    with session_factory.begin() as db:
        db.add(m.Ticket(id="ticket-a", customer_id="cust-01", order_id="demo-order-01", topic="delivery dispute"))

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    path = "/tickets/ticket-a"
    try:
        with TestClient(app) as client:
            assert client.get(path, headers=FOREIGN_CUSTOMER).status_code == 404
            assert client.get(path, headers=SUPPORT).status_code == 404
            assert client.post(path + "/messages", json={"body": "unauthorized"}, headers=FOREIGN_SUPPORT).status_code == 404
            assert client.post(path + "/resolve", json={"body": "unauthorized"}, headers=SUPPORT).status_code == 404
            assert client.post(path + "/messages", json={"body": "My package did not arrive."}, headers=CUSTOMER).status_code == 200
            assert client.post("/supervisor/tickets/ticket-a/assign", json={"support_actor_id": "support-a"}, headers=SUPERVISOR).status_code == 200
            assert client.post(path + "/messages", json={"body": "We are checking the shipment."}, headers=SUPPORT).status_code == 200
            assert client.post(path + "/messages", json={"body": "unauthorized"}, headers=FOREIGN_SUPPORT).status_code == 404
            detail = client.get(path, headers=CUSTOMER)
            assert detail.status_code == 200
            assert [(message["actor_type"], message["body"]) for message in detail.json()["messages"]] == [
                ("customer", "My package did not arrive."), ("support", "We are checking the shipment.")
            ]
            assert client.get(path, headers=SUPPORT).status_code == 200
            assert client.get(path, headers=FOREIGN_SUPPORT).status_code == 404
            assert client.get(path, headers=SUPERVISOR).status_code == 200
            assert client.post(path + "/resolve", json={"body": "We verified the shipment and contacted the carrier."}, headers=CUSTOMER).status_code == 403
            resolved = client.post(path + "/resolve", json={"body": "We verified the shipment and contacted the carrier."}, headers=SUPPORT)
            assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
            assert client.post(path + "/resolve", json={"body": "again"}, headers=SUPPORT).status_code == 409
            assert client.post(path + "/messages", json={"body": "after closure"}, headers=CUSTOMER).status_code == 409
            assert client.post("/supervisor/tickets/ticket-a/assign", json={"support_actor_id": "support-b"}, headers=SUPERVISOR).status_code == 409
            assert client.get(path, headers=CUSTOMER).json()["messages"][-1]["body"] == "We verified the shipment and contacted the carrier."
            assert client.post("/supervisor/proposals/fake/decision", json={"approve": True}, headers=SUPPORT).status_code == 403
        with session_factory() as db:
            assert db.scalar(select(func.count()).select_from(m.ConversationMessage)) == 3
            assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0
            assert {row.action for row in db.scalars(select(m.AuditEvent).where(m.AuditEvent.entity_id == "ticket-a"))} >= {"assign_ticket", "ticket_message", "resolve_ticket"}
    finally:
        app.dependency_overrides.clear()


def test_ticket_requires_nonblank_bounded_message(session_factory):
    with session_factory.begin() as db:
        db.add(m.Ticket(id="ticket-b", customer_id="cust-01", topic="question"))

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            for body in ("", "   ", "x" * 2001):
                assert client.post("/tickets/ticket-b/messages", json={"body": body}, headers=CUSTOMER).status_code == 422
            assert client.post("/supervisor/tickets/ticket-b/assign", json={"support_actor_id": "   "}, headers=SUPERVISOR).status_code == 422
            assert client.post("/tickets/ticket-b/resolve", json={"body": "valid"}, headers=SUPPORT).status_code == 404
        with session_factory() as db:
            assert db.scalar(select(func.count()).select_from(m.ConversationMessage)) == 0
    finally:
        app.dependency_overrides.clear()


def test_multirole_customer_support_cannot_resolve_unassigned_ticket(session_factory):
    with session_factory.begin() as db:
        db.add_all([
            m.Ticket(id="own-ticket", customer_id="cust-01", topic="mine", support_actor_id="support-b"),
            m.Ticket(id="assigned-ticket", customer_id="cust-02", topic="assigned", support_actor_id="support-a"),
        ])

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[principal] = lambda: Principal("support-a", frozenset({"customer", "support"}), "cust-01")
    try:
        with TestClient(app) as client:
            listed = client.get("/tickets")
            assert {ticket["id"] for ticket in listed.json()} == {"own-ticket", "assigned-ticket"}
            assert client.get("/tickets/own-ticket").status_code == 200
            assert client.get("/tickets/assigned-ticket").status_code == 200
            assert client.post("/tickets/own-ticket/resolve", json={"body": "improper"}).status_code == 404
            reply = client.post("/tickets/assigned-ticket/messages", json={"body": "Support response"})
            assert reply.status_code == 200 and reply.json()["actor_type"] == "support"
            assert client.post("/tickets/assigned-ticket/resolve", json={"body": "Resolved by assignee"}).status_code == 200
        with session_factory() as db:
            assert db.get(m.Ticket, "own-ticket").status == "open"
            assert db.get(m.Ticket, "assigned-ticket").status == "resolved"
            assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0
    finally:
        app.dependency_overrides.clear()


def test_failed_inspection_creates_one_linked_human_ticket_without_refund(session_factory):
    clock = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

    with session_factory.begin() as db:
        request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "not needed", True, "exception-return", clock)
        d.record_receipt(db, "warehouse-a", request.id, 1, clock)
        return_id = request.id

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            path = f"/warehouse/returns/{return_id}/inspection"
            first = client.post(path, json={"passed": False, "note": "damaged carton"}, headers=WAREHOUSE)
            assert first.status_code == 200
            assert client.post(path, json={"passed": False, "note": "damaged carton"}, headers=WAREHOUSE).status_code == 200
            listed = client.get("/tickets", headers=CUSTOMER).json()
            assert len(listed) == 1 and listed[0]["return_id"] == return_id
            ticket_id = listed[0]["id"]
            detail = client.get(f"/tickets/{ticket_id}", headers=CUSTOMER).json()
            assert detail["return_id"] == return_id
            assert detail["messages"][0]["actor_type"] == "warehouse"
            assert "damaged carton" in detail["messages"][0]["body"]
            assert client.get(f"/tickets/{ticket_id}", headers=FOREIGN_CUSTOMER).status_code == 404
            assert client.get(f"/tickets/{ticket_id}", headers=SUPPORT).status_code == 404
            assert client.post(f"/returns/{return_id}/proposal", headers=WAREHOUSE).status_code == 409
            assert client.post(f"/supervisor/tickets/{ticket_id}/assign", json={"support_actor_id": "support-a"}, headers=SUPERVISOR).status_code == 200
            assert client.post(f"/tickets/{ticket_id}/resolve", json={"body": "We reviewed the inspection. A refund cannot be issued from this ticket."}, headers=SUPPORT).status_code == 200
            assert client.get(f"/returns/{return_id}", headers=CUSTOMER).json()["status"] == "exception"
        with session_factory() as db:
            assert db.scalar(select(func.count()).select_from(m.Ticket).where(m.Ticket.return_id == return_id)) == 1
            assert db.scalar(select(func.count()).select_from(m.RefundProposal)) == 0
            assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0
            assert {event.action for event in db.scalars(select(m.AuditEvent).where(m.AuditEvent.entity_type == "ticket", m.AuditEvent.entity_id == ticket_id))} >= {"create_ticket", "assign_ticket", "resolve_ticket"}
    finally:
        app.dependency_overrides.clear()


def test_ticket_queue_pages_newest_first_without_losing_overflow(session_factory):
    start = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    with session_factory.begin() as db:
        for index in range(105):
            db.add(m.Ticket(id=f"ticket-{index:03d}", customer_id="cust-01", topic="synthetic review",
                            created_at=start + timedelta(seconds=index)))

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            first = client.get("/tickets", headers=CUSTOMER).json()
            second = client.get("/tickets?offset=100", headers=CUSTOMER).json()
            assert len(first) == 100 and len(second) == 5
            assert first[0]["id"] == "ticket-104"
            assert {row["id"] for row in first + second} == {f"ticket-{index:03d}" for index in range(105)}
    finally:
        app.dependency_overrides.clear()


def test_receipt_shortage_is_reported_for_human_review_before_receipt(session_factory):
    clock = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    with session_factory.begin() as db:
        request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "not needed", True, "shortage-return", clock)
        return_id = request.id

    def override_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            path = f"/warehouse/returns/{return_id}/receipt-dispute"
            body = {"observed_quantity": 0, "note": "parcel empty"}
            assert client.post(path, json=body, headers=CUSTOMER).status_code == 403
            assert client.post(path, json={"observed_quantity": -1, "note": "invalid"}, headers=WAREHOUSE).status_code == 422
            assert client.post(path, json={"observed_quantity": 0, "note": "   "}, headers=WAREHOUSE).status_code == 422
            assert client.post("/warehouse/returns/missing/receipt-dispute", json=body, headers=WAREHOUSE).status_code == 404
            assert client.post(path, json={"observed_quantity": 1, "note": "same"}, headers=WAREHOUSE).status_code == 409
            first = client.post(path, json=body, headers=WAREHOUSE)
            assert first.status_code == 200
            ticket_id = first.json()["ticket_id"]
            assert client.get(f"/returns/{return_id}", headers=CUSTOMER).json()["status"] == "exception"
            assert client.post(path, json=body, headers=WAREHOUSE).json()["ticket_id"] == ticket_id
            assert client.post(path, json={"observed_quantity": 2, "note": "different count"}, headers=WAREHOUSE).status_code == 409
            assert client.post(f"/warehouse/returns/{return_id}/receipt", json={"quantity": 1}, headers=WAREHOUSE).status_code == 409
            assert client.post(f"/warehouse/returns/{return_id}/inspection", json={"passed": False, "note": "incomplete"}, headers=WAREHOUSE).status_code == 409
            assert client.post(f"/returns/{return_id}/proposal", headers=WAREHOUSE).status_code == 409
            assert client.get(f"/tickets/{ticket_id}", headers=FOREIGN_CUSTOMER).status_code == 404
            assert "parcel empty" in client.get(f"/tickets/{ticket_id}", headers=CUSTOMER).json()["messages"][0]["body"]
            assert client.post(f"/supervisor/tickets/{ticket_id}/assign", json={"support_actor_id": "support-a"}, headers=SUPERVISOR).status_code == 200
            assert client.post(f"/tickets/{ticket_id}/resolve", json={"body": "Review complete; warehouse must verify actual quantity."}, headers=SUPPORT).status_code == 200
            retry = client.post(path, json=body, headers=WAREHOUSE)
            assert retry.status_code == 200 and retry.json()["ticket_id"] == ticket_id
            assert retry.json()["status"] == "resolved"
            assert client.post(f"/warehouse/returns/{return_id}/receipt", json={"quantity": 1}, headers=WAREHOUSE).status_code == 409
            assert client.post(f"/returns/{return_id}/proposal", headers=WAREHOUSE).status_code == 409
        with session_factory() as db:
            assert db.get(m.ReturnRequest, return_id).status == "exception"
            assert db.scalar(select(func.count()).select_from(m.WarehouseReceipt).where(m.WarehouseReceipt.return_id == return_id)) == 0
            assert db.scalar(select(func.count()).select_from(m.Ticket).where(m.Ticket.return_id == return_id)) == 1
            assert db.scalar(select(func.count()).select_from(m.RefundProposal)) == 0
            assert db.scalar(select(func.count()).select_from(m.RefundLedger)) == 0
            assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action == "create_ticket", m.AuditEvent.entity_id == ticket_id)) == 1
            assert db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action == "report_receipt_dispute", m.AuditEvent.entity_id == return_id)) == 1
    finally:
        app.dependency_overrides.clear()
