"""Verify bounded specialist conflict retry through a real PostgresSaver."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from resolveai import agent
from resolveai.checkpoint import setup_checkpointer
from resolveai.db import SessionLocal, engine
from resolveai.schemas import ChatInput, DelegationTask, SpecialistFinding


def main() -> None:
    assert engine.dialect.name == "postgresql", "Use isolated PostgreSQL"
    setup_checkpointer()
    original_order = agent.build_order_graph
    original_policy = agent.build_policy_graph
    calls = {"order": 0, "policy": 0}

    def order_graph(*args, **kwargs):
        real = original_order(*args, **kwargs)

        class Wrapped:
            def invoke(self, state):
                calls["order"] += 1
                if calls["order"] == 1:
                    task = DelegationTask.model_validate(state["task"])
                    finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="conflict", queried_at=datetime.now(timezone.utc), unresolved=["synthetic_conflict"])
                    return {"finding": finding.model_dump(mode="json")}
                return real.invoke(state)

        return Wrapped()

    def policy_graph(*args, **kwargs):
        real = original_policy(*args, **kwargs)

        class Wrapped:
            def invoke(self, state):
                calls["policy"] += 1
                return real.invoke(state)

        return Wrapped()

    agent.build_order_graph = order_graph
    agent.build_policy_graph = policy_graph
    with SessionLocal.begin() as db:
        result = agent.run_chat(db, "cust-01", ChatInput(thread_id="postgres-conflict-reuse", message="包裹没到能退吗", order_id="demo-order-02", agent_mode="collab"))
    assert result["status"] == "answered" and result["replan_count"] == 1
    assert calls == {"order": 2, "policy": 1}
    assert len(result["findings"]) == 2 and {finding["plan_revision"] for finding in result["findings"]} == {2}
    print(json.dumps({"status": result["status"], "replan_count": result["replan_count"], "specialist_calls": calls, "finding_count": len(result["findings"])}))


if __name__ == "__main__":
    main()
