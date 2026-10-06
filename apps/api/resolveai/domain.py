from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m

SHANGHAI = ZoneInfo("Asia/Shanghai")


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def audit(db: Session, actor: str, action: str, entity: str, entity_id: str, *, before: int | None = None, after: int | None = None, bundle: str | None = None, key: str | None = None, details: dict | None = None):
    from .telemetry import current_trace_id
    db.add(m.AuditEvent(actor_id=actor, action=action, entity_type=entity, entity_id=entity_id, before_version=before, after_version=after, policy_bundle_id=bundle, idempotency_key=key, trace_id=current_trace_id(), details=details or {}))


def owned_order(db: Session, customer_id: str, order_id: str) -> m.Order:
    order = db.get(m.Order, order_id)
    if order is None or order.customer_id != customer_id:
        raise DomainError("order_not_found", "Order unavailable", 404)
    return order


def active_policy(db: Session) -> m.PolicyBundle:
    bundle = db.scalar(select(m.PolicyBundle).where(m.PolicyBundle.active.is_(True)))
    if not bundle:
        raise DomainError("policy_unavailable", "No active policy", 503)
    return bundle


def delivery_for_order(db: Session, order_id: str) -> datetime | None:
    shipments = db.scalars(select(m.Shipment).where(m.Shipment.order_id == order_id)).all()
    if len(shipments) != 1:
        return None
    shipment = shipments[0]
    return aware(shipment.delivered_at) if shipment.status == "delivered" and shipment.delivered_at else None


def eligibility(db: Session, customer_id: str, order_id: str, item_id: str, quantity: int, at: datetime) -> dict:
    order = owned_order(db, customer_id, order_id)
    item = db.get(m.OrderItem, item_id)
    if item is None or item.order_id != order_id:
        raise DomainError("item_not_found", "Item unavailable", 404)
    product = db.get(m.Product, item.product_id)
    bundle = db.get(m.PolicyBundle, order.policy_bundle_id)
    if not bundle or not product:
        raise DomainError("facts_missing", "Order facts incomplete")
    if order.currency != "CNY" or order.status != "paid" or product.seller_id != order.seller_id:
        return {"eligible": False, "reason": "unsupported_order", "policy_bundle_id": bundle.id}
    if quantity < 1 or quantity > item.quantity:
        return {"eligible": False, "reason": "invalid_quantity", "policy_bundle_id": bundle.id}
    committed = sum(r.quantity for r in db.scalars(select(m.ReturnRequest).where(m.ReturnRequest.order_item_id == item_id, m.ReturnRequest.status != "rejected")).all())
    if committed + quantity > item.quantity:
        return {"eligible": False, "reason": "quantity_already_requested", "policy_bundle_id": bundle.id}
    if not product.physical or not product.returnable or product.special_notice_accepted:
        return {"eligible": False, "reason": "product_exception", "policy_bundle_id": bundle.id}
    delivered = delivery_for_order(db, order_id)
    if delivered is None:
        return {"eligible": False, "reason": "delivery_unverified", "policy_bundle_id": bundle.id}
    delivery_day = delivered.astimezone(SHANGHAI).date()
    today = aware(at).astimezone(SHANGHAI).date()
    days = max(bundle.window_days, order.favorable_window_days or 0)
    if today <= delivery_day or today > delivery_day + timedelta(days=days):
        return {"eligible": False, "reason": "outside_window", "policy_bundle_id": bundle.id, "last_day": str(delivery_day + timedelta(days=days))}
    return {"eligible": True, "reason": "within_window", "policy_bundle_id": bundle.id, "last_day": str(delivery_day + timedelta(days=days))}


