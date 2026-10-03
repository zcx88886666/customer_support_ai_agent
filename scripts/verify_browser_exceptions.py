"""Check isolated OIDC browser exception journeys against database facts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import func, select

from resolveai import models as m
from resolveai.db import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--after-worker", action="store_true")
    args = parser.parse_args()
    root = Path(".local")
    exception = json.loads((root / "browser-exception-result.json").read_text())
    stale = json.loads((root / "browser-stale-result.json").read_text())
    with SessionLocal() as db:
        failed_return = db.get(m.ReturnRequest, exception["exception_return_id"])
        assert failed_return.status == "exception"
        assert db.scalar(select(func.count()).select_from(m.RefundProposal).where(m.RefundProposal.return_id == failed_return.id)) == 0
        old = db.get(m.RefundProposal, stale["old_proposal_id"])
        new = db.get(m.RefundProposal, stale["new_proposal_id"])
        assert old.return_id == new.return_id == stale["first_return_id"]
        assert old.status == "stale"
        assert db.scalar(select(func.count()).select_from(m.Approval).where(m.Approval.proposal_id == old.id)) == 0
        assert new.status == ("issued" if args.after_worker else "approved")
        assert db.scalar(select(func.count()).select_from(m.Approval).where(m.Approval.proposal_id == new.id, m.Approval.decision == "approved")) == 1
        second_return = db.get(m.ReturnRequest, stale["second_return_id"])
        assert second_return.status == "return_requested"
        ledgers = db.scalars(select(m.RefundLedger)).all()
        assert len(ledgers) == (1 if args.after_worker else 0)
        if args.after_worker:
            assert ledgers[0].proposal_id == new.id
            assert ledgers[0].amount_cents == new.amount_cents
        print(json.dumps({"stage": "after_worker" if args.after_worker else "before_worker", "failed_inspection": failed_return.status, "old_proposal": old.status, "new_proposal": new.status, "ledger_count": len(ledgers)}))


if __name__ == "__main__":
    main()
