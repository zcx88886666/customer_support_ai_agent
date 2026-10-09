from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError

from . import domain as d, models as m, specialist_planning as planning
from .schemas import ChatInput, DelegationTask, RouteDecision, SpecialistFinding, SpecialistReview, OrderToolPlan, PolicyRetrievalPlan
from .telemetry import tracer, current_trace_id
from .openrouter import configured as model_configured, call_structured, ModelUnavailable
from .config import settings
from .policy_retrieval import retrieve
from .commerce_client import read_order, read_selected_order_tools, CommerceUnavailable
from .memory import list_preferences
from .db import session_read_lock
from .delegation import TaskRuns, merge_findings
from .request_budget import BudgetExceeded, BudgetLimits, RequestBudget, budget_scope, current_budget


class AgentState(TypedDict, total=False):
    thread_id: str
    customer_id: str
    text: str
    order_id: str | None
    shipment_id: str | None
    bundle_id: str
    order_version: int | None
    plan_revision: int
    intents: list[str]
    route: dict
    tasks: list[dict]
    dispatch_tasks: list[dict]
    reuse_findings: dict[str, dict]
    findings: Annotated[list[dict], merge_findings]
    verified_findings: list[dict]
    answer: str
    status: str
    replan_reason: str
    mode: str
    route_candidate: dict


class SpecialistState(TypedDict, total=False):
    task: dict
    finding: dict
    read_plan: dict


def classify(text: str) -> RouteDecision:
    lower = text.lower()
    return_action = any(term in lower for term in ("申请退货", "我要退货", "我要退这", "提交退货",
                                                "改为退订单", "改成退订单", "return request"))
    policy_question = any(term in lower for term in ("政策", "规则", "七天", "七日", "policy", "eligible"))
    shipment_question = any(term in lower for term in ("包裹", "物流", "配送", "shipment", "delivery"))
    high_risk_word = any(term in lower for term in ("退款", "refund", "取消", "cancel", "投诉", "complaint"))
    refund_policy_inquiry = (shipment_question and policy_question
                             and any(term in lower for term in ("退款规则", "退款政策", "退款审批政策", "refund policy", "refund rules"))
                             and not any(term in lower for term in ("我要退款", "请退款", "帮我退款", "给我退款", "马上退款", "立即退款", "执行退款", "issue refund", "refund me")))
    if lower.strip(" ！!。.，,？?") in {"你好", "您好", "hello", "hi"}:
        return RouteDecision(route="knowledge", intents=[])
    if any(term in lower for term in ("人工", "真人客服", "human agent")):
        return RouteDecision(route="human_handoff", intents=["human_request"])
    # A question about an undelivered parcel is an inquiry. Model intent labels
    # cannot turn it into a return submission that asks for item/quantity slots.
    if (any(term in lower for term in ("包裹", "物流", "配送", "shipment", "delivery"))
            and any(term in lower for term in ("没到", "未签收", "not delivered", "not arrived"))
            and any(term in lower for term in ("能退", "可以退", "退吗", "return"))
            and not any(term in lower for term in ("我要退", "申请退货", "提交退货", "确认提交"))):
        return RouteDecision(route="knowledge", intents=["shipment_tracking", "policy_qa"])
    if (any(term in lower for term in ("能退吗", "可以退吗", "可退吗", "can i return", "eligible for return"))
            and not any(term in lower for term in ("我要退", "申请退货", "提交退货", "确认提交", "return request", "退款", "refund", "取消", "投诉", "人工"))):
        return RouteDecision(route="knowledge", intents=["policy_qa"])
    if model_configured():
        try:
            candidate = call_structured("intent", {"message": text[:1000]}, RouteDecision, settings.prompt_release)
            if any(term in lower for term in ("退款", "refund")) and "refund_request" not in candidate.intents and not refund_policy_inquiry:
                return RouteDecision(route="human_handoff", intents=["refund_request"], uncertainty="high_risk_intent_conflict")
            if any(term in lower for term in ("取消", "cancel")) and "cancel_request" not in candidate.intents:
                return RouteDecision(route="human_handoff", intents=["cancel_request"], uncertainty="high_risk_intent_conflict")
            if any(term in lower for term in ("投诉", "complaint")) and "complaint" not in candidate.intents:
                return RouteDecision(route="human_handoff", intents=["complaint"], uncertainty="high_risk_intent_conflict")
            if return_action and "return_request" not in candidate.intents:
                return RouteDecision(route="human_handoff", intents=["return_request"], uncertainty="high_risk_intent_conflict")
            intents = list(candidate.intents)
            if not return_action and "return_request" in intents:
                intents = [intent for intent in intents if intent != "return_request"]
                if policy_question and "policy_qa" not in intents:
                    intents.append("policy_qa")
            if "refund_request" in intents and not policy_question:
                intents = [intent for intent in intents if intent != "policy_qa"]
            if "cancel_request" in intents:
                intents = [intent for intent in intents if intent != "order_status"]
            if "complaint" in intents:
                intents = [intent for intent in intents if intent != "shipment_tracking"]
            if shipment_question and not high_risk_word and not any(term in lower for term in ("订单", "order")) and "order_status" in intents:
                intents = [intent for intent in intents if intent != "order_status"]
                if "shipment_tracking" not in intents:
                    intents.append("shipment_tracking")
            if shipment_question and intents == ["unknown"] and not high_risk_word and not return_action:
                intents = ["shipment_tracking"] + (["policy_qa"] if policy_question else [])
            if shipment_question and policy_question and not return_action and not any(intent in intents for intent in ("cancel_request", "complaint")):
                # Explicitly mixed read questions need both verified sources even
                # when the model labels only one. Keep a refund intent if present.
                intents = [intent for intent in intents if intent not in ("order_status", "unknown")]
                for intent in ("shipment_tracking", "policy_qa"):
                    if intent not in intents:
                        intents.append(intent)
            if not intents:
                return RouteDecision(route="clarify", intents=["unknown"], uncertainty="unverified_action")
            if candidate.route == "human_handoff" and intents == ["unknown"]:
                return RouteDecision(route="clarify", intents=["unknown"], uncertainty="intent_unclear")
            if candidate.route in {"clarify", "out_of_scope"} and shipment_question and not high_risk_word and not return_action:
                return RouteDecision(route="knowledge", intents=["shipment_tracking"] + (["policy_qa"] if policy_question else []))
            candidate = candidate.model_copy(update={"intents": intents[:3]})
            if refund_policy_inquiry:
                return candidate.model_copy(update={"route": "after_sales"})
            if any(intent in intents for intent in ("return_request", "refund_request", "cancel_request", "complaint")) and candidate.route == "knowledge":
                candidate = candidate.model_copy(update={"route": "after_sales"})
            if candidate.intents and set(candidate.intents) <= {"shipment_tracking", "order_status", "policy_qa"}:
                return candidate.model_copy(update={"route": "knowledge"})
            return candidate
        except ModelUnavailable:
            return RouteDecision(route="human_handoff", intents=["unknown"], uncertainty="model_unavailable")
    intents: list[str] = []
    if any(term in lower for term in ("物流", "包裹", "配送", "shipment", "delivery", "arrive")):
        intents.append("shipment_tracking")
    if any(term in lower for term in ("订单", "order")) and "shipment_tracking" not in intents:
        intents.append("order_status")
    if any(term in lower for term in ("政策", "规则", "七天", "七日", "无理由", "能退", "可以退", "policy", "eligible")):
        intents.append("policy_qa")
    if return_action:
        intents.append("return_request")
    if any(term in lower for term in ("退款", "refund")):
        intents.append("refund_request")
    if any(term in lower for term in ("投诉", "complaint")):
        intents.append("complaint")
    if any(term in lower for term in ("取消", "cancel")):
        intents.append("cancel_request")
    if "cancel_request" in intents:
        intents = [intent for intent in intents if intent != "order_status"]
    if "complaint" in intents:
        intents = [intent for intent in intents if intent != "shipment_tracking"]
    if not intents:
        return RouteDecision(route="clarify", intents=["unknown"], uncertainty="intent_unclear")
    return RouteDecision(route="after_sales" if any(x in intents for x in ("return_request", "refund_request", "cancel_request", "complaint")) else "knowledge", intents=intents[:3])


