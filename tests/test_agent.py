from __future__ import annotations

import pytest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from langgraph.checkpoint.memory import MemorySaver

from resolveai.agent import classify, run_chat, validate_finding, review_evidence
from resolveai.domain import DomainError
from resolveai import models as m
from resolveai.schemas import ChatInput, DelegationTask, SpecialistFinding


def test_composite_dispatch_and_unreceived_limit(db):
    result = run_chat(db, "cust-01", ChatInput(thread_id="thread-1", message="包裹没到能退吗", order_id="demo-order-02", agent_mode="collab"))
    assert result["status"] == "answered"
    assert len(result["findings"]) == 2
    assert "尚未确认签收" in result["answer"]
    assert {finding["source_version"] for finding in result["findings"]} == {"1", "policy-demo-v1"}


def test_undelivered_return_question_is_not_a_submission(db, monkeypatch):
    from resolveai import agent
    from resolveai.schemas import RouteDecision

    monkeypatch.setattr(agent, "model_configured", lambda: True)
    monkeypatch.setattr(agent, "call_structured", lambda *args: RouteDecision(route="after_sales", intents=["return_request"]))
    result = run_chat(db, "cust-01", ChatInput(thread_id="undelivered-question", message="包裹没到能退吗", order_id="demo-order-02", agent_mode="collab"))
    assert result["status"] == "answered"
    assert result["route"]["intents"] == ["shipment_tracking", "policy_qa"]
    assert len(result["findings"]) == 2
    assert "尚未确认签收" in result["answer"]


def test_single_domain_and_thread_isolation(db):
    result = run_chat(db, "cust-01", ChatInput(thread_id="thread-2", message="七天无理由退货政策是什么", agent_mode="collab"))
    assert len(result["findings"]) == 1
    with pytest.raises(DomainError):
        run_chat(db, "cust-02", ChatInput(thread_id="thread-2", message="政策是什么"))


def test_checkpoint_does_not_replay_previous_turn_findings(db, monkeypatch):
    saver = MemorySaver()

    @contextmanager
    def checkpointer():
        yield saver

    monkeypatch.setattr("resolveai.checkpoint.parent_checkpointer", checkpointer)
    body = ChatInput(thread_id="checkpoint-turns", message="包裹没到能退吗", order_id="demo-order-02", agent_mode="collab")
    first = run_chat(db, "cust-01", body)
    db.flush()
    second = run_chat(db, "cust-01", body)
    assert len(first["findings"]) == len(second["findings"]) == 2
    assert {finding["plan_revision"] for finding in first["findings"]} == {1}
    assert {finding["plan_revision"] for finding in second["findings"]} == {2}


def test_agent_cannot_refund_or_submit_without_confirmation(db):
    result = run_chat(db, "cust-01", ChatInput(thread_id="thread-3", message="我要退货并立即退款", order_id="demo-order-01", item_id="demo-item-01", quantity=1, reason="no longer needed"))
    assert result["status"] == "clarify"
    assert "明确确认" in result["answer"]


def test_forged_and_late_specialist_findings_rejected(db):
    task = DelegationTask(task_id="task-1", thread_id="thread", plan_revision=2, specialist="order", question_scope="包裹", verified_order_ref="demo-order-01", evidence_version_hint=1, deadline=datetime.now(timezone.utc) + timedelta(seconds=10))
    good = SpecialistFinding(task_id="task-1", plan_revision=2, status="ok", facts={"order_status": "paid", "shipment_status": "delivered", "delivered_at": db.get(m.Shipment, "demo-shipment-01").delivered_at.isoformat()}, source_ids=["demo-order-01", "demo-shipment-01"], source_version="1", queried_at=datetime.now(timezone.utc), tool_calls=1)
    assert validate_finding(db, "cust-01", task, good, 2)
    assert not validate_finding(db, "cust-01", task, good.model_copy(update={"plan_revision": 1}), 2)
    assert not validate_finding(db, "cust-01", task, good.model_copy(update={"source_ids": ["demo-order-05", "demo-shipment-05"]}), 2)
    assert not validate_finding(db, "cust-01", task, good.model_copy(update={"facts": {**good.facts, "shipment_status": "in_transit"}}), 2)
    assert not validate_finding(db, "cust-01", task, good.model_copy(update={"facts": {**good.facts, "approved": True}}), 2)
    assert not validate_finding(db, "cust-02", task, good, 2)
    assert not validate_finding(db, "cust-01", task, good.model_copy(update={"model_reviewed": True, "reviewed_source_ids": ["other-customer-order"]}), 2)


def test_model_evidence_review_uses_only_verified_aliases(monkeypatch):
    from resolveai import agent
    from resolveai.schemas import SpecialistReview

    monkeypatch.setattr(agent, "model_configured", lambda: True)
    seen = []

    def respond(task, variables, schema, release):
        seen.append(variables["question"])
        return SpecialistReview(selected_evidence=["e2", "e1"], unresolved_conditions=[])

    monkeypatch.setattr(agent, "call_structured", respond)
    sources = [("private-order-123", {"order_status": "paid"}), ("shipment-456", {"shipment_status": "in_transit"})]
    assert review_evidence("order_agent", "包裹", sources) == ["shipment-456", "private-order-123"]
    assert "private-order-123" not in seen[0] and "shipment-456" not in seen[0]
    monkeypatch.setattr(agent, "call_structured", lambda *args: SpecialistReview(selected_evidence=["e3"], unresolved_conditions=[]))
    assert review_evidence("order_agent", "包裹", sources) == []


def test_return_slots_continue_across_turns_but_confirmation_is_current(db):
    first = run_chat(db, "cust-01", ChatInput(thread_id="return-thread", message="我要退这件商品", order_id="demo-order-01"))
    assert first["status"] == "clarify" and "商品项编号" in first["answer"]
    second = run_chat(db, "cust-01", ChatInput(thread_id="return-thread", message="这件", item_id="demo-item-01"))
    assert second["status"] == "clarify" and "数量" in second["answer"]
    third = run_chat(db, "cust-01", ChatInput(thread_id="return-thread", message="我确认提交", quantity=1, reason="changed mind", confirmed=True, idempotency_key="multi-turn-return"))
    assert third["status"] == "return_requested"
    assert third["plan_revision"] == 3
