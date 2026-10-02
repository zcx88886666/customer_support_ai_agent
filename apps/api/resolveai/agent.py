from __future__ import annotations

import operator
from datetime import datetime, timedelta, timezone
from typing import Annotated, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import domain as d, models as m
from .schemas import ChatInput, DelegationTask, RouteDecision, SpecialistFinding
from .telemetry import tracer, current_trace_id
from .openrouter import configured as model_configured, call_structured, ModelUnavailable
from .config import settings
from .policy_retrieval import retrieve
from .commerce_client import read_order, CommerceUnavailable


class AgentState(TypedDict, total=False):
    thread_id: str
    customer_id: str
    text: str
    order_id: str | None
    bundle_id: str
    order_version: int | None
    plan_revision: int
    intents: list[str]
    route: dict
    tasks: list[dict]
    findings: Annotated[list[dict], operator.add]
    answer: str
    status: str
    mode: str
    route_candidate: dict


class SpecialistState(TypedDict, total=False):
    task: dict
    finding: dict


def classify(text: str) -> RouteDecision:
    lower = text.lower()
    if any(term in lower for term in ("人工", "真人客服", "human agent")):
        return RouteDecision(route="human_handoff", intents=["human_request"])
    # A question about an undelivered parcel is an inquiry. Model intent labels
    # cannot turn it into a return submission that asks for item/quantity slots.
    if (any(term in lower for term in ("包裹", "物流", "配送", "shipment", "delivery"))
            and any(term in lower for term in ("没到", "未签收", "not delivered", "not arrived"))
            and any(term in lower for term in ("能退", "可以退", "退吗", "return"))
            and not any(term in lower for term in ("我要退", "申请退货", "提交退货", "确认提交"))):
        return RouteDecision(route="knowledge", intents=["shipment_tracking", "policy_qa"])
    if model_configured():
        try:
            candidate = call_structured("intent", {"message": text[:1000]}, RouteDecision, settings.prompt_release)
            if any(term in lower for term in ("退款", "refund")) and "refund_request" not in candidate.intents:
                return RouteDecision(route="human_handoff", intents=["refund_request"], uncertainty="high_risk_intent_conflict")
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
    if any(term in lower for term in ("申请退货", "我要退", "提交退货", "return request")):
        intents.append("return_request")
    if any(term in lower for term in ("退款", "refund")):
        intents.append("refund_request")
    if any(term in lower for term in ("投诉", "complaint")):
        intents.append("complaint")
    if any(term in lower for term in ("取消", "cancel")):
        intents.append("cancel_request")
    if not intents:
        return RouteDecision(route="clarify", intents=["unknown"], uncertainty="intent_unclear")
    return RouteDecision(route="after_sales" if any(x in intents for x in ("return_request", "refund_request", "cancel_request", "complaint")) else "knowledge", intents=intents[:3])


def safe_scope(text: str) -> str:
    """Only keep task keywords in graph checkpoints and delegation contracts."""
    terms = ("包裹", "没到", "能退", "可以退", "退吗", "我要退", "物流", "配送", "政策", "七天", "七日", "无理由", "订单", "退货", "退款", "投诉", "取消", "签收", "shipment", "delivery", "return", "refund", "policy", "order")
    return " ".join(term for term in terms if term.lower() in text.lower())[:160]


def validate_finding(db: Session, customer_id: str, task: DelegationTask, finding: SpecialistFinding, current_revision: int) -> bool:
    if finding.task_id != task.task_id or finding.plan_revision != current_revision or task.plan_revision != current_revision or finding.status != "ok":
        return False
    if task.specialist == "policy":
        if set(finding.facts) != {"window_days", "clauses"}:
            return False
        bundle = db.get(m.PolicyBundle, task.policy_bundle_id)
        if not bundle or finding.source_version != bundle.id or finding.facts.get("window_days") != bundle.window_days or not finding.source_ids:
            return False
        clauses = db.scalars(select(m.PolicyClause).where(m.PolicyClause.bundle_id == bundle.id, m.PolicyClause.id.in_(finding.source_ids))).all()
        if len(clauses) != len(set(finding.source_ids)):
            return False
        return {clause["id"] for clause in finding.facts.get("clauses", [])} == set(finding.source_ids)
    if task.specialist == "order":
        if set(finding.facts) != {"order_status", "shipment_status", "delivered_at"}:
            return False
        try:
            order = d.owned_order(db, customer_id, task.verified_order_ref)
        except d.DomainError:
            return False
        shipments = db.scalars(select(m.Shipment).where(m.Shipment.order_id == order.id)).all()
        if len(shipments) != 1 or finding.source_version != str(order.version) or finding.source_ids != [order.id, shipments[0].id]:
            return False
        shipment = shipments[0]
        return finding.facts.get("order_status") == order.status and finding.facts.get("shipment_status") == shipment.status and finding.facts.get("delivered_at") == (shipment.delivered_at.isoformat() if shipment.delivered_at else None)
    return False


