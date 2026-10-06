from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import re

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import domain as d, models as m
from .auth import Principal, principal
from .db import get_db, init_db
from .config import settings
from .checkpoint import setup_checkpointer
from .prompts import PromptRegistry
from .models_config import ModelRegistry
from .request_budget import BudgetLimits
from .telemetry import configure_telemetry, tracer, current_trace_id, should_export_request
from .schemas import ChatInput, ConsentInput, DecisionInput, InspectionInput, PolicyDraftInput, PreferenceInput, ReceiptInput, ReturnInput, TicketAssignInput, TicketMessageInput

@asynccontextmanager
async def lifespan(_app: FastAPI):
    PromptRegistry(settings.prompt_release)
    ModelRegistry()
    BudgetLimits.from_environment()
    init_db()
    setup_checkpointer()
    from .memory import setup_long_term_store
    setup_long_term_store()
    configure_telemetry()
    yield


app = FastAPI(title="ResolveAI", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"], allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["authorization", "content-type", "x-mock-actor", "x-mock-role"])


@app.middleware("http")
async def trace_request(request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    attributes = {"http.method": request.method}
    for header, attribute in (("x-eval-run-id", "run_id"), ("x-eval-case-id", "case_id")):
        value = request.headers.get(header, "")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
            attributes[attribute] = value
    if should_export_request(request.url.path, attributes.get("run_id"), attributes.get("case_id")):
        attributes["resolveai.cloud_export"] = True
    with tracer().start_as_current_span("http.request", attributes=attributes):
        response = await call_next(request)
        if current_trace_id():
            response.headers["x-trace-id"] = current_trace_id()
        return response


@app.exception_handler(d.DomainError)
async def domain_error(_request, exc: d.DomainError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status, content={"code": exc.code, "detail": exc.message})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/orders")
def list_orders(actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    orders = db.scalars(select(m.Order).where(m.Order.customer_id == actor.customer_id).order_by(m.Order.placed_at.desc()).limit(20)).all()
    return [{"id": order.id, "status": order.status, "placed_at": order.placed_at, "version": order.version} for order in orders]


@app.get("/orders/{order_id}")
def get_order(order_id: str, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    order = d.owned_order(db, actor.customer_id, order_id)
    return {"id": order.id, "status": order.status, "version": order.version, "currency": order.currency, "items": [{"id": item.id, "product_id": item.product_id, "quantity": item.quantity, "paid_cents": item.paid_cents} for item in order.items]}


@app.get("/orders/{order_id}/shipments")
def shipments(order_id: str, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    d.owned_order(db, actor.customer_id, order_id)
    return [{"id": shipment.id, "status": shipment.status, "delivered_at": shipment.delivered_at, "version": shipment.version} for shipment in db.scalars(select(m.Shipment).where(m.Shipment.order_id == order_id)).all()]


@app.get("/orders/{order_id}/eligibility")
def check_eligibility(order_id: str, item_id: str, quantity: int = 1, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    return d.eligibility(db, actor.customer_id, order_id, item_id, quantity, datetime.now(timezone.utc))


@app.post("/returns")
def create_return(body: ReturnInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    with db.begin():
        request = d.create_return(db, actor.customer_id, body.order_id, body.order_item_id, body.quantity, body.reason, body.confirmed, body.idempotency_key, datetime.now(timezone.utc), body.plan_revision)
    return {"id": request.id, "status": request.status}


@app.get("/returns/{return_id}")
def get_return(return_id: str, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    request = db.get(m.ReturnRequest, return_id)
    if request is None or request.customer_id != actor.customer_id:
        raise HTTPException(404, "Return unavailable")
    return {"id": request.id, "status": request.status, "order_id": request.order_id, "quantity": request.quantity}


@app.post("/warehouse/returns/{return_id}/receipt")
def receipt(return_id: str, body: ReceiptInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("warehouse")
    with db.begin():
        result = d.record_receipt(db, actor.subject, return_id, body.quantity, body.received_at or datetime.now(timezone.utc))
    return {"id": result.id, "return_id": return_id}


@app.post("/warehouse/returns/{return_id}/inspection")
def inspection(return_id: str, body: InspectionInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("warehouse")
    with db.begin():
        result = d.record_inspection(db, actor.subject, return_id, body.passed, body.note, datetime.now(timezone.utc))
    return {"id": result.id, "passed": result.passed}


@app.post("/returns/{return_id}/proposal")
def proposal(return_id: str, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("warehouse")
    with db.begin():
        result = d.create_proposal(db, return_id, datetime.now(timezone.utc))
    from .approval_checkpoint import reconcile_stale_approval_wait, start_approval_wait
    if result.status == "pending":
        start_approval_wait(db, result.id)
        stale_ids = db.scalars(select(m.RefundProposal.id).where(m.RefundProposal.return_id == return_id, m.RefundProposal.status == "stale")).all()
        for stale_id in stale_ids:
            reconcile_stale_approval_wait(db, stale_id)
    return {"id": result.id, "status": result.status, "amount_cents": result.amount_cents}


@app.get("/supervisor/proposals")
def proposals(actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("supervisor")
    return [{"id": p.id, "return_id": p.return_id, "amount_cents": p.amount_cents, "status": p.status} for p in db.scalars(select(m.RefundProposal).where(m.RefundProposal.status == "pending")).all()]


@app.get("/supervisor/refund-deadlines")
def refund_deadlines(actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("supervisor")
    rows = db.execute(select(m.RefundDeadlineAlert).join(m.ReturnRequest, m.RefundDeadlineAlert.return_id == m.ReturnRequest.id).where(m.ReturnRequest.status != "refund_issued").order_by(m.RefundDeadlineAlert.deadline_at, m.RefundDeadlineAlert.return_id)).scalars()
    latest = {}
    for alert in rows:
        if alert.kind == "overdue" or alert.return_id not in latest:
            latest[alert.return_id] = alert
    return [{"return_id": alert.return_id, "kind": alert.kind, "deadline_at": alert.deadline_at, "created_at": alert.created_at} for alert in latest.values()]


@app.post("/supervisor/proposals/{proposal_id}/decision")
def decision(proposal_id: str, body: DecisionInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("supervisor")
    with db.begin():
        result = d.decide_proposal(db, actor.subject, proposal_id, body.approve, datetime.now(timezone.utc))
    from .approval_checkpoint import resume_approval_wait
    resume_approval_wait(db, proposal_id)
    return {"id": result.id, "decision": result.decision}


@app.get("/audit/{entity_type}/{entity_id}")
def audit_events(entity_type: str, entity_id: str, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("supervisor")
    return [{"action": event.action, "actor_id": event.actor_id, "created_at": event.created_at, "details": event.details} for event in db.scalars(select(m.AuditEvent).where(m.AuditEvent.entity_type == entity_type, m.AuditEvent.entity_id == entity_id).order_by(m.AuditEvent.created_at)).all()]


@app.post("/policies/drafts")
def create_policy_draft(body: PolicyDraftInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("support")
    from .policy import create_draft
    with db.begin():
        bundle = create_draft(db, actor.subject, body.id, body.window_days, [clause.model_dump() for clause in body.clauses], body.effective_from)
    return {"id": bundle.id, "status": bundle.status, "content_hash": bundle.content_hash}


@app.post("/policies/{bundle_id}/verify")
def verify_policy(bundle_id: str, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("supervisor")
    from .policy import index_and_verify
    with db.begin():
        bundle = index_and_verify(db, actor.subject, bundle_id)
    return {"id": bundle.id, "status": bundle.status}


@app.post("/policies/{bundle_id}/activate")
def activate_policy(bundle_id: str, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("supervisor")
    from .policy import activate
    with db.begin():
        bundle = activate(db, actor.subject, bundle_id)
    return {"id": bundle.id, "status": bundle.status, "content_hash": bundle.content_hash}


@app.post("/policies/{bundle_id}/rollback")
def rollback_policy(bundle_id: str, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("supervisor")
    from .policy import rollback
    with db.begin():
        bundle = rollback(db, actor.subject, bundle_id)
    return {"id": bundle.id, "status": bundle.status, "content_hash": bundle.content_hash}


@app.get("/profile/preferences")
def preferences(actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    from .memory import list_preferences
    profile = db.get(m.CustomerProfile, actor.customer_id)
    return {"consent": profile.memory_consent, "preferences": list_preferences(db, actor.customer_id)}


@app.post("/profile/memory-consent")
def memory_consent(body: ConsentInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    from .memory import set_consent
    with db.begin():
        set_consent(db, actor.customer_id, body.consent)
    return {"consent": body.consent}


@app.put("/profile/preferences/{key}")
def put_preference(key: str, body: PreferenceInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    from .memory import upsert_preference
    with db.begin():
        entry = upsert_preference(db, actor.customer_id, key, body.value, body.confirmed)
    return {"key": entry.key, "value": entry.value}


@app.delete("/profile/preferences/{key}")
def remove_preference(key: str, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    from .memory import delete_preference
    with db.begin():
        delete_preference(db, actor.customer_id, key)
    return {"deleted": True}


@app.get("/tickets")
def tickets(offset: int = Query(default=0, ge=0), actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    if "supervisor" in actor.roles:
        query = select(m.Ticket)
    else:
        conditions = []
        if "customer" in actor.roles:
            conditions.append(m.Ticket.customer_id == actor.customer_id)
        if "support" in actor.roles:
            conditions.append(m.Ticket.support_actor_id == actor.subject)
        if not conditions:
            raise HTTPException(403, "Role required")
        query = select(m.Ticket).where(or_(*conditions))
    query = query.order_by(m.Ticket.created_at.desc(), m.Ticket.id.desc()).offset(offset).limit(100)
    return [{"id": ticket.id, "order_id": ticket.order_id, "return_id": ticket.return_id, "status": ticket.status, "topic": ticket.topic} for ticket in db.scalars(query).all()]


def _visible_ticket(db: Session, actor: Principal, ticket_id: str, *, lock: bool = False) -> m.Ticket:
    ticket = db.get(m.Ticket, ticket_id, with_for_update=lock)
    if not actor.roles.intersection({"customer", "support", "supervisor"}):
        raise HTTPException(403, "Role required")
    allowed = ticket is not None and (
        "supervisor" in actor.roles
        or "customer" in actor.roles and ticket.customer_id == actor.customer_id
        or "support" in actor.roles and ticket.support_actor_id == actor.subject
    )
    if not allowed:
        raise HTTPException(404, "Ticket unavailable")
    return ticket


@app.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: str, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    with db.begin():
        ticket = _visible_ticket(db, actor, ticket_id, lock=True)
        messages = db.scalars(select(m.ConversationMessage).where(m.ConversationMessage.ticket_id == ticket.id).order_by(m.ConversationMessage.created_at, m.ConversationMessage.id)).all()
        return {"id": ticket.id, "order_id": ticket.order_id, "return_id": ticket.return_id, "status": ticket.status, "topic": ticket.topic,
                "messages": [{"id": message.id, "actor_type": message.actor_type, "body": message.body, "created_at": message.created_at} for message in messages]}


@app.post("/tickets/{ticket_id}/messages")
def add_ticket_message(ticket_id: str, body: TicketMessageInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    if not ("customer" in actor.roles or "support" in actor.roles):
        raise HTTPException(403, "Role required")
    with db.begin():
        ticket = _visible_ticket(db, actor, ticket_id, lock=True)
        if "support" in actor.roles and ticket.support_actor_id == actor.subject:
            author = "support"
        elif "customer" in actor.roles and ticket.customer_id == actor.customer_id:
            author = "customer"
        else:
            raise HTTPException(404, "Ticket unavailable")
        if ticket.status != "open":
            raise HTTPException(409, "Ticket is closed")
        message = m.ConversationMessage(ticket_id=ticket.id, actor_type=author, body=body.body, created_at=datetime.now(timezone.utc))
        db.add(message)
        db.flush()
        d.audit(db, actor.subject, "ticket_message", "ticket", ticket.id, details={"message_id": message.id, "actor_type": author})
    return {"id": message.id, "ticket_id": ticket.id, "actor_type": author}


@app.post("/tickets/{ticket_id}/resolve")
def resolve_ticket(ticket_id: str, body: TicketMessageInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("support")
    with db.begin():
        ticket = db.get(m.Ticket, ticket_id, with_for_update=True)
        if ticket is None or ticket.support_actor_id != actor.subject:
            raise HTTPException(404, "Ticket unavailable")
        if ticket.status != "open":
            raise HTTPException(409, "Ticket is closed")
        message = m.ConversationMessage(ticket_id=ticket.id, actor_type="support", body=body.body, created_at=datetime.now(timezone.utc))
        db.add(message)
        db.flush()
        ticket.status = "resolved"
        d.audit(db, actor.subject, "resolve_ticket", "ticket", ticket.id, details={"message_id": message.id})
    return {"id": ticket.id, "status": ticket.status}


@app.post("/supervisor/tickets/{ticket_id}/assign")
def assign_ticket(ticket_id: str, body: TicketAssignInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("supervisor")
    with db.begin():
        ticket = db.get(m.Ticket, ticket_id, with_for_update=True)
        if not ticket:
            raise HTTPException(404, "Ticket unavailable")
        if ticket.status != "open":
            raise HTTPException(409, "Ticket is closed")
        ticket.support_actor_id = body.support_actor_id
        d.audit(db, actor.subject, "assign_ticket", "ticket", ticket_id, details={"support_actor_id": body.support_actor_id})
    return {"id": ticket_id, "support_actor_id": body.support_actor_id}


@app.post("/chat")
def chat(body: ChatInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    from .agent import run_chat
    result = run_chat(db, actor.customer_id, body, access_token=actor.access_token)
    db.commit()
    return result


@app.post("/chat/stream")
def chat_stream(body: ChatInput, actor: Principal = Depends(principal), db: Session = Depends(get_db)):
    actor.require("customer")
    from .agent import run_chat
    import json
    result = run_chat(db, actor.customer_id, body, access_token=actor.access_token)
    db.commit()
    return StreamingResponse(iter(["event: result\ndata: " + json.dumps(result, ensure_ascii=False, default=str) + "\n\n"]), media_type="text/event-stream")
