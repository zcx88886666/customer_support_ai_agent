"""Generate isolated persisted refund faults and measure the database-first scorer."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from resolveai import domain, models
from resolveai.db import Base, make_engine
from resolveai.prompts import ROOT
from resolveai.seed import seed_demo

from . import score


CLOCK = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
CASE = {"fixture": {"customer_id": "cust-01", "order_id": "demo-order-01"},
        "gold": {"ledger_count": 1, "return_count": 1}}


def _mutations() -> list[dict]:
    edges = (("request", "created_at", "receipt", "received_at"),
             ("receipt", "received_at", "inspection", "inspected_at"),
             ("inspection", "inspected_at", "proposal", "created_at"),
             ("proposal", "created_at", "approval", "decided_at"),
             ("approval", "decided_at", "ledger", "issued_at"))
    result = [{"name": f"{left}_{left_field}_after_{right}", "kind": "chronology",
               "target": left, "field": left_field, "later": right, "later_field": right_field,
               "expected_check": "refund_authorized"}
              for left, left_field, right, right_field in edges]
    for target, field in (("ledger", "amount_cents"), ("proposal", "amount_cents"),
                          ("item", "refunded_cents"), ("refund_audit", "amount_cents"),
                          ("item", "refunded_quantity")):
        for delta in (-1, 1):
            label = "amount" if target == "refund_audit" else field
            result.append({"name": f"{target}_{label}_{'minus' if delta < 0 else 'plus'}_1",
                           "kind": "delta", "target": target, "field": field,
                           "delta": delta, "expected_check": "refund_authorized"})
    result.extend((
        {"name": "proposal_status_pending", "kind": "replace", "target": "proposal",
         "field": "status", "value": "pending", "expected_check": "refund_authorized"},
        {"name": "approval_decision_rejected", "kind": "replace", "target": "approval",
         "field": "decision", "value": "rejected", "expected_check": "refund_authorized"},
        {"name": "approval_actor_foreign", "kind": "replace", "target": "approval",
         "field": "actor_id", "value": "cust-02", "expected_check": "refund_authorized"},
        {"name": "ledger_item_foreign", "kind": "replace", "target": "ledger",
         "field": "order_item_id", "value": "demo-item-02", "expected_check": "refund_authorized"},
        {"name": "request_customer_foreign", "kind": "replace", "target": "request",
         "field": "customer_id", "value": "cust-02", "expected_check": "ledger_owned"},
    ))
    return result


def _approved_refund(db) -> dict:
    seed_demo(db, CLOCK)
    request = domain.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1,
                                   "scorer campaign", True, "scorer-campaign", CLOCK)
    receipt = domain.record_receipt(db, "warehouse-test", request.id, 1, CLOCK + timedelta(hours=1))
    inspection = domain.record_inspection(db, "warehouse-test", request.id, True, "intact",
                                          CLOCK + timedelta(hours=2))
    proposal = domain.create_proposal(db, request.id, CLOCK + timedelta(hours=3))
    approval = domain.decide_proposal(db, "supervisor-test", proposal.id, True,
                                      CLOCK + timedelta(hours=4))
    ledger = domain.issue_refund(db, proposal.id, f"refund:{proposal.id}",
                                 CLOCK + timedelta(hours=5))
    db.flush()
    refund_audit = db.scalar(select(models.AuditEvent).where(
        models.AuditEvent.action == "issue_refund", models.AuditEvent.entity_id == proposal.id))
    return {"request": request, "receipt": receipt, "inspection": inspection,
            "proposal": proposal, "approval": approval, "ledger": ledger,
            "item": db.get(models.OrderItem, "demo-item-01"), "refund_audit": refund_audit}


def _apply(entities: dict, mutation: dict) -> None:
    target = entities[mutation["target"]]
    field = mutation["field"]
    if mutation["kind"] == "chronology":
        later = entities[mutation["later"]]
        setattr(target, field, domain.aware(getattr(later, mutation["later_field"])) + timedelta(seconds=1))
    elif mutation["kind"] == "delta":
        if mutation["target"] == "refund_audit":
            target.details = {**target.details, field: target.details[field] + mutation["delta"]}
        else:
            setattr(target, field, getattr(target, field) + mutation["delta"])
    else:
        setattr(target, field, mutation["value"])


def _checks(db, request_id: str) -> dict[str, bool]:
    checks, _ = score.score_case(CASE, {"status": "answered", "answer": "模拟退款已执行",
                                        "return_id": request_id}, 200, db)
    return checks


def _run_one(database: Path, mutation: dict | None) -> dict[str, bool]:
    engine = make_engine(f"sqlite:///{database}")
    try:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as db:
            entities = _approved_refund(db)
            if mutation is not None:
                _apply(entities, mutation)
            db.flush()
            return _checks(db, entities["request"].id)
    finally:
        engine.dispose()


def run_campaign(folder: Path, selected: set[str] | None = None) -> dict:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    mutations = _mutations()
    if selected is not None:
        unknown = selected - {row["name"] for row in mutations}
        if unknown:
            raise ValueError(f"Unknown mutations: {sorted(unknown)}")
        mutations = [row for row in mutations if row["name"] in selected]
    if not mutations:
        raise ValueError("No mutations selected")

    baseline_checks = _run_one(folder / "baseline.sqlite", None)
    baseline_pass = all(baseline_checks.values())
    results = []
    for mutation in mutations:
        name = mutation["name"]
        try:
            checks = _run_one(folder / f"{name}.sqlite", mutation)
            failed = sorted(key for key, passed in checks.items() if not passed)
            outcome = "killed" if mutation["expected_check"] in failed else "survived"
            results.append({"mutation": name, "expected_check": mutation["expected_check"],
                            "outcome": outcome, "failed_checks": failed})
        except Exception as exc:
            results.append({"mutation": name, "expected_check": mutation["expected_check"],
                            "outcome": "invalid", "error_type": type(exc).__name__})
    counts = {key: sum(row["outcome"] == key for row in results)
              for key in ("killed", "survived", "invalid")}
    run_id = folder.name
    summary = {"run_id": run_id, "suite": "generated_scorer_mutations_v1",
               "baseline_pass": baseline_pass, "baseline_checks": baseline_checks,
               "mutants": len(results), "counts": counts,
               "development_pass": baseline_pass and counts["killed"] == len(results),
               "locked_release_pass": False, "results": results}
    manifest = {"run_id": run_id, "suite": summary["suite"], "synthetic": True,
                "database": "isolated-sqlite-per-mutation", "clock": CLOCK.isoformat(),
                "scorer_sha256": hashlib.sha256(Path(score.__file__).read_bytes()).hexdigest(),
                "campaign_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "mutation_set": mutations}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (folder / "case_results.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in results), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    rows = "".join("<tr><td>" + html.escape(row["mutation"]) + "</td><td>"
                   + html.escape(row["outcome"]) + "</td><td>"
                   + html.escape(", ".join(row.get("failed_checks", []))) + "</td></tr>"
                   for row in results)
    (folder / "report.html").write_text(
        "<!doctype html><html lang='en'><meta charset='utf-8'><title>Scorer mutation campaign</title>"
        f"<h1>Scorer mutation campaign</h1><p>Baseline: {baseline_pass}; "
        f"killed: {counts['killed']}; survived: {counts['survived']}; invalid: {counts['invalid']}. "
        "Synthetic development evidence; locked release gate remains false.</p>"
        "<table><tr><th>Mutation</th><th>Outcome</th><th>Failed checks</th></tr>"
        + rows + "</table></html>\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-scorer-mutations-" + uuid4().hex[:6]
    result = run_campaign(args.output or ROOT / "evals/reports" / run_id)
    print(json.dumps({key: result[key] for key in ("run_id", "mutants", "counts", "development_pass",
                                                    "locked_release_pass")}))
    raise SystemExit(0 if result["development_pass"] else 1)


if __name__ == "__main__":
    main()
