import { expect, test } from "@playwright/test";

test.beforeEach(() => {
  test.skip(process.env.ACCESSIBILITY_UI_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
});

test("the customer page fits a narrow viewport and exposes a clear keyboard focus", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto("http://localhost:3001/");
  const width = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(width.document).toBeLessThanOrEqual(width.viewport);

  await page.keyboard.press("Tab");
  const focused = page.locator(":focus");
  await expect(focused).toHaveCSS("outline-width", "3px");
});

test("chat answer and completion notice are announced as status updates", async ({ page }) => {
  const cors = {
    "access-control-allow-origin": "http://localhost:3001",
    "access-control-allow-headers": "content-type,x-mock-actor,x-mock-role",
    "access-control-allow-methods": "GET,POST,OPTIONS",
  };
  await page.route("http://localhost:8000/chat", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors,
      body: JSON.stringify({ status: "answered", answer: "Policy answer", findings: [] }) });
  });
  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "Policy answer" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "answered · 0 条专职证据" })).toBeVisible();
});

test("a failed chat request is announced as an alert", async ({ page }) => {
  await page.route("http://localhost:8000/chat", async (route) => {
    await route.fulfill({ status: 503, contentType: "application/json",
      headers: { "access-control-allow-origin": "http://localhost:3001" },
      body: JSON.stringify({ detail: "服务暂时不可用" }) });
  });
  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "服务暂时不可用" })).toBeVisible();
});
