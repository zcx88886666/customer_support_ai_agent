from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from resolveai.delegation import TaskRuns, merge_findings
from resolveai.request_budget import RequestBudget
from resolveai.schemas import DelegationTask, SpecialistFinding


def delegation():
    return DelegationTask(task_id="duplicate-task", thread_id="duplicate-thread", plan_revision=1,
        specialist="order", question_scope="物流", verified_order_ref="owned-order",
        deadline=datetime.now(timezone.utc) + timedelta(seconds=10))


def finding(task):
    return SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="ok",
        queried_at=datetime.now(timezone.utc), source_ids=["owned-order", "owned-package"], source_version="1",
        facts={"order_status": "paid", "shipment_status": "in_transit", "delivered_at": None}, tool_calls=2).model_dump(mode="json")


def test_duplicate_results_merge_once_and_conflicting_facts_converge_independent_of_order():
    value = finding(delegation())
    assert merge_findings([value], [value]) == [value]
    changed = {**value, "facts": {**value["facts"], "shipment_status": "delivered"}}
    forward = merge_findings([value], [changed])
    assert forward == merge_findings([changed], [value])
    assert len(forward) == 1 and forward[0]["status"] == "conflict"
    assert forward[0]["facts"] == {} and forward[0]["source_ids"] == []
    assert merge_findings(forward, [value]) == forward
    later = {**value, "task_id": "later-task", "plan_revision": 2}
    assert len(merge_findings([value], [later])) == 2


def test_schema_valid_naive_and_aware_timestamps_converge_without_crashing():
    value = finding(delegation())
    value["queried_at"] = "2026-10-04T00:00:00Z"
    equivalent = {**value, "queried_at": "2026-10-04T00:00:00"}
    assert merge_findings([value], [equivalent]) == merge_findings([equivalent], [value]) == [value]
    changed = {**equivalent, "facts": {**value["facts"], "shipment_status": "delivered"}}
    assert merge_findings([value], [changed]) == merge_findings([changed], [value])
    assert merge_findings([value], [changed])[0]["status"] == "conflict"


def test_concurrent_duplicate_dispatch_executes_once_and_copies_result():
    runs = TaskRuns(RequestBudget())
    task = delegation()
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def execute():
        calls.append(task.task_id)
        entered.set()
        assert release.wait(timeout=2)
        return finding(task)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(runs.invoke, task, execute)
        assert entered.wait(timeout=2)
        second = executor.submit(runs.invoke, task, execute)
        release.set()
        a, b = first.result(timeout=3), second.result(timeout=3)
    assert calls == [task.task_id] and a == b
    b["facts"]["shipment_status"] = "invented"
    assert runs.invoke(task, execute) == a


def test_task_id_collision_never_executes_changed_order_reference():
    runs = TaskRuns(RequestBudget())
    task = delegation()
    runs.invoke(task, lambda: finding(task))
    changed = task.model_copy(update={"verified_order_ref": "foreign-order"})

    def forbidden():
        raise AssertionError("Conflicting task must not execute")

    result = runs.invoke(changed, forbidden)
    assert result["status"] == "conflict" and result["facts"] == {} and result["source_ids"] == []


def test_owner_failure_unblocks_duplicates_and_never_reexecutes():
    runs = TaskRuns(RequestBudget())
    task = delegation()
    calls = []

    def failed():
        calls.append(1)
        raise RuntimeError("synthetic failure")

    for _ in range(2):
        try:
            runs.invoke(task, failed)
        except RuntimeError as error:
            assert str(error) == "synthetic failure"
        else:
            raise AssertionError("Failed dispatch must not appear successful")
    assert calls == [1]


def test_expired_task_never_executes_or_returns_cached_success():
    runs = TaskRuns(RequestBudget())
    task = delegation().model_copy(update={"deadline": datetime.now(timezone.utc) - timedelta(seconds=1)})

    def forbidden():
        raise AssertionError("Expired task must not execute")

    result = runs.invoke(task, forbidden)
    assert result["status"] == "error" and result["facts"] == {} and result["source_ids"] == []

    task = delegation()
    clock = [datetime.now(timezone.utc)]
    runs = TaskRuns(RequestBudget(), now=lambda: clock[0])
    assert runs.invoke(task, lambda: finding(task))["status"] == "ok"
    clock[0] = task.deadline + timedelta(seconds=1)
    assert runs.invoke(task, forbidden)["status"] == "error"


def test_duplicate_wait_is_bounded_while_original_execution_is_still_running():
    budget = RequestBudget()
    runs = TaskRuns(budget)
    task = delegation().model_copy(update={"deadline": datetime.now(timezone.utc) + timedelta(seconds=0.15)})
    entered = threading.Event()
    release = threading.Event()

    def execute():
        entered.set()
        assert release.wait(timeout=2)
        return finding(task)

    with ThreadPoolExecutor(max_workers=2) as executor:
        original = executor.submit(runs.invoke, task, execute)
        assert entered.wait(timeout=2)
        duplicate = executor.submit(runs.invoke, task, execute)
        try:
            result = duplicate.result(timeout=0.8)
            assert result["status"] == "error" and result["facts"] == {}
        finally:
            release.set()
        assert original.result(timeout=2)["status"] == "error"


def test_parent_graph_duplicate_sends_share_one_execution_and_one_finding(db, monkeypatch):
    from resolveai import agent, models as m
    from resolveai.schemas import ChatInput

    original_edges = agent.StateGraph.add_conditional_edges
    guard = threading.Lock()
    calls = {"order": 0, "policy": 0}

    def edges(graph, source, path, *args, **kwargs):
        if source == "dispatch":
            original_path = path

            def duplicate(state):
                result = original_path(state)
                return result + result if isinstance(result, list) else result
            path = duplicate
        return original_edges(graph, source, path, *args, **kwargs)

    def measured(original, role):
        def build(*args, **kwargs):
            real = original(*args, **kwargs)

            class Counted:
                def invoke(self, state):
                    with guard:
                        calls[role] += 1
                    return real.invoke(state)
            return Counted()
        return build

    monkeypatch.setattr(agent.StateGraph, "add_conditional_edges", edges)
    monkeypatch.setattr(agent, "build_order_graph", measured(agent.build_order_graph, "order"))
    monkeypatch.setattr(agent, "build_policy_graph", measured(agent.build_policy_graph, "policy"))
    result = agent.run_chat(db, "cust-01", ChatInput(thread_id="duplicate-sends", message="包裹没到能退吗",
                            order_id="demo-order-02", agent_mode="collab"))
    assert calls == {"order": 1, "policy": 1}
    assert result["status"] == "answered" and len(result["findings"]) == 2
    assert all(finding["status"] == "ok" for finding in result["findings"])
    assert db.query(m.ReturnRequest).count() == db.query(m.RefundLedger).count() == 0