def safe_scope(text: str) -> str:
    """Only keep task keywords in graph checkpoints and delegation contracts."""
    terms = ("包裹", "没到", "能退", "可以退", "退吗", "我要退", "物流", "配送", "政策", "规则", "七天", "七日", "无理由", "订单", "退货", "退款", "投诉", "取消", "签收", "仓库", "质检", "例外", "商品", "实付", "审批", "shipment", "delivery", "return", "refund", "policy", "order", "warehouse", "inspection")
    return " ".join(term for term in terms if term.lower() in text.lower())[:160]


def review_evidence(task_name: str, question: str, evidence: list[tuple[str, dict]]) -> list[str]:
    """Let the model rank read-only evidence; reject unknown IDs and keep all facts."""
    if not model_configured() or not evidence:
        return []
    aliases = {f"e{index}": source_id for index, (source_id, _) in enumerate(evidence, 1)}
    context = {"question": question, "verified_evidence": [{"ref": alias, **facts} for alias, (_, facts) in zip(aliases, evidence)], "instruction": "Select relevant evidence refs only. Unresolved conditions are advisory; do not infer eligibility or money."}
    try:
        review = call_structured(task_name, {"question": json.dumps(context, ensure_ascii=False)}, SpecialistReview, settings.prompt_release)
    except ModelUnavailable:
        return []
    if not isinstance(review, SpecialistReview):
        return []
    refs = review.selected_evidence
    if not refs or len(refs) != len(set(refs)) or any(ref not in aliases for ref in refs):
        return []
    return [aliases[ref] for ref in refs]


def _shipment_delivery_conflict(status: str, delivered_at: object) -> bool:
    return (status == "delivered" and not delivered_at) or (
        status in {"in_transit", "shipped"} and delivered_at is not None)


