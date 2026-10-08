import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

type Account = { password: string; role: string };
const accounts = JSON.parse(readFileSync(resolve(process.cwd(), "../../.local/demo-accounts.json"), "utf8")) as Record<string, Account>;
const isolatedApi = process.env.BROWSER_RECOVERY_API_URL;

test("an OIDC browser retries a committed return after its first response is lost", async ({ page }) => {
  test.setTimeout(90_000);
  test.skip(!isolatedApi, "Set BROWSER_RECOVERY_API_URL to a fresh isolated OIDC API");
  let committedReturnId = "";
  const submittedKeys: string[] = [];
  await page.route("http://localhost:8000/**", async (route) => {
    const request = route.request();
    const target = request.url().replace("http://localhost:8000", isolatedApi!);
    if (new URL(request.url()).pathname === "/returns" && request.method() === "POST") {
      submittedKeys.push((request.postDataJSON() as { idempotency_key: string }).idempotency_key);
      if (!committedReturnId) {
        const upstream = await route.fetch({ url: target });
        expect(upstream.status()).toBe(200);
        committedReturnId = (await upstream.json()).id as string;
        await route.abort("failed");
        return;
      }
    }
    await route.continue({ url: target });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Keycloak 登录" }).click();
  await page.locator("#username").fill("customer-one");
  await page.locator("#password").fill(accounts["customer-one"].password);
  await page.locator("#kc-login").click();
  await expect(page.getByText("已登录")).toBeVisible();
  await page.getByRole("button", { name: "刷新订单" }).click();
  await page.getByRole("button", { name: "demo-order-01" }).click();
  await expect(page.getByText("订单 demo-order-01 · 商品 demo-item-01")).toBeVisible();
  await page.getByRole("checkbox", { name: /我确认订单/ }).check();
  const submit = page.getByRole("button", { name: "提交退货", exact: true });
  await submit.click();
  await expect(page.getByText("提交结果未确认。请在当前页面重试；系统会使用同一请求编号避免重复申请。")).toBeVisible();
  expect(committedReturnId).toBeTruthy();

  const [retryResponse] = await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === "/returns" && response.request().method() === "POST"),
    submit.click(),
  ]);
  expect(retryResponse.status()).toBe(200);
  expect((await retryResponse.json()).id).toBe(committedReturnId);
  await expect(page.getByText(`申请已提交：${committedReturnId}，尚未退款`)).toBeVisible();
  expect(submittedKeys).toHaveLength(2);
  expect(submittedKeys[0]).toBeTruthy();
  expect(submittedKeys[1]).toBe(submittedKeys[0]);
});
