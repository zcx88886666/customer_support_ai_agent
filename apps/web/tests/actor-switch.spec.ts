import { expect, test } from "@playwright/test";

const cors = {
  "access-control-allow-origin": "http://localhost:3001",
  "access-control-allow-headers": "content-type,x-mock-actor,x-mock-role",
  "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS",
};

test("late order and chat responses stay with the customer who requested them", async ({ page }) => {
  test.skip(process.env.ACTOR_SWITCH_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  let releaseOld = () => {};
  let arrived = () => {};
  let oldRequests = 0;
  const held = new Promise<void>((resolve) => { releaseOld = resolve; });
  const bothArrived = new Promise<void>((resolve) => { arrived = resolve; });

  await page.route(/http:\/\/localhost:8000\/(orders|chat)$/, async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    const oldActor = route.request().headers()["x-mock-actor"] === "cust-01";
    if (oldActor && ++oldRequests === 2) arrived();
    if (oldActor) await held;
    const chat = route.request().url().endsWith("/chat");
    const body = chat
      ? { answer: oldActor ? "PRIVATE_OLD_ANSWER" : "NEW_ANSWER", status: "answered", findings: [] }
      : [{ id: oldActor ? "PRIVATE_OLD_ORDER" : "NEW_ORDER", status: "delivered", version: 1 }];
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify(body) });
  });

  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "刷新订单" }).click();
  await page.getByRole("button", { name: "发送" }).click();
  await bothArrived;
  await page.getByLabel("演示 Actor").fill("cust-02");
  const oldOrders = page.waitForResponse((response) => response.url().endsWith("/orders") && response.request().headers()["x-mock-actor"] === "cust-01");
  const oldChat = page.waitForResponse((response) => response.url().endsWith("/chat") && response.request().headers()["x-mock-actor"] === "cust-01");
  releaseOld();
  await Promise.all([oldOrders, oldChat]);
  await page.waitForTimeout(150);
  await expect(page.getByText("PRIVATE_OLD_ORDER")).toHaveCount(0);
  await expect(page.getByText("PRIVATE_OLD_ANSWER")).toHaveCount(0);
  await expect(page.getByText("answered · 0 条专职证据")).toHaveCount(0);

  await page.getByRole("button", { name: "刷新订单" }).click();
  await expect(page.getByText("NEW_ORDER")).toBeVisible();
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("NEW_ANSWER")).toBeVisible();
});

test("switching actor clears return ID and notice while same-actor role changes retain the ID", async ({ page }) => {
  test.skip(process.env.ACTOR_SWITCH_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  await page.route(/http:\/\/localhost:8000\/warehouse\/returns\/.+\/receipt$/, async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify({ status: "PRIVATE_OLD_NOTICE" }) });
  });
  await page.goto("http://localhost:3001/");
  await page.getByLabel("角色").selectOption("warehouse");
  await page.getByLabel("退货申请 ID").fill("PRIVATE_OLD_RETURN");
  await page.getByLabel("角色").selectOption("supervisor");
  await page.getByLabel("角色").selectOption("warehouse");
  await expect(page.getByLabel("退货申请 ID")).toHaveValue("PRIVATE_OLD_RETURN");
  await page.getByRole("button", { name: "记录入库" }).click();
  await expect(page.getByText(/PRIVATE_OLD_NOTICE/)).toBeVisible();
  await page.getByLabel("演示 Actor").fill("cust-02");
  await expect(page.getByLabel("退货申请 ID")).toHaveValue("");
  await expect(page.getByText(/PRIVATE_OLD_NOTICE/)).toHaveCount(0);
});

test("a late old-customer API error cannot set the new customer's notice", async ({ page }) => {
  test.skip(process.env.ACTOR_SWITCH_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  let releaseOld = () => {};
  let arrived = () => {};
  const held = new Promise<void>((resolve) => { releaseOld = resolve; });
  const oldArrived = new Promise<void>((resolve) => { arrived = resolve; });
  await page.route("http://localhost:8000/orders", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    arrived();
    await held;
    await route.fulfill({ status: 403, contentType: "application/json", headers: cors, body: JSON.stringify({ detail: "PRIVATE_OLD_ERROR" }) });
  });
  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "刷新订单" }).click();
  await oldArrived;
  await page.getByLabel("演示 Actor").fill("cust-02");
  const response = page.waitForResponse("http://localhost:8000/orders");
  releaseOld();
  await response;
  await page.waitForTimeout(150);
  await expect(page.getByText(/PRIVATE_OLD_ERROR/)).toHaveCount(0);
});

test("switching away and back still rejects the earlier customer request", async ({ page }) => {
  test.skip(process.env.ACTOR_SWITCH_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  let releaseOld = () => {};
  let arrived = () => {};
  const held = new Promise<void>((resolve) => { releaseOld = resolve; });
  const oldArrived = new Promise<void>((resolve) => { arrived = resolve; });
  let first = true;
  await page.route("http://localhost:8000/orders", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    if (first) {
      first = false;
      arrived();
      await held;
      await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify([{ id: "PRIVATE_EARLIER_ORDER", status: "delivered", version: 1 }]) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify([{ id: "CURRENT_ORDER", status: "delivered", version: 1 }]) });
  });
  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "刷新订单" }).click();
  await oldArrived;
  await page.getByLabel("演示 Actor").fill("cust-02");
  await page.getByLabel("演示 Actor").fill("cust-01");
  const oldResponse = page.waitForResponse("http://localhost:8000/orders");
  releaseOld();
  await oldResponse;
  await page.waitForTimeout(150);
  await expect(page.getByText("PRIVATE_EARLIER_ORDER")).toHaveCount(0);
  await page.getByRole("button", { name: "刷新订单" }).click();
  await expect(page.getByText("CURRENT_ORDER")).toBeVisible();
});