def validate_finding(db: Session, customer_id: str, task: DelegationTask, finding: SpecialistFinding, current_revision: int) -> bool:
    if finding.task_id != task.task_id or finding.plan_revision != current_revision or task.plan_revision != current_revision or finding.status != "ok":
        return False
    if d.aware(finding.queried_at) > d.aware(task.deadline):
        return False
    if finding.model_reviewed != bool(finding.reviewed_source_ids) or not set(finding.reviewed_source_ids).issubset(finding.source_ids) or len(finding.reviewed_source_ids) != len(set(finding.reviewed_source_ids)):
        return False
    if task.specialist == "policy":
        if set(finding.facts) != {"window_days", "clauses"}:
            return False
        bundle = db.get(m.PolicyBundle, task.policy_bundle_id)
        if not bundle or finding.source_version != bundle.id or finding.facts.get("window_days") != bundle.window_days or not finding.source_ids:
            return False
        clauses = db.scalars(select(m.PolicyClause).where(m.PolicyClause.bundle_id == bundle.id, m.PolicyClause.id.in_(finding.source_ids))).all()
        if len(finding.source_ids) != len(set(finding.source_ids)) or len(clauses) != len(finding.source_ids):
            return False
        claimed = finding.facts.get("clauses")
        if not isinstance(claimed, list) or len(claimed) != len(clauses):
            return False
        actual = {clause.id: {"id": clause.id, "title": clause.title, "body": clause.body} for clause in clauses}
        return all(isinstance(clause, dict) and set(clause) == {"id", "title", "body"} and isinstance(clause["id"], str) and actual.get(clause["id"]) == clause for clause in claimed) and {clause["id"] for clause in claimed} == set(finding.source_ids)
    if task.specialist == "order":
        if set(finding.facts) != {"order_status", "shipment_status", "delivered_at"}:
            return False
        try:
            order = d.owned_order(db, customer_id, task.verified_order_ref)
        except d.DomainError:
            return False
        if task.order_read_scope == "status" and finding.source_ids == [order.id]:
            return finding.source_version == str(order.version) and finding.facts == {
                "order_status": order.status, "shipment_status": None, "delivered_at": None}
        shipments = db.scalars(select(m.Shipment).where(m.Shipment.order_id == order.id)).all()
        shipment = next((row for row in shipments if row.id == task.verified_shipment_ref), None) if task.verified_shipment_ref else shipments[0] if len(shipments) == 1 else None
        if shipment is None or finding.source_version != str(order.version) or finding.source_ids != [order.id, shipment.id]:
            return False
        if _shipment_delivery_conflict(shipment.status, shipment.delivered_at):
            return False
        reported_delivery = finding.facts.get("delivered_at")
        if shipment.delivered_at is None:
            delivery_matches = reported_delivery is None
        elif not isinstance(reported_delivery, str):
            delivery_matches = False
        else:
            try:
                delivery_matches = d.aware(datetime.fromisoformat(reported_delivery)) == d.aware(shipment.delivered_at)
            except ValueError:
                delivery_matches = False
        return finding.facts.get("order_status") == order.status and finding.facts.get("shipment_status") == shipment.status and delivery_matches
    return False


def _order_finding_snapshot_mismatch(
        db: Session, customer_id: str, task: DelegationTask,
        finding: SpecialistFinding, current_revision: int) -> bool:
    """Identify a changed owned source, without retrying foreign or malformed references."""
    if (task.specialist != "order" or finding.status != "ok" or finding.task_id != task.task_id
            or finding.plan_revision != current_revision or task.plan_revision != current_revision
            or d.aware(finding.queried_at) > d.aware(task.deadline)
            or set(finding.facts) != {"order_status", "shipment_status", "delivered_at"}):
        return False
    try:
        order = d.owned_order(db, customer_id, task.verified_order_ref)
    except d.DomainError:
        return False
    if finding.source_version != str(order.version):
        return False
    if task.order_read_scope == "status":
        return (finding.source_ids == [order.id]
                and finding.facts["shipment_status"] is None
                and finding.facts["delivered_at"] is None
                and finding.facts["order_status"] != order.status)
    shipments = db.scalars(select(m.Shipment).where(m.Shipment.order_id == order.id)).all()
    shipment = next((row for row in shipments if row.id == task.verified_shipment_ref), None) if task.verified_shipment_ref else shipments[0] if len(shipments) == 1 else None
    if shipment is None or finding.source_ids != [order.id, shipment.id]:
        return False
    if (finding.facts["order_status"] != order.status
            or finding.facts["shipment_status"] != shipment.status):
        return True
    reported_delivery = finding.facts["delivered_at"]
    if shipment.delivered_at is None:
        return reported_delivery is not None
    if not isinstance(reported_delivery, str):
        return True
    try:
        return d.aware(datetime.fromisoformat(reported_delivery)) != d.aware(shipment.delivered_at)
    except ValueError:
        return True


def build_policy_graph(db: Session):
    read_lock = session_read_lock(db)

    def plan(state: SpecialistState):
        task = DelegationTask.model_validate(state["task"])
        return {"read_plan": planning.plan_policy(task, settings.prompt_release).model_dump()}

    def inspect(state: SpecialistState):
        task = DelegationTask.model_validate(state["task"])
        queries = PolicyRetrievalPlan.model_validate(state["read_plan"]).queries
        calls = 0
        with read_lock:
            bundle = db.get(m.PolicyBundle, task.policy_bundle_id)
            if not bundle:
                finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="incomplete", queried_at=datetime.now(timezone.utc), unresolved=["policy_bundle_missing"])
                return {"finding": finding.model_dump(mode="json")}
            bundle_id, window_days = bundle.id, bundle.window_days
            by_id = {}
            for query in queries:
                calls += 1
                # Anchor every rewrite to the original, server-filtered scope.
                for clause in retrieve(db, bundle_id, task.question_scope + " " + query, limit=5):
                    by_id.setdefault(clause.id, {"id": clause.id, "title": clause.title, "body": clause.body})
            clauses = list(by_id.values())[:5]
        if not clauses:
            finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="incomplete", queried_at=datetime.now(timezone.utc), unresolved=["policy_evidence_missing"], tool_calls=calls)
        else:
            ranked = review_evidence("policy_agent", task.question_scope, [(c["id"], {"title": c["title"][:120], "body": c["body"][:350]}) for c in clauses])
            order = {source_id: index for index, source_id in enumerate(ranked)}
            clauses.sort(key=lambda clause: order.get(clause["id"], len(ranked)))
            finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="ok", facts={"window_days": window_days, "clauses": clauses}, source_ids=[c["id"] for c in clauses], source_version=bundle_id, queried_at=datetime.now(timezone.utc), tool_calls=calls, model_reviewed=bool(ranked), reviewed_source_ids=ranked)
        return {"finding": finding.model_dump(mode="json")}
    graph = StateGraph(SpecialistState)
    graph.add_node("plan_policy_reads", plan)
    graph.add_node("inspect_policy", inspect)
    graph.add_edge(START, "plan_policy_reads")
    graph.add_edge("plan_policy_reads", "inspect_policy")
    graph.add_edge("inspect_policy", END)
    return graph.compile()


