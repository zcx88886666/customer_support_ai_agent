import { expect, test } from "@playwright/test";

const cors = {
  "access-control-allow-origin": "http://localhost:3001",
  "access-control-allow-headers": "content-type,x-mock-actor,x-mock-role",
  "access-control-allow-methods": "GET,POST,OPTIONS",
};

test("confirmed Agent return submission reuses its key and shows the owned review ticket", async ({ page }) => {
  test.skip(process.env.CHAT_RETURN_UI_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  const submitted: Array<Record<string, unknown>> = [];
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    const path = new URL(request.url()).pathname;
    let data: unknown = {};
    if (path === "/orders/demo-order-02") {
      data = { id: "demo-order-02", items: [{ id: "demo-item-02", quantity: 1, paid_cents: 1018 }] };
    } else if (path === "/orders/demo-order-02/shipments") {
      data = [];
    } else if (path === "/chat" || path === "/returns") {
      submitted.push({ path, ...request.postDataJSON() });
      data = path === "/chat"
        ? { status: "human_review", answer: "人工复核工单已创建", ticket_id: "ticket-chat-review", reason_code: "delivery_unverified", findings: [] }
        : { status: "human_review", ticket_id: "ticket-chat-review", reason_code: "delivery_unverified" };
    } else if (path === "/tickets") {
      data = [{ id: "ticket-chat-review", topic: "return eligibility review: delivery_unverified", status: "open", order_id: "demo-order-02" }];
    }
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(data) });
  });

  await page.goto("http://localhost:3001/");
  await page.getByLabel("订单编号").fill("demo-order-02");
  await page.getByRole("button", { name: "查看商品" }).click();
  await expect(page.getByText("订单 demo-order-02 · 商品 demo-item-02")).toBeVisible();
  await page.getByRole("textbox", { name: "原因", exact: true }).fill("parcel never arrived");
  const agentSubmit = page.getByRole("button", { name: "通过 Agent 提交退货" });
  await expect(agentSubmit).toBeDisabled();
  await page.getByRole("checkbox", { name: /我确认订单/ }).check();
  await agentSubmit.click();
  await expect(page.getByText("已提交人工复核工单 ticket-chat-review；尚未建立退货申请或退款。")).toBeVisible();
  await expect(page.getByText("ticket-chat-review", { exact: true })).toBeVisible();
  await agentSubmit.click();
  await page.getByRole("button", { name: "提交退货", exact: true }).click();
  expect(submitted).toHaveLength(3);
  expect(submitted.map((row) => row.path)).toEqual(["/chat", "/chat", "/returns"]);
  for (const row of submitted.slice(0, 2)) {
    expect(row).toMatchObject({ order_id: "demo-order-02", item_id: "demo-item-02", quantity: 1,
      reason: "parcel never arrived", confirmed: true });
    expect(String(row.message)).toContain("退");
  }
  expect(submitted[0].idempotency_key).toBeTruthy();
  expect(submitted.map((row) => row.idempotency_key)).toEqual([
    submitted[0].idempotency_key, submitted[0].idempotency_key, submitted[0].idempotency_key,
  ]);
  await page.getByRole("textbox", { name: "原因", exact: true }).fill("different reason");
  await expect(page.getByRole("checkbox", { name: /我确认订单/ })).not.toBeChecked();
  await expect(agentSubmit).toBeDisabled();
});

test("eligible Agent return submission shows the created return without a refund claim", async ({ page }) => {
  test.skip(process.env.CHAT_RETURN_UI_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    const path = new URL(request.url()).pathname;
    const data = path === "/orders/demo-order-01"
      ? { id: "demo-order-01", items: [{ id: "demo-item-01", quantity: 1, paid_cents: 1018 }] }
      : path === "/orders/demo-order-01/shipments" ? []
      : path === "/chat" ? { status: "return_requested", answer: "退货申请已提交，尚未退款。", return_id: "return-agent-01", findings: [] }
      : {};
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(data) });
  });
  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "查看商品" }).click();
  await page.getByRole("checkbox", { name: /我确认订单/ }).check();
  await page.getByRole("button", { name: "通过 Agent 提交退货" }).click();
  await expect(page.getByText("申请已提交：return-agent-01，尚未退款")).toBeVisible();
  await expect(page.getByText("return-agent-01", { exact: true })).toBeVisible();
  await page.getByLabel("订单编号").fill("demo-order-02");
  await expect(page.getByText("return-agent-01", { exact: true })).toHaveCount(0);
  await expect(page.getByText("申请已提交：return-agent-01，尚未退款")).toHaveCount(0);
});

