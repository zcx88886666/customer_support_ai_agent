import { expect, test } from "@playwright/test";

const cors = {
  "access-control-allow-origin": "http://localhost:3001",
  "access-control-allow-headers": "content-type,x-mock-actor,x-mock-role",
  "access-control-allow-methods": "GET,POST,OPTIONS",
};

test("chat response for an earlier order cannot replace the current order answer", async ({ page }) => {
  test.skip(process.env.CHAT_CONTEXT_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  let release = () => {};
  let arrived = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  const requestArrived = new Promise<void>((resolve) => { arrived = resolve; });
  await page.route("http://localhost:8000/chat", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    expect(route.request().postDataJSON().order_id).toBe("demo-order-01");
    arrived();
    await held;
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors,
      body: JSON.stringify({ status: "answered", answer: "OLD_ORDER_ANSWER", findings: [] }) });
  });

  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "发送" }).click();
  await requestArrived;
  await page.getByLabel("订单编号").fill("demo-order-02");
  const response = page.waitForResponse("http://localhost:8000/chat");
  release();
  await response;
  await page.waitForTimeout(150);

  await expect(page.getByLabel("订单编号")).toHaveValue("demo-order-02");
  await expect(page.getByText("OLD_ORDER_ANSWER")).toHaveCount(0);
  await expect(page.getByText("answered · 0 条专职证据")).toHaveCount(0);
});