def build_order_graph(db: Session, customer_id: str, access_token: str | None = None):
    read_lock = session_read_lock(db)

    def plan(state: SpecialistState):
        task = DelegationTask.model_validate(state["task"])
        return {"read_plan": planning.plan_order(task, settings.prompt_release).model_dump()}

    def inspect(state: SpecialistState):
        task = DelegationTask.model_validate(state["task"])
        tools = OrderToolPlan.model_validate(state["read_plan"]).tools
        if not tools:
            finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="incomplete", queried_at=datetime.now(timezone.utc), unresolved=["order_evidence_missing"])
            return {"finding": finding.model_dump(mode="json")}
        if settings.auth_mode == "oidc":
            try:
                if tools == ["get_order", "track_shipment"]:
                    order, shipments = read_order(access_token, task.verified_order_ref)
                else:
                    selected = read_selected_order_tools(access_token, task.verified_order_ref, tools)
                    order, shipments = selected.get("get_order"), selected.get("track_shipment", [])
            except (CommerceUnavailable, KeyError, TypeError) as error:
                finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="error", queried_at=datetime.now(timezone.utc), unresolved=["commerce_unavailable"], tool_calls=getattr(error, "tool_calls", len(tools)))
                return {"finding": finding.model_dump(mode="json")}
        else:
            with read_lock:
                owned = d.owned_order(db, customer_id, task.verified_order_ref)
                order = {"id": owned.id, "status": owned.status, "version": owned.version} if "get_order" in tools else None
                shipments = [{"id": row.id, "status": row.status, "delivered_at": row.delivered_at.isoformat() if row.delivered_at else None}
                             for row in db.scalars(select(m.Shipment).where(m.Shipment.order_id == owned.id)).all()] if "track_shipment" in tools else []
        shipment = next((row for row in shipments if row.get("id") == task.verified_shipment_ref), None) if task.verified_shipment_ref else shipments[0] if len(shipments) == 1 else None
        complete = order is not None and (shipment is not None or task.order_read_scope == "status")
        if not complete:
            finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="incomplete", queried_at=datetime.now(timezone.utc), unresolved=["order_evidence_missing"], tool_calls=len(tools))
        else:
            try:
                if shipment and _shipment_delivery_conflict(shipment["status"], shipment.get("delivered_at")):
                    finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision,
                                                status="conflict", queried_at=datetime.now(timezone.utc),
                                                unresolved=["shipment_delivery_conflict"], tool_calls=len(tools))
                    return {"finding": finding.model_dump(mode="json")}
                facts = {"order_status": order["status"], "shipment_status": shipment["status"] if shipment else None,
                         "delivered_at": shipment.get("delivered_at") if shipment else None}
                evidence = [(order["id"], {"order_status": facts["order_status"]})]
                if shipment:
                    evidence.append((shipment["id"], {"shipment_status": facts["shipment_status"], "delivered_at": facts["delivered_at"]}))
                finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="ok", facts=facts,
                    source_ids=[ref for ref, _ in evidence], source_version=str(order["version"]), queried_at=datetime.now(timezone.utc),
                    tool_calls=len(tools))
                with read_lock:
                    verified = validate_finding(db, customer_id, task, finding, task.plan_revision)
                if not verified:
                    finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision,
                                                status="conflict", queried_at=datetime.now(timezone.utc),
                                                unresolved=["order_source_mismatch"], tool_calls=len(tools))
                    return {"finding": finding.model_dump(mode="json")}
                ranked = review_evidence("order_agent", task.question_scope, evidence)
                finding = finding.model_copy(update={"model_reviewed": bool(ranked), "reviewed_source_ids": ranked})
            except (KeyError, TypeError):
                finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="error", queried_at=datetime.now(timezone.utc), unresolved=["commerce_unavailable"], tool_calls=len(tools))
        return {"finding": finding.model_dump(mode="json")}
    graph = StateGraph(SpecialistState)
    graph.add_node("plan_order_reads", plan)
    graph.add_node("inspect_order", inspect)
    graph.add_edge(START, "plan_order_reads")
    graph.add_edge("plan_order_reads", "inspect_order")
    graph.add_edge("inspect_order", END)
    return graph.compile()


