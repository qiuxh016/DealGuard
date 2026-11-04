import { expect, test, type Page } from "@playwright/test";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_BACKEND_PORT ?? "8001"}`;

async function registerAdmin(page: Page) {
  const suffix = Date.now().toString().slice(-8);
  await page.goto("/");
  await page.getByRole("button", { name: "创建工作区" }).click();
  await page.getByLabel("公司名称").fill(`Impact Trading ${suffix}`);
  await page.getByLabel("管理员姓名").fill("Impact Administrator");
  await page.getByLabel("邮箱").fill(`impact-${suffix}@dealguard.test`);
  await page.getByLabel("密码").fill("secure-impact-password-123");
  const registration = page.waitForResponse((response) => response.url().endsWith("/api/v1/auth/register") && response.request().method() === "POST");
  await page.getByRole("button", { name: "创建工作区", exact: true }).last().click();
  const response = await registration;
  expect(response.status(), `Registration failed: ${response.status()} ${await response.text()}`).toBe(201);
  await expect(page.getByRole("heading", { name: "询盘与报价工作台" })).toBeVisible();
  return suffix;
}

async function post<T>(page: Page, path: string, data: unknown, headers?: Record<string, string>): Promise<T> {
  const response = await page.request.post(`${API}${path}`, { data, headers });
  expect(response.ok(), `${path}: ${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<T>;
}

test("admin compares a candidate policy against saved inquiries with traceable facts", async ({ page, browser }) => {
  const suffix = await registerAdmin(page);
  const customer = await post<{ id: string }>(page, "/api/v1/customers", { name: `Impact Buyer ${suffix}`, tier: "VIP" });
  const product = await post<{ id: string }>(page, "/api/v1/products", {
    id: `P-IMPACT${suffix}`, name: "Impact Test Product", currency: "USD", unit_price: "50.00", stock: 500,
  });
  const policyId = `IMPACT-${suffix}`;
  const source = "VIP customers may receive a discount of up to 10 percent on all products.";
  const baseline = await post<{ id: string }>(page, "/api/v1/policy-drafts/confirmed", {
    policy_id: policyId, version: "v1", title: "VIP discount up to 10 percent", source,
    action_type: "quote_discount", policy_scope: "general", scope_product_id: null,
    customer_tier: "VIP", max_discount_pct: 10, review_max_discount_pct: null,
    exception_approval_allowed: false,
  });
  await post(page, `/api/v1/policy-drafts/${baseline.id}/activate`, {});
  const invitation = await post<{ token: string }>(page, "/api/v1/auth/invitations", {
    email: `sales-${suffix}@dealguard.test`, role: "sales", customer_id: customer.id,
  });
  const salesContext = await browser.newContext();
  try {
    const accepted = await salesContext.request.post(`${API}/api/v1/auth/accept-invitation`, {
      data: { token: invitation.token, display_name: "Impact Sales", password: "secure-sales-password-123" },
    });
    expect(accepted.status(), await accepted.text()).toBe(201);
    const response = await salesContext.request.post(`${API}/api/v1/runs`, {
      data: { inquiry: `Please quote ${product.id} 100 units at 8% discount.`, customer_id: customer.id },
      headers: { "Idempotency-Key": `impact-${suffix}` },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    const run = await response.json() as { run_id: string; decision: { decision: string } };
    expect(run.decision.decision).toBe("ALLOW");
  } finally {
    await salesContext.close();
  }
  await post(page, "/api/v1/policy-drafts/confirmed", {
    policy_id: policyId, version: "v2", title: "VIP discount up to 5 percent",
    source: "VIP customers may receive a discount of up to 5 percent on all products.",
    action_type: "quote_discount", policy_scope: "general", scope_product_id: null,
    customer_tier: "VIP", max_discount_pct: 5, review_max_discount_pct: null,
    exception_approval_allowed: false,
  });

  await page.getByRole("link", { name: "政策影响分析" }).click();
  await expect(page.getByRole("heading", { name: "政策影响分析" })).toBeVisible();
  await page.getByLabel("候选政策草稿").selectOption({ label: `${policyId} · v2 — VIP discount up to 5 percent` });
  await page.getByRole("button", { name: "运行影响分析" }).click();
  await expect(page.getByRole("heading", { name: "逐条回放结果" })).toBeVisible();
  await expect(page.getByText("决策发生变化").locator(".." )).toContainText("1");
  await page.getByText("查看可信快照与引用").click();
  await expect(page.getByText(/VIP customers may receive a discount of up to 10 percent/)).toBeVisible();
  await expect(page.getByText(/SHA-256/)).toBeVisible();
});
