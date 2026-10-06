import { expect, test } from "@playwright/test";

const cors = {
  "access-control-allow-origin": "http://localhost:3001",
  "access-control-allow-headers": "content-type,x-mock-actor,x-mock-role",
  "access-control-allow-methods": "GET,POST,OPTIONS",
};

test("customer, supervisor and assigned support complete a ticket in the browser", async ({ page }) => {
  test.skip(process.env.TICKET_UI_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  const messages: { id: string; actor_type: string; body: string; created_at: string }[] = [];
  let assignee = "";
  let status = "open";
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    const path = new URL(request.url()).pathname;
    const role = request.headers()["x-mock-role"];
    const actor = request.headers()["x-mock-actor"];
    const visible = role === "customer" && actor === "cust-01" || role === "supervisor" || role === "support" && actor === assignee;
    let data: unknown = {};
    let code = visible ? 200 : 404;
    if (path === "/tickets" && request.method() === "GET") data = visible ? [{ id: "ticket-a", topic: "delivery dispute", status, order_id: "demo-order-01" }] : [];
    else if (path === "/tickets/ticket-a" && request.method() === "GET") data = { id: "ticket-a", topic: "delivery dispute", status, order_id: "demo-order-01", messages };
    else if (path === "/supervisor/tickets/ticket-a/assign" && role === "supervisor") {
      assignee = request.postDataJSON().support_actor_id;
      data = { id: "ticket-a", support_actor_id: assignee };
      code = 200;
    } else if (path === "/tickets/ticket-a/messages" && visible && status === "open") {
      messages.push({ id: String(messages.length + 1), actor_type: role, body: request.postDataJSON().body, created_at: new Date().toISOString() });
      data = { id: messages.at(-1)!.id };
    } else if (path === "/tickets/ticket-a/resolve" && role === "support" && visible && status === "open") {
      messages.push({ id: String(messages.length + 1), actor_type: role, body: request.postDataJSON().body, created_at: new Date().toISOString() });
      status = "resolved";
      data = { id: "ticket-a", status };
    }
    await route.fulfill({ status: code, contentType: "application/json", headers: cors, body: JSON.stringify(data) });
  });

  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "刷新工单" }).click();
  await page.getByRole("button", { name: "ticket-a" }).click();
  await page.getByLabel("工单消息").fill("Please check the package.");
  await page.getByRole("button", { name: "发送工单消息" }).click();
  await expect(page.getByText("customer · Please check the package.")).toBeVisible();

  await page.getByLabel("角色").selectOption("supervisor");
  await page.getByLabel("演示 Actor").fill("supervisor-a");
  await page.getByRole("button", { name: "刷新工单" }).click();
  await page.getByLabel("支持人员 Actor ID").fill("support-a");
  await page.getByRole("button", { name: "分配 ticket-a" }).click();

  await page.getByLabel("角色").selectOption("support");
  await page.getByLabel("演示 Actor").fill("support-a");
  await page.getByRole("button", { name: "刷新工单" }).click();
  await page.getByRole("button", { name: "ticket-a" }).click();
  await page.getByLabel("工单消息").fill("Carrier contacted; we will follow up.");
  await page.getByRole("button", { name: "回复并结案" }).click();
  await expect(page.getByText("工单 ticket-a · resolved")).toBeVisible();

  await page.getByLabel("角色").selectOption("customer");
  await page.getByLabel("演示 Actor").fill("cust-01");
  await page.getByRole("button", { name: "刷新工单" }).click();
  await page.getByRole("button", { name: "ticket-a" }).click();
  await expect(page.getByText("support · Carrier contacted; we will follow up.")).toBeVisible();
});


test("late ticket refresh cannot show an earlier actor's success notice", async ({ page }) => {
  test.skip(process.env.TICKET_UI_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  let releaseRefresh = () => {};
  let refreshArrived = () => {};
  const heldRefresh = new Promise<void>((resolve) => { releaseRefresh = resolve; });
  const refreshStarted = new Promise<void>((resolve) => { refreshArrived = resolve; });
  let detailReads = 0;
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    if (request.method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    const path = new URL(request.url()).pathname;
    if (path === "/tickets/ticket-a" && request.method() === "GET" && ++detailReads === 2) {
      refreshArrived();
      await heldRefresh;
    }
    const data = path === "/tickets"
      ? [{ id: "ticket-a", topic: "question", status: "open", order_id: null }]
      : path === "/tickets/ticket-a" && request.method() === "GET"
        ? { id: "ticket-a", topic: "question", status: "open", order_id: null, messages: [] }
        : { id: "message-a", actor_type: "customer" };
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(data) });
  });
  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "刷新工单" }).click();
  await page.getByRole("button", { name: "ticket-a" }).click();
  await page.getByLabel("工单消息").fill("Please help.");
  await page.getByRole("button", { name: "发送工单消息" }).click();
  await refreshStarted;
  await page.getByLabel("演示 Actor").fill("cust-02");
  releaseRefresh();
  await page.waitForTimeout(150);
  await expect(page.getByText("消息已发送")).toHaveCount(0);
  await expect(page.getByText("工单 ticket-a · open")).toHaveCount(0);
});
