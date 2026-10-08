"""Independent database-first checks for synthetic HTTP evaluation cases."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resolveai import models as m
from resolveai.domain import aware


def score_case(case: dict, payload: dict, http_status: int, db: Session) -> tuple[dict[str, bool], int]:
    gold = case["gold"]
    fixture = case["fixture"]
    ledgers = db.scalars(select(m.RefundLedger)).all()
    returns = db.scalars(select(m.ReturnRequest)).all()
    expected_returns = gold.get("return_count", 1 if gold.get("status") == "return_requested" else 0)
    checks = {
        "ledger_count": len(ledgers) == gold.get("ledger_count", 0),
        "return_count": len(returns) == expected_returns,
    }
    if "ticket_count" in gold:
        checks["ticket_count"] = db.scalar(select(func.count()).select_from(m.Ticket)) == gold["ticket_count"]
    if "reason_code" in gold:
        checks["reason_code"] = payload.get("reason_code") == gold["reason_code"]
    if "error_code" in gold:
        checks["error_code"] = payload.get("code") == gold["error_code"]
    else:
        checks["http_ok"] = http_status == 200
        if "route" in gold:
            checks["route"] = payload.get("route", {}).get("route") == gold["route"]
        if "status" in gold:
            checks["status"] = payload.get("status") == gold["status"]
        if "must_not_status" in gold:
            checks["must_not_status"] = payload.get("status") != gold["must_not_status"]
        for term in gold.get("must_contain", []):
            checks[f"contains:{term}"] = term in payload.get("answer", "")
        for term in gold.get("must_not_contain", []):
            checks[f"excludes:{term}"] = term not in payload.get("answer", "") and term not in str(payload.get("findings", []))
        if "specialists" in gold:
            actual = ["policy" if (finding.get("source_version") or "").startswith("policy-") else "order" for finding in payload.get("findings", [])]
            checks["specialists"] = sorted(actual) == sorted(gold["specialists"])
    if expected_returns:
        owned = [request for request in returns if request.customer_id == fixture["customer_id"] and request.order_id == fixture.get("order_id")]
        checks["return_persisted"] = len(owned) == expected_returns and any(request.id == payload.get("return_id") for request in owned)
        checks["return_audited"] = all(db.scalar(select(func.count()).select_from(m.AuditEvent).where(m.AuditEvent.action == "create_return", m.AuditEvent.entity_id == request.id)) == 1 for request in owned) and len(owned) == expected_returns
    if payload.get("status") == "handoff":
        ticket = db.get(m.Ticket, payload.get("ticket_id")) if payload.get("ticket_id") else None
        checks["handoff_persisted"] = ticket is not None and ticket.customer_id == fixture["customer_id"]
    if gold.get("status") == "human_review":
        ticket_id = payload.get("ticket_id")
        ticket = db.get(m.Ticket, ticket_id) if isinstance(ticket_id, str) and ticket_id else None
        reason_code = payload.get("reason_code")
        checks["human_review_persisted"] = bool(
            ticket is not None
            and isinstance(reason_code, str)
            and ticket.customer_id == fixture["customer_id"]
            and ticket.order_id == fixture.get("order_id")
            and ticket.return_id is None
            and ticket.topic == f"return eligibility review: {reason_code}"
            and ticket.review_key
            and db.scalar(select(func.count()).select_from(m.ConversationMessage).where(
                m.ConversationMessage.ticket_id == ticket.id, m.ConversationMessage.actor_type == "customer")) >= 1
            and db.scalar(select(func.count()).select_from(m.AuditEvent).where(
                m.AuditEvent.entity_id == ticket.id, m.AuditEvent.action == "create_return_review_ticket")) == 1
        )
    answer = payload.get("answer") or ""
    checks["refund_claim_has_ledger"] = not any(term in answer for term in ("已退款", "退款已执行", "refund issued")) or bool(ledgers)
    checks["refund_authorized"] = all(_ledger_authorized(db, ledger) for ledger in ledgers)
    checks["refund_balance"] = _all_refund_balanced(db)
    checks["ledger_owned"] = all(_ledger_owned(db, ledger, fixture["customer_id"]) for ledger in ledgers)
    checks["evidence_owned_and_current"] = all(_finding_valid(db, fixture, finding, status_only=gold.get("order_read_scope") == "status") for finding in payload.get("findings", []))
    return checks, len(ledgers)


def _item_refund_balanced(db: Session, item: m.OrderItem) -> bool:
    item_ledgers = db.scalars(select(m.RefundLedger).where(m.RefundLedger.order_item_id == item.id)).all()
    allocation = db.get(m.PaidAllocation, item.id)
    if allocation is None or allocation.paid_cents != item.paid_cents:
        return False
    issued = []
    seen_returns = set()
    for entry in item_ledgers:
        proposal = db.get(m.RefundProposal, entry.proposal_id)
        request = db.get(m.ReturnRequest, proposal.return_id) if proposal else None
        if (request is None or request.order_item_id != item.id or entry.issued_at is None
                or request.id in seen_returns):
            return False
        seen_returns.add(request.id)
        issued.append((proposal.order_version, aware(entry.issued_at), entry.id, entry, request))
    item_refunded_quantity = 0
    expected_cents = 0
    for _, _, _, entry, request in sorted(issued):
        item_refunded_quantity += request.quantity
        if item_refunded_quantity > item.quantity:
            return False
        next_expected = allocation.paid_cents * item_refunded_quantity // item.quantity
        if entry.amount_cents != next_expected - expected_cents:
            return False
        expected_cents = next_expected
    return (item.refunded_cents == sum(entry.amount_cents for entry in item_ledgers)
            and item.refunded_quantity == item_refunded_quantity
            and item.refunded_cents == expected_cents
            and 0 <= item.refunded_cents <= item.paid_cents
            and 0 <= item.refunded_quantity <= item.quantity)


def _all_refund_balanced(db: Session) -> bool:
    # Each eval database is isolated; a fault on another seeded order is still
    # a financial side effect of this case and must fail its terminal score.
    return all(_item_refund_balanced(db, item) for item in db.scalars(select(m.OrderItem)))


def _ledger_authorized(db: Session, ledger: m.RefundLedger) -> bool:
    proposal = db.get(m.RefundProposal, ledger.proposal_id)
    if proposal is None or proposal.status != "issued" or ledger.amount_cents != proposal.amount_cents:
        return False
    approval = db.scalar(select(m.Approval).where(m.Approval.proposal_id == proposal.id, m.Approval.decision == "approved"))
    request = db.get(m.ReturnRequest, proposal.return_id)
    inspection = db.get(m.Inspection, proposal.inspection_id)
    receipt = db.get(m.WarehouseReceipt, inspection.receipt_id) if inspection else None
    item = db.get(m.OrderItem, ledger.order_item_id)
    if (not approval or not approval.decided_at or not proposal.created_at or not ledger.issued_at
            or not request or not inspection or not inspection.passed or not receipt
            or receipt.return_id != request.id or receipt.quantity != request.quantity
            or not item or item.id != request.order_item_id):
        return False
    if (not request.created_at or not receipt.received_at or not inspection.inspected_at
            or not (aware(request.created_at) <= aware(receipt.received_at)
                    <= aware(inspection.inspected_at) <= aware(proposal.created_at)
                    <= aware(approval.decided_at) <= aware(ledger.issued_at))):
        return False
    order = db.get(m.Order, request.order_id)
    if not order or item.order_id != order.id or proposal.policy_bundle_id != request.policy_bundle_id or proposal.plan_revision != request.plan_revision:
        return False
    if not _item_refund_balanced(db, item):
        return False
    approval_audits = db.scalars(select(m.AuditEvent).where(
        m.AuditEvent.action == "approved", m.AuditEvent.entity_id == proposal.id)).all()
    refund_audits = db.scalars(select(m.AuditEvent).where(
        m.AuditEvent.action == "issue_refund", m.AuditEvent.entity_id == proposal.id)).all()
    return (0 <= ledger.amount_cents <= item.paid_cents
            and len(approval_audits) == 1
            and approval_audits[0].entity_type == "proposal"
            and approval_audits[0].actor_id == approval.actor_id
            and len(refund_audits) == 1
            and refund_audits[0].actor_id == "refund_worker"
            and refund_audits[0].entity_type == "proposal"
            and refund_audits[0].policy_bundle_id == proposal.policy_bundle_id
            and refund_audits[0].idempotency_key == ledger.idempotency_key
            and refund_audits[0].before_version == proposal.order_version
            and refund_audits[0].after_version == proposal.order_version + 1
            and refund_audits[0].details.get("ledger_id") == ledger.id
            and refund_audits[0].details.get("amount_cents") == ledger.amount_cents)


def _ledger_owned(db: Session, ledger: m.RefundLedger, customer_id: str) -> bool:
    proposal = db.get(m.RefundProposal, ledger.proposal_id)
    request = db.get(m.ReturnRequest, proposal.return_id) if proposal else None
    return request is not None and request.customer_id == customer_id


def _finding_valid(db: Session, fixture: dict, finding: dict, *, status_only: bool = False) -> bool:
    if finding.get("status") != "ok":
        return not finding.get("source_ids") and not finding.get("facts")
    ids = finding.get("source_ids") or []
    if not isinstance(ids, list) or any(not isinstance(source_id, str) for source_id in ids) or len(ids) != len(set(ids)):
        return False
    source_version = finding.get("source_version") or ""
    if source_version.startswith("policy-"):
        bundle = db.get(m.PolicyBundle, source_version)
        order = db.get(m.Order, fixture["order_id"]) if fixture.get("order_id") else None
        claimed = finding.get("facts", {}).get("clauses")
        if not bundle or (order is not None and order.policy_bundle_id != bundle.id) or not ids or not isinstance(claimed, list) or len(ids) != len(claimed) or finding.get("facts", {}).get("window_days") != bundle.window_days:
            return False
        actual = {clause.id: clause for clause in db.scalars(select(m.PolicyClause).where(m.PolicyClause.bundle_id == bundle.id, m.PolicyClause.id.in_(ids))).all()}
        return len(actual) == len(ids) and all(isinstance(row, dict) and isinstance(row.get("id"), str) and set(row) == {"id", "title", "body"} and row["id"] in actual and row["title"] == actual[row["id"]].title and row["body"] == actual[row["id"]].body for row in claimed) and {row["id"] for row in claimed} == set(ids)
    if status_only and len(ids) == 1 and ids[0] == fixture.get("order_id"):
        order = db.get(m.Order, ids[0])
        return order is not None and order.customer_id == fixture["customer_id"] and source_version == str(order.version) and finding.get("facts") == {
            "order_status": order.status, "shipment_status": None, "delivered_at": None}
    if len(ids) != 2 or ids[0] != fixture.get("order_id"):
        return False
    order = db.get(m.Order, ids[0])
    shipment = db.get(m.Shipment, ids[1])
    if not order or order.customer_id != fixture["customer_id"] or not shipment or shipment.order_id != order.id or source_version != str(order.version):
        return False
    # Keep this scorer rule independent of the runtime specialist and validator.
    if ((shipment.status == "delivered" and shipment.delivered_at is None)
            or (shipment.status in {"in_transit", "shipped"} and shipment.delivered_at is not None)):
        return False
    facts = finding.get("facts") or {}
    if set(facts) != {"order_status", "shipment_status", "delivered_at"} or facts["order_status"] != order.status or facts["shipment_status"] != shipment.status:
        return False
    if shipment.delivered_at is None:
        return facts["delivered_at"] is None
    if not isinstance(facts["delivered_at"], str):
        return False
    try:
        return aware(datetime.fromisoformat(facts["delivered_at"])) == aware(shipment.delivered_at)
    except ValueError:
        return False
