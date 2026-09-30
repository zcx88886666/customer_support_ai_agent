"""Controlled, retry-safe simulated refund processor.

Only an operator running this process can trigger issuance. Customer and agent APIs
do not expose a refund tool or endpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

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


if __name__ == "__main__":
    print({"issued_ledger_ids": issue_approved_once()})