def build_coordinator(db: Session, customer_id: str, mode: str, checkpointer=None, access_token: str | None = None, language: str = "zh"):
    policy_graph = build_policy_graph(db)
    order_graph = build_order_graph(db, customer_id, access_token)
    request_budget = current_budget() or RequestBudget()
    task_runs = TaskRuns(request_budget, now=lambda: datetime.now(timezone.utc))

    def say(zh: str, en: str) -> str:
        return en if language == "en" else zh

    def route(state: AgentState):
        decision = RouteDecision.model_validate(state["route_candidate"]) if state.get("route_candidate") else classify(state["text"])
        intents = decision.intents
        order_id = state.get("order_id")
        if decision.route == "human_handoff":
            return {"route": decision.model_dump(), "intents": intents, "status": "handoff", "answer": say("已转人工处理。", "I have referred this to a human support agent.")}
        if "complaint" in intents:
            return {"route": decision.model_dump(), "intents": intents, "status": "handoff", "answer": say("投诉已转人工处理。", "I have referred your complaint to a human support agent.")}
        if order_id:
            order = d.owned_order(db, customer_id, order_id)
            bundle_id, version = order.policy_bundle_id, order.version
        else:
            bundle_id, version = d.active_policy(db).id, None
        order_needed = any(x in intents for x in ("shipment_tracking", "order_status", "return_request", "refund_request", "cancel_request"))
        if order_needed and not order_id:
            return {"route": decision.model_dump(), "intents": intents, "status": "clarify", "answer": say("请提供要查询的订单编号。", "Please provide the order number you want to check."), "bundle_id": bundle_id}
        return {"route": decision.model_dump(), "intents": intents, "bundle_id": bundle_id, "order_version": version, "status": "ready"}

    def dispatch(state: AgentState):
        try:
            # Sequential mode must allow the second task to wait for the first;
            # each invocation still gets at most ten seconds within this limit.
            timeout = min(20 if mode == "single" else 10, request_budget.remaining_seconds())
        except BudgetExceeded:
            return {"status": "handoff", "answer": say("自动处理资源上限已达到，请联系人工客服。", "The automatic processing limit has been reached. Please contact a human support agent.")}
        intents = state["intents"]
        needs_order = any(x in intents for x in ("shipment_tracking", "order_status", "return_request", "refund_request", "cancel_request"))
        needs_policy = "policy_qa" in intents or "return_request" in intents
        if "shipment_tracking" in intents and any(term in state["text"] for term in ("能退", "可以退", "退吗")):
            needs_policy = True
        if mode == "single" and needs_order and needs_policy:
            # Baseline still gathers both facts; it does so in one node.
            pass
        tasks = []
        dispatched = []
        reused = []
        for specialist, needed in (("order", needs_order), ("policy", needs_policy)):
            if needed:
                read_scope = "shipment" if any(intent in intents for intent in ("shipment_tracking", "return_request", "refund_request", "cancel_request")) else "status"
                task = DelegationTask(task_id=uuid4().hex, thread_id=state["thread_id"], plan_revision=state["plan_revision"], specialist=specialist, question_scope=state["text"][:160], verified_order_ref=state.get("order_id") if specialist == "order" else None, verified_shipment_ref=state.get("shipment_id") if specialist == "order" else None, policy_bundle_id=state["bundle_id"] if specialist == "policy" else None, evidence_version_hint=state.get("order_version"), deadline=datetime.now(timezone.utc) + timedelta(seconds=timeout), order_read_scope=read_scope)
                tasks.append(task.model_dump(mode="json"))
                prior = state.get("reuse_findings", {}).get(specialist)
                if prior:
                    finding = SpecialistFinding.model_validate({**prior, "task_id": task.task_id, "plan_revision": task.plan_revision})
                    if validate_finding(db, customer_id, task, finding, state["plan_revision"]):
                        reused.append(finding.model_dump(mode="json"))
                        continue
                dispatched.append(task.model_dump(mode="json"))
        return {"tasks": tasks, "dispatch_tasks": dispatched, "findings": reused}

    def fanout(state: AgentState):
        if state.get("status") != "ready":
            return "answer"
        if not state.get("dispatch_tasks"):
            return "answer"
        if mode == "single":
            return "single"
        return [Send("specialist", {"tasks": [task], "findings": [], "bundle_id": state["bundle_id"], "order_id": state.get("order_id"), "shipment_id": state.get("shipment_id"), "order_version": state.get("order_version"), "plan_revision": state["plan_revision"], "thread_id": state["thread_id"], "customer_id": state["customer_id"], "text": state["text"], "intents": state["intents"], "route": state["route"], "status": state["status"], "mode": mode}) for task in state["dispatch_tasks"]]

    def execute_specialist(task: DelegationTask):
        with tracer().start_as_current_span("specialist." + task.specialist) as span:
            span.set_attribute("agent_role", task.specialist)
            span.set_attribute("task_id", task.task_id)
            span.set_attribute("plan_revision", task.plan_revision)
            span.set_attribute("policy_bundle_id", task.policy_bundle_id or "")
            if task.deadline < datetime.now(timezone.utc):
                finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="error", queried_at=datetime.now(timezone.utc), unresolved=["deadline_expired"])
            else:
                graph = policy_graph if task.specialist == "policy" else order_graph
                effective_deadline = min(task.deadline, datetime.now(timezone.utc) + timedelta(seconds=10))
                with budget_scope(request_budget, timeout_seconds=(effective_deadline - datetime.now(timezone.utc)).total_seconds()):
                    finding = SpecialistFinding.model_validate(graph.invoke({"task": task.model_dump(mode="json")})["finding"])
                if datetime.now(timezone.utc) > effective_deadline:
                    finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="error", queried_at=datetime.now(timezone.utc), unresolved=["deadline_expired"])
        return finding.model_dump(mode="json")

    def specialist(state: AgentState):
        task = DelegationTask.model_validate(state["tasks"][0])
        return {"findings": [task_runs.invoke(task, lambda: execute_specialist(task))]}

    def single(state: AgentState):
        findings = []
        # The owned order and bundle are already selected by the server. Read
        # independent local policy before waiting on an external logistics tool
        # so its timeout cannot consume the queued policy task's deadline.
        for task in sorted(state["dispatch_tasks"], key=lambda value: value["specialist"] != "policy"):
            findings.extend(specialist({"tasks": [task]})["findings"])
        return {"findings": findings}

    def answer(state: AgentState):
        if state.get("status") != "ready":
            return {}
        validated = []
        tasks = {task["task_id"]: DelegationTask.model_validate(task) for task in state.get("tasks", [])}
        for raw in state.get("findings", []):
            finding = SpecialistFinding.model_validate(raw)
            task = tasks.get(finding.task_id)
            if task and finding.status == "conflict" and finding.plan_revision == state["plan_revision"]:
                return {"status": "replan", "replan_reason": "specialist_conflict"}
            if not task:
                continue
            if not validate_finding(db, customer_id, task, finding, state["plan_revision"]):
                if _order_finding_snapshot_mismatch(db, customer_id, task, finding, state["plan_revision"]):
                    return {"status": "replan", "replan_reason": "order_evidence_mismatch"}
                continue
            validated.append((task.specialist, finding))
        # Refresh after read-only branches; another transaction may have changed
        # the order while the specialists were working.
        if state.get("order_id"):
            order = d.owned_order(db, customer_id, state["order_id"])
            db.refresh(order)
            if order.version != state.get("order_version") or order.policy_bundle_id != state["bundle_id"]:
                return {"status": "replan", "replan_reason": "order_changed"}
        elif d.active_policy(db).id != state["bundle_id"]:
            return {"status": "replan", "replan_reason": "policy_changed"}
        public_findings = [finding.model_dump(mode="json") for _, finding in validated]
        for raw in state.get("findings", []):
            finding = SpecialistFinding.model_validate(raw)
            task = tasks.get(finding.task_id)
            if task and finding.plan_revision == state["plan_revision"] and finding.status in {"error", "incomplete"} and not finding.facts and not finding.source_ids:
                marker = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status=finding.status, queried_at=finding.queried_at, unresolved=["specialist_unavailable" if finding.status == "error" else "evidence_incomplete"], tool_calls=finding.tool_calls)
                public_findings.append(marker.model_dump(mode="json"))
        pieces = []
        for role, finding in validated:
            if role == "order":
                status = finding.facts.get("shipment_status")
                if tasks[finding.task_id].order_read_scope == "status":
                    pieces.append(say(f"查到的订单事实：订单状态为 {finding.facts['order_status']}。", f"Verified order fact: the order status is {finding.facts['order_status']}."))
                else:
                    pieces.append(say(f"查到的订单事实：包裹 {finding.source_ids[1]} 状态为 {status or '未知'}。", f"Verified order fact: package {finding.source_ids[1]} has status {status or 'unknown'}."))
                if len(finding.source_ids) > 1 and status != "delivered" and ("policy_qa" in state["intents"] or "return_request" in state["intents"]):
                    pieces.append(say("包裹尚未确认签收，不能按签收次日起算的退货申请期限直接提交；请联系人工核查配送异常。", "Delivery has not been confirmed. The return window measured from the day after delivery cannot be applied yet; please contact support to check the delivery issue."))
            if role == "policy":
                refs = "、".join(finding.source_ids)
                days = finding.facts["window_days"]
                pieces.append(say(
                    f"适用条款：{refs}。该政策包的一般退货申请期限为签收次日起 {days} 个自然日；具体资格仍需核查商品和订单事实。",
                    f"Relevant policy clauses: {', '.join(finding.source_ids)}. This policy bundle's general return request window is {days} calendar days starting the day after delivery; product and order eligibility still need verification.",
                ))
        if len(validated) < len(state.get("tasks", [])):
            pieces.append(say("部分证据未核实，退货资格和退款需人工复核。", "Some evidence could not be verified. A human must review return eligibility and any refund."))
        if "refund_request" in state["intents"] or ("退款" in state["text"] and any(term in state["text"] for term in ("政策", "规则"))) or ("refund" in state["text"] and "policy" in state["text"]):
            pieces.append(say("退款须待仓库质检、服务端提案及主管批准后才能执行。", "A refund requires warehouse inspection, a server-side proposal, and supervisor approval before it can be issued."))
        if "cancel_request" in state["intents"]:
            pieces.append(say("取消订单请联系人工客服。", "Please contact a human support agent to request an order cancellation."))
        if "complaint" in state["intents"]:
            pieces.append(say("投诉已转人工处理。", "I have referred your complaint to a human support agent."))
        if not pieces:
            pieces.append(say("请补充问题或联系人工客服。", "Please add more detail or contact a human support agent."))
        return {"status": "answered", "answer": " ".join(pieces) if language == "en" else "".join(pieces), "verified_findings": public_findings}

    graph = StateGraph(AgentState)
    graph.add_node("route", route)
    graph.add_node("dispatch", dispatch)
    graph.add_node("specialist", specialist)
    graph.add_node("single", single)
    graph.add_node("answer", answer)
    graph.add_edge(START, "route")
    graph.add_edge("route", "dispatch")
    graph.add_conditional_edges("dispatch", fanout, {"answer": "answer", "single": "single"})
    graph.add_edge("specialist", "answer")
    graph.add_edge("single", "answer")
    graph.add_edge("answer", END)
    return graph.compile(checkpointer=checkpointer)


