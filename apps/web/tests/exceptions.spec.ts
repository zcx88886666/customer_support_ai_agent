import { expect, test, Browser, Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

type Account = { password: string; role: string };
const accounts = JSON.parse(readFileSync(resolve(process.cwd(), "../../.local/demo-accounts.json"), "utf8")) as Record<string, Account>;
const isolatedApi = process.env.BROWSER_FLOW_API_URL;

async function signedInPage(browser: Browser, username: string): Promise<Page> {
  const page = await browser.newPage();
  await page.route("http://localhost:8000/**", (route) => route.continue({ url: route.request().url().replace("http://localhost:8000", isolatedApi!) }));
  await page.goto("/");
  await page.getByRole("button", { name: "Keycloak 登录" }).click();
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(accounts[username].password);
  await page.locator("#kc-login").click();
  await expect(page.getByText("已登录")).toBeVisible();
  return page;
}

async function submitReturn(page: Page, orderId: string, reason: string): Promise<string> {
  await page.getByRole("button", { name: "刷新订单" }).click();
  await page.getByRole("button", { name: orderId }).click();
  await expect(page.getByText(`订单 ${orderId} · 商品`)).toBeVisible();
  await page.getByRole("textbox", { name: "原因", exact: true }).fill(reason);
  await page.getByRole("checkbox", { name: /我确认订单/ }).check();
  const [response] = await Promise.all([
    page.waitForResponse((item) => new URL(item.url()).pathname === "/returns" && item.request().method() === "POST"),
    page.getByRole("button", { name: "提交退货" }).click(),
  ]);
  expect(response.status()).toBe(200);
  const returnId = (await response.json()).id as string;
  await expect(page.getByText(`申请已提交：${returnId}，尚未退款`)).toBeVisible();
  return returnId;
}

async function warehouseStep(page: Page, returnId: string, button: string, path: string, expectedStatus: number): Promise<Record<string, unknown>> {
  await page.getByLabel("退货申请 ID").fill(returnId);
  const [response] = await Promise.all([
    page.waitForResponse((item) => new URL(item.url()).pathname === path && item.request().method() === "POST"),
    page.getByRole("button", { name: button }).click(),
  ]);
  expect(response.status()).toBe(expectedStatus);
  return response.json();
}

test("failed warehouse inspection cannot create a refund proposal", async ({ browser }) => {
  test.setTimeout(90_000);
  test.skip(!isolatedApi, "Set BROWSER_FLOW_API_URL to an isolated OIDC API");
  const customer = await signedInPage(browser, "customer-one");
  const returnId = await submitReturn(customer, "demo-order-06", "damaged during return");
  const warehouse = await signedInPage(browser, "warehouse-demo");
  await warehouseStep(warehouse, returnId, "记录入库", `/warehouse/returns/${returnId}/receipt`, 200);
  await warehouseStep(warehouse, returnId, "质检异常", `/warehouse/returns/${returnId}/inspection`, 200);
  await warehouseStep(warehouse, returnId, "生成规则提案", `/returns/${returnId}/proposal`, 409);
  const supervisor = await signedInPage(browser, "supervisor-demo");
  const [list] = await Promise.all([
    supervisor.waitForResponse((item) => new URL(item.url()).pathname === "/supervisor/proposals"),
    supervisor.getByRole("button", { name: "刷新待审提案与期限" }).click(),
  ]);
  const proposals = await list.json() as Array<{ return_id: string }>;
  expect(proposals.some((proposal) => proposal.return_id === returnId)).toBe(false);
  writeFileSync(resolve(process.cwd(), "../../.local/browser-exception-result.json"), JSON.stringify({ exception_return_id: returnId }) + "\n");
  await Promise.all([customer.close(), warehouse.close(), supervisor.close()]);
});

test("an intervening return makes a proposal stale and requires new approval", async ({ browser }) => {
  test.setTimeout(90_000);
  test.skip(!isolatedApi, "Set BROWSER_FLOW_API_URL to an isolated OIDC API");
  const customer = await signedInPage(browser, "customer-one");
  const firstReturnId = await submitReturn(customer, "demo-order-03", "first item");
  const warehouse = await signedInPage(browser, "warehouse-demo");
  await warehouseStep(warehouse, firstReturnId, "记录入库", `/warehouse/returns/${firstReturnId}/receipt`, 200);
  await warehouseStep(warehouse, firstReturnId, "质检通过", `/warehouse/returns/${firstReturnId}/inspection`, 200);
  const oldProposal = await warehouseStep(warehouse, firstReturnId, "生成规则提案", `/returns/${firstReturnId}/proposal`, 200);
  const oldProposalId = oldProposal.id as string;

  const secondReturnId = await submitReturn(customer, "demo-order-03", "second item");
  expect(secondReturnId).not.toBe(firstReturnId);
  const supervisor = await signedInPage(browser, "supervisor-demo");
  await supervisor.getByRole("button", { name: "刷新待审提案与期限" }).click();
  const staleRow = supervisor.getByRole("listitem").filter({ hasText: oldProposalId });
  await expect(staleRow).toBeVisible();
  const [staleDecision] = await Promise.all([
    supervisor.waitForResponse((item) => new URL(item.url()).pathname === `/supervisor/proposals/${oldProposalId}/decision`),
    staleRow.getByRole("button", { name: "批准" }).click(),
  ]);
  expect(staleDecision.status()).toBe(409);

  const newProposal = await warehouseStep(warehouse, firstReturnId, "生成规则提案", `/returns/${firstReturnId}/proposal`, 200);
  const newProposalId = newProposal.id as string;
  expect(newProposalId).not.toBe(oldProposalId);
  await supervisor.getByRole("button", { name: "刷新待审提案与期限" }).click();
  await expect(supervisor.getByRole("listitem").filter({ hasText: oldProposalId })).toHaveCount(0);
  const currentRow = supervisor.getByRole("listitem").filter({ hasText: newProposalId });
  const [approval] = await Promise.all([
    supervisor.waitForResponse((item) => new URL(item.url()).pathname === `/supervisor/proposals/${newProposalId}/decision`),
    currentRow.getByRole("button", { name: "批准" }).click(),
  ]);
  expect(approval.status()).toBe(200);
  writeFileSync(resolve(process.cwd(), "../../.local/browser-stale-result.json"), JSON.stringify({ first_return_id: firstReturnId, second_return_id: secondReturnId, old_proposal_id: oldProposalId, new_proposal_id: newProposalId }) + "\n");
  await Promise.all([customer.close(), warehouse.close(), supervisor.close()]);
});