def build_policy_graph(db: Session):
    def inspect(state: SpecialistState):
        task = DelegationTask.model_validate(state["task"])
        bundle = db.get(m.PolicyBundle, task.policy_bundle_id)
        if not bundle:
            finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="incomplete", queried_at=datetime.now(timezone.utc), unresolved=["policy_bundle_missing"])
            return {"finding": finding.model_dump(mode="json")}
        clauses = retrieve(db, bundle.id, task.question_scope, limit=5)
        if not clauses:
            finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="incomplete", queried_at=datetime.now(timezone.utc), unresolved=["policy_evidence_missing"])
        else:
            finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="ok", facts={"window_days": bundle.window_days, "clauses": [{"id": c.id, "title": c.title, "body": c.body} for c in clauses]}, source_ids=[c.id for c in clauses], source_version=bundle.id, queried_at=datetime.now(timezone.utc), tool_calls=1)
        return {"finding": finding.model_dump(mode="json")}
    graph = StateGraph(SpecialistState)
    graph.add_node("inspect_policy", inspect)
    graph.add_edge(START, "inspect_policy")
    graph.add_edge("inspect_policy", END)
    return graph.compile()


def build_order_graph(db: Session, customer_id: str, access_token: str | None = None):
    def inspect(state: SpecialistState):
        task = DelegationTask.model_validate(state["task"])
        if settings.auth_mode == "oidc":
            try:
                order, shipments = read_order(access_token, task.verified_order_ref)
                shipment = shipments[0] if len(shipments) == 1 else None
                finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="ok" if shipment else "incomplete", facts={"order_status": order["status"], "shipment_status": shipment["status"] if shipment else None, "delivered_at": shipment.get("delivered_at") if shipment else None}, source_ids=[order["id"]] + ([shipment["id"]] if shipment else []), source_version=str(order["version"]), queried_at=datetime.now(timezone.utc), unresolved=[] if shipment else ["package_ambiguous_or_missing"], tool_calls=2)
            except (CommerceUnavailable, KeyError, TypeError):
                finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="error", queried_at=datetime.now(timezone.utc), unresolved=["commerce_unavailable"], tool_calls=2)
            return {"finding": finding.model_dump(mode="json")}
        order = d.owned_order(db, customer_id, task.verified_order_ref)
        shipments = db.scalars(select(m.Shipment).where(m.Shipment.order_id == order.id)).all()
        if len(shipments) > 1:
            finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="incomplete", queried_at=datetime.now(timezone.utc), unresolved=["package_ambiguous"], source_version=str(order.version), tool_calls=1)
        else:
            shipment = shipments[0] if shipments else None
            finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="ok" if shipment else "incomplete", facts={"order_status": order.status, "shipment_status": shipment.status if shipment else None, "delivered_at": shipment.delivered_at.isoformat() if shipment and shipment.delivered_at else None}, source_ids=[order.id] + ([shipment.id] if shipment else []), source_version=str(order.version), queried_at=datetime.now(timezone.utc), unresolved=[] if shipment else ["shipment_missing"], tool_calls=1)
        return {"finding": finding.model_dump(mode="json")}
    graph = StateGraph(SpecialistState)
    graph.add_node("inspect_order", inspect)
    graph.add_edge(START, "inspect_order")
    graph.add_edge("inspect_order", END)
    return graph.compile()