def run_chat(db: Session, customer_id: str, body: ChatInput, *, access_token: str | None = None) -> dict:
    with budget_scope(RequestBudget()) as budget:
        with tracer().start_as_current_span("agent.request_resources") as span:
            try:
                result = _run_chat(db, customer_id, body, access_token=access_token)
                db.flush()
            except (BudgetExceeded, DBAPIError) as exc:
                if isinstance(exc, DBAPIError) and getattr(exc.orig, "sqlstate", None) != "57014":
                    raise
                # The caller's I/O allowance is exhausted. Invalidate closes
                # locally, discards pending SQL and clears ORM state without
                # attempting another network rollback under that deadline.
                from .db import discard_expired_transaction
                discard_expired_transaction(db)
                budget.stop("database_deadline_expired" if isinstance(exc, DBAPIError) else "deadline_expired")
                span.set_attribute("langfuse.observation.level", "WARNING")
                # Only the handoff transaction gets a short separate deadline.
                # Re-read thread ownership after rollback, and omit unverified
                # request order/slot references. No business action is retried.
                handoff = body.model_copy(update={"order_id": None, "shipment_id": None, "item_id": None,
                                                  "quantity": None, "reason": None, "confirmed": False})
                try:
                    with budget_scope(RequestBudget(BudgetLimits(timeout_seconds=3))):
                        result = _run_chat(db, customer_id, handoff, forced_decision=RouteDecision(
                            route="human_handoff", intents=["unknown"], uncertainty="resource_limit"))
                        db.flush()
                except (BudgetExceeded, DBAPIError):
                    discard_expired_transaction(db)
                    raise d.DomainError("request_deadline_expired", "Please retry or contact human support", 503) from None
            summary = budget.snapshot()
            result["resource_usage"] = summary
            for key in ("llm_attempts", "reported_input_tokens", "reported_output_tokens", "reported_cost_usd", "accounted_tokens", "accounted_cost_usd", "unknown_usage_calls", "elapsed_seconds"):
                span.set_attribute("agent.resources." + key, summary[key])
            if summary["exhausted_reason"]:
                span.set_attribute("agent.resources.exhausted_reason", summary["exhausted_reason"])
            return result