test("a late Agent review cannot appear under a different order for the same customer", async ({ page }) => {
  test.skip(process.env.CHAT_RETURN_UI_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  let releaseChat = () => {};
  let chatArrived = () => {};
  const heldChat = new Promise<void>((resolve) => { releaseChat = resolve; });
  const requestArrived = new Promise<void>((resolve) => { chatArrived = resolve; });
  let ticketReads = 0;
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    const path = new URL(request.url()).pathname;
    let data: unknown = {};
    if (path === "/orders/demo-order-02") {
      data = { id: "demo-order-02", items: [{ id: "demo-item-02", quantity: 1, paid_cents: 1018 }] };
    } else if (path === "/orders/demo-order-02/shipments") {
      data = [];
    } else if (path === "/chat") {
      chatArrived();
      await heldChat;
      data = { status: "human_review", answer: "PRIVATE_ORDER_A_REVIEW", ticket_id: "ticket-for-order-a", findings: [] };
    } else if (path === "/tickets") {
      ticketReads += 1;
      data = [{ id: "ticket-for-order-a", topic: "review", status: "open", order_id: "demo-order-02" }];
    }
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(data) });
  });
  await page.goto("http://localhost:3001/");
  await page.getByLabel("订单编号").fill("demo-order-02");
  await page.getByRole("button", { name: "查看商品" }).click();
  await page.getByRole("checkbox", { name: /我确认订单/ }).check();
  await page.getByRole("button", { name: "通过 Agent 提交退货" }).click();
  await requestArrived;
  await page.getByLabel("订单编号").fill("demo-order-01");
  const response = page.waitForResponse((item) => new URL(item.url()).pathname === "/chat");
  releaseChat();
  await response;
  await page.waitForTimeout(100);
  await expect(page.getByText(/ticket-for-order-a|PRIVATE_ORDER_A_REVIEW/)).toHaveCount(0);
  expect(ticketReads).toBe(0);
});

test("switching orders from the list disables the old return form while the new order loads", async ({ page }) => {
  test.skip(process.env.CHAT_RETURN_UI_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  let releaseOrder = () => {};
  let orderArrived = () => {};
  const heldOrder = new Promise<void>((resolve) => { releaseOrder = resolve; });
  const requestArrived = new Promise<void>((resolve) => { orderArrived = resolve; });
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    const path = new URL(request.url()).pathname;
    let data: unknown = {};
    if (path === "/orders") {
      data = [{ id: "demo-order-01", status: "delivered", version: 1 }, { id: "demo-order-02", status: "delivered", version: 1 }];
    } else if (path === "/orders/demo-order-01") {
      data = { id: "demo-order-01", items: [{ id: "demo-item-01", quantity: 1, paid_cents: 1018 }] };
    } else if (path === "/orders/demo-order-02") {
      orderArrived();
      await heldOrder;
      data = { id: "demo-order-02", items: [{ id: "demo-item-02", quantity: 1, paid_cents: 1018 }] };
    } else if (path.endsWith("/shipments")) {
      data = [];
    }
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(data) });
  });
  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "刷新订单" }).click();
  await page.getByRole("button", { name: "demo-order-01" }).click();
  await page.getByRole("checkbox", { name: /我确认订单/ }).check();
  const agentSubmit = page.getByRole("button", { name: "通过 Agent 提交退货" });
  await expect(agentSubmit).toBeEnabled();
  await page.getByRole("button", { name: "demo-order-02" }).click();
  await requestArrived;
  await expect(agentSubmit).toBeDisabled();
  releaseOrder();
  await expect(page.getByText("订单 demo-order-02 · 商品 demo-item-02")).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /我确认订单/ })).not.toBeChecked();
  await expect(agentSubmit).toBeDisabled();
});

test("an older ticket refresh cannot erase a newer return success notice", async ({ page }) => {
  test.skip(process.env.CHAT_RETURN_UI_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  let releaseTickets = () => {};
  let ticketsArrived = () => {};
  const heldTickets = new Promise<void>((resolve) => { releaseTickets = resolve; });
  const requestArrived = new Promise<void>((resolve) => { ticketsArrived = resolve; });
  let chatCalls = 0;
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    const path = new URL(request.url()).pathname;
    let data: unknown = {};
    if (path === "/orders/demo-order-01") {
      data = { id: "demo-order-01", items: [{ id: "demo-item-01", quantity: 1, paid_cents: 1018 }] };
    } else if (path === "/orders/demo-order-01/shipments") {
      data = [];
    } else if (path === "/chat") {
      chatCalls += 1;
      data = chatCalls === 1
        ? { status: "human_review", answer: "review", ticket_id: "old-ticket", findings: [] }
        : { status: "return_requested", answer: "submitted", return_id: "new-return", findings: [] };
    } else if (path === "/tickets") {
      ticketsArrived();
      await heldTickets;
      data = [{ id: "old-ticket", topic: "review", status: "open", order_id: "demo-order-01" }];
    }
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(data) });
  });
  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "查看商品" }).click();
  await page.getByRole("checkbox", { name: /我确认订单/ }).check();
  const agentSubmit = page.getByRole("button", { name: "通过 Agent 提交退货" });
  await agentSubmit.click();
  await requestArrived;
  await page.getByRole("textbox", { name: "原因", exact: true }).fill("new reason");
  await page.getByRole("checkbox", { name: /我确认订单/ }).check();
  await agentSubmit.click();
  await expect(page.getByText("申请已提交：new-return，尚未退款")).toBeVisible();
  const response = page.waitForResponse((item) => new URL(item.url()).pathname === "/tickets");
  releaseTickets();
  await response;
  await expect(page.getByText("申请已提交：new-return，尚未退款")).toBeVisible();
});
