import { expect, test, Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

type Account = { password: string; role: string };
const accounts = JSON.parse(readFileSync(resolve(process.cwd(), "../../.local/demo-accounts.json"), "utf8")) as Record<string, Account>;

async function login(page: Page, username: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "Keycloak 登录" }).click();
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(accounts[username].password);
  await page.locator("#kc-login").click();
  await expect(page).toHaveURL(/^http:\/\/(localhost|127\.0\.0\.1):3000\//);
  await expect(page.getByText("已登录")).toBeVisible();
}

test("customer signs in and sees only owned orders", async ({ page }) => {
  await login(page, "customer-one");
  await expect(page.getByRole("heading", { name: "我的订单" })).toBeVisible();
  await page.getByRole("button", { name: "刷新订单" }).click();
  await expect(page.getByRole("button", { name: "demo-order-02" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "主管审批" })).toHaveCount(0);
});

for (const [username, heading] of [
  ["support-demo", "已分配工单"],
  ["warehouse-demo", "仓库"],
  ["supervisor-demo", "主管审批"],
] as const) {
  test(`${username} receives the correct role view`, async ({ page }) => {
    await login(page, username);
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    await expect(page.getByRole("heading", { name: "我的订单" })).toHaveCount(0);
  });
}
