"use client";

import { useEffect, useRef, useState } from "react";
import Keycloak from "keycloak-js";

type Role = "customer" | "support" | "warehouse" | "supervisor";
type Order = { id: string; status: string; version: number };
type Item = { id: string; quantity: number; paid_cents: number };
type Shipment = { id: string; status: string; delivered_at: string | null };
type Proposal = { id: string; return_id: string; amount_cents: number; status: string };
type DeadlineAlert = { return_id: string; kind: string; deadline_at: string };
type Profile = { consent: boolean; preferences: Record<string, string> };

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
const authMode = process.env.NEXT_PUBLIC_AUTH_MODE || "mock";

export default function Home() {
  const [actor, setActor] = useState("cust-01");
  const [role, setRole] = useState<Role>("customer");
  const [token, setToken] = useState("");
  const [orders, setOrders] = useState<Order[]>([]);
  const [orderId, setOrderId] = useState("demo-order-01");
  const [items, setItems] = useState<Item[]>([]);
  const [shipments, setShipments] = useState<Shipment[]>([]);
  const [shipmentId, setShipmentId] = useState("");
  const [pendingShipmentOptions, setPendingShipmentOptions] = useState<string[]>([]);
  const [message, setMessage] = useState("包裹没到能退吗");
  const [answer, setAnswer] = useState("");
  const [agentMode, setAgentMode] = useState<"single" | "collab">("collab");
  const [returnId, setReturnId] = useState("");
  const [reason, setReason] = useState("不再需要");
  const [quantity, setQuantity] = useState(1);
  const [confirmed, setConfirmed] = useState(false);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [deadlineAlerts, setDeadlineAlerts] = useState<DeadlineAlert[]>([]);
  const [notice, setNotice] = useState("");
  const [keycloak, setKeycloak] = useState<Keycloak | null>(null);
  const threadId = useRef("");
  const submission = useRef({ signature: "", key: "" });
  const [tickets, setTickets] = useState<{ id: string; topic: string; status: string }[]>([]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [languageChoice, setLanguageChoice] = useState("中文");
  const profileIdentity = useRef("");
  profileIdentity.current = `${actor}:${role}`;

  useEffect(() => {
    threadId.current = crypto.randomUUID();
    setOrders([]); setItems([]); setShipments([]); setShipmentId(""); setPendingShipmentOptions([]); setAnswer(""); setConfirmed(false); setProfile(null); setLanguageChoice("中文");
    submission.current = { signature: "", key: "" };
  }, [actor, role]);

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
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (authMode === "oidc") {
      if (keycloak?.authenticated) { await keycloak.updateToken(30); if (keycloak.token) setToken(keycloak.token); }
      headers.authorization = `Bearer ${keycloak?.token || token}`;
    } else {
      headers["x-mock-actor"] = actor;
      headers["x-mock-role"] = role;
    }
    const response = await fetch(apiBase + path, { ...init, headers: { ...headers, ...(init.headers || {}) } });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || data.code || "请求失败");
    return data;
  }

  async function loadOrders() {
    try { setOrders(await call("/orders")); setNotice(""); } catch (error) { setNotice(String(error)); }
  }
  async function loadOrder(id: string) {
    try {
      const [value, packages] = await Promise.all([call(`/orders/${encodeURIComponent(id)}`), call(`/orders/${encodeURIComponent(id)}/shipments`)]);
      setItems(value.items); setShipments(packages); setShipmentId(packages.length === 1 ? packages[0].id : ""); setPendingShipmentOptions([]); setOrderId(id); setNotice("");
    } catch (error) { setNotice(String(error)); }
  }
  async function sendChat() {
    try {
      const value = await call("/chat", { method: "POST", body: JSON.stringify({ thread_id: threadId.current, message, order_id: orderId || null, shipment_id: shipmentId || null, agent_mode: agentMode }) });
      setAnswer(value.answer);
      setPendingShipmentOptions(value.shipment_options || []);
      setNotice(`${value.status} · ${value.findings?.length || 0} 条专职证据`);
    } catch (error) { setNotice(String(error)); }
  }
  async function submitReturn() {
    if (!confirmed || !items[0]) return;
    try {
      const signature = JSON.stringify([orderId, items[0].id, quantity, reason]);
      if (submission.current.signature !== signature) submission.current = { signature, key: crypto.randomUUID() };
      const value = await call("/returns", { method: "POST", body: JSON.stringify({ order_id: orderId, order_item_id: items[0].id, quantity, reason, confirmed, idempotency_key: submission.current.key }) });
      setReturnId(value.id); setNotice(`申请已提交：${value.id}，尚未退款`);
    } catch (error) { setNotice(String(error)); }
  }
  async function warehouse(path: string, body: object) {
    try { const value = await call(path, { method: "POST", body: JSON.stringify(body) }); setNotice(JSON.stringify(value)); } catch (error) { setNotice(String(error)); }
  }
  async function loadProposals() {
    try {
      const [pending, alerts] = await Promise.all([call("/supervisor/proposals"), call("/supervisor/refund-deadlines")]);
      setProposals(pending); setDeadlineAlerts(alerts); setNotice("");
    } catch (error) { setNotice(String(error)); }
  }
  async function loadProfile() {
    const requestedIdentity = profileIdentity.current;
    const value = await call("/profile/preferences") as Profile;
    if (profileIdentity.current !== requestedIdentity) return null;
    setProfile(value);
    setLanguageChoice(value.preferences.language?.toLowerCase() === "english" ? "English" : "中文");
    return value;
  }
  async function updateProfile(path: string, method: "POST" | "PUT" | "DELETE", body?: object) {
    const requestedIdentity = profileIdentity.current;
    try {
      await call(path, { method, ...(body ? { body: JSON.stringify(body) } : {}) });
      if (profileIdentity.current !== requestedIdentity) return;
      await loadProfile();
      if (profileIdentity.current === requestedIdentity) setNotice("偏好设置已更新；下一次咨询生效。请勿在偏好中填写订单或支付资料。");
    } catch (error) { if (profileIdentity.current === requestedIdentity) setNotice(String(error)); }
  }

  return <main>
    <h1>ResolveAI 售后演示</h1>
    <p className="muted">所有订单与退款均为合成模拟。客户申请、仓库质检和主管审批分步执行。</p>
    {authMode === "oidc" ? <p><button disabled={!keycloak || !!token} onClick={() => keycloak?.login()}>Keycloak 登录</button><button className="secondary" disabled={!keycloak || !token} onClick={() => keycloak?.logout()}>退出</button>{token ? "已登录" : "未登录"}</p> : <div className="card"><strong>本机 Mock 身份</strong><label>角色<select value={role} onChange={(e) => setRole(e.target.value as Role)}><option value="customer">customer</option><option value="support">support</option><option value="warehouse">warehouse</option><option value="supervisor">supervisor</option></select></label><label>演示 Actor<input value={actor} onChange={(e) => setActor(e.target.value)} /></label></div>}
    {notice && <p className={notice.includes("Error") ? "error" : "success"}>{notice}</p>}
    <div className="grid">
      {role === "support" && <section className="card"><h2>已分配工单</h2><button onClick={async () => { try { setTickets(await call("/tickets")); } catch (error) { setNotice(String(error)); } }}>刷新工单</button><ul>{tickets.map((ticket) => <li key={ticket.id}>{ticket.id} · {ticket.topic} · {ticket.status}</li>)}</ul></section>}
      {role === "customer" && <>
        <section className="card"><h2>我的订单</h2><button onClick={loadOrders}>刷新订单</button><ul>{orders.map((order) => <li key={order.id}><button className="secondary" onClick={() => loadOrder(order.id)}>{order.id}</button> {order.status}</li>)}</ul><label>订单编号<input value={orderId} onChange={(e) => { setOrderId(e.target.value); setItems([]); setShipments([]); setShipmentId(""); setPendingShipmentOptions([]); }} /></label><button onClick={() => loadOrder(orderId)}>查看商品</button><pre>{JSON.stringify(items, null, 2)}</pre></section>
        <section className="card"><h2>咨询</h2><label>问题<textarea value={message} onChange={(e) => setMessage(e.target.value)} /></label>{(shipments.length > 1 || pendingShipmentOptions.length > 1) && <label>查询包裹<select value={shipmentId} onChange={(e) => setShipmentId(e.target.value)}><option value="">请选择包裹</option>{(shipments.length > 1 ? shipments.map((row) => row.id) : pendingShipmentOptions).map((id) => <option key={id} value={id}>{id}</option>)}</select></label>}<label>Agent 模式<select value={agentMode} onChange={(e) => setAgentMode(e.target.value as "single" | "collab")}><option value="single">单图基线</option><option value="collab">双专职协作</option></select></label><button onClick={sendChat}>发送</button><pre>{answer}</pre></section>
        <section className="card"><h2>答复语言偏好</h2><p>仅在明确同意后保存。语言偏好只改变答复文字，不改变订单、退货或退款规则。</p><button onClick={async () => { const requestedIdentity = profileIdentity.current; try { await loadProfile(); if (profileIdentity.current === requestedIdentity) setNotice(""); } catch (error) { if (profileIdentity.current === requestedIdentity) setNotice(String(error)); } }}>读取偏好</button>{profile && <><p>记忆同意：{profile.consent ? "已同意" : "未同意"} · 已保存语言：{profile.preferences.language || "无"}</p>{!profile.consent ? <button onClick={() => updateProfile("/profile/memory-consent", "POST", { consent: true })}>同意保存偏好</button> : <><label>答复语言<select value={languageChoice} onChange={(e) => setLanguageChoice(e.target.value)}><option value="中文">中文</option><option value="English">English</option></select></label><button onClick={() => updateProfile("/profile/preferences/language", "PUT", { value: languageChoice, confirmed: true })}>确认并保存语言</button><button className="secondary" onClick={() => updateProfile("/profile/preferences/language", "DELETE")}>删除语言偏好</button><button className="secondary" onClick={() => updateProfile("/profile/memory-consent", "POST", { consent: false })}>撤回记忆同意并清除偏好</button></>}</>}</section>
        <section className="card"><h2>申请退货</h2><p>订单 {orderId} · 商品 {items[0]?.id || "请先查看商品"}</p><label>数量<input type="number" min="1" value={quantity} onChange={(e) => setQuantity(Number(e.target.value))} /></label><label>原因<input value={reason} onChange={(e) => setReason(e.target.value)} /></label><label><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />我确认订单、商品、数量、原因并提交申请</label><button disabled={!confirmed || !items[0]} onClick={submitReturn}>提交退货</button><p>{returnId}</p></section>
      </>}
      {role === "warehouse" && <section className="card"><h2>仓库</h2><label>退货申请 ID<input value={returnId} onChange={(e) => setReturnId(e.target.value)} /></label><label>实收数量<input type="number" value={quantity} onChange={(e) => setQuantity(Number(e.target.value))} /></label><button onClick={() => warehouse(`/warehouse/returns/${encodeURIComponent(returnId)}/receipt`, { quantity })}>记录入库</button><button onClick={() => warehouse(`/warehouse/returns/${encodeURIComponent(returnId)}/inspection`, { passed: true, note: "intact" })}>质检通过</button><button className="secondary" onClick={() => warehouse(`/warehouse/returns/${encodeURIComponent(returnId)}/inspection`, { passed: false, note: "exception" })}>质检异常</button><button onClick={() => warehouse(`/returns/${encodeURIComponent(returnId)}/proposal`, {})}>生成规则提案</button></section>}
      {role === "supervisor" && <section className="card"><h2>主管审批</h2><button onClick={loadProposals}>刷新待审提案与期限</button><ul>{proposals.map((proposal) => <li key={proposal.id}>{proposal.id} · ¥{(proposal.amount_cents / 100).toFixed(2)}<br /><button onClick={() => warehouse(`/supervisor/proposals/${proposal.id}/decision`, { approve: true })}>批准</button><button className="secondary" onClick={() => warehouse(`/supervisor/proposals/${proposal.id}/decision`, { approve: false })}>拒绝</button></li>)}</ul><h3>退款处理期限</h3><ul>{deadlineAlerts.map((alert) => <li key={`${alert.return_id}:${alert.kind}`}>{alert.return_id} · {alert.kind === "overdue" ? "已逾期" : "24 小时内到期"} · {new Date(alert.deadline_at).toLocaleString()}</li>)}</ul><p>批准后由受控退款 Worker 执行单笔模拟退款。</p></section>}
    </div>
  </main>;
}