def build_coordinator(db: Session, customer_id: str, mode: str, checkpointer=None, access_token: str | None = None):
    policy_graph = build_policy_graph(db)
    order_graph = build_order_graph(db, customer_id, access_token)

    def route(state: AgentState):
        decision = RouteDecision.model_validate(state["route_candidate"]) if state.get("route_candidate") else classify(state["text"])
        intents = decision.intents
        order_id = state.get("order_id")
        if decision.route == "human_handoff":
            return {"route": decision.model_dump(), "intents": intents, "status": "handoff", "answer": "已转人工处理。"}
        if "complaint" in intents:
            return {"route": decision.model_dump(), "intents": intents, "status": "handoff", "answer": "投诉已转人工处理。"}
        if order_id:
            order = d.owned_order(db, customer_id, order_id)
            bundle_id, version = order.policy_bundle_id, order.version
        else:
            bundle_id, version = d.active_policy(db).id, None
        order_needed = any(x in intents for x in ("shipment_tracking", "order_status", "return_request", "refund_request", "cancel_request"))
        if order_needed and not order_id:
            return {"route": decision.model_dump(), "intents": intents, "status": "clarify", "answer": "请提供要查询的订单编号。", "bundle_id": bundle_id}
        return {"route": decision.model_dump(), "intents": intents, "bundle_id": bundle_id, "order_version": version, "status": "ready"}

    def dispatch(state: AgentState):
        intents = state["intents"]
        needs_order = any(x in intents for x in ("shipment_tracking", "order_status", "return_request", "refund_request", "cancel_request"))
        needs_policy = "policy_qa" in intents or "return_request" in intents
        if "shipment_tracking" in intents and any(term in state["text"] for term in ("能退", "可以退", "退吗")):
            needs_policy = True
        if mode == "single" and needs_order and needs_policy:
            # Baseline still gathers both facts; it does so in one node.
            pass
        tasks = []
        for specialist, needed in (("order", needs_order), ("policy", needs_policy)):
            if needed:
                task = DelegationTask(task_id=uuid4().hex, thread_id=state["thread_id"], plan_revision=state["plan_revision"], specialist=specialist, question_scope=state["text"][:160], verified_order_ref=state.get("order_id") if specialist == "order" else None, policy_bundle_id=state["bundle_id"] if specialist == "policy" else None, evidence_version_hint=state.get("order_version"), deadline=datetime.now(timezone.utc) + timedelta(seconds=10))
                tasks.append(task.model_dump(mode="json"))
        return {"tasks": tasks}

    def fanout(state: AgentState):
        if state.get("status") != "ready":
            return "answer"
        if not state.get("tasks"):
            return "answer"
        if mode == "single":
            return "single"
        return [Send("specialist", {"tasks": [task], "findings": [], "bundle_id": state["bundle_id"], "order_id": state.get("order_id"), "order_version": state.get("order_version"), "plan_revision": state["plan_revision"], "thread_id": state["thread_id"], "customer_id": state["customer_id"], "text": state["text"], "intents": state["intents"], "route": state["route"], "status": state["status"], "mode": mode}) for task in state["tasks"]]

    def specialist(state: AgentState):
        task = DelegationTask.model_validate(state["tasks"][0])
        with tracer().start_as_current_span("specialist." + task.specialist) as span:
            span.set_attribute("agent_role", task.specialist)
            span.set_attribute("task_id", task.task_id)
            span.set_attribute("plan_revision", task.plan_revision)
            span.set_attribute("policy_bundle_id", task.policy_bundle_id or "")
            if task.deadline < datetime.now(timezone.utc):
                finding = SpecialistFinding(task_id=task.task_id, plan_revision=task.plan_revision, status="error", queried_at=datetime.now(timezone.utc), unresolved=["deadline_expired"])
            else:
                graph = policy_graph if task.specialist == "policy" else order_graph
                finding = SpecialistFinding.model_validate(graph.invoke({"task": task.model_dump(mode="json")})["finding"])
        return {"findings": [finding.model_dump(mode="json")]}

    def single(state: AgentState):
        findings = []
        for task in state["tasks"]:
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
            if not task or not validate_finding(db, customer_id, task, finding, state["plan_revision"]):
                continue
            validated.append((task.specialist, finding))
        # Recheck after read-only branches, so a changed order cannot validate an old answer.
        if state.get("order_id") and d.owned_order(db, customer_id, state["order_id"]).version != state.get("order_version"):
            return {"status": "clarify", "answer": "订单状态已变化，请重新查询。"}
        pieces = []
        for role, finding in validated:
            if role == "order":
                status = finding.facts.get("shipment_status")
                pieces.append(f"查到的订单事实：包裹状态为 {status or '未知'}。")
                if status != "delivered" and ("policy_qa" in state["intents"] or "return_request" in state["intents"]):
                    pieces.append("包裹尚未确认签收，不能按签收次日起算的七日无理由退货流程直接提交；请联系人工核查配送异常。")
            if role == "policy":
                refs = "、".join(finding.source_ids)
                pieces.append(f"适用条款：{refs}。签收后的申请期限由该政策包确定。")
        if len(validated) < len(state.get("tasks", [])):
            pieces.append("部分证据未核实，退货资格和退款需人工复核。")
        if "refund_request" in state["intents"]:
            pieces.append("退款须待仓库质检、服务端提案及主管批准后才能执行。")
        if "cancel_request" in state["intents"]:
            pieces.append("取消订单请联系人工客服。")
        if "complaint" in state["intents"]:
            pieces.append("投诉已转人工处理。")
        if not pieces:
            pieces.append("请补充问题或联系人工客服。")
        return {"status": "answered", "answer": "".join(pieces)}

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
    thread = db.get(m.ThreadState, body.thread_id)
    if thread and thread.customer_id != customer_id:
        raise d.DomainError("thread_not_found", "Thread unavailable", 404)
    old = thread.state if thread else {}
    revision = min(int(old.get("plan_revision", 0)) + 1, 4)
    if int(old.get("plan_revision", 0)) >= 4:
        return {"status": "handoff", "answer": "已达到自动处理轮次上限，请联系人工客服。", "plan_revision": 4}
    order_id = body.order_id or old.get("order_id")
    decision = classify(body.message)
    if decision.route == "human_handoff":
        order_id = old.get("order_id") if not body.order_id else None
    elif order_id:
        d.owned_order(db, customer_id, order_id)
    if old.get("pending_intent") == "return_request" and decision.route == "clarify":
        decision = RouteDecision(route="after_sales", intents=["return_request"])
    item_id = body.item_id or old.get("item_id")
    quantity = body.quantity or old.get("quantity")
    reason = body.reason or old.get("reason")
    if decision.route == "human_handoff":
        result = {"status": "handoff", "answer": "已转人工处理。", "route": decision.model_dump(), "plan_revision": revision, "findings": []}
    elif "return_request" in decision.intents:
        if int(old.get("clarifications", 0)) >= 3:
            result = {"status": "handoff", "answer": "已达到澄清轮次上限，请联系人工客服。", "route": decision.model_dump(), "plan_revision": revision, "findings": []}
        else:
            missing = next((pair for pair in ((not order_id, "请提供订单编号。"), (not item_id, "请提供要退的商品项编号。"), (not quantity, "请提供退货数量。"), (not reason, "请提供退货原因。"), (not body.confirmed, "请明确确认订单、商品、数量、原因并提交退货申请。")) if pair[0]), (False, ""))
            if missing[0]:
                result = {"status": "clarify", "answer": missing[1], "route": decision.model_dump(), "plan_revision": revision, "findings": []}
            elif not body.idempotency_key:
                result = {"status": "clarify", "answer": "请使用提交按钮生成幂等请求编号。", "route": decision.model_dump(), "plan_revision": revision, "findings": []}
            else:
                request = d.create_return(db, customer_id, order_id, item_id, quantity, reason, True, body.idempotency_key, datetime.now(timezone.utc), revision)
                result = {"status": "return_requested", "answer": "退货申请已提交，尚未退款。", "return_id": request.id, "route": decision.model_dump(), "plan_revision": revision, "findings": []}
    else:
        from .checkpoint import parent_checkpointer
        with parent_checkpointer() as checkpointer:
            graph = build_coordinator(db, customer_id, body.agent_mode, checkpointer, access_token)
            state = graph.invoke({"thread_id": body.thread_id, "customer_id": customer_id, "text": safe_scope(body.message), "route_candidate": decision.model_dump(), "order_id": order_id, "plan_revision": revision, "mode": body.agent_mode, "findings": []}, config={"configurable": {"thread_id": f"{customer_id}:{body.thread_id}:r{revision}"}})
        result = {"status": state.get("status"), "answer": state.get("answer"), "route": state.get("route"), "plan_revision": revision, "findings": state.get("findings", []), "agent_mode": body.agent_mode}
    if result["status"] == "handoff":
        ticket = db.get(m.Ticket, old.get("ticket_id")) if old.get("ticket_id") else None
        if ticket is None:
            ticket = m.Ticket(customer_id=customer_id, order_id=order_id, topic="customer requested support" if decision.route == "human_handoff" else "complaint")
            db.add(ticket)
            db.flush()
            d.audit(db, customer_id, "create_ticket", "ticket", ticket.id)
        result["ticket_id"] = ticket.id
    safe_state = {"order_id": order_id, "plan_revision": revision, "status": result["status"], "clarifications": int(old.get("clarifications", 0)) + 1 if result["status"] == "clarify" else 0}
    if result["status"] == "clarify" and "return_request" in decision.intents:
        safe_state.update({"pending_intent": "return_request", "item_id": item_id, "quantity": quantity, "reason": reason})
    if result.get("ticket_id"):
        safe_state["ticket_id"] = result["ticket_id"]
    if thread:
        thread.state = safe_state
        thread.updated_at = datetime.now(timezone.utc)
    else:
        db.add(m.ThreadState(id=body.thread_id, customer_id=customer_id, state=safe_state))
    result["trace_id"] = current_trace_id()
    return result