def create_return(db: Session, customer_id: str, order_id: str, item_id: str, quantity: int, reason: str, confirmed: bool, key: str, at: datetime, plan_revision: int = 1) -> m.ReturnRequest:
    if not confirmed or not reason.strip() or not key.strip():
        raise DomainError("confirmation_required", "Order, item, quantity, reason and explicit confirmation required", 422)
    previous = db.scalar(select(m.ReturnRequest).where(m.ReturnRequest.idempotency_key == key))
    if previous:
        if previous.customer_id != customer_id:
            raise DomainError("return_not_found", "Return unavailable", 404)
        if (previous.customer_id, previous.order_id, previous.order_item_id, previous.quantity, previous.reason) != (customer_id, order_id, item_id, quantity, reason):
            raise DomainError("idempotency_conflict", "Key reused with different request")
        return previous
    # Serialize the quantity check and insert for this order on PostgreSQL.
    # The unique idempotency key remains the final guard for duplicate retries.
    order = db.scalar(select(m.Order).where(m.Order.id == order_id, m.Order.customer_id == customer_id).with_for_update())
    if order is None:
        raise DomainError("order_not_found", "Order unavailable", 404)
    # A concurrent request with this key may have committed while we waited.
    previous = db.scalar(select(m.ReturnRequest).where(m.ReturnRequest.idempotency_key == key))
    if previous:
        if (previous.customer_id, previous.order_id, previous.order_item_id, previous.quantity, previous.reason) != (customer_id, order_id, item_id, quantity, reason):
            raise DomainError("idempotency_conflict", "Key reused with different request")
        return previous
    result = eligibility(db, customer_id, order_id, item_id, quantity, at)
    if not result["eligible"]:
        raise DomainError(result["reason"], "Return requires human review or is ineligible")
    request = m.ReturnRequest(order_id=order_id, customer_id=customer_id, order_item_id=item_id, quantity=quantity, reason=reason.strip(), created_at=aware(at), idempotency_key=key, plan_revision=plan_revision, policy_bundle_id=result["policy_bundle_id"])
    db.add(request)
    db.flush()
    old = order.version
    order.version += 1
    audit(db, customer_id, "create_return", "return", request.id, before=old, after=order.version, bundle=request.policy_bundle_id, key=key)
    return request


def record_receipt(db: Session, actor: str, return_id: str, quantity: int, at: datetime) -> m.WarehouseReceipt:
    request = db.get(m.ReturnRequest, return_id, with_for_update=True)
    if not request:
        raise DomainError("return_not_found", "Return unavailable", 404)
    existing = db.scalar(select(m.WarehouseReceipt).where(m.WarehouseReceipt.return_id == return_id))
    if existing:
        if existing.quantity != quantity:
            raise DomainError("receipt_conflict", "Receipt already recorded with different quantity")
        return existing
    if request.status != "return_requested" or quantity != request.quantity or quantity < 1:
        raise DomainError("receipt_exception", "Wrong state or quantity; human review required")
    receipt = m.WarehouseReceipt(return_id=return_id, quantity=quantity, received_at=aware(at), actor_id=actor)
    db.add(receipt)
    db.flush()
    request.status = "received"
    audit(db, actor, "record_receipt", "return", return_id, bundle=request.policy_bundle_id, details={"quantity": quantity})
    return receipt


def report_receipt_dispute(db: Session, actor: str, return_id: str, observed_quantity: int, note: str, at: datetime) -> m.Ticket:
    request = db.get(m.ReturnRequest, return_id, with_for_update=True)
    if not request:
        raise DomainError("return_not_found", "Return unavailable", 404)
    if observed_quantity < 0 or observed_quantity == request.quantity:
        raise DomainError("no_receipt_discrepancy", "Observed quantity must differ from the requested return quantity")
    summary = f"Warehouse observed {observed_quantity} of {request.quantity} expected return items. Note: {note.strip()[:200]}"
    existing = db.scalar(select(m.Ticket).where(m.Ticket.return_id == return_id))
    if existing:
        original = db.scalar(select(m.ConversationMessage).where(m.ConversationMessage.ticket_id == existing.id, m.ConversationMessage.actor_type == "warehouse").order_by(m.ConversationMessage.created_at, m.ConversationMessage.id))
        if existing.topic != "warehouse receipt discrepancy" or original is None or original.body != summary:
            raise DomainError("receipt_dispute_conflict", "A different return exception is already recorded")
        return existing
    if request.status != "return_requested" or db.scalar(select(m.WarehouseReceipt).where(m.WarehouseReceipt.return_id == return_id)):
        raise DomainError("invalid_state", "Return is no longer awaiting warehouse receipt")
    ticket = m.Ticket(customer_id=request.customer_id, order_id=request.order_id, return_id=return_id, topic="warehouse receipt discrepancy")
    db.add(ticket)
    db.flush()
    db.add(m.ConversationMessage(ticket_id=ticket.id, actor_type="warehouse", body=summary, created_at=aware(at)))
    request.status = "exception"
    audit(db, actor, "report_receipt_dispute", "return", return_id, bundle=request.policy_bundle_id,
          details={"ticket_id": ticket.id, "observed_quantity": observed_quantity, "expected_quantity": request.quantity})
    audit(db, actor, "create_ticket", "ticket", ticket.id, bundle=request.policy_bundle_id,
          details={"return_id": return_id, "observed_quantity": observed_quantity, "expected_quantity": request.quantity})
    return ticket


