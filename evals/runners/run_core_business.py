"""Development replay of Agent turns followed by committed cross-role business actions."""

from __future__ import annotations

import hashlib
import html
import json
import os
import sqlite3
import tempfile
import time
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from resolveai import models as m, worker
from resolveai.api import app
from resolveai.config import settings
from resolveai.db import Base, get_db, make_engine
from resolveai.prompts import PromptRegistry, ROOT

if __package__:
    from . import run_business
else:
    import run_business


DATASET = ROOT / "evals/datasets/core_business_dev_v1.jsonl"


def failure_database_path(case_id: object) -> Path:
    encoded = json.dumps(case_id, ensure_ascii=True, sort_keys=True, default=str).encode("ascii")
    return Path("failure_dbs") / (hashlib.sha256(encoded).hexdigest()[:16] + ".sqlite3")


def snapshot_sqlite(source: Path, destination: Path) -> None:
    """Publish a consistent, private snapshot even when the source uses WAL."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".snapshot-", suffix=".sqlite3", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        with closing(sqlite3.connect(source, timeout=3)) as original:
            with closing(sqlite3.connect(temporary)) as saved:
                original.backup(saved)
                if saved.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise sqlite3.DatabaseError("SQLite failure snapshot failed integrity check")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def capture_review_tickets(db) -> list[dict]:
    """Capture committed review evidence before terminal actions can change the database."""
    rows = []
    for ticket in db.scalars(select(m.Ticket)).all():
        messages = db.scalars(select(m.ConversationMessage).where(m.ConversationMessage.ticket_id == ticket.id)).all()
        audits = db.scalars(select(m.AuditEvent).where(m.AuditEvent.entity_id == ticket.id,
                                                       m.AuditEvent.action == "create_return_review_ticket")).all()
        rows.append({"id": ticket.id, "customer_id": ticket.customer_id, "order_id": ticket.order_id,
                     "return_id": ticket.return_id, "topic": ticket.topic, "review_key": ticket.review_key,
                     "review_payload_hash": ticket.review_payload_hash,
                     "messages": [{"actor_type": message.actor_type, "body": message.body} for message in messages],
                     "audits": [{"action": audit.action, "entity_type": audit.entity_type,
                                 "entity_id": audit.entity_id, "actor_id": audit.actor_id,
                                 "idempotency_key": audit.idempotency_key, "details": audit.details} for audit in audits]})
    return rows


def load_cases() -> list[dict]:
    cases = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases or not all(isinstance(case, dict) for case in cases):
        raise ValueError("Core business cases must be nonempty objects")
    ids = [case.get("case_id") for case in cases]
    for case_id in ids:
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("Core business case ID must be a nonempty UTF-8 string")
        try:
            case_id.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Core business case ID must be a nonempty UTF-8 string") from exc
    if len(ids) != len(set(ids)):
        raise ValueError("Core business cases must have unique IDs")
    for case in cases:
        if (case.get("schema_version") != "v1" or case.get("suite") != "core_business_dev_v1"
                or case.get("split") != "dev" or not case.get("dialogue_script")
                or not case.get("group_keys") or not case.get("gold")
                or case.get("review", {}).get("status") != "pending"
                or case.get("terminal_scenario") not in {"approved_refund", "inspection_exception", "stale_proposal", "cross_customer_denial", "unconfirmed_return", "expired_window", "scripted"}
                or (case.get("terminal_scenario") == "scripted" and not case.get("workflow_steps"))):
            raise ValueError(f"Invalid development case {case.get('case_id')}")
    return cases


def score_chat_phase(case: dict, chat_rows: list[dict], chat_return_ids: list[str],
                     chat_ledger_count: int, observations: dict) -> dict[str, bool]:
    payload = chat_rows[-1]["payload"]
    checks = {
        "chat_http_status": all(row["http_status"] == case["gold"]["chat_http_status"] for row in chat_rows),
        "chat_status": payload.get("status") == case["gold"].get("chat_status"),
        "chat_error_code": payload.get("code") == case["gold"].get("chat_error_code"),
        "chat_route": payload.get("route", {}).get("route") == case["gold"].get("chat_route"),
        "chat_committed_return": len(chat_return_ids) == case["gold"]["chat_return_count"]
            and (payload.get("return_id") in chat_return_ids
                 and observations.get("return_id") == payload.get("return_id")
                 if chat_return_ids else payload.get("return_id") is None),
        "chat_no_early_refund": chat_ledger_count == case["gold"]["chat_ledger_count"],
    }
    if "chat_ticket_count" in case["gold"]:
        ticket_ids = chat_rows[-1].get("ticket_ids", [])
        expected_count = case["gold"]["chat_ticket_count"]
        checks["chat_committed_ticket"] = (len(ticket_ids) == expected_count
            and (payload.get("ticket_id") in ticket_ids if expected_count else payload.get("ticket_id") is None))
        if case["gold"].get("chat_status") == "human_review" and expected_count:
            fixture = case.get("fixture", {})
            ticket = next((row for row in chat_rows[-1].get("ticket_reviews", [])
                           if row["id"] == payload.get("ticket_id")), None)
            reason_code = case["gold"].get("chat_reason_code")
            turn = case["dialogue_script"][-1]
            expected_details = {"order_id": fixture.get("order_id"), "item_id": turn.get("item_id"),
                                "quantity": turn.get("quantity"), "reason_code": reason_code}
            customer_messages = [message["body"] for message in ticket["messages"]
                                 if message["actor_type"] == "customer"] if ticket else []
            matching_audits = [audit for audit in ticket["audits"]
                               if audit == {"action": "create_return_review_ticket", "entity_type": "ticket",
                                            "entity_id": ticket["id"], "actor_id": fixture.get("customer_id"),
                                            "idempotency_key": case.get("case_id"), "details": expected_details}] if ticket else []
            checks["chat_review_ticket"] = bool(
                ticket and ticket["customer_id"] == fixture.get("customer_id")
                and ticket["order_id"] == fixture.get("order_id") and ticket["return_id"] is None
                and ticket["topic"] == f"return eligibility review: {reason_code}"
                and ticket["review_key"] == case.get("case_id") and ticket.get("review_payload_hash")
                and len(customer_messages) == 1
                and all(value in customer_messages[0] for value in (
                    str(turn.get("item_id")), f"quantity {turn.get('quantity')}", str(turn.get("reason"))))
                and len(matching_audits) == 1 and len(ticket["audits"]) == 1)
    if "chat_reason_code" in case["gold"]:
        checks["chat_reason_code"] = payload.get("reason_code") == case["gold"]["chat_reason_code"]
    turn_gold = case["gold"].get("turn_gold", [])
    if turn_gold:
        checks["turn_count"] = len(chat_rows) == len(turn_gold)
        for index, expected in enumerate(turn_gold):
            if index >= len(chat_rows):
                break
            row = chat_rows[index]
            for field, actual in (
                ("http_status", row["http_status"]),
                ("status", row["payload"].get("status")),
                ("route", row["payload"].get("route", {}).get("route")),
                ("return_count", row["return_count"]),
                ("ledger_count", row["ledger_count"]),
                ("ticket_count", len(row.get("ticket_ids", []))),
            ):
                if field in expected:
                    checks[f"turn_{index}_{field}"] = actual == expected[field]
            if "answer_contains" in expected:
                checks[f"turn_{index}_answer_contains"] = all(
                    fragment in row["payload"].get("answer", "") for fragment in expected["answer_contains"])
    return checks


def execute_scripted(client: TestClient | None, case: dict, run_id: str, chat_rows: list[dict],
                     *, post_request=None, issue_worker=None) -> tuple[dict, dict]:
    """Replay versioned role actions; gold remains separate from the requests."""
    issue_worker = issue_worker or worker.issue_approved_once
    values = {"case_id": case["case_id"], **case["fixture"]}
    chat_return_id = chat_rows[-1]["payload"].get("return_id")
    if chat_return_id:
        values["return_id"] = chat_return_id
    statuses: dict[str, int] = {}
    observations: dict = {"preapproval_issued": 0, "worker_replay_issued": 0}
    if chat_return_id:
        observations["return_id"] = chat_return_id

    def resolve(value):
        if isinstance(value, str):
            return value.format_map(values)
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    for step in case["workflow_steps"]:
        name = step["name"]
        if step["kind"] == "worker":
            observations[step["observation"]] = len(issue_worker())
            continue
        role = step["role"]
        actor = case["fixture"]["customer_id"] if role == "customer" else f"{role}-test"
        path = resolve(step["path"])
        body = resolve(step.get("body", {}))
        if post_request:
            status, payload = post_request(path, role, actor, body)
        else:
            response = client.post(path, json=body, headers={"x-mock-actor": actor, "x-mock-role": role,
                                                            "x-eval-run-id": run_id, "x-eval-case-id": case["case_id"]})
            status, payload = response.status_code, response.json()
        statuses[name] = status
        if step.get("same_as"):
            observations[f"{name}_same_id"] = payload.get("id") == values[step["same_as"]]
        if step.get("capture"):
            captured = payload.get("id")
            if not captured:
                raise ValueError(f"Step {name} could not capture an ID")
            values[step["capture"]] = captured
            if step["capture"] == "return_id":
                observations["return_id"] = captured
    return statuses, observations


def run_case(case: dict, run_id: str, seed_clock: datetime, *,
             failure_artifacts_dir: Path | None = None) -> dict:
    with tempfile.TemporaryDirectory(prefix="resolveai-core-business-") as temp:
        database_path = Path(temp) / "case.db"
        engine = make_engine(f"sqlite:///{database_path}")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        result = None
        with factory.begin() as db:
            run_business.seed_demo(db, seed_clock)
        if case["terminal_scenario"] == "expired_window":
            with factory.begin() as db:
                shipment = db.scalar(select(m.Shipment).where(m.Shipment.order_id == case["fixture"]["order_id"]))
                shipment.delivered_at = seed_clock - timedelta(days=20)

        def override_db():
            with factory() as db:
                yield db

        original_worker_factory = worker.SessionLocal
        app.dependency_overrides[get_db] = override_db
        worker.SessionLocal = factory
        started = time.perf_counter()
        try:
            with TestClient(app) as client:
                chat_rows = []
                for turn in case["dialogue_script"]:
                    response = client.post(
                        "/chat",
                        json={**turn, "thread_id": case["case_id"],
                              "order_id": turn.get("order_id", case["fixture"]["order_id"]),
                              "idempotency_key": case["case_id"], "agent_mode": "single"},
                        headers={"x-mock-actor": case["fixture"]["customer_id"], "x-mock-role": "customer",
                                 "x-eval-run-id": run_id, "x-eval-case-id": case["case_id"]},
                    )
                    chat_rows.append({"http_status": response.status_code, "payload": response.json(),
                                      "trace_id": response.headers.get("x-trace-id")})
                    with factory() as db:
                        chat_rows[-1]["return_count"] = len(db.scalars(select(m.ReturnRequest)).all())
                        chat_rows[-1]["ledger_count"] = len(db.scalars(select(m.RefundLedger)).all())
                        chat_rows[-1]["ticket_reviews"] = capture_review_tickets(db)
                        chat_rows[-1]["ticket_ids"] = [ticket["id"] for ticket in chat_rows[-1]["ticket_reviews"]]
                with factory() as db:
                    chat_return_ids = [row.id for row in db.scalars(select(m.ReturnRequest)).all()]
                    chat_ledger_count = len(db.scalars(select(m.RefundLedger)).all())
                terminal_case = {**case, "scenario": case["terminal_scenario"]}
                if case["terminal_scenario"] == "scripted":
                    statuses, observations = execute_scripted(client, case, run_id, chat_rows)
                else:
                    statuses, observations = run_business.execute(client, terminal_case, run_id)
            with factory() as db:
                checks = run_business.score(terminal_case, statuses, observations, db)
            for name, expected in case["gold"].get("expected_observations", {}).items():
                checks[f"observation_{name}"] = observations.get(name) == expected
            last_chat = chat_rows[-1]
            payload = last_chat["payload"]
            checks.update(score_chat_phase(case, chat_rows, chat_return_ids, chat_ledger_count, observations))
            result = {"case_id": case["case_id"], "split": case["split"], "risk_tier": case["risk_tier"],
                    "status": "pass" if all(checks.values()) else "fail", "checks": checks,
                    "chat_status": payload.get("status"), "chat_error_code": payload.get("code"),
                    "chat_http_status": last_chat["http_status"], "http_statuses": statuses,
                    "trace_ids": [row["trace_id"] for row in chat_rows],
                    "seed_clock": seed_clock.isoformat(),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
            return result
        finally:
            worker.SessionLocal = original_worker_factory
            app.dependency_overrides.clear()
            engine.dispose()
            if failure_artifacts_dir is not None and (result is None or result["status"] != "pass") and database_path.exists():
                relative = failure_database_path(case["case_id"])
                snapshot = failure_artifacts_dir / relative
                try:
                    snapshot_sqlite(database_path, snapshot)
                    if result is not None:
                        result["failure_database"] = str(relative)
                except (OSError, sqlite3.Error) as exc:
                    if result is not None:
                        result["failure_database_error"] = type(exc).__name__


def main() -> None:
    if settings.auth_mode != "mock":
        raise SystemExit("Core business development runner requires AUTH_MODE=mock")
    cases = load_cases()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-core-" + uuid4().hex[:6]
    seed_clock = datetime.now(timezone.utc)
    folder = ROOT / "evals/reports" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    results = []
    for case in cases:
        try:
            results.append(run_case(case, run_id, seed_clock, failure_artifacts_dir=folder))
        except Exception as exc:
            row = {"case_id": case["case_id"], "split": case["split"], "risk_tier": case["risk_tier"],
                   "status": "incomplete", "error": type(exc).__name__ + ": " + str(exc)}
            relative = failure_database_path(case["case_id"])
            if (folder / relative).exists():
                row["failure_database"] = str(relative)
            results.append(row)
    counts = Counter(row["status"] for row in results)
    development_pass = counts.get("pass", 0) == len(cases)
    summary = {"run_id": run_id, "suite": "core_business_dev_v1", "unique_cases": len(cases),
               "executions": len(results), "counts": dict(counts),
               "critical_failures": [row["case_id"] for row in results if row["risk_tier"] == "critical" and row["status"] != "pass"],
               "development_pass": development_pass, "v6_minimum_cases_met": len(cases) >= 30,
               "release_gate_pass": False}
    manifest = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(), "seed_clock": seed_clock.isoformat(),
                "scorer_version": "core-business-dev-v1", "auth_mode": "mock", "database": "isolated-sqlite-per-case",
                "model": "deterministic-mock", "prompt_release_id": os.getenv("PROMPT_RELEASE", "release-v1"),
                "prompt_hashes": PromptRegistry(os.getenv("PROMPT_RELEASE", "release-v1")).manifest["prompts"]}
    (folder / "case_results.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results), encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = ["<html><meta charset='utf-8'><title>ResolveAI core business development report</title><body>",
            f"<h1>Run {html.escape(run_id)}</h1>",
            f"<p>{len(cases)} development cases; {counts.get('pass', 0)} pass, {counts.get('fail', 0)} fail, {counts.get('incomplete', 0)} incomplete.</p>",
            "<table border='1'><tr><th>Case</th><th>Status</th><th>Checks</th></tr>"]
    for row in results:
        rows.append(f"<tr><td>{html.escape(row['case_id'])}</td><td>{html.escape(row['status'])}</td><td>{html.escape(json.dumps(row.get('checks', row.get('error')), ensure_ascii=False))}</td></tr>")
    rows.append("</table></body></html>")
    (folder / "report.html").write_text("\n".join(rows), encoding="utf-8")
    print(json.dumps({**summary, "report": str(folder)}, ensure_ascii=False))
    raise SystemExit(0 if development_pass else 1)


if __name__ == "__main__":
    main()
