from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from resolveai import specialist_planning as planning
from resolveai.schemas import DelegationTask, OrderToolPlan, PolicyRetrievalPlan


def task(role="order", scope="shipment", question="查物流"):
    return DelegationTask(task_id="plan-test", thread_id="plan-thread", plan_revision=1, specialist=role,
                          question_scope=question, verified_order_ref="demo-order-02" if role == "order" else None,
                          policy_bundle_id="policy-demo-v1" if role == "policy" else None,
                          order_read_scope=scope, deadline=datetime.now(timezone.utc) + timedelta(seconds=10))


@pytest.mark.parametrize("value", [
    {"tools": ["issue_refund"]}, {"tools": ["get_order"], "order_id": "foreign-order"},
    {"tools": ["get_order", "get_order"]}, {"tools": ["get_order", "track_shipment", "get_order"]},
])
def test_order_plan_rejects_write_tools_identity_arguments_and_duplicate_dispatch(value):
    with pytest.raises(ValidationError):
        OrderToolPlan.model_validate(value)


@pytest.mark.parametrize("value", [
    {"queries": ["退货"], "policy_bundle_id": "another-bundle"}, {"queries": ["退货", "退货"]},
    {"queries": [" "]}, {"queries": ["x" * 201]}, {"queries": ["a", "b", "c"]},
])
def test_policy_plan_rejects_bundle_override_duplicates_and_unbounded_queries(value):
    with pytest.raises(ValidationError):
        PolicyRetrievalPlan.model_validate(value)


def test_no_key_plans_are_deterministic_and_bounded(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert planning.plan_order(task(scope="status"), "release-v1").tools == ["get_order"]
    assert planning.plan_order(task(), "release-v1").tools == ["get_order", "track_shipment"]
    assert planning.plan_policy(task("policy", question="退货政策"), "release-v1").queries == ["退货政策"]


def test_model_plan_receives_no_identity_and_exact_release(monkeypatch):
    captured = []
    monkeypatch.setattr(planning, "enabled", lambda _release: True)

    def call(name, variables, schema, release):
        captured.append((name, variables, schema, release))
        return OrderToolPlan(tools=["get_order"])

    monkeypatch.setattr(planning, "call_structured", call)
    assert planning.plan_order(task(scope="status", question="订单状态"), "specialists-dev-v1").tools == ["get_order"]
    name, variables, schema, release = captured[0]
    assert name == "order_plan" and schema is OrderToolPlan and release == "specialists-dev-v1"
    assert "demo-order" not in str(variables) and "plan-thread" not in str(variables)
    assert "status" in variables["question"]


def test_technical_planner_failure_uses_safe_deterministic_reads(monkeypatch):
    monkeypatch.setattr(planning, "enabled", lambda _release: True)

    def unavailable(*_args):
        raise planning.ModelUnavailable("synthetic unavailable")

    monkeypatch.setattr(planning, "call_structured", unavailable)
    assert planning.plan_order(task(), "specialists-dev-v1").tools == ["get_order", "track_shipment"]
    assert planning.plan_policy(task("policy", question="退货政策"), "specialists-dev-v1").queries == ["退货政策"]


def test_valid_empty_plan_is_preserved_and_unrelated_policy_cannot_be_rewritten(monkeypatch):
    monkeypatch.setattr(planning, "enabled", lambda _release: True)
    calls = []

    def call(name, *_args):
        calls.append(name)
        return OrderToolPlan(tools=[]) if name == "order_plan" else PolicyRetrievalPlan(queries=["退货政策"])

    monkeypatch.setattr(planning, "call_structured", call)
    assert planning.plan_order(task(), "specialists-dev-v1").tools == []
    assert planning.plan_policy(task("policy", question="怎么购买股票"), "specialists-dev-v1").queries == []
    assert calls == ["order_plan"]
