import { expect, test, Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

type Account = { password: string; role: string };
const accounts = JSON.parse(readFileSync(resolve(process.cwd(), "../../.local/demo-accounts.json"), "utf8")) as Record<string, Account>;
const isolatedApi = process.env.BROWSER_FLOW_API_URL;
const orderId = process.env.BROWSER_FLOW_ORDER_ID || "demo-order-01";

async function useIsolatedApi(page: Page) {
  await page.route("http://localhost:8000/**", async (route) => {
    const target = route.request().url().replace("http://localhost:8000", isolatedApi!);
    await route.continue({ url: target });
  });
}

async function login(page: Page, username: string) {
  await useIsolatedApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Keycloak 登录" }).click();
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(accounts[username].password);
  await page.locator("#kc-login").click();
  await expect(page.getByText("已登录")).toBeVisible();
}

test("customer, warehouse, and supervisor complete the browser return flow", async ({ browser }) => {
  test.setTimeout(90_000);
  test.skip(!isolatedApi, "Set BROWSER_FLOW_API_URL to an isolated OIDC API");

  const customerContext = await browser.newContext();
  const customer = await customerContext.newPage();
  await login(customer, "customer-one");
  await customer.getByRole("button", { name: "刷新订单" }).click();
  await customer.getByRole("button", { name: orderId }).click();
  await expect(customer.getByText(`订单 ${orderId} · 商品`)).toBeVisible();
  await customer.getByRole("checkbox", { name: /我确认订单/ }).check();
  const [returnResponse] = await Promise.all([
    customer.waitForResponse((response) => new URL(response.url()).pathname === "/returns" && response.request().method() === "POST"),
    customer.getByRole("button", { name: "提交退货" }).click(),
  ]);
  expect(returnResponse.status()).toBe(200);
  const returnId = (await returnResponse.json()).id as string;
  await expect(customer.getByText(`申请已提交：${returnId}，尚未退款`)).toBeVisible();
  await customerContext.close();

  const warehouseContext = await browser.newContext();
  const warehouse = await warehouseContext.newPage();
  await login(warehouse, "warehouse-demo");
  await warehouse.getByLabel("退货申请 ID").fill(returnId);
  let proposalId = "";
  for (const [button, path] of [
    ["记录入库", `/warehouse/returns/${returnId}/receipt`],
    ["质检通过", `/warehouse/returns/${returnId}/inspection`],
    ["生成规则提案", `/returns/${returnId}/proposal`],
  ] as const) {
    const [response] = await Promise.all([
      warehouse.waitForResponse((item) => new URL(item.url()).pathname === path && item.request().method() === "POST"),
      warehouse.getByRole("button", { name: button }).click(),
    ]);
    expect(response.status()).toBe(200);
    if (button === "生成规则提案") {
      proposalId = (await response.json()).id as string;
    }
  }
  expect(proposalId).toBeTruthy();
  await warehouseContext.close();

  const supervisorContext = await browser.newContext();
  const supervisor = await supervisorContext.newPage();
  await login(supervisor, "supervisor-demo");
  await supervisor.getByRole("button", { name: "刷新待审提案与期限" }).click();
  const proposal = supervisor.getByRole("listitem").filter({ hasText: proposalId });
  await expect(proposal).toBeVisible();
  const [approval] = await Promise.all([
    supervisor.waitForResponse((response) => new URL(response.url()).pathname === `/supervisor/proposals/${proposalId}/decision` && response.request().method() === "POST"),
    proposal.getByRole("button", { name: "批准" }).click(),
  ]);
  expect(approval.status()).toBe(200);
  await supervisorContext.close();

  writeFileSync(resolve(process.cwd(), "../../.local/browser-flow-result.json"), JSON.stringify({ order_id: orderId, return_id: returnId, proposal_id: proposalId }) + "\n");
});
