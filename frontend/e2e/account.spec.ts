import { expect, test, type Page } from "@playwright/test";

async function registerAdmin(page: Page) {
  const suffix = Date.now().toString().slice(-8);
  await page.goto("/");
  await page.getByRole("button", { name: "创建工作区" }).click();
  await page.getByLabel("公司名称").fill(`Account Trading ${suffix}`);
  await page.getByLabel("管理员姓名").fill("Workspace Owner");
  await page.getByLabel("邮箱").fill(`account-${suffix}@dealguard.test`);
  await page.getByLabel("密码").fill("secure-account-password-123");
  const registration = page.waitForResponse((response) => response.url().endsWith("/api/v1/auth/register") && response.request().method() === "POST");
  await page.getByRole("button", { name: "创建工作区", exact: true }).last().click();
  const response = await registration;
  expect(response.status(), `Registration failed: ${response.status()} ${await response.text()}`).toBe(201);
  await expect(page.getByRole("heading", { name: "询盘与报价工作台" })).toBeVisible();
}

test("admin can update personal profile, password, and manage workspace invitations", async ({ page }) => {
  await registerAdmin(page);
  await page.getByRole("link", { name: "账户" }).click();
  await expect(page.getByRole("heading", { name: "账户与团队" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "团队成员" })).toBeVisible();
  await page.getByLabel("工作区角色").selectOption("manager");

  await page.getByLabel("姓名").fill("Updated Workspace Owner");
  await page.getByRole("button", { name: "保存资料" }).click();
  await expect(page.getByRole("status")).toContainText("个人资料已更新");

  await page.getByLabel("当前密码").fill("secure-account-password-123");
  await page.getByLabel("新密码", { exact: true }).fill("secure-account-password-456");
  await page.getByLabel("确认新密码").fill("secure-account-password-456");
  await page.getByRole("button", { name: "更新密码" }).click();
  await expect(page.getByRole("status")).toContainText("密码已修改");
  await expect(page.getByRole("heading", { name: "账户与团队" })).toBeVisible();

  await page.getByLabel("员工邮箱").fill(`new-sales-${Date.now()}@dealguard.test`);
  await page.getByRole("button", { name: "生成邀请链接" }).click();
  await expect(page.getByText("待接受邀请")).toBeVisible();
  await expect(page.getByLabel("一次性链接")).toHaveValue(/\/join\?token=/);
});