def record_inspection(db: Session, actor: str, return_id: str, passed: bool, note: str, at: datetime) -> m.Inspection:
    request = db.get(m.ReturnRequest, return_id)
    receipt = db.scalar(select(m.WarehouseReceipt).where(m.WarehouseReceipt.return_id == return_id))
    if not request or not receipt:
        raise DomainError("receipt_required", "Warehouse receipt required")
    existing = db.scalar(select(m.Inspection).where(m.Inspection.receipt_id == receipt.id))
    if existing:
        if existing.passed != passed:
            raise DomainError("inspection_conflict", "Inspection already recorded")
        return existing
    if request.status != "received":
        raise DomainError("invalid_state", "Return is not ready for inspection")
    inspection = m.Inspection(receipt_id=receipt.id, passed=passed, note=note[:200], inspected_at=aware(at))
    db.add(inspection)
    db.flush()
    request.status = "inspected_passed" if passed else "exception"
    audit(db, actor, "record_inspection", "return", return_id, bundle=request.policy_bundle_id, details={"passed": passed})
    if not passed:
        ticket = m.Ticket(customer_id=request.customer_id, order_id=request.order_id, return_id=return_id, topic="return inspection exception")
        db.add(ticket)
        db.flush()
        summary = "Warehouse inspection did not pass; manual review required."
        if note.strip():
            summary += " Note: " + note.strip()[:200]
        db.add(m.ConversationMessage(ticket_id=ticket.id, actor_type="warehouse", body=summary, created_at=aware(at)))
        audit(db, actor, "create_ticket", "ticket", ticket.id, bundle=request.policy_bundle_id, details={"return_id": return_id, "inspection_id": inspection.id})
    return inspection


def expected_refund(item: m.OrderItem, quantity: int) -> int:
    cumulative = item.refunded_quantity + quantity
    if quantity < 1 or cumulative > item.quantity:
        raise DomainError("quantity_exceeded", "Refund quantity exceeds purchase")
    target = item.paid_cents * cumulative // item.quantity
    amount = target - item.refunded_cents
    if amount < 0 or amount > item.paid_cents - item.refunded_cents:
        raise DomainError("amount_invalid", "Refund amount invalid")
    return amount


def create_proposal(db: Session, return_id: str, at: datetime) -> m.RefundProposal:
    request = db.get(m.ReturnRequest, return_id)
    if not request:
        raise DomainError("inspection_required", "Passing inspection required")
    order = db.get(m.Order, request.order_id)
    existing = db.scalar(select(m.RefundProposal).where(m.RefundProposal.return_id == return_id).order_by(m.RefundProposal.created_at.desc(), m.RefundProposal.id.desc()))
    if request.status == "refund_issued" and existing and existing.status == "issued":
        return existing
    if request.status not in ("inspected_passed", "proposal_pending", "approved"):
        raise DomainError("inspection_required", "Passing inspection required")
    if existing and existing.status in ("pending", "approved"):
        if existing.order_version == order.version and existing.plan_revision == request.plan_revision and existing.policy_bundle_id == request.policy_bundle_id:
            return existing
        existing.status = "stale"
    receipt = db.scalar(select(m.WarehouseReceipt).where(m.WarehouseReceipt.return_id == return_id))
    inspection = db.scalar(select(m.Inspection).where(m.Inspection.receipt_id == receipt.id))
    if not inspection.passed or receipt.quantity != request.quantity:
        raise DomainError("inspection_required", "Passing inspection required")
    item = db.get(m.OrderItem, request.order_item_id)
    amount = expected_refund(item, request.quantity)
    proposal = m.RefundProposal(return_id=return_id, inspection_id=inspection.id, order_version=order.version, plan_revision=request.plan_revision, policy_bundle_id=request.policy_bundle_id, amount_cents=amount, status="pending", created_at=aware(at))
    db.add(proposal)
    db.flush()
    request.status = "proposal_pending"
    audit(db, "rule_service", "create_proposal", "proposal", proposal.id, before=order.version, after=order.version, bundle=proposal.policy_bundle_id, details={"amount_cents": amount})
    return proposal


