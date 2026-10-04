"""Durable parent-graph wait for a supervisor decision; SQL remains authoritative."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models as m
from .checkpoint import parent_checkpointer
from .config import settings
from .domain import DomainError


class ApprovalState(TypedDict, total=False):
    proposal_id: str
    status: str
    approval_id: str | None


def _status(db: Session, proposal_id: str) -> tuple[str, str | None]:
    db.expire_all()
    proposal = db.get(m.RefundProposal, proposal_id)
    if proposal is None:
        raise DomainError("proposal_not_found", "Proposal unavailable", 404)
    approval = db.scalar(select(m.Approval).where(m.Approval.proposal_id == proposal_id))
    if proposal.status == "stale":
        return "stale", approval.id if approval else None
    if approval is None:
        return "pending" if proposal.status == "pending" else "inconsistent", None
    if approval.decision == "rejected" and proposal.status == "rejected":
        return "rejected", approval.id
    if approval.decision != "approved" or proposal.status not in {"approved", "issued"}:
        return "inconsistent", approval.id
    request = db.get(m.ReturnRequest, proposal.return_id)
    order = db.get(m.Order, request.order_id) if request else None
    if not order or request.plan_revision != proposal.plan_revision or request.policy_bundle_id != proposal.policy_bundle_id:
        return "stale", approval.id
    if proposal.status == "issued":
        ledger = db.scalar(select(m.RefundLedger).where(m.RefundLedger.proposal_id == proposal_id))
        return ("issued" if ledger else "inconsistent"), approval.id
    return ("approved" if order.version == proposal.order_version else "stale"), approval.id


def build_approval_graph(db: Session, checkpointer):
    def wait_for_supervisor(state: ApprovalState):
        # Resume payload has no authority. Verification reads the committed SQL facts.
        interrupt({"kind": "supervisor_decision", "proposal_id": state["proposal_id"]})
        return {}

    def verify(state: ApprovalState):
        status, approval_id = _status(db, state["proposal_id"])
        return {"status": status, "approval_id": approval_id}

    def next_step(state: ApprovalState):
        return "wait_for_supervisor" if state["status"] == "pending" else END

    graph = StateGraph(ApprovalState)
    graph.add_node("wait_for_supervisor", wait_for_supervisor)
    graph.add_node("verify", verify)
    graph.add_edge(START, "wait_for_supervisor")
    graph.add_edge("wait_for_supervisor", "verify")
    graph.add_conditional_edges("verify", next_step)
    return graph.compile(checkpointer=checkpointer)


def _config(proposal_id: str) -> dict:
    return {"configurable": {"thread_id": "approval:" + proposal_id}}


def start_approval_wait(db: Session, proposal_id: str) -> str:
    if not settings.database_url.startswith("postgresql"):
        return "sql_only"
    with parent_checkpointer() as checkpointer:
        graph = build_approval_graph(db, checkpointer)
        snapshot = graph.get_state(_config(proposal_id))
        if snapshot.values:
            if snapshot.values.get("proposal_id") != proposal_id:
                raise DomainError("approval_checkpoint_conflict", "Approval checkpoint is inconsistent", 503)
            return snapshot.values.get("status", "pending")
        result = graph.invoke({"proposal_id": proposal_id}, _config(proposal_id))
        if not result.get("__interrupt__"):
            raise DomainError("approval_checkpoint_missing", "Approval wait was not saved", 503)
        return "pending"


def resume_approval_wait(db: Session, proposal_id: str) -> str:
    if not settings.database_url.startswith("postgresql"):
        return _status(db, proposal_id)[0]
    with parent_checkpointer() as checkpointer:
        graph = build_approval_graph(db, checkpointer)
        snapshot = graph.get_state(_config(proposal_id))
        if not snapshot.values:
            # A failed post-commit checkpoint can be recovered on the next
            # idempotent decision request.
            graph.invoke({"proposal_id": proposal_id}, _config(proposal_id))
            snapshot = graph.get_state(_config(proposal_id))
        if snapshot.values.get("proposal_id") != proposal_id:
            raise DomainError("approval_checkpoint_conflict", "Approval checkpoint is inconsistent", 503)
        if snapshot.next:
            graph.invoke(Command(resume={"proposal_id": proposal_id}), _config(proposal_id))
        status, _ = _status(db, proposal_id)
        if status not in {"approved", "rejected", "issued"}:
            raise DomainError("approval_resume_incomplete", "Approval could not be verified", 503)
        completed = graph.get_state(_config(proposal_id))
        expected_checkpoint_status = {"approved", "issued"} if status == "issued" else {status}
        if completed.next or completed.values.get("status") not in expected_checkpoint_status:
            raise DomainError("approval_resume_incomplete", "Approval checkpoint did not complete", 503)
        return status


def reconcile_stale_approval_wait(db: Session, proposal_id: str) -> str:
    if _status(db, proposal_id)[0] != "stale":
        raise DomainError("approval_not_stale", "Proposal is not stale", 409)
    if not settings.database_url.startswith("postgresql"):
        return "stale"
    with parent_checkpointer() as checkpointer:
        graph = build_approval_graph(db, checkpointer)
        snapshot = graph.get_state(_config(proposal_id))
        if not snapshot.values:
            # Older proposals may predate this graph; SQL status still controls them.
            return "stale"
        if snapshot.values.get("proposal_id") != proposal_id:
            raise DomainError("approval_checkpoint_conflict", "Approval checkpoint is inconsistent", 503)
        if snapshot.next:
            result = graph.invoke(Command(resume={"reason": "proposal_superseded"}), _config(proposal_id))
            if result.get("status") != "stale" or graph.get_state(_config(proposal_id)).next:
                raise DomainError("approval_resume_incomplete", "Stale approval wait was not closed", 503)
    return "stale"
