import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const isolatedApi = process.env.BROWSER_PACKAGES_API_URL;
const accounts = JSON.parse(readFileSync(resolve(process.cwd(), "../../.local/demo-accounts.json"), "utf8")) as Record<string, { password: string }>;

test("customer selects one owned package before a composite logistics answer", async ({ page }) => {
  test.setTimeout(90_000);
  test.skip(!isolatedApi, "Set BROWSER_PACKAGES_API_URL to an isolated OIDC API and MCP");
  await page.route("http://localhost:8000/**", (route) => route.continue({ url: route.request().url().replace("http://localhost:8000", isolatedApi!) }));
  await page.goto("/");
  await page.getByRole("button", { name: "Keycloak 登录" }).click();
  await page.locator("#username").fill("customer-one");
  await page.locator("#password").fill(accounts["customer-one"].password);
  await page.locator("#kc-login").click();
  await expect(page.getByText("已登录")).toBeVisible();
  await page.getByRole("button", { name: "刷新订单" }).click();
  await page.getByRole("button", { name: "demo-order-02" }).click();
  const choice = page.getByLabel("查询包裹");
  await expect(choice).toBeVisible();
  await expect(choice.locator("option")).toHaveCount(3);
  const [clarification] = await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === "/chat" && response.request().method() === "POST"),
    page.getByRole("button", { name: "发送" }).click(),
  ]);
  expect(clarification.status()).toBe(200);
  const first = await clarification.json();
  expect(first.status).toBe("clarify");
  expect(first.findings).toEqual([]);
  expect(new Set(first.shipment_options)).toEqual(new Set(["demo-shipment-02", "browser-package-02b"]));
  await choice.selectOption("browser-package-02b");
  const [answered] = await Promise.all([
    page.waitForResponse((response) => new URL(response.url()).pathname === "/chat" && response.request().method() === "POST"),
    page.getByRole("button", { name: "发送" }).click(),
  ]);
  expect(answered.status()).toBe(200);
  const second = await answered.json();
  expect(second.status).toBe("answered");
  expect(second.findings.find((finding: { source_ids: string[] }) => finding.source_ids.includes("browser-package-02b"))).toBeTruthy();
  await expect(page.getByText(/包裹 browser-package-02b 状态为 delivered/)).toBeVisible();
});
