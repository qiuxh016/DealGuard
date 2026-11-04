import { expect, test, type Page } from "@playwright/test";

async function registerAdmin(page: Page) {
  const suffix = Date.now().toString().slice(-8);
  await page.goto("/");
  await page.getByRole("button", { name: "创建工作区" }).click();
  await page.getByLabel("公司名称").fill(`Policy Import ${suffix}`);
  await page.getByLabel("管理员姓名").fill("Policy Administrator");
  await page.getByLabel("邮箱").fill(`policy-${suffix}@dealguard.test`);
  await page.getByLabel("密码").fill("secure-policy-password-123");
  const registration = page.waitForResponse((response) => response.url().endsWith("/api/v1/auth/register") && response.request().method() === "POST");
  await page.getByRole("button", { name: "创建工作区", exact: true }).last().click();
  const response = await registration;
  expect(response.status(), `Registration failed: ${response.status()} ${await response.text()}`).toBe(201);
  await expect(page.getByRole("heading", { name: "询盘与报价工作台" })).toBeVisible();
  await page.locator("#policy-center").scrollIntoViewIfNeeded();
}

test("admin previews and imports policy spreadsheet rows as pending drafts", async ({ page }) => {
  await registerAdmin(page);
  await page.getByText("CSV / Excel 批量导入").click();
  await page.getByLabel("选择政策表格").setInputFiles({
    name: "policies.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("policy_id,version,title,source,action_type,policy_scope,scope_product_id,customer_tier,max_discount_pct,review_max_discount_pct,exception_approval_allowed\nE2E-IMPORT,v1,VIP promotion,VIP discounts up to 5 percent.,quote_discount,general,,VIP,5,,false\n"),
  });
  await page.getByRole("button", { name: "预览并校验" }).click();
  await expect(page.getByText("共 1 条 · 可导入 1 条 · 需修正 0 条")).toBeVisible();
  await page.getByRole("button", { name: "创建 1 条待审核草稿" }).click();
  await expect(page.getByText("已导入 1 条待审核政策草稿。政策尚未激活。")).toBeVisible();
  await expect(page.getByText("E2E-IMPORT@v1")).toBeVisible();
  await page.getByRole("button", { name: "审核草稿" }).click();
  await expect(page.getByRole("heading", { name: "E2E-IMPORT@v1 · draft" })).toBeVisible();
});

test("AI policy extraction is reviewable and saves only a draft", async ({ page }) => {
  await registerAdmin(page);
  await page.route("**/api/v1/policy-drafts/extract", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      filename: "vip-policy.md", extraction_mode: "llm", source_characters: 36,
      fields: {
        title: "VIP discount terms", action_type: "quote_discount", policy_scope: "general",
        scope_product_id: null, customer_tier: "VIP", max_discount_pct: "4.5",
        review_max_discount_pct: null, exception_approval_allowed: false,
        supporting_evidence: ["VIP discounts up to 4.5 percent."], uncertain_fields: [],
      },
    }),
  }));
  await page.getByLabel("政策编号").fill("E2E-EXTRACT");
  await page.getByLabel("政策文本文件").setInputFiles({
    name: "vip-policy.md", mimeType: "text/markdown", buffer: Buffer.from("VIP discounts up to 4.5 percent."),
  });
  await page.getByRole("button", { name: "AI 识别规则" }).click();
  await expect(page.getByRole("heading", { name: "模型识别建议 · 请核对" })).toBeVisible();
  await expect(page.getByText("“VIP discounts up to 4.5 percent.”")).toBeVisible();
  await expect(page.getByLabel("直接折扣上限 %")).toHaveValue("4.5");
  await page.getByRole("checkbox", { name: "我已对照政策原文核对识别结果" }).check();
  await page.getByRole("button", { name: "确认并保存为待审核草稿" }).click();
  await expect(page.getByText("E2E-EXTRACT@v1")).toBeVisible();
  await expect(page.getByRole("heading", { name: "E2E-EXTRACT@v1 · draft" })).toBeVisible();
});
