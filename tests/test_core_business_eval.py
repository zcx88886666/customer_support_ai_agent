from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3

import pytest

from evals.runners import run_core_business


@pytest.mark.parametrize("case", run_core_business.load_cases(), ids=lambda case: case["case_id"])
def test_agent_business_terminal_case(case):
    result = run_core_business.run_case(case, "core-business-test", datetime.now(timezone.utc))
    assert result["status"] == "pass", result
    assert all(result["checks"].values())


def test_scripted_core_scorer_rejects_missing_refund_worker(monkeypatch):
    case = next(case for case in run_core_business.load_cases() if case["case_id"] == "core-partial-quantity-approved")
    monkeypatch.setattr(run_core_business.worker, "issue_approved_once", lambda: [])
    result = run_core_business.run_case(case, "core-no-worker-test", datetime.now(timezone.utc))
    assert result["status"] == "fail"
    assert not result["checks"]["ledger_count"]
    assert not result["checks"]["return_statuses"]


def test_failed_core_case_keeps_isolated_database_for_diagnosis(monkeypatch, tmp_path):
    case = next(case for case in run_core_business.load_cases() if case["case_id"] == "core-partial-quantity-approved")
    monkeypatch.setattr(run_core_business.worker, "issue_approved_once", lambda: [])
    result = run_core_business.run_case(case, "core-failure-artifact-test", datetime.now(timezone.utc),
                                        failure_artifacts_dir=tmp_path)
    assert result["status"] == "fail"
    snapshot = tmp_path / result["failure_database"]
    assert snapshot.is_file()
    assert snapshot.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT COUNT(*) FROM refund_ledger").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_core_report_links_failed_database_snapshot(monkeypatch, tmp_path):
    case = next(case for case in run_core_business.load_cases() if case["case_id"] == "core-partial-quantity-approved")
    monkeypatch.setattr(run_core_business, "load_cases", lambda: [case])
    monkeypatch.setattr(run_core_business, "ROOT", tmp_path)
    monkeypatch.setattr(run_core_business.worker, "issue_approved_once", lambda: [])
    with pytest.raises(SystemExit) as stopped:
        run_core_business.main()
    assert stopped.value.code == 1
    report = next((tmp_path / "evals/reports").iterdir())
    result = json.loads((report / "case_results.jsonl").read_text())
    assert result["status"] == "fail"
    assert (report / result["failure_database"]).is_file()


def test_failure_snapshot_includes_committed_wal_rows(tmp_path):
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "saved.sqlite3"
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE facts (value TEXT)")
        connection.execute("INSERT INTO facts VALUES ('committed')")
        connection.commit()
        assert (tmp_path / "source.sqlite3-wal").is_file()
        run_core_business.snapshot_sqlite(source, destination)
        with sqlite3.connect(destination) as saved:
            assert saved.execute("SELECT value FROM facts").fetchone() == ("committed",)
    finally:
        connection.close()


def test_failed_snapshot_publish_leaves_no_partial_file(monkeypatch, tmp_path):
    source = tmp_path / "source.sqlite3"
    destination = tmp_path / "saved.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE facts (value TEXT)")
    monkeypatch.setattr(run_core_business.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("publish failed")))
    with pytest.raises(OSError, match="publish failed"):
        run_core_business.snapshot_sqlite(source, destination)
    assert not destination.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["source.sqlite3"]


@pytest.mark.parametrize("case_id", [123, "\ud800"])
def test_failure_snapshot_path_cannot_mask_invalid_case_id(case_id):
    relative = run_core_business.failure_database_path(case_id)
    assert relative.parent.name == "failure_dbs"
    assert relative.suffix == ".sqlite3"


@pytest.mark.parametrize("case_id", [123, "\ud800"])
def test_core_dataset_rejects_invalid_case_id(monkeypatch, tmp_path, case_id):
    case = run_core_business.load_cases()[0]
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({**case, "case_id": case_id}, ensure_ascii=True) + "\n")
    monkeypatch.setattr(run_core_business, "DATASET", path)
    with pytest.raises(ValueError, match="case ID"):
        run_core_business.load_cases()


def test_core_chat_scorer_requires_ticket_committed_during_chat_phase():
    case = next(case for case in run_core_business.load_cases() if case["case_id"] == "core-chat-expired-window")
    row = {"http_status": 200, "payload": {"status": "human_review", "ticket_id": "ticket-a",
                                           "route": {"route": "after_sales"}}, "ticket_ids": [],
           "return_count": 0, "ledger_count": 0}
    checks = run_core_business.score_chat_phase(case, [row], [], 0, {})
    assert not checks["chat_committed_ticket"]
    row["ticket_ids"] = ["ticket-a"]
    checks = run_core_business.score_chat_phase(case, [row], [], 0, {})
    assert checks["chat_committed_ticket"]


def test_core_chat_scorer_binds_review_ticket_to_owned_audited_request():
    case = next(case for case in run_core_business.load_cases() if case["case_id"] == "core-chat-expired-window")
    row = {"http_status": 200, "payload": {"status": "human_review", "ticket_id": "ticket-a",
                                            "reason_code": "outside_window", "route": {"route": "after_sales"}},
           "ticket_ids": ["ticket-a"], "return_count": 0, "ledger_count": 0,
           "ticket_reviews": [{"id": "ticket-a", "customer_id": "cust-02", "order_id": "demo-order-01",
                               "return_id": None, "topic": "return eligibility review: outside_window",
                               "review_key": case["case_id"], "review_payload_hash": "stored-hash",
                               "messages": [{"actor_type": "customer", "body": "Customer requested return eligibility review for item demo-item-01, quantity 1. Reason: changed mind"}],
                               "audits": [{"action": "create_return_review_ticket", "entity_type": "ticket",
                                           "entity_id": "unrelated", "actor_id": "cust-01", "idempotency_key": case["case_id"],
                                           "details": {"order_id": "demo-order-01", "item_id": "demo-item-01",
                                                       "quantity": 1, "reason_code": "outside_window"}}]}]}
    checks = run_core_business.score_chat_phase(case, [row], [], 0, {})
    assert checks["chat_committed_ticket"]
    assert not checks["chat_review_ticket"]
    row["ticket_reviews"][0]["customer_id"] = "cust-01"
    row["ticket_reviews"][0]["audits"][0]["entity_id"] = "ticket-a"
    checks = run_core_business.score_chat_phase(case, [row], [], 0, {})
    assert checks["chat_review_ticket"]


def test_core_chat_scorer_checks_ticket_count_at_each_turn():
    case = next(case for case in run_core_business.load_cases() if case["case_id"] == "core-chat-expired-window")
    case = {**case, "gold": {**case["gold"], "turn_gold": [{"ticket_count": 1}]}}
    row = {"http_status": 200, "payload": {"status": "human_review", "ticket_id": "ticket-a",
                                            "reason_code": "outside_window", "route": {"route": "after_sales"}},
           "ticket_ids": [], "return_count": 0, "ledger_count": 0}
    checks = run_core_business.score_chat_phase(case, [row], [], 0, {})
    assert checks["turn_0_ticket_count"] is False