def _run_chat(db: Session, customer_id: str, body: ChatInput, *, access_token: str | None = None, forced_decision: RouteDecision | None = None) -> dict:
    thread = db.get(m.ThreadState, body.thread_id)
    if thread and thread.customer_id != customer_id:
        raise d.DomainError("thread_not_found", "Thread unavailable", 404)
    language_value = list_preferences(db, customer_id).get("language", "").strip().lower()
    language = "en" if language_value in {"english", "en", "en-us"} else "zh"

    def say(zh: str, en: str) -> str:
        return en if language == "en" else zh

    now = datetime.now(timezone.utc)
    previous = thread.state if thread else {}
    pending_expired = bool(thread and previous.get("status") == "clarify" and thread.updated_at and now - d.aware(thread.updated_at) > timedelta(hours=24))
    decision = forced_decision or classify(body.message)
    continuing = previous.get("status") == "clarify" and not pending_expired
    supplied_slot = bool(body.order_id or body.shipment_id or body.item_id or body.quantity is not None or body.reason)
    explicit_confirmation = body.confirmed and any(term in body.message.lower() for term in ("确认", "submit", "confirm"))
    if (continuing and decision.route == "out_of_scope" and decision.intents == ["unknown"]
            and (supplied_slot or explicit_confirmation)):
        decision = RouteDecision(route="clarify", intents=["unknown"], uncertainty="slot_followup")
    if continuing and decision.route == "clarify":
        if previous.get("pending_route"):
            pending_route = RouteDecision.model_validate(previous["pending_route"])
            if pending_route.intents != ["unknown"]:
                decision = pending_route
        elif previous.get("pending_intent") == "return_request":
            decision = RouteDecision(route="after_sales", intents=["return_request"])
    elif continuing and previous.get("pending_intent") == "return_request" and decision.route != "human_handoff" and "return_request" not in decision.intents:
        continuing = False
    old = previous if continuing else ({"order_id": previous.get("order_id")} if not pending_expired else {})
    task_id = old.get("task_id") or uuid4().hex
    revision = int(old.get("plan_revision", 0)) + 1 if continuing else 1
    over_revision_limit = revision > 4
    revision = min(revision, 4)
    order_id = body.order_id or old.get("order_id")
    if decision.route == "human_handoff":
        order_id = old.get("order_id") if not body.order_id else None
    elif order_id:
        d.owned_order(db, customer_id, order_id)
    order_changed = bool(continuing and body.order_id and body.order_id != old.get("order_id"))
    shipment_id = body.shipment_id or (None if order_changed else old.get("shipment_id"))
    if shipment_id:
        shipment = db.get(m.Shipment, shipment_id)
        if not order_id or shipment is None or shipment.order_id != order_id:
            raise d.DomainError("shipment_not_found", "Shipment unavailable", 404)
    item_id = body.item_id or (None if order_changed else old.get("item_id"))
    quantity = body.quantity or (None if order_changed else old.get("quantity"))
    reason = body.reason or (None if order_changed else old.get("reason"))
    if over_revision_limit:
        result = {"status": "handoff", "answer": say("已达到自动处理轮次上限，请联系人工客服。", "The automatic handling limit has been reached. Please contact a human support agent."), "route": decision.model_dump(), "plan_revision": revision, "findings": []}
    elif decision.route == "human_handoff":
        result = {"status": "handoff", "answer": say("已转人工处理。", "I have referred this to a human support agent."), "route": decision.model_dump(), "plan_revision": revision, "findings": []}
    elif decision.intents == ["unknown"]:
        result = {"status": "clarify", "answer": say("请说明要查询的订单、物流或退货问题。", "Please describe your order, delivery, or return question."), "route": decision.model_dump(), "plan_revision": revision, "findings": []}
    elif order_id and any(intent in decision.intents for intent in ("shipment_tracking", "refund_request", "cancel_request")) and not shipment_id and len(ships := db.scalars(select(m.Shipment).where(m.Shipment.order_id == order_id)).all()) > 1:
        result = {"status": "clarify", "answer": say("此订单有多个包裹，请选择要查询的包裹编号。", "This order has multiple packages. Please select the package number you want to check."), "shipment_options": [ship.id for ship in ships], "route": decision.model_dump(), "plan_revision": revision, "findings": []}
    elif "return_request" in decision.intents:
        missing = next((pair for pair in ((not order_id, "请提供订单编号。"), (not item_id, "请提供要退的商品项编号。"), (not quantity, "请提供退货数量。"), (not reason, "请提供退货原因。"), (not body.confirmed, "请明确确认订单、商品、数量、原因并提交退货申请。"), (not body.idempotency_key, "请使用提交按钮生成幂等请求编号。")) if pair[0]), (False, ""))
        if missing[0]:
            translations = {"请提供订单编号。": "Please provide the order number.", "请提供要退的商品项编号。": "Please provide the order item you want to return.", "请提供退货数量。": "Please provide the quantity to return.", "请提供退货原因。": "Please provide the reason for the return.", "请明确确认订单、商品、数量、原因并提交退货申请。": "Please explicitly confirm the order, item, quantity, and reason before submitting the return request.", "请使用提交按钮生成幂等请求编号。": "Please use the submit button to generate a unique request key."}
            result = {"status": "clarify", "answer": say(missing[1], translations[missing[1]]), "route": decision.model_dump(), "plan_revision": revision, "findings": []}
        else:
            try:
                current_budget().remaining_seconds()
            except BudgetExceeded:
                result = {"status": "handoff", "route": decision.model_dump(), "plan_revision": revision, "findings": []}
            else:
                outcome = d.submit_return_or_review(db, customer_id, order_id, item_id, quantity, reason, True,
                                                    body.idempotency_key, now, revision)
                if isinstance(outcome, tuple):
                    ticket, reason_code = outcome
                    result = {"status": "human_review", "answer": say("退货申请需要人工复核，工单已创建；尚未建立退货申请或退款。", "A human review ticket was created. No return or refund has been issued."),
                              "ticket_id": ticket.id, "reason_code": reason_code, "route": decision.model_dump(), "plan_revision": revision, "findings": []}
                else:
                    result = {"status": "return_requested", "answer": say("退货申请已提交，尚未退款。", "Your return request was submitted. No refund has been issued."), "return_id": outcome.id, "route": decision.model_dump(), "plan_revision": revision, "findings": []}
    else:
        from .checkpoint import parent_checkpointer
        replan_count = 0
        reuse_findings: dict[str, dict] = {}
        while True:
            with parent_checkpointer() as checkpointer:
                graph = build_coordinator(db, customer_id, body.agent_mode, checkpointer, access_token, language)
                state = graph.invoke({"thread_id": body.thread_id, "customer_id": customer_id, "text": safe_scope(body.message), "route_candidate": decision.model_dump(), "order_id": order_id, "shipment_id": shipment_id, "plan_revision": revision, "mode": body.agent_mode, "findings": [], "reuse_findings": reuse_findings}, config={"configurable": {"thread_id": f"{customer_id}:{body.thread_id}:t{task_id}:r{revision}"}})
            if state.get("status") != "replan":
                result = {"status": state.get("status"), "answer": state.get("answer"), "route": state.get("route"), "plan_revision": revision, "findings": state.get("verified_findings", []), "agent_mode": body.agent_mode, "replan_count": replan_count}
                break
            if replan_count >= 2 or revision >= 4:
                result = {"status": "handoff", "answer": say("业务事实持续变化，已转人工处理。", "The verified business facts kept changing. I have referred this to a human support agent."), "route": decision.model_dump(), "plan_revision": revision, "findings": [], "agent_mode": body.agent_mode, "replan_count": replan_count}
                break
            replan_count += 1
            tasks_by_id = {task["task_id"]: task["specialist"] for task in state.get("tasks", [])}
            keep = {"policy"} if state.get("replan_reason") in {"order_changed", "order_evidence_mismatch"} else {"order"} if state.get("replan_reason") == "policy_changed" else {"order", "policy"}
            reuse_findings = {tasks_by_id[finding["task_id"]]: finding for finding in state.get("findings", []) if finding.get("status") == "ok" and finding.get("task_id") in tasks_by_id and tasks_by_id[finding["task_id"]] in keep}
            db.expire_all()
            revision += 1
    if result["status"] not in ("return_requested", "human_review"):
        try:
            current_budget().remaining_seconds()
        except BudgetExceeded:
            result = {"status": "handoff", "answer": say("自动处理资源上限已达到，请联系人工客服。", "The automatic processing limit has been reached. Please contact a human support agent."), "route": decision.model_dump(), "plan_revision": revision, "findings": [], "agent_mode": body.agent_mode, "replan_count": result.get("replan_count", 0)}
    if result["status"] == "clarify" and (int(old.get("clarifications", 0)) >= 2 or (decision.intents == ["unknown"] and int(old.get("unknown_clarifications", 0)) >= 1)):
        result = {"status": "handoff", "answer": say("已达到澄清轮次上限，请联系人工客服。", "The clarification limit has been reached. Please contact a human support agent."), "route": decision.model_dump(), "plan_revision": revision, "findings": []}
    if result["status"] == "handoff":
        ticket = db.get(m.Ticket, old.get("ticket_id")) if old.get("ticket_id") else None
        if ticket is None:
            topic = "automatic processing limit" if decision.uncertainty == "resource_limit" else "customer requested support" if decision.route == "human_handoff" else "clarification limit" if "上限" in result["answer"] or "limit has been reached" in result["answer"] else "complaint"
            ticket = m.Ticket(customer_id=customer_id, order_id=order_id, topic=topic)
            db.add(ticket)
            db.flush()
            db.add(m.ConversationMessage(ticket_id=ticket.id, actor_type="customer", body=body.message, created_at=now))
            d.audit(db, customer_id, "create_ticket", "ticket", ticket.id)
        result["ticket_id"] = ticket.id
    safe_state = {"order_id": order_id, "shipment_id": shipment_id, "task_id": task_id, "plan_revision": revision, "status": result["status"], "clarifications": int(old.get("clarifications", 0)) + 1 if result["status"] == "clarify" else 0}
    if result["status"] == "clarify":
        safe_state["pending_route"] = decision.model_dump()
        safe_state["unknown_clarifications"] = int(old.get("unknown_clarifications", 0)) + 1 if decision.intents == ["unknown"] else 0
        if "return_request" in decision.intents:
            safe_state.update({"pending_intent": "return_request", "item_id": item_id, "quantity": quantity, "reason": reason})
    if result.get("ticket_id"):
        safe_state["ticket_id"] = result["ticket_id"]
    if thread:
        thread.state = safe_state
        thread.updated_at = now
    else:
        db.add(m.ThreadState(id=body.thread_id, customer_id=customer_id, state=safe_state))
    result["trace_id"] = current_trace_id()
    return result
