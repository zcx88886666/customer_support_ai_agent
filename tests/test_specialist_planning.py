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


@pytest.mark.parametrize("mode", ["single", "collab"])
def test_selected_one_tool_answers_order_status_without_shipment_or_write(db, monkeypatch, mode):
    from resolveai import agent, models as m
    from resolveai.schemas import ChatInput

    selected = []

    def plan(delegation, _release):
        selected.append(delegation.order_read_scope)
        return OrderToolPlan(tools=["get_order"])

    monkeypatch.setattr(planning, "plan_order", plan)
    # A status query does not need a package choice, even for a split shipment.
    db.add(m.Shipment(id="planning-extra-package", order_id="demo-order-02", status="in_transit", version=1))
    db.flush()
    result = agent.run_chat(db, "cust-01", ChatInput(thread_id="selected-status-" + mode,
                            message="查我的订单状态", order_id="demo-order-02", agent_mode=mode))
    assert selected == ["status"]
    assert result["status"] == "answered" and "订单状态" in result["answer"]
    finding = result["findings"][0]
    assert finding["source_ids"] == ["demo-order-02"] and finding["tool_calls"] == 1
    assert finding["facts"]["shipment_status"] is None
    assert db.query(m.ReturnRequest).count() == db.query(m.RefundLedger).count() == 0


def test_insufficient_selected_order_tools_exposes_evidence_gap(db, monkeypatch):
    from resolveai import agent
    from resolveai.schemas import ChatInput

    monkeypatch.setattr(planning, "plan_order", lambda *_args: OrderToolPlan(tools=["get_order"]))
    result = agent.run_chat(db, "cust-01", ChatInput(thread_id="selected-insufficient", message="查包裹物流",
                            order_id="demo-order-02"))
    assert result["status"] == "answered" and "部分证据未核实" in result["answer"]
    assert len(result["findings"]) == 1 and result["findings"][0]["status"] == "incomplete"
    assert result["findings"][0]["facts"] == {} and result["findings"][0]["source_ids"] == []
    assert result["findings"][0]["tool_calls"] == 1


@pytest.mark.parametrize("mode", ["single", "collab"])
def test_status_query_displays_order_status_when_model_selects_both_tools(db, monkeypatch, mode):
    from resolveai import agent
    from resolveai.schemas import ChatInput

    monkeypatch.setattr(planning, "plan_order", lambda *_args: OrderToolPlan(tools=["get_order", "track_shipment"]))
    result = agent.run_chat(db, "cust-01", ChatInput(thread_id="status-two-tools-" + mode,
                            message="查我的订单状态", order_id="demo-order-02", agent_mode=mode))
    assert result["status"] == "answered" and "订单状态为 paid" in result["answer"]
    assert result["findings"][0]["source_ids"] == ["demo-order-02", "demo-shipment-02"]
    assert result["findings"][0]["tool_calls"] == 2


def test_two_scoped_policy_queries_are_bounded_and_verified(db, monkeypatch):
    from resolveai import agent, models as m
    from resolveai.schemas import ChatInput
    from sqlalchemy import select

    clause = db.scalars(select(m.PolicyClause).where(m.PolicyClause.bundle_id == "policy-demo-v1")).first()
    seen = []
    monkeypatch.setattr(planning, "plan_policy", lambda *_args: PolicyRetrievalPlan(queries=["条件", "例外"]))

    def retrieve(_db, bundle_id, query, limit):
        seen.append((bundle_id, query))
        return [] if len(seen) == 1 else [clause]

    monkeypatch.setattr(agent, "retrieve", retrieve)
    result = agent.run_chat(db, "cust-01", ChatInput(thread_id="selected-policy", message="七天退货政策是什么"))
    assert result["status"] == "answered" and len(seen) == 2
    assert {bundle for bundle, _query in seen} == {"policy-demo-v1"}
    assert all("七天" in query and "政策" in query for _bundle, query in seen)
    assert result["findings"][0]["source_ids"] == [clause.id]
    assert result["findings"][0]["tool_calls"] == 2


