import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const accounts = JSON.parse(readFileSync(resolve(process.cwd(), "../../.local/demo-accounts.json"), "utf8")) as Record<string, { password: string }>;
const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

test("customer can confirm, use, and revoke a response language preference", async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await page.getByRole("button", { name: "Keycloak 登录" }).click();
  await page.locator("#username").fill("customer-one");
  await page.locator("#password").fill(accounts["customer-one"].password);
  await page.locator("#kc-login").click();
  await expect(page.getByRole("heading", { name: "答复语言偏好" })).toBeVisible();

  const initialResponse = page.waitForResponse((response) => response.url().endsWith("/profile/preferences") && response.request().method() === "GET");
  await page.getByRole("button", { name: "读取偏好" }).click();
  const response = await initialResponse;
  const original = await response.json() as { consent: boolean; preferences: Record<string, string> };
  const authorization = response.request().headers().authorization;
  expect(authorization).toMatch(/^Bearer /);
  const headers = { authorization, "content-type": "application/json" };

  try {
    if (!original.consent) {
      await page.getByRole("button", { name: "同意保存偏好" }).click();
      await expect(page.getByRole("button", { name: "确认并保存语言" })).toBeVisible();
    }
    await page.getByLabel("答复语言").selectOption("English");
    await page.getByRole("button", { name: "确认并保存语言" }).click();
    await expect(page.getByText("已保存语言：English")).toBeVisible();
    await page.getByLabel("订单编号").fill("demo-order-02");
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText(/Delivery has not been confirmed/)).toBeVisible();

    await page.getByRole("button", { name: "删除语言偏好" }).click();
    await expect(page.getByText("已保存语言：无")).toBeVisible();
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.getByText(/尚未确认签收/)).toBeVisible();
  } finally {
    const previous = original.preferences.language;
    if (previous !== undefined) {
      const restored = await request.put(`${apiBase}/profile/preferences/language`, { headers, data: { value: previous, confirmed: true } });
      expect(restored.ok()).toBeTruthy();
    } else {
      const cleared = await request.delete(`${apiBase}/profile/preferences/language`, { headers });
      expect(cleared.ok()).toBeTruthy();
    }
    if (!original.consent) {
      const withdrawn = await request.post(`${apiBase}/profile/memory-consent`, { headers, data: { consent: false } });
      expect(withdrawn.ok()).toBeTruthy();
    }
    const check = await request.get(`${apiBase}/profile/preferences`, { headers });
    expect(await check.json()).toEqual(original);
  }
});
