"""Untrusted bounded read plans; authorization arguments never come from models."""
from __future__ import annotations

import json

from .openrouter import ModelUnavailable, call_structured, configured
from .policy_retrieval import in_policy_scope
from .prompts import PromptRegistry
from .schemas import DelegationTask, OrderToolPlan, PolicyRetrievalPlan


def enabled(release_id: str) -> bool:
    if not configured():
        return False
    return {"order_tool_plan", "policy_retrieval_plan"}.issubset(PromptRegistry(release_id).manifest["prompts"])


def plan_order(task: DelegationTask, release_id: str) -> OrderToolPlan:
    fallback = OrderToolPlan(tools=["get_order"] if task.order_read_scope == "status" else ["get_order", "track_shipment"])
    if not enabled(release_id):
        return fallback
    context = {"question": task.question_scope, "required_evidence": task.order_read_scope}
    try:
        return call_structured("order_plan", {"question": json.dumps(context, ensure_ascii=False)}, OrderToolPlan, release_id)
    except ModelUnavailable:
        return fallback


def plan_policy(task: DelegationTask, release_id: str) -> PolicyRetrievalPlan:
    # A rewrite cannot turn an unrelated user question into policy evidence.
    if not in_policy_scope(task.question_scope):
        return PolicyRetrievalPlan(queries=[])
    fallback = PolicyRetrievalPlan(queries=[task.question_scope])
    if not enabled(release_id):
        return fallback
    try:
        return call_structured("policy_plan", {"question": task.question_scope}, PolicyRetrievalPlan, release_id)
    except ModelUnavailable:
        return fallback
