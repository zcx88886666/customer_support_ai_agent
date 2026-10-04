import { expect, test } from "@playwright/test";

test("late profile response cannot display a previous mock actor's preference", async ({ page }) => {
  test.skip(process.env.PROFILE_RACE_TEST !== "1", "Run with a temporary mock-mode web server on port 3001");
  let releaseOwner = () => {};
  let ownerArrived = () => {};
  const heldOwner = new Promise<void>((resolve) => { releaseOwner = resolve; });
  const ownerRequest = new Promise<void>((resolve) => { ownerArrived = resolve; });
  const cors = {
    "access-control-allow-origin": "http://localhost:3001",
    "access-control-allow-headers": "content-type,x-mock-actor,x-mock-role",
    "access-control-allow-methods": "GET,POST,PUT,DELETE,OPTIONS",
  };

  await page.route("http://localhost:8000/profile/preferences", async (route) => {
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers: cors });
      return;
    }
    if (route.request().headers()["x-mock-actor"] === "cust-01") {
      ownerArrived();
      await heldOwner;
      await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify({ consent: true, preferences: { language: "English" } }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", headers: cors, body: JSON.stringify({ consent: true, preferences: { language: "中文" } }) });
  });

  await page.goto("http://localhost:3001/");
  await page.getByRole("button", { name: "读取偏好" }).click();
  await ownerRequest;
  await page.getByLabel("演示 Actor").fill("cust-02");
  releaseOwner();
  await expect(page.getByText("已保存语言：English")).toHaveCount(0);
  await page.getByRole("button", { name: "读取偏好" }).click();
  await expect(page.getByText("已保存语言：中文")).toBeVisible();
});
