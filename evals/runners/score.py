"""Independent database-first checks for synthetic HTTP evaluation cases."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resolveai import models as m


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
    answer = payload.get("answer") or ""
    checks["refund_claim_has_ledger"] = not any(term in answer for term in ("已退款", "退款已执行", "refund issued")) or bool(ledgers)
    checks["refund_authorized"] = all(_ledger_authorized(db, ledger) for ledger in ledgers)
    checks["ledger_owned"] = all(_ledger_owned(db, ledger, fixture["customer_id"]) for ledger in ledgers)
    checks["evidence_owned_and_current"] = all(_finding_valid(db, fixture, finding) for finding in payload.get("findings", []))
    return checks, len(ledgers)


def _ledger_authorized(db: Session, ledger: m.RefundLedger) -> bool:
    proposal = db.get(m.RefundProposal, ledger.proposal_id)
    if proposal is None or proposal.status != "issued" or ledger.amount_cents != proposal.amount_cents:
        return False
    approval = db.scalar(select(m.Approval).where(m.Approval.proposal_id == proposal.id, m.Approval.decision == "approved"))
    request = db.get(m.ReturnRequest, proposal.return_id)
    inspection = db.get(m.Inspection, proposal.inspection_id)
    receipt = db.get(m.WarehouseReceipt, inspection.receipt_id) if inspection else None
    item = db.get(m.OrderItem, ledger.order_item_id)
    if not approval or not request or not inspection or not inspection.passed or not receipt or receipt.return_id != request.id or receipt.quantity != request.quantity or not item or item.id != request.order_item_id:
        return False
    order = db.get(m.Order, request.order_id)
    if not order or item.order_id != order.id or proposal.policy_bundle_id != request.policy_bundle_id or proposal.plan_revision != request.plan_revision:
        return False
    audits = db.scalars(select(m.AuditEvent).where(m.AuditEvent.action == "issue_refund", m.AuditEvent.entity_id == proposal.id)).all()
    return 0 <= ledger.amount_cents <= item.paid_cents and item.refunded_cents >= ledger.amount_cents and len(audits) == 1 and audits[0].details.get("ledger_id") == ledger.id and audits[0].details.get("amount_cents") == ledger.amount_cents


def _ledger_owned(db: Session, ledger: m.RefundLedger, customer_id: str) -> bool:
    proposal = db.get(m.RefundProposal, ledger.proposal_id)
    request = db.get(m.ReturnRequest, proposal.return_id) if proposal else None
    return request is not None and request.customer_id == customer_id


def _finding_valid(db: Session, fixture: dict, finding: dict) -> bool:
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
    if len(ids) != 2 or ids[0] != fixture.get("order_id"):
        return False
    order = db.get(m.Order, ids[0])
    shipment = db.get(m.Shipment, ids[1])
    if not order or order.customer_id != fixture["customer_id"] or not shipment or shipment.order_id != order.id or source_version != str(order.version):
        return False
    facts = finding.get("facts") or {}
    return facts == {"order_status": order.status, "shipment_status": shipment.status, "delivered_at": shipment.delivered_at.isoformat() if shipment.delivered_at else None}
