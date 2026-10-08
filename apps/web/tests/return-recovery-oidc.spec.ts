import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

type Account = { password: string; role: string };
type RecoveryCase = { label: string; orderId: string; path: string; button: string; outcome: "return" | "review" };
const accounts = JSON.parse(readFileSync(resolve(process.cwd(), "../../.local/demo-accounts.json"), "utf8")) as Record<string, Account>;
const isolatedApi = process.env.BROWSER_RECOVERY_API_URL;
const scenarios: RecoveryCase[] = [
  { label: "direct return", orderId: "demo-order-01", path: "/returns", button: "提交退货", outcome: "return" },
  { label: "Agent return", orderId: "demo-order-06", path: "/chat", button: "通过 Agent 提交退货", outcome: "return" },
  { label: "explicit review", orderId: "demo-order-02", path: "/returns/review", button: "自动退货不适用时申请人工复核", outcome: "review" },
];

for (const scenario of scenarios) {
  test(`an OIDC browser retries a committed ${scenario.label} after its response is lost`, async ({ page }) => {
    test.setTimeout(90_000);
    test.skip(!isolatedApi, "Set BROWSER_RECOVERY_API_URL to a fresh isolated OIDC API");
    let committedId = "";
    const submittedKeys: string[] = [];
    await page.route("http://localhost:8000/**", async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const target = request.url().replace("http://localhost:8000", isolatedApi!);
      if (path === scenario.path && request.method() === "POST") {
        submittedKeys.push((request.postDataJSON() as { idempotency_key: string }).idempotency_key);
        if (!committedId) {
          const upstream = await route.fetch({ url: target });
          expect(upstream.status()).toBe(200);
          const payload = await upstream.json() as Record<string, string>;
          if (scenario.outcome === "return") {
            if (scenario.path === "/chat") expect(payload.status).toBe("return_requested");
            committedId = payload.return_id || payload.id;
          } else {
            expect(payload.status).toBe("human_review");
            committedId = payload.ticket_id;
          }
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
    await page.getByRole("button", { name: scenario.orderId }).click();
    await expect(page.getByText(new RegExp(`订单 ${scenario.orderId} · 商品`))).toBeVisible();
    await page.getByRole("checkbox", { name: /我确认订单/ }).check();
    const submit = page.getByRole("button", { name: scenario.button, exact: true });
    await submit.click();
    await expect(page.getByText("提交结果未确认。请在当前页面重试；系统会使用同一请求编号避免重复申请。")).toBeVisible();
    expect(committedId).toBeTruthy();

    const [retryResponse] = await Promise.all([
      page.waitForResponse((response) => new URL(response.url()).pathname === scenario.path && response.request().method() === "POST"),
      submit.click(),
    ]);
    expect(retryResponse.status()).toBe(200);
    const retryPayload = await retryResponse.json() as Record<string, string>;
    expect(retryPayload.return_id || retryPayload.ticket_id || retryPayload.id).toBe(committedId);
    if (scenario.outcome === "return") {
      await expect(page.getByText(`申请已提交：${committedId}，尚未退款`)).toBeVisible();
    } else {
      await expect(page.getByText(`已提交人工复核工单 ${committedId}；尚未建立退货申请或退款。`)).toBeVisible();
    }
    expect(submittedKeys).toHaveLength(2);
    expect(submittedKeys[0]).toBeTruthy();
    expect(submittedKeys[1]).toBe(submittedKeys[0]);
  });
}
