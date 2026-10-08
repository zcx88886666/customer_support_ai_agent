"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Keycloak from "keycloak-js";

type Role = "customer" | "support" | "warehouse" | "supervisor";
type TicketStatus = "all" | "open" | "resolved";
type Order = { id: string; status: string; version: number };
type Item = { id: string; quantity: number; paid_cents: number };
type Shipment = { id: string; status: string; delivered_at: string | null };
type Proposal = { id: string; return_id: string; amount_cents: number; status: string };
type DeadlineAlert = { return_id: string; kind: string; deadline_at: string };
type Profile = { consent: boolean; preferences: Record<string, string> };
type Ticket = { id: string; topic: string; status: string; order_id: string | null; return_id?: string | null; created_at: string };
type TicketDetail = Ticket & { messages: { id: string; actor_type: string; body: string; created_at: string }[] };

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const authMode = process.env.NEXT_PUBLIC_AUTH_MODE || "mock";
const uncertainReturnNotice = "提交结果未确认。请在当前页面重试；系统会使用同一请求编号避免重复申请。";
class StaleRequest extends Error {}

export default function Home() {
  const [actor, setActor] = useState("cust-01");
  const [role, setRole] = useState<Role>("customer");
  const [token, setToken] = useState("");
  const [orders, setOrders] = useState<Order[]>([]);
  const [orderId, setOrderId] = useState("demo-order-01");
  const [items, setItems] = useState<Item[]>([]);
  const [returnItemId, setReturnItemId] = useState("");
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [shipmentId, setShipmentId] = useState("");
  const [pendingShipmentOptions, setPendingShipmentOptions] = useState<string[]>([]);
  const [message, setMessage] = useState("包裹没到能退吗");
  const [answer, setAnswer] = useState("");
  const [agentMode, setAgentMode] = useState<"single" | "collab">("collab");
  const [returnId, setReturnId] = useState("");
  const [reason, setReason] = useState("不再需要");
  const [quantity, setQuantity] = useState(1);
  const [warehouseNote, setWarehouseNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [deadlineAlerts, setDeadlineAlerts] = useState<DeadlineAlert[]>([]);
  const [notice, setNotice] = useState("");
  const [keycloak, setKeycloak] = useState<Keycloak | null>(null);
  const threadId = useRef("");
  const submission = useRef({ signature: "", key: "" });
  const returnFormVersion = useRef(0);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [ticketStatus, setTicketStatus] = useState<TicketStatus>("all");
  const ticketStatusRef = useRef<TicketStatus>("all");
  const [ticketsHasMore, setTicketsHasMore] = useState(false);
  const ticketListVersion = useRef(0);
  const [ticketDetail, setTicketDetail] = useState<TicketDetail | null>(null);
  const [ticketText, setTicketText] = useState("");
  const [supportAssignee, setSupportAssignee] = useState("");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [languageChoice, setLanguageChoice] = useState("中文");
  const returnItem = items.find((item) => item.id === returnItemId);
  const profileIdentity = useRef("");
  const identityGeneration = useRef(0);
  const currentIdentity = `${actor}:${role}`;
  if (profileIdentity.current !== currentIdentity) {
    profileIdentity.current = currentIdentity;
    identityGeneration.current += 1;
  }

  useLayoutEffect(() => {
    threadId.current = crypto.randomUUID();
    ticketListVersion.current += 1;
    ticketStatusRef.current = role === "customer" ? "all" : "open";
    setTicketStatus(ticketStatusRef.current);
    setOrders([]); setItems([]); setReturnItemId(""); setShipments([]); setShipmentId(""); setPendingShipmentOptions([]); setAnswer(""); setConfirmed(false); setProfile(null); setLanguageChoice("中文"); setTickets([]); setTicketsHasMore(false); setTicketDetail(null); setTicketText(""); setSupportAssignee(""); setWarehouseNote(""); setProposals([]); setDeadlineAlerts([]); setNotice("");
    submission.current = { signature: "", key: "" };
  }, [actor, role]);

  useLayoutEffect(() => {
    setReturnId(""); setOrderId("demo-order-01"); setMessage("包裹没到能退吗"); setReason("不再需要"); setQuantity(1);
  }, [actor]);

  useEffect(() => {
    if (authMode !== "oidc") return;
    const client = new Keycloak({ url: process.env.NEXT_PUBLIC_OIDC_URL || "http://localhost:8080", realm: "resolveai", clientId: "resolveai-web" });
    client.init({ onLoad: "check-sso", pkceMethod: "S256" }).then((ok) => {
      setKeycloak(client);
      if (ok && client.token) {
        setToken(client.token);
        const roles = client.realmAccess?.roles || [];
        setActor(client.subject || "");
        setRole(roles.includes("supervisor") ? "supervisor" : roles.includes("warehouse") ? "warehouse" : roles.includes("support") ? "support" : "customer");
      }
    }).catch(() => setNotice("OIDC 登录初始化失败"));
  }, []);

  async function call(path: string, init: RequestInit = {}) {
    const requestedGeneration = identityGeneration.current;
    const headers: Record<string, string> = { "content-type": "application/json" };
    try {
      if (authMode === "oidc") {
        if (keycloak?.authenticated) { await keycloak.updateToken(30); if (keycloak.token) setToken(keycloak.token); }
        if (identityGeneration.current !== requestedGeneration) throw new StaleRequest();
        headers.authorization = `Bearer ${keycloak?.token || token}`;
      } else {
        headers["x-mock-actor"] = actor;
        headers["x-mock-role"] = role;
      }
      const response = await fetch(apiBase + path, { ...init, headers: { ...headers, ...(init.headers || {}) } });
      const data = await response.json();
      if (identityGeneration.current !== requestedGeneration) throw new StaleRequest();
      if (!response.ok) throw new Error(data.detail || data.code || "请求失败");
      return data;
    } catch (error) {
      if (identityGeneration.current !== requestedGeneration) throw new StaleRequest();
      throw error;
    }
  }

  function showError(error: unknown) {
    if (!(error instanceof StaleRequest)) setNotice(String(error));
  }

  function showReturnSubmissionError(error: unknown) {
    if (error instanceof TypeError) setNotice(uncertainReturnNotice);
    else showError(error);
  }

  function invalidateReturnForm() {
    returnFormVersion.current += 1;
    setReturnId("");
    setNotice("");
  }

  async function loadOrders() {
    try { setOrders(await call("/orders")); setNotice(""); } catch (error) { showError(error); }
  }
  async function loadOrder(id: string) {
    invalidateReturnForm();
    const requestedFormVersion = returnFormVersion.current;
    setItems([]); setReturnItemId(""); setShipments([]); setShipmentId(""); setPendingShipmentOptions([]); setQuantity(1); setConfirmed(false);
    try {
      const [value, packages] = await Promise.all([call(`/orders/${encodeURIComponent(id)}`), call(`/orders/${encodeURIComponent(id)}/shipments`)]);
      if (returnFormVersion.current !== requestedFormVersion) return;
      setItems(value.items); setReturnItemId(value.items.length === 1 ? value.items[0].id : ""); setShipments(packages); setShipmentId(packages.length === 1 ? packages[0].id : ""); setPendingShipmentOptions([]); setOrderId(id); setNotice("");
    } catch (error) { if (returnFormVersion.current === requestedFormVersion) showError(error); }
  }
  async function sendChat() {
    try {
      const value = await call("/chat", { method: "POST", body: JSON.stringify({ thread_id: threadId.current, message, order_id: orderId || null, shipment_id: shipmentId || null, agent_mode: agentMode }) });
      setAnswer(value.answer);
      setPendingShipmentOptions(value.shipment_options || []);
      setNotice(value.ticket_id ? `已转人工，工单 ${value.ticket_id}。请在“我的工单”查看进度。` : `${value.status} · ${value.findings?.length || 0} 条专职证据`);
    } catch (error) { showError(error); }
  }
  async function submitReturnThroughAgent() {
    if (!confirmed || !returnItem) return;
    const requestedGeneration = identityGeneration.current;
    const requestedFormVersion = returnFormVersion.current;
    try {
      const signature = JSON.stringify([orderId, returnItem.id, quantity, reason]);
      if (submission.current.signature !== signature) submission.current = { signature, key: crypto.randomUUID() };
      const value = await call("/chat", { method: "POST", body: JSON.stringify({
        thread_id: threadId.current, message: "我要退这件商品", order_id: orderId, item_id: returnItem.id,
        quantity, reason, confirmed: true, idempotency_key: submission.current.key, agent_mode: agentMode,
      }) });
      if (identityGeneration.current !== requestedGeneration || returnFormVersion.current !== requestedFormVersion) return;
      setAnswer(value.answer || "");
      setPendingShipmentOptions([]);
      if (value.status === "human_review" && value.ticket_id) {
        setReturnId("");
        await loadTickets(null, ticketStatusRef.current, false);
        if (identityGeneration.current === requestedGeneration && returnFormVersion.current === requestedFormVersion) setNotice(`已提交人工复核工单 ${value.ticket_id}；尚未建立退货申请或退款。`);
      } else if (value.status === "return_requested" && value.return_id) {
        setReturnId(value.return_id);
        setNotice(`申请已提交：${value.return_id}，尚未退款`);
      } else {
        if (value.ticket_id) await loadTickets(null, ticketStatusRef.current, false);
        if (identityGeneration.current === requestedGeneration && returnFormVersion.current === requestedFormVersion) setNotice(value.ticket_id ? `已转人工，工单 ${value.ticket_id}。请在“我的工单”查看进度。` : value.answer || value.status);
      }
    } catch (error) { if (identityGeneration.current === requestedGeneration && returnFormVersion.current === requestedFormVersion) showReturnSubmissionError(error); }
  }
  async function submitReturn() {
    if (!confirmed || !returnItem) return;
    const requestedGeneration = identityGeneration.current;
    const requestedFormVersion = returnFormVersion.current;
    try {
      const signature = JSON.stringify([orderId, returnItem.id, quantity, reason]);
      if (submission.current.signature !== signature) submission.current = { signature, key: crypto.randomUUID() };
      const value = await call("/returns", { method: "POST", body: JSON.stringify({ order_id: orderId, order_item_id: returnItem.id, quantity, reason, confirmed, idempotency_key: submission.current.key }) });
      if (identityGeneration.current !== requestedGeneration || returnFormVersion.current !== requestedFormVersion) return;
      if (value.ticket_id) {
        setReturnId("");
        await loadTickets(null, ticketStatusRef.current, false);
        if (identityGeneration.current === requestedGeneration && returnFormVersion.current === requestedFormVersion) setNotice(`已提交人工复核工单 ${value.ticket_id}；尚未建立退货申请或退款。`);
      } else {
        setReturnId(value.id); setNotice(`申请已提交：${value.id}，尚未退款`);
      }
    } catch (error) { if (identityGeneration.current === requestedGeneration && returnFormVersion.current === requestedFormVersion) showReturnSubmissionError(error); }
  }
  async function requestReturnReview() {
    if (!confirmed || !returnItem) return;
    const requestedGeneration = identityGeneration.current;
    const requestedFormVersion = returnFormVersion.current;
    try {
      const signature = JSON.stringify([orderId, returnItem.id, quantity, reason]);
      if (submission.current.signature !== signature) submission.current = { signature, key: crypto.randomUUID() };
      const value = await call("/returns/review", { method: "POST", body: JSON.stringify({ order_id: orderId, order_item_id: returnItem.id, quantity, reason, confirmed, idempotency_key: submission.current.key }) });
      if (identityGeneration.current !== requestedGeneration || returnFormVersion.current !== requestedFormVersion) return;
      setReturnId("");
      await loadTickets(null, ticketStatusRef.current, false);
      if (identityGeneration.current === requestedGeneration && returnFormVersion.current === requestedFormVersion) setNotice(`已提交人工复核工单 ${value.ticket_id}；尚未建立退货申请或退款。`);
    } catch (error) { if (identityGeneration.current === requestedGeneration && returnFormVersion.current === requestedFormVersion) showReturnSubmissionError(error); }
  }
  async function warehouse(path: string, body: object) {
    try {
      const value = await call(path, { method: "POST", body: JSON.stringify(body) });
      setNotice(value.ticket_id ? `数量异常已转人工，退货进入异常状态。工单 ${value.ticket_id}` : JSON.stringify(value));
    } catch (error) { showError(error); }
  }
  async function loadProposals() {
    try {
      const [pending, alerts] = await Promise.all([call("/supervisor/proposals"), call("/supervisor/refund-deadlines")]);
      setProposals(pending); setDeadlineAlerts(alerts); setNotice("");
    } catch (error) { showError(error); }
  }
  async function loadProfile() {
    const requestedGeneration = identityGeneration.current;
    const value = await call("/profile/preferences") as Profile;
    if (identityGeneration.current !== requestedGeneration) return null;
    setProfile(value);
    setLanguageChoice(value.preferences.language?.toLowerCase() === "english" ? "English" : "中文");
    return value;
  }
  async function updateProfile(path: string, method: "POST" | "PUT" | "DELETE", body?: object) {
    const requestedGeneration = identityGeneration.current;
    try {
      await call(path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
      if (identityGeneration.current !== requestedGeneration) return;
      await loadProfile();
      if (identityGeneration.current === requestedGeneration) setNotice("偏好设置已更新；下一次咨询生效。请勿在偏好中填写订单或支付资料。");
    } catch (error) { if (identityGeneration.current === requestedGeneration) showError(error); }
  }

  async function loadTickets(cursor: Ticket | null = null, status: TicketStatus = ticketStatusRef.current, clearNotice = true) {
    const version = ++ticketListVersion.current;
    try {
      const params = new URLSearchParams({ status });
      if (cursor) {
        params.set("before_created_at", cursor.created_at);
        params.set("before_id", cursor.id);
      }
      const values = await call(`/tickets?${params}`) as Ticket[];
      if (version !== ticketListVersion.current) return;
      setTickets((current) => cursor ? [...current, ...values.filter((ticket) => !current.some((seen) => seen.id === ticket.id))] : values);
      setTicketsHasMore(values.length === 100);
      if (!cursor) setTicketDetail((current) => current && values.some((ticket) => ticket.id === current.id) ? current : null);
      if (clearNotice) setNotice("");
    } catch (error) { if (clearNotice && version === ticketListVersion.current) showError(error); }
  }
  async function openTicket(id: string) {
    try { setTicketDetail(await call(`/tickets/${encodeURIComponent(id)}`)); setTicketText(""); setNotice(""); } catch (error) { showError(error); }
  }
  async function ticketAction(action: "messages" | "resolve") {
    if (!ticketDetail || !ticketText.trim()) return;
    const requestedGeneration = identityGeneration.current;
    const id = ticketDetail.id;
    try {
      await call(`/tickets/${encodeURIComponent(id)}/${action}`, { method: "POST", body: JSON.stringify({ body: ticketText }) });
      await openTicket(id);
      await loadTickets();
      if (identityGeneration.current === requestedGeneration) setNotice(action === "resolve" ? "工单已处理。退货与退款仍需各自的业务审核。" : "消息已发送");
    } catch (error) { showError(error); }
  }
  async function assignTicket(id: string) {
    if (!supportAssignee.trim()) return;
    const requestedGeneration = identityGeneration.current;
    try {
      await call(`/supervisor/tickets/${encodeURIComponent(id)}/assign`, { method: "POST", body: JSON.stringify({ support_actor_id: supportAssignee.trim() }) });
      await loadTickets();
      if (identityGeneration.current === requestedGeneration) setNotice("工单已分配");
    } catch (error) { showError(error); }
  }

  return <main>
    <h1>ResolveAI 售后演示</h1>
    <p className="muted">所有订单与退款均为合成模拟。客户申请、仓库质检和主管审批分步执行。</p>
    {authMode === "oidc" ? <p><button disabled={!keycloak || !!token} onClick={() => keycloak?.login()}>Keycloak 登录</button><button className="secondary" disabled={!keycloak || !token} onClick={() => keycloak?.logout()}>退出</button>{token ? "已登录" : "未登录"}</p> : <div className="card"><strong>本机 Mock 身份</strong><label>角色<select value={role} onChange={(e) => setRole(e.target.value as Role)}><option value="customer">customer</option><option value="support">support</option><option value="warehouse">warehouse</option><option value="supervisor">supervisor</option></select></label><label>演示 Actor<input value={actor} onChange={(e) => setActor(e.target.value)} /></label></div>}
    {notice && <p className={notice.includes("Error") || notice === uncertainReturnNotice ? "error" : "success"}>{notice}</p>}
    <div className="grid">
      {(role === "customer" || role === "support" || role === "supervisor") && <section className="card">
        <h2>{role === "customer" ? "我的工单" : role === "support" ? "已分配工单" : "人工工单分配"}</h2>
        {role === "support" && <p>我的 Actor ID：{actor}</p>}
        <label>工单状态<select value={ticketStatus} onChange={(event) => {
          const next = event.target.value as TicketStatus;
          ticketStatusRef.current = next;
          setTicketStatus(next); setTickets([]); setTicketsHasMore(false); setTicketDetail(null);
          void loadTickets(null, next);
        }}><option value="all">全部</option><option value="open">待处理</option><option value="resolved">已结案</option></select></label>
        <button onClick={() => loadTickets()}>刷新工单</button>
        <ul>{tickets.map((ticket) => <li key={ticket.id}><button className="secondary" onClick={() => openTicket(ticket.id)}>{ticket.id}</button> · {ticket.topic} · {ticket.status}{ticket.return_id && <> · 退货 {ticket.return_id}</>}</li>)}</ul>
        {ticketsHasMore && <button className="secondary" onClick={() => loadTickets(tickets[tickets.length - 1])}>加载更多工单</button>}
        {role === "supervisor" && <><label>支持人员 Actor ID<input value={supportAssignee} onChange={(e) => setSupportAssignee(e.target.value)} /></label>{tickets.filter((ticket) => ticket.status === "open").map((ticket) => <button key={ticket.id} disabled={!supportAssignee.trim()} onClick={() => assignTicket(ticket.id)}>分配 {ticket.id}</button>)}</>}
        {ticketDetail && <div><h3>工单 {ticketDetail.id} · {ticketDetail.status}</h3>{ticketDetail.return_id && <p>关联退货：{ticketDetail.return_id}</p>}<ul>{ticketDetail.messages.map((item) => <li key={item.id}>{item.actor_type} · {item.body}</li>)}</ul>{ticketDetail.status === "open" && role !== "supervisor" && <><label>工单消息<textarea value={ticketText} onChange={(e) => setTicketText(e.target.value)} /></label><button disabled={!ticketText.trim()} onClick={() => ticketAction("messages")}>发送工单消息</button>{role === "support" && <button disabled={!ticketText.trim()} onClick={() => ticketAction("resolve")}>回复并结案</button>}</>}</div>}
      </section>}
      {role === "customer" && <>
        <section className="card"><h2>我的订单</h2><button onClick={loadOrders}>刷新订单</button><ul>{orders.map((order) => <li key={order.id}><button className="secondary" onClick={() => loadOrder(order.id)}>{order.id}</button> {order.status}</li>)}</ul><label>订单编号<input value={orderId} onChange={(e) => { invalidateReturnForm(); setOrderId(e.target.value); setItems([]); setReturnItemId(""); setShipments([]); setShipmentId(""); setPendingShipmentOptions([]); setConfirmed(false); }} /></label><button onClick={() => loadOrder(orderId)}>查看商品</button><pre>{JSON.stringify(items, null, 2)}</pre></section>
        <section className="card"><h2>咨询</h2><label>问题<textarea value={message} onChange={(e) => setMessage(e.target.value)} /></label>{(shipments.length > 1 || pendingShipmentOptions.length > 1) && <label>查询包裹<select value={shipmentId} onChange={(e) => setShipmentId(e.target.value)}><option value="">请选择包裹</option>{(shipments.length > 1 ? shipments.map((row) => row.id) : pendingShipmentOptions).map((id) => <option key={id} value={id}>{id}</option>)}</select></label>}<label>Agent 模式<select value={agentMode} onChange={(e) => setAgentMode(e.target.value as "single" | "collab")}><option value="single">单图基线</option><option value="collab">双专职协作</option></select></label><button onClick={sendChat}>发送</button><pre>{answer}</pre></section>
        <section className="card"><h2>答复语言偏好</h2><p>仅在明确同意后保存。语言偏好只改变答复文字，不改变订单、退货或退款规则。</p><button onClick={async () => { const requestedGeneration = identityGeneration.current; try { await loadProfile(); if (identityGeneration.current === requestedGeneration) setNotice(""); } catch (error) { if (identityGeneration.current === requestedGeneration) showError(error); } }}>读取偏好</button>{profile && <><p>记忆同意：{profile.consent ? "已同意" : "未同意"} · 已保存语言：{profile.preferences.language || "无"}</p>{!profile.consent ? <button onClick={() => updateProfile("/profile/memory-consent", "POST", { consent: true })}>同意保存偏好</button> : <><label>答复语言<select value={languageChoice} onChange={(e) => setLanguageChoice(e.target.value)}><option value="中文">中文</option><option value="English">English</option></select></label><button onClick={() => updateProfile("/profile/preferences/language", "PUT", { value: languageChoice, confirmed: true })}>确认并保存语言</button><button className="secondary" onClick={() => updateProfile("/profile/preferences/language", "DELETE")}>删除语言偏好</button><button className="secondary" onClick={() => updateProfile("/profile/memory-consent", "POST", { consent: false })}>撤回记忆同意并清除偏好</button></>}</>}</section>
        <section className="card"><h2>申请退货</h2><p>订单 {orderId} · 商品 {returnItem?.id || (items.length > 1 ? "请先选择商品" : "请先查看商品")}</p><label>退货商品项<select value={returnItemId} onChange={(e) => { invalidateReturnForm(); setReturnItemId(e.target.value); setQuantity(1); setConfirmed(false); }}><option value="">请选择商品</option>{items.map((item) => <option key={item.id} value={item.id}>{item.id} · 数量 {item.quantity}</option>)}</select></label><label>数量<input type="number" min="1" max={returnItem?.quantity} value={quantity} onChange={(e) => { invalidateReturnForm(); setQuantity(Number(e.target.value)); setConfirmed(false); }} /></label><label>原因<input value={reason} onChange={(e) => { invalidateReturnForm(); setReason(e.target.value); setConfirmed(false); }} /></label><label><input type="checkbox" checked={confirmed} onChange={(e) => { invalidateReturnForm(); setConfirmed(e.target.checked); }} />我确认订单、商品、数量、原因并提交申请</label><button disabled={!confirmed || !returnItem} onClick={submitReturn}>提交退货</button><button className="secondary" disabled={!confirmed || !returnItem} onClick={submitReturnThroughAgent}>通过 Agent 提交退货</button><button className="secondary" disabled={!confirmed || !returnItem} onClick={requestReturnReview}>自动退货不适用时申请人工复核</button><p>{returnId}</p></section>
      </>}
      {role === "warehouse" && <section className="card"><h2>仓库</h2><label>退货申请 ID<input value={returnId} onChange={(e) => setReturnId(e.target.value)} /></label><label>实收数量<input type="number" min="0" value={quantity} onChange={(e) => setQuantity(Number(e.target.value))} /></label><label>数量异常说明<input value={warehouseNote} onChange={(e) => setWarehouseNote(e.target.value)} /></label><button onClick={() => warehouse(`/warehouse/returns/${encodeURIComponent(returnId)}/receipt`, { quantity, note: warehouseNote.trim() })}>记录入库</button><button className="secondary" disabled={!returnId.trim() || !warehouseNote.trim() || !Number.isInteger(quantity) || quantity < 0} onClick={() => warehouse(`/warehouse/returns/${encodeURIComponent(returnId)}/receipt-dispute`, { observed_quantity: quantity, note: warehouseNote.trim() })}>上报数量异常并转人工</button><button onClick={() => warehouse(`/warehouse/returns/${encodeURIComponent(returnId)}/inspection`, { passed: true, note: "intact" })}>质检通过</button><button className="secondary" onClick={() => warehouse(`/warehouse/returns/${encodeURIComponent(returnId)}/inspection`, { passed: false, note: "exception" })}>质检异常</button><button onClick={() => warehouse(`/returns/${encodeURIComponent(returnId)}/proposal`, {})}>生成规则提案</button></section>}
      {role === "supervisor" && <section className="card"><h2>主管审批</h2><button onClick={loadProposals}>刷新待审提案与期限</button><ul>{proposals.map((proposal) => <li key={proposal.id}>{proposal.id} · ¥{(proposal.amount_cents / 100).toFixed(2)}<br /><button onClick={() => warehouse(`/supervisor/proposals/${proposal.id}/decision`, { approve: true })}>批准</button><button className="secondary" onClick={() => warehouse(`/supervisor/proposals/${proposal.id}/decision`, { approve: false })}>拒绝</button></li>)}</ul><h3>退款处理期限</h3><ul>{deadlineAlerts.map((alert) => <li key={`${alert.return_id}:${alert.kind}`}>{alert.return_id} · {alert.kind === "overdue" ? "已逾期" : "24 小时内到期"} · {new Date(alert.deadline_at).toLocaleString()}</li>)}</ul><p>批准后由受控退款 Worker 执行单笔模拟退款。</p></section>}
    </div>
  </main>;
}
