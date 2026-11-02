import { expect, test } from "@playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";

test("Sales A can create the expected VIP quote and the demo data is reset", async ({ page }) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "询盘与报价工作台" })).toBeVisible();
  try {
    await page.getByLabel("询盘内容").fill(
      "I'm a VIP customer and want to buy 1,000 units of Product A. Can you give me an 8% discount? Please prepare a quote.",
    );
    await page.getByRole("button", { name: "运行询盘" }).click();
    await expect(page.locator(".dg-decision-name")).toHaveText("ALLOW");
    await expect(page.locator(".dg-quote-total")).toHaveText("$92,000.00");
    await expect(page.locator(".dg-quote-policy")).toContainText("@v1");
    await expect(page.locator(".dg-evidence").first()).toBeVisible();
    await page.screenshot({ path: join(tmpdir(), "dealguard-e2e-quote.png"), fullPage: true });
  } finally {
    const reset = await page.request.post("http://127.0.0.1:8001/api/v1/demo/reset", {
      headers: { "X-Demo-Actor": "admin" },
    });
    expect(reset.ok()).toBeTruthy();
  }
  expect(pageErrors).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