def decide_proposal(db: Session, actor: str, proposal_id: str, approve: bool, at: datetime) -> m.Approval:
    proposal = db.get(m.RefundProposal, proposal_id)
    if not proposal:
        raise DomainError("proposal_not_found", "Proposal unavailable", 404)
    existing = db.scalar(select(m.Approval).where(m.Approval.proposal_id == proposal_id))
    if existing:
        if existing.decision != ("approved" if approve else "rejected"):
            raise DomainError("decision_conflict", "Decision already made")
        return existing
    if proposal.status != "pending":
        raise DomainError("invalid_state", "Proposal not pending")
    request = db.get(m.ReturnRequest, proposal.return_id)
    order = db.get(m.Order, request.order_id)
    if order.version != proposal.order_version or request.plan_revision != proposal.plan_revision or request.policy_bundle_id != proposal.policy_bundle_id:
        raise DomainError("stale_proposal", "Proposal facts changed; a new proposal is required")
    decision = "approved" if approve else "rejected"
    approval = m.Approval(proposal_id=proposal_id, actor_id=actor, decision=decision, decided_at=aware(at))
    db.add(approval)
    proposal.status = decision
    request.status = decision
    audit(db, actor, decision, "proposal", proposal_id, before=order.version, after=order.version, bundle=proposal.policy_bundle_id)
    return approval


def issue_refund(db: Session, proposal_id: str, key: str, at: datetime) -> m.RefundLedger:
    if not key.strip():
        raise DomainError("idempotency_required", "Idempotency key required", 422)
    # Serialize all refunds for this order before checking for a prior ledger.
    # A pre-lock read can miss another worker's uncommitted issuance and leave
    # stale ORM objects in this session after the lock is released.
    order = db.scalar(
        select(m.Order)
        .join(m.ReturnRequest, m.ReturnRequest.order_id == m.Order.id)
        .join(m.RefundProposal, m.RefundProposal.return_id == m.ReturnRequest.id)
        .where(m.RefundProposal.id == proposal_id)
        .with_for_update(of=m.Order)
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise DomainError("approval_required", "Valid supervisor approval required")
    existing = db.scalar(select(m.RefundLedger).where(m.RefundLedger.proposal_id == proposal_id))
    if existing:
        if existing.idempotency_key != key:
            raise DomainError("idempotency_conflict", "Proposal already issued using another key")
        return existing
    proposal = db.get(m.RefundProposal, proposal_id, populate_existing=True)
    if not proposal or proposal.status != "approved":
        raise DomainError("approval_required", "Valid supervisor approval required")
    approval = db.scalar(select(m.Approval).where(m.Approval.proposal_id == proposal_id, m.Approval.decision == "approved"))
    if not approval:
        raise DomainError("approval_required", "Valid supervisor approval required")
    request = db.get(m.ReturnRequest, proposal.return_id, populate_existing=True)
    item = db.scalar(select(m.OrderItem).where(m.OrderItem.id == request.order_item_id).with_for_update().execution_options(populate_existing=True))
    receipt = db.scalar(select(m.WarehouseReceipt).where(m.WarehouseReceipt.return_id == request.id))
    inspection = db.get(m.Inspection, proposal.inspection_id)
    if not receipt or not inspection or not inspection.passed or inspection.receipt_id != receipt.id:
        raise DomainError("inspection_required", "Inspection evidence invalid")
    if order.version != proposal.order_version or request.plan_revision != proposal.plan_revision or request.policy_bundle_id != proposal.policy_bundle_id:
        raise DomainError("stale_proposal", "Proposal facts changed")
    amount = expected_refund(item, request.quantity)
    if amount != proposal.amount_cents:
        raise DomainError("stale_amount", "Refund amount changed")
    payment = db.scalar(select(m.Payment).where(m.Payment.order_id == order.id))
    if not payment or payment.currency != "CNY" or payment.method != "synthetic_original":
        raise DomainError("payment_exception", "Payment requires human review")
    ledger_total = sum(x.amount_cents for x in db.scalars(select(m.RefundLedger).join(m.RefundProposal, m.RefundLedger.proposal_id == m.RefundProposal.id).join(m.ReturnRequest, m.RefundProposal.return_id == m.ReturnRequest.id).where(m.ReturnRequest.order_id == order.id)).all())
    if amount > payment.paid_cents - ledger_total:
        raise DomainError("balance_exceeded", "Refund exceeds order paid balance")
    ledger = m.RefundLedger(proposal_id=proposal_id, order_item_id=item.id, amount_cents=amount, idempotency_key=key, issued_at=aware(at))
    db.add(ledger)
    item.refunded_cents += amount
    item.refunded_quantity += request.quantity
    old = order.version
    order.version += 1
    proposal.status = "issued"
    request.status = "refund_issued"
    db.flush()
    audit(db, "refund_worker", "issue_refund", "proposal", proposal_id, before=old, after=order.version, bundle=proposal.policy_bundle_id, key=key, details={"amount_cents": amount, "ledger_id": ledger.id})
    return ledger