def test_selected_mcp_tools_bind_runtime_reference_only(monkeypatch):
    from resolveai import commerce_client

    captured = []

    async def calls(token, selected, url=None):
        captured.append((token, selected))
        return [{"id": "owned-order", "status": "paid", "version": 1}]

    monkeypatch.setattr(commerce_client, "call_read_tools", calls)
    result = commerce_client.read_selected_order_tools("synthetic-token", "owned-order", ["get_order"])
    assert result == {"get_order": {"id": "owned-order", "status": "paid", "version": 1}}
    assert captured == [("synthetic-token", [("get_order", {"order_id": "owned-order"})])]


def test_status_only_finding_is_rejected_for_shipment_task(db):
    from resolveai.agent import validate_finding
    from resolveai.schemas import SpecialistFinding

    finding = SpecialistFinding(task_id="plan-test", plan_revision=1, status="ok", queried_at=datetime.now(timezone.utc),
        source_ids=["demo-order-02"], source_version="1", tool_calls=1,
        facts={"order_status": "paid", "shipment_status": None, "delivered_at": None})
    assert validate_finding(db, "cust-01", task(scope="status"), finding, 1)
    assert not validate_finding(db, "cust-01", task(scope="shipment"), finding, 1)


def test_single_specialist_deadline_allows_time_for_queued_policy_work(db, monkeypatch):
    from resolveai import agent
    from resolveai.schemas import ChatInput

    elapsed = 0

    class ControlledDatetime:
        @staticmethod
        def now(tz=None):
            return datetime.now(tz) + timedelta(seconds=elapsed)

    def timed_graph(original, duration):
        def build(*args, **kwargs):
            real = original(*args, **kwargs)

            class Timed:
                def invoke(self, state):
                    nonlocal elapsed
                    result = real.invoke(state)
                    elapsed += duration
                    return result
            return Timed()
        return build

    monkeypatch.setattr(agent, "build_policy_graph", timed_graph(agent.build_policy_graph, 6))
    monkeypatch.setattr(agent, "build_order_graph", timed_graph(agent.build_order_graph, 8))
    monkeypatch.setattr(agent, "datetime", ControlledDatetime)
    result = agent.run_chat(db, "cust-01", ChatInput(thread_id="queued-planning", message="包裹没到能退吗",
                            order_id="demo-order-02", agent_mode="single"))
    assert result["status"] == "answered"
    assert all(finding["status"] == "ok" for finding in result["findings"])
    assert "尚未确认签收" in result["answer"]


def test_parallel_live_planning_uses_shared_attempt_limit_and_safe_handoff(db, monkeypatch):
    import json
    import httpx
    from dataclasses import replace
    from resolveai import agent, models as m
    from resolveai.schemas import ChatInput

    names = []

    async def post(_client, url, **kwargs):
        name = kwargs["json"]["response_format"]["json_schema"]["name"]
        names.append(name)
        if name == "OrderToolPlan":
            result = {"tools": ["get_order", "track_shipment"]}
        elif name == "PolicyRetrievalPlan":
            result = {"queries": ["退货条件"]}
        else:
            result = {"selected_evidence": [], "unresolved_conditions": []}
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": json.dumps(result)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.00003}})

    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-key")
    monkeypatch.setenv("AGENT_MAX_LLM_CALLS", "3")
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(agent, "settings", replace(agent.settings, prompt_release="specialists-dev-v1"))
    result = agent.run_chat(db, "cust-01", ChatInput(thread_id="planning-budget", message="包裹没到能退吗",
                            order_id="demo-order-02", agent_mode="collab"))
    assert {"OrderToolPlan", "PolicyRetrievalPlan"}.issubset(names) and len(names) == 3
    assert result["status"] == "handoff" and result["findings"] == []
    assert result["resource_usage"]["exhausted_reason"] == "llm_call_limit"
    assert result["resource_usage"]["llm_attempts"] == 3 and result["resource_usage"]["pending_calls"] == 0
    assert db.query(m.ReturnRequest).count() == db.query(m.RefundLedger).count() == 0
