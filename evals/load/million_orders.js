import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Run only against the isolated million-order database with AUTH_MODE=mock and
// no model key. This workload is synthetic and makes no refund/payment calls.
const target = __ENV.TARGET_URL || 'http://localhost:8002';
const errorRate = new Rate('resolveai_errors');
const durations = {
  list: new Trend('resolveai_list_ms'),
  detail: new Trend('resolveai_detail_ms'),
  shipment: new Trend('resolveai_shipment_ms'),
  chat: new Trend('resolveai_chat_ms'),
};

const fixedVus = Number(__ENV.FIXED_VUS || 0);
export const options = {
  ...(fixedVus > 0 ? { vus: fixedVus, duration: __ENV.FIXED_DURATION || '20s' } : {
  stages: [
    { duration: '10s', target: 20 }, { duration: '20s', target: 20 },
    { duration: '10s', target: 50 }, { duration: '20s', target: 50 },
    { duration: '10s', target: 100 }, { duration: '20s', target: 100 },
    { duration: '5s', target: 0 },
  ],
  }),
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)'],
};

export default function () {
  const index = ((__VU * 104729) + (__ITER * 17)) % 1000000;
  const orderId = `gen-order-${String(index).padStart(9, '0')}`;
  const customerId = `gen-customer-${String(index % 166666).padStart(8, '0')}`;
  const headers = { 'x-mock-actor': customerId, 'x-mock-role': 'customer' };
  let kind;
  let response;
  const choice = __ITER % 10;
  if (choice === 0) {
    kind = 'chat';
    response = http.post(`${target}/chat`, JSON.stringify({
      thread_id: `load-${__VU}-${__ITER}-${Date.now()}`,
      message: '查询订单', order_id: orderId, agent_mode: 'single',
    }), { headers: { ...headers, 'Content-Type': 'application/json' }, tags: { kind } });
  } else if (choice < 3) {
    kind = 'shipment';
    response = http.get(`${target}/orders/${orderId}/shipments`, { headers, tags: { kind } });
  } else if (choice < 6) {
    kind = 'detail';
    response = http.get(`${target}/orders/${orderId}`, { headers, tags: { kind } });
  } else {
    kind = 'list';
    response = http.get(`${target}/orders`, { headers, tags: { kind } });
  }
  const ok = check(response, { 'HTTP 200': (r) => r.status === 200 });
  errorRate.add(!ok, { kind });
  durations[kind].add(response.timings.duration);
  sleep(0.3);
}
