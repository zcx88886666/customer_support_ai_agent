from __future__ import annotations

from datetime import datetime, timezone

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
