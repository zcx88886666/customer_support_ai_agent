"""Controlled, retry-safe simulated refund processor.

Only an operator running this process can trigger issuance. Customer and agent APIs
do not expose a refund tool or endpoint.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import domain as d, models as m
from .db import SessionLocal
from .telemetry import configure_telemetry, tracer


def issue_approved_once() -> list[str]:
    configure_telemetry()
    issued = []
    with SessionLocal() as db:
        ids = db.scalars(select(m.RefundProposal.id).where(m.RefundProposal.status == "approved")).all()
    for proposal_id in ids:
        try:
            with tracer().start_as_current_span("refund.worker"):
                with SessionLocal.begin() as db:
                    ledger = d.issue_refund(db, proposal_id, f"refund:{proposal_id}", datetime.now(timezone.utc))
                    issued.append(ledger.id)
        except d.DomainError:
            # Stale/invalid proposals remain visible for human resolution.
            continue
    return issued


def alert_refund_deadlines_once(at: datetime | None = None) -> list[tuple[str, str]]:
    """Record one 24-hour warning and one overdue alert per received return."""
    configure_telemetry()
    at = d.aware(at or datetime.now(timezone.utc))
    alerted = []
    with SessionLocal() as db:
        return_ids = db.scalars(select(m.WarehouseReceipt.return_id)).all()
    for return_id in return_ids:
        try:
            with tracer().start_as_current_span("refund.deadline_alert"):
                with SessionLocal.begin() as db:
                    request = db.scalar(select(m.ReturnRequest).where(m.ReturnRequest.id == return_id).with_for_update())
                    if request is None or request.status == "refund_issued":
                        continue
                    receipt = db.scalar(select(m.WarehouseReceipt).where(m.WarehouseReceipt.return_id == return_id))
                    deadline = d.aware(receipt.received_at) + timedelta(days=7)
                    if at < deadline - timedelta(days=1):
                        continue
                    kind = "overdue" if at >= deadline else "due_soon"
                    if db.get(m.RefundDeadlineAlert, (return_id, kind)):
                        continue
                    db.add(m.RefundDeadlineAlert(return_id=return_id, kind=kind, deadline_at=deadline, created_at=at))
                    d.audit(db, "refund_worker", "refund_deadline_" + kind, "return", return_id, bundle=request.policy_bundle_id, details={"deadline_at": deadline.isoformat()})
                    alerted.append((return_id, kind))
        except IntegrityError:
            # The composite primary key handles a concurrent retry.
            continue
    return alerted


if __name__ == "__main__":
    print({"issued_ledger_ids": issue_approved_once(), "deadline_alerts": alert_refund_deadlines_once()})
