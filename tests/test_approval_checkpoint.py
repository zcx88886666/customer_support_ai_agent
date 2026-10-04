from __future__ import annotations

from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlalchemy import select

from resolveai import domain as d, models as m
from resolveai.approval_checkpoint import _config, build_approval_graph


AT = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)


def pending_proposal(db, key: str):
    request = d.create_return(db, "cust-01", "demo-order-01", "demo-item-01", 1, "changed mind", True, key, AT)
    d.record_receipt(db, "warehouse-1", request.id, 1, AT + timedelta(hours=1))
    d.record_inspection(db, "warehouse-1", request.id, True, "intact", AT + timedelta(hours=2))
    proposal = d.create_proposal(db, request.id, AT + timedelta(hours=2))
    db.flush()
    return proposal


def test_approval_graph_waits_and_rechecks_committed_facts(db):
    proposal = pending_proposal(db, "graph-approval")
    saver = MemorySaver()
    graph = build_approval_graph(db, saver)
    config = _config(proposal.id)
    initial = graph.invoke({"proposal_id": proposal.id}, config)
    assert initial.get("__interrupt__")
    assert graph.get_state(config).next == ("wait_for_supervisor",)
    assert db.scalars(select(m.RefundLedger)).all() == []

    forged = graph.invoke(Command(resume={"decision": "approved", "amount_cents": 0}), config)
    assert forged.get("__interrupt__")
    assert graph.get_state(config).next == ("wait_for_supervisor",)
    assert db.scalars(select(m.RefundLedger)).all() == []

    approval = d.decide_proposal(db, "supervisor-1", proposal.id, True, AT + timedelta(hours=3))
    db.flush()
    resumed = graph.invoke(Command(resume={"decision": "rejected"}), config)
    assert resumed["status"] == "approved" and resumed["approval_id"] == approval.id
    assert graph.get_state(config).next == ()
    assert db.scalars(select(m.RefundLedger)).all() == []


def test_approval_graph_rechecks_stale_and_rejected_state(db):
    proposal = pending_proposal(db, "graph-stale")
    graph = build_approval_graph(db, MemorySaver())
    config = _config(proposal.id)
    assert graph.invoke({"proposal_id": proposal.id}, config).get("__interrupt__")
    db.get(m.Order, "demo-order-01").version += 1
    db.flush()
    refreshed = d.create_proposal(db, proposal.return_id, AT + timedelta(hours=3))
    db.flush()
    assert refreshed.id != proposal.id
    assert graph.invoke(Command(resume={"decision": "approved"}), config)["status"] == "stale"
    assert db.scalars(select(m.Approval)).all() == []
    assert db.scalars(select(m.RefundLedger)).all() == []

    fresh_graph = build_approval_graph(db, MemorySaver())
    fresh_config = _config(refreshed.id)
    assert fresh_graph.invoke({"proposal_id": refreshed.id}, fresh_config).get("__interrupt__")
    d.decide_proposal(db, "supervisor-1", refreshed.id, False, AT + timedelta(hours=4))
    db.flush()
    assert fresh_graph.invoke(Command(resume={"decision": "approved"}), fresh_config)["status"] == "rejected"
    assert db.scalars(select(m.RefundLedger)).all() == []
