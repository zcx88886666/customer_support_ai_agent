"""Measure detection of deliberate application faults in isolated business runs."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from resolveai import domain as d, models as m, worker
from resolveai.config import settings
from resolveai.prompts import ROOT

if __package__:
    from . import run_business, run_core_business
else:
    import run_business
    import run_core_business


MUTANTS = (
    ("worker_never_issues", "approved_refund"),
    ("foreign_customer_accepted", "cross_customer_denial"),
    ("refund_amount_minus_one", "approved_refund"),
    ("stale_proposal_approved", "stale_proposal"),
    ("refund_audit_omitted", "approved_refund"),
    ("return_audit_omitted", "approved_refund"),
    ("failed_inspection_passed", "inspection_exception"),
    ("confirmation_ignored", "unconfirmed_return"),
    ("expired_window_ignored", "expired_window"),
    ("review_audit_wrong_actor", "core-chat-expired-window"),
    ("review_ticket_wrong_order", "core-chat-expired-window"),
    ("approval_timestamp_after_refund", "approved_refund"),
)


@contextmanager
def inject(name: str):
    if name == "worker_never_issues":
        with patch.object(worker, "issue_approved_once", lambda: []):
            yield
        return
    if name in {"foreign_customer_accepted", "confirmation_ignored"}:
        original = d.create_return

        def changed(*args, **kwargs):
            values = list(args)
            if name == "foreign_customer_accepted":
                values[1] = values[0].get(m.Order, values[2]).customer_id
            else:
                values[6] = True
            return original(*values, **kwargs)

        with patch.object(d, "create_return", changed):
            yield
        return
    if name == "refund_amount_minus_one":
        original = d.expected_refund
        with patch.object(d, "expected_refund", lambda item, quantity: original(item, quantity) - 1):
            yield
        return
    if name == "stale_proposal_approved":
        original = d.decide_proposal

        def changed(db, actor, proposal_id, approve, at):
            proposal = db.get(m.RefundProposal, proposal_id)
            if proposal and approve:
                request = db.get(m.ReturnRequest, proposal.return_id)
                proposal.order_version = db.get(m.Order, request.order_id).version
            return original(db, actor, proposal_id, approve, at)

        with patch.object(d, "decide_proposal", changed):
            yield
        return
    if name == "approval_timestamp_after_refund":
        original = d.decide_proposal

        def changed(db, actor, proposal_id, approve, at):
            return original(db, actor, proposal_id, approve, at + timedelta(days=1))

        with patch.object(d, "decide_proposal", changed):
            yield
        return
    if name in {"refund_audit_omitted", "return_audit_omitted"}:
        original = d.audit
        target = "issue_refund" if name == "refund_audit_omitted" else "create_return"

        def changed(db, actor, action, entity, entity_id, **kwargs):
            if action != target:
                original(db, actor, action, entity, entity_id, **kwargs)

        with patch.object(d, "audit", changed):
            yield
        return
    if name == "failed_inspection_passed":
        original = d.record_inspection

        def changed(db, actor, return_id, passed, note, at):
            return original(db, actor, return_id, True, note, at)

        with patch.object(d, "record_inspection", changed):
            yield
        return
    if name == "expired_window_ignored":
        original = d.eligibility

        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            return {**result, "eligible": True, "reason": "within_window"} if result["reason"] == "outside_window" else result

        with patch.object(d, "eligibility", changed):
            yield
        return
    if name == "review_audit_wrong_actor":
        original = d.audit

        def changed(db, actor, action, entity, entity_id, **kwargs):
            return original(db, "cust-02" if action == "create_return_review_ticket" else actor,
                            action, entity, entity_id, **kwargs)

        with patch.object(d, "audit", changed):
            yield
        return
    if name == "review_ticket_wrong_order":
        original = d.request_return_review

        def changed(*args, **kwargs):
            ticket, reason = original(*args, **kwargs)
            ticket.order_id = "demo-order-02"
            return ticket, reason

        with patch.object(d, "request_return_review", changed):
            yield
        return
    raise ValueError("Unknown mutation")


def run_mutants() -> dict:
    if settings.auth_mode != "mock":
        raise RuntimeError("Mutation runner requires AUTH_MODE=mock")
    cases = {case["scenario"]: case for case in run_business.load_cases()}
    core_cases = {case["case_id"]: case for case in run_core_business.load_cases()}
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-business-mutations-" + uuid4().hex[:6]
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    results = []
    for name, scenario in MUTANTS:
        case = core_cases[scenario] if scenario in core_cases else cases[scenario]
        try:
            with inject(name):
                result = (run_core_business.run_case(case, run_id, datetime.now(timezone.utc))
                          if scenario in core_cases else run_business.run_case(case, run_id))
            outcome = "survived" if result["status"] == "pass" else "killed"
            results.append({"mutation": name, "case_id": result["case_id"], "outcome": outcome, "case_status": result["status"], "failed_checks": [key for key, passed in result["checks"].items() if not passed]})
        except Exception as exc:
            results.append({"mutation": name, "case_id": case["case_id"], "outcome": "invalid", "error": type(exc).__name__ + ": " + str(exc)[:160]})
    counts = {outcome: sum(row["outcome"] == outcome for row in results) for outcome in ("killed", "survived", "invalid")}
    valid = counts["killed"] + counts["survived"]
    summary = {"run_id": run_id, "dataset": "business_workflows_v2+core_business_dev_v1", "mutants": len(results), "counts": counts, "mutation_score": round(counts["killed"] / valid, 4) if valid else None, "gate_pass": counts["killed"] == len(results), "report": str(folder), "results": results}
    (folder / "manifest.json").write_text(json.dumps({"run_id": run_id, "dataset_sha256": hashlib.sha256(run_business.DATASET.read_bytes()).hexdigest(), "core_dataset_sha256": hashlib.sha256(run_core_business.DATASET.read_bytes()).hexdigest(), "mutation_set": [row[0] for row in MUTANTS], "auth_mode": "mock", "database": "isolated-sqlite-per-mutation"}, indent=2) + "\n", encoding="utf-8")
    (folder / "mutations.jsonl").write_text("".join(json.dumps(row) + "\n" for row in results), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    result = run_mutants()
    print(json.dumps(result))
    raise SystemExit(0 if result["gate_pass"] else 1)
