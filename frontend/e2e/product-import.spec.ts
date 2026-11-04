import { expect, test, type Page } from "@playwright/test";

async function registerAdmin(page: Page) {
  const suffix = Date.now().toString().slice(-8);
  await page.goto("/");
  await page.getByRole("button", { name: "创建工作区" }).click();
  await page.getByLabel("公司名称").fill(`Import Trading ${suffix}`);
  await page.getByLabel("管理员姓名").fill("Workspace Owner");
  await page.getByLabel("邮箱").fill(`import-${suffix}@dealguard.test`);
  await page.getByLabel("密码").fill("secure-import-password-123");
  const registration = page.waitForResponse((response) => response.url().endsWith("/api/v1/auth/register") && response.request().method() === "POST");
  await page.getByRole("button", { name: "创建工作区", exact: true }).last().click();
  const response = await registration;
  expect(response.status(), `Registration failed: ${response.status()} ${await response.text()}`).toBe(201);
  await expect(page.getByRole("heading", { name: "询盘与报价工作台" })).toBeVisible();
}

test("workspace admin previews and imports a valid product CSV", async ({ page }) => {
  await registerAdmin(page);
  await page.getByRole("link", { name: "商品" }).click();
  await expect(page.getByRole("heading", { name: "商品目录" })).toBeVisible();
  await page.getByRole("button", { name: "批量导入" }).click();
  await page.getByLabel("选择商品表格").setInputFiles({
    name: "products.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("sku,name,currency,unit_price,stock\nP-E2E-IMPORT,Imported Pump,USD,24.50,35\n"),
  });
  await page.getByRole("button", { name: "预览并校验" }).click();
  await expect(page.getByTestId("product-import-valid-rows")).toContainText("1");
  await page.getByRole("button", { name: "确认导入 1 件商品" }).click();
  await expect(page.getByText("已导入 1 件商品。")).toBeVisible();
  await expect(page.getByRole("cell", { name: "P-E2E-IMPORT" })).toBeVisible();
});
