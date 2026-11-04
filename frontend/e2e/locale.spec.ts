import { expect, test } from "@playwright/test";

test("login screen renders in English when English is selected", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("界面语言").selectOption("en");

  await expect(page.getByRole("heading", { name: "Sign in to DealGuard" })).toBeVisible();
  await expect(page.getByLabel("Email")).toBeVisible();
  await expect(page.getByLabel("Password")).toBeVisible();
});
