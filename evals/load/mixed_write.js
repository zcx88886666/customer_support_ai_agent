import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

// The runner supplies an isolated mock-auth API and one unique order per turn.
const target = __ENV.TARGET_URL;
const maxOrders = Number(__ENV.LOAD_ORDERS || 500);
const vus = Number(__ENV.LOAD_VUS || 10);
const roundsPerVu = Math.floor(maxOrders / vus);
const failureRate = new Rate('load_http_errors');
const completed = new Counter('load_completed_workflows');
const exhausted = new Counter('load_exhausted_orders');
const reads = new Counter('load_successful_reads');
const writes = new Counter('load_successful_writes');
const stageTime = new Trend('load_workflow_ms');

export const options = {
  vus,
  duration: __ENV.LOAD_DURATION || '60s',
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)'],
};

function call(kind, method, path, body, actor, role) {
  const headers = { 'x-mock-actor': actor, 'x-mock-role': role };
  if (body !== null) headers['Content-Type'] = 'application/json';
  const response = http.request(method, `${target}${path}`, body === null ? null : JSON.stringify(body),
                                { headers, tags: { kind }, timeout: '12s' });
  const ok = check(response, { 'HTTP 200': (r) => r.status === 200 });
  failureRate.add(!ok, { kind });
  if (ok) (method === 'GET' ? reads : writes).add(1, { kind });
  return ok ? response : null;
}

export default function () {
  exhausted.add(0);
  const index = (__VU - 1) * roundsPerVu + __ITER;
  if (index >= maxOrders || __ITER >= roundsPerVu) {
    exhausted.add(1);
    sleep(0.5);
    return;
  }
  const key = String(index).padStart(5, '0');
  const orderId = `load-order-${key}`;
  const itemId = `load-item-${key}`;
  const start = Date.now();
  if (!call('detail', 'GET', `/orders/${orderId}`, null, 'load-customer', 'customer')) return;
  if (!call('chat', 'POST', '/chat', {
    thread_id: `load-chat-${key}`, message: '查询订单', order_id: orderId, agent_mode: 'single',
  }, 'load-customer', 'customer')) return;
  const request = call('return', 'POST', '/returns', {
    order_id: orderId, order_item_id: itemId, quantity: 1, reason: 'synthetic load',
    confirmed: true, idempotency_key: `load-return-${key}`,
  }, 'load-customer', 'customer');
  if (!request) return;
  const returnId = request.json('id');
  if (!returnId) return;
  if (!call('receipt', 'POST', `/warehouse/returns/${returnId}/receipt`, { quantity: 1 }, 'warehouse-load', 'warehouse')) return;
  if (!call('inspection', 'POST', `/warehouse/returns/${returnId}/inspection`, { passed: true, note: 'synthetic intact' }, 'warehouse-load', 'warehouse')) return;
  const proposal = call('proposal', 'POST', `/returns/${returnId}/proposal`, {}, 'warehouse-load', 'warehouse');
  if (!proposal) return;
  const proposalId = proposal.json('id');
  if (!proposalId) return;
  if (!call('approval', 'POST', `/supervisor/proposals/${proposalId}/decision`, { approve: true }, 'supervisor-load', 'supervisor')) return;
  completed.add(1);
  stageTime.add(Date.now() - start);
  sleep(0.2);
}
